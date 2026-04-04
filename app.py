"""
Pennysnipe — web app
Flask server + background RSS scanner + SQLite storage.
Run with: python app.py
"""

import os, re, ssl, json, sqlite3, urllib.request, urllib.parse, urllib.error, time
import threading, atexit
from datetime import datetime, timezone
from typing import Optional

import feedparser
from flask import Flask, render_template, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler

# ── Config ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR        = os.path.dirname(os.path.abspath(__file__))
# DB_PATH can be overridden via env var for Railway volume persistence
DB_PATH           = os.environ.get("DB_PATH", os.path.join(SCRIPT_DIR, "deals.db"))
CHECK_INTERVAL    = 120          # seconds between scans
PULSE_INTERVAL    = 600          # seconds between pulse checks (10 min)
MIN_DISCOUNT      = 80           # minimum % off to flag
MAX_PRICE         = 10.00        # maximum price to flag (USD)
AMAZON_AFFILIATE  = os.environ.get("AMAZON_TAG", "")  # set via Railway env var
PORT              = int(os.environ.get("PORT", 5000))  # Railway sets PORT automatically

# ── Feeds ──────────────────────────────────────────────────────────────────────

FEEDS = [
    {"name": "Slickdeals",           "url": "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"},
    {"name": "Slickdeals Price Err", "url": "https://slickdeals.net/newsearch.php?q=price+error&searcharea=deals&rss=1"},
    {"name": "Slickdeals Tech",      "url": "https://slickdeals.net/newsearch.php?forumid=9&mode=frontpage&searcharea=deals&rss=1"},
    # All subreddits combined into ONE request — avoids 429 rate limiting
    {"name": "Reddit",               "url": "https://www.reddit.com/r/deals+buildapcsales+GameDeals+frugalmalefashion+frugal+consoledeals/.rss"},
    {"name": "DealNews Electronics", "url": "https://www.dealnews.com/c142/Electronics/?rss=1"},
    {"name": "DealNews Computers",   "url": "https://www.dealnews.com/c39/Computers/?rss=1"},
    {"name": "9to5Toys",             "url": "https://9to5toys.com/feed/"},
    {"name": "9to5Mac Deals",        "url": "https://9to5mac.com/guides/deals/feed/"},
    {"name": "Hip2Save",             "url": "https://hip2save.com/feed/"},
    {"name": "BensBargains",         "url": "https://bensbargains.com/rss/"},
    {"name": "Money Saving Mom",     "url": "https://moneysavingmom.com/feed/"},
    {"name": "Living Rich Coupons",  "url": "https://www.livingrichwithcoupons.com/feed"},
    # Bing News RSS — replaces Twitter/Nitter, indexes social + news deal mentions
    {"name": "Bing: price error",    "url": "https://www.bing.com/news/search?q=price+error+amazon&format=RSS&mkt=en-US"},
    {"name": "Bing: price glitch",   "url": "https://www.bing.com/news/search?q=price+glitch+deal&format=RSS&mkt=en-US"},
    {"name": "Bing: $0.01 deal",     "url": "https://www.bing.com/news/search?q=%240.01+deal&format=RSS&mkt=en-US"},
    {"name": "Bing: extreme sale",   "url": "https://www.bing.com/news/search?q=extreme+discount+sale+today&format=RSS&mkt=en-US"},
    {"name": "Bing: mispriced",      "url": "https://www.bing.com/news/search?q=mispriced+item+deal&format=RSS&mkt=en-US"},
]

# Triggers — genuine price errors and penny deals only
KEYWORDS = [
    "price error", "price mistake", "mispriced", "pricing error",
    "glitch", "accidental", "$0.01", "0.01", "1 cent", "one cent",
    "penny deal", "99% off", "98% off", "97% off", "96% off", "95% off",
]

# If any of these appear in the deal text, skip it entirely
EXCLUDE_KEYWORDS = [
    "uber eats", "doordash", "grubhub", "instacart", "postmates", "seamless",
    "promo code", "coupon code", "discount code", "voucher", "code:",
    "free trial", "per month", "/month", "monthly", "subscription", "annual plan",
    "gift card", "e-gift", "egift", "store credit",
    "free shipping", "free ship", "ship free",
    "restaurant", "food delivery", "meal kit", "takeout", "takeaway",
    "% cashback", "cash back", "rakuten", "ibotta",
    "app only", "in-store only", "in store only",
]

_SSL_CTX    = ssl.create_default_context()
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ── Database ───────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id            TEXT PRIMARY KEY,
                source        TEXT NOT NULL,
                title         TEXT NOT NULL,
                link          TEXT,
                reason        TEXT,
                price         REAL,
                discount      INTEGER,
                score         INTEGER DEFAULT 0,
                pulse         TEXT DEFAULT 'unverified',
                pulse_ts      TEXT,
                confirm_count INTEGER DEFAULT 0,
                ts            TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts     ON deals(ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON deals(source)")

        # Migration: safely add new columns if upgrading from an older schema
        # Must run BEFORE creating any index that references these columns
        for col, typedef in [
            ("score",         "INTEGER DEFAULT 0"),
            ("pulse",         "TEXT DEFAULT 'unverified'"),
            ("pulse_ts",      "TEXT"),
            ("confirm_count", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE deals ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists

        # Score index after migration ensures the column exists
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON deals(score DESC)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen (
                id TEXT PRIMARY KEY,
                ts TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id TEXT NOT NULL,
                price   REAL NOT NULL,
                ts      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ph_deal ON price_history(deal_id, ts)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS confirmations (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id TEXT NOT NULL,
                ts      TEXT DEFAULT (datetime('now'))
            )
        """)

# ── Scanner helpers ────────────────────────────────────────────────────────────

def _fetch_bytes(url: str, _retry: bool = True) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/rss+xml, application/atom+xml, */*",
        })
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retry:
            print(f"  [429] Rate limited on {url[:50]}… waiting 60s then retrying once.")
            time.sleep(60)
            return _fetch_bytes(url, _retry=False)
        print(f"  [WARN] fetch failed ({url[:60]}…): {e}")
        return None
    except Exception as e:
        print(f"  [WARN] fetch failed ({url[:60]}…): {e}")
        return None

def _looks_like_xml(data: bytes) -> bool:
    h = data[:200].lstrip()
    return h.startswith(b"<?xml") or h.startswith(b"<rss") or h.startswith(b"<feed")

def make_uid(source: str, title: str, url: str = "") -> str:
    """Stable dedup key: source + normalised title (first 80 chars)."""
    clean = re.sub(r"[^a-z0-9 ]", "", title.lower())
    clean = re.sub(r"\s+", " ", clean).strip()[:80]
    return f"{source}::{clean}" if clean else url

def strip_tracking(url: str) -> str:
    """Remove common tracking query params from a URL."""
    _TRACKING = {"utm_source","utm_medium","utm_campaign","utm_content",
                 "utm_term","ref","source","affiliate","clickid","fbclid"}
    try:
        u = urllib.parse.urlparse(url)
        qs = {k: v for k, v in urllib.parse.parse_qs(u.query).items()
              if k.lower() not in _TRACKING}
        return u._replace(query=urllib.parse.urlencode(qs, doseq=True)).geturl()
    except Exception:
        return url

def extract_price(text: str) -> Optional[float]:
    m = re.findall(r"\$\s*(\d+(?:\.\d+)?)", text)
    return min(float(x) for x in m) if m else None

def extract_discount(text: str) -> Optional[int]:
    m = re.findall(r"(\d+)\s*%\s*off", text, re.IGNORECASE)
    return max(int(x) for x in m) if m else None

def is_extreme_deal(title: str, summary: str):
    combined = (title + " " + summary).lower()
    for ex in EXCLUDE_KEYWORDS:
        if ex in combined:
            return False, "", None, None
    for kw in KEYWORDS:
        if kw in combined:
            return True, f'keyword: "{kw}"', None, None
    pct = extract_discount(combined)
    if pct and pct >= MIN_DISCOUNT:
        return True, f"{pct}% off", extract_price(combined), pct
    price = extract_price(combined)
    if price is not None and price <= MAX_PRICE:
        return True, f"price ${price:.2f}", price, extract_discount(combined)
    return False, "", None, None

def apply_affiliate(url: str) -> str:
    if not AMAZON_AFFILIATE or not url:
        return url
    try:
        u = urllib.parse.urlparse(url)
        if "amazon.com" in u.netloc or "amzn." in u.netloc:
            q = urllib.parse.parse_qs(u.query)
            q["tag"] = [AMAZON_AFFILIATE]
            url = u._replace(query=urllib.parse.urlencode(q, doseq=True)).geturl()
    except Exception:
        pass
    return url

# ── Deal scoring ───────────────────────────────────────────────────────────────

SOURCE_CREDIBILITY = {
    "Slickdeals": 15, "Slickdeals Price Err": 15, "Slickdeals Tech": 12,
    "DealNews Electronics": 12, "DealNews Computers": 12, "DealNews": 12,
    "Woot": 12, "9to5Toys": 10, "9to5Mac Deals": 10, "Reddit": 10,
    "TechBargains": 8, "Brad's Deals": 8, "BensBargains": 8, "Hip2Save": 7,
    "Bing: price error": 8, "Bing: price glitch": 8, "Bing: $0.01 deal": 7,
    "Bing: extreme sale": 5, "Bing: mispriced": 7,
    "Money Saving Mom": 6, "Living Rich Coupons": 6, "Krazy Coupon Lady": 6,
}

def calculate_score(discount: Optional[int], price: Optional[float],
                    source: str, reason: str, confirm_count: int = 0) -> int:
    """Score a deal 0-100 based on discount depth, price, source credibility,
    reason quality, and community confirmations."""
    score = 0

    # Discount component: 0-35 pts
    if discount:
        score += min(35, int(discount * 0.35))

    # Price component: 0-25 pts
    if price is not None:
        if price <= 0.01:    score += 25
        elif price <= 0.50:  score += 22
        elif price <= 1.00:  score += 18
        elif price <= 5.00:  score += 12
        elif price <= 10.00: score += 6

    # Source credibility: 0-15 pts
    score += SOURCE_CREDIBILITY.get(source, 5)

    # Reason quality: 0-15 pts
    if reason:
        r = reason.lower()
        if any(k in r for k in ["price error","price mistake","mispriced",
                                  "pricing error","glitch","accidental"]):
            score += 15
        elif any(k in r for k in ["0.01","1 cent","one cent","penny"]):
            score += 15
        elif "99%" in r or "98%" in r:
            score += 12
        elif any(x in r for x in ["97%","96%","95%"]):
            score += 10
        elif "% off" in r:
            score += 6
        else:
            score += 3

    # Community confirmations: 0-10 pts (each confirmation worth 2 pts, caps at 5)
    score += min(10, confirm_count * 2)

    return min(100, score)

# ── HTML scrapers (for sites with dead RSS feeds) ─────────────────────────────

SCRAPE_SITES = [
    {"name": "Woot",             "url": "https://www.woot.com"},
    {"name": "DealNews",         "url": "https://www.dealnews.com/"},
    {"name": "TechBargains",     "url": "https://www.techbargains.com/"},
    {"name": "Brad's Deals",     "url": "https://www.bradsdeals.com/"},
    {"name": "Krazy Coupon Lady","url": "https://thekrazycouponlady.com/deals"},
]

_STRIP_TAGS = re.compile(r"<[^>]+>")

def scrape_deals(site, seen_set):
    """Scrape a deal site's HTML and return matching extreme deals."""
    raw = _fetch_bytes(site["url"])
    if not raw:
        return []
    html = raw.decode("utf-8", errors="replace")
    base = urllib.parse.urlparse(site["url"])
    deals = []

    for m in re.finditer(r'<a\s[^>]*href="([^"]{5,})"[^>]*>([\s\S]*?)</a>', html, re.IGNORECASE):
        href, inner = m.group(1), m.group(2)
        title = _STRIP_TAGS.sub("", inner).strip()
        title = re.sub(r"\s+", " ", title)

        if len(title) < 12 or len(title) > 300:
            continue

        start = max(0, m.start() - 300)
        end   = min(len(html), m.end() + 300)
        context = _STRIP_TAGS.sub(" ", html[start:end])

        if href.startswith("/"):
            href = f"{base.scheme}://{base.netloc}{href}"
        elif not href.startswith("http"):
            continue

        uid = make_uid(site["name"], title, href)
        if uid in seen_set:
            continue
        seen_set.add(uid)

        matched, reason, price, discount = is_extreme_deal(title, context)
        if matched:
            score = calculate_score(discount, price, site["name"], reason)
            deals.append({
                "id": uid, "source": site["name"], "title": title,
                "link": apply_affiliate(strip_tracking(href)), "reason": reason,
                "price": price, "discount": discount, "score": score,
            })
    return deals

# ── Scanner ────────────────────────────────────────────────────────────────────

_last_scan: Optional[str] = None
_scan_count: int = 0

def scan():
    global _last_scan, _scan_count
    new_count = 0
    db = get_db()

    # Load seen IDs
    seen = set(r[0] for r in db.execute("SELECT id FROM seen"))

    # — RSS feeds — 2s delay between each to avoid rate limits
    for feed in FEEDS:
        time.sleep(2)
        raw = _fetch_bytes(feed["url"])
        if not raw or not _looks_like_xml(raw):
            continue
        parsed = feedparser.parse(raw)
        for entry in parsed.entries:
            title   = entry.get("title", "")
            link    = strip_tracking(entry.get("link", ""))
            summary = entry.get("summary", "") or entry.get("description", "")
            uid     = make_uid(feed["name"], title, link)
            if not uid or uid in seen:
                continue
            seen.add(uid)
            db.execute("INSERT OR IGNORE INTO seen(id) VALUES(?)", (uid,))
            matched, reason, price, discount = is_extreme_deal(title, summary)
            if matched:
                score = calculate_score(discount, price, feed["name"], reason)
                db.execute(
                    "INSERT OR IGNORE INTO deals(id,source,title,link,reason,price,discount,score) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (uid, feed["name"], title, apply_affiliate(link),
                     reason, price, discount, score)
                )
                if price is not None:
                    db.execute(
                        "INSERT INTO price_history(deal_id,price) VALUES(?,?)",
                        (uid, price)
                    )
                new_count += 1

    # — HTML scrapers (sites with dead RSS) —
    for site in SCRAPE_SITES:
        for deal in scrape_deals(site, seen):
            db.execute("INSERT OR IGNORE INTO seen(id) VALUES(?)", (deal["id"],))
            db.execute(
                "INSERT OR IGNORE INTO deals(id,source,title,link,reason,price,discount,score) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (deal["id"], deal["source"], deal["title"], deal["link"],
                 deal["reason"], deal["price"], deal["discount"], deal["score"])
            )
            if deal["price"] is not None:
                db.execute(
                    "INSERT INTO price_history(deal_id,price) VALUES(?,?)",
                    (deal["id"], deal["price"])
                )
            new_count += 1

    # Prune seen table older than 30 days
    db.execute("DELETE FROM seen WHERE ts < datetime('now','-30 days')")
    db.commit()
    db.close()

    _last_scan = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    _scan_count += 1
    print(f"[{_last_scan}] Scan #{_scan_count}: {new_count} new deals found.")

# ── Pulse check ────────────────────────────────────────────────────────────────

def pulse_check():
    """Ping the 30 most recent unverified/live deals.
    Updates pulse status (live / expired / unverified) and logs current price
    into price_history when the page loads successfully."""
    db = get_db()
    rows = db.execute(
        """SELECT id, link FROM deals
           WHERE pulse IN ('unverified','live')
             AND (pulse_ts IS NULL OR pulse_ts < datetime('now','-30 minutes'))
           ORDER BY ts DESC LIMIT 30"""
    ).fetchall()

    updated = 0
    for row in rows:
        deal_id, link = row["id"], row["link"]
        if not link:
            db.execute(
                "UPDATE deals SET pulse='unverified', pulse_ts=datetime('now') WHERE id=?",
                (deal_id,)
            )
            continue

        status = "unverified"
        try:
            req = urllib.request.Request(link, headers={"User-Agent": _BROWSER_UA})
            with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as r:
                body = r.read(8192).decode("utf-8", errors="replace")
            current_price = extract_price(body)
            status = "live"
            if current_price is not None:
                db.execute(
                    "INSERT INTO price_history(deal_id,price) VALUES(?,?)",
                    (deal_id, current_price)
                )
        except urllib.error.HTTPError as e:
            status = "expired" if e.code in (404, 410) else "unverified"
        except Exception:
            status = "unverified"

        db.execute(
            "UPDATE deals SET pulse=?, pulse_ts=datetime('now') WHERE id=?",
            (status, deal_id)
        )
        updated += 1
        time.sleep(0.5)   # be polite to retailer servers

    db.commit()
    db.close()
    if updated:
        print(f"[Pulse] Checked {updated} deals.")

# ── Flask app ──────────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/")
def index():
    db = get_db()
    sources = [r[0] for r in db.execute("SELECT DISTINCT source FROM deals ORDER BY source")]
    total   = db.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    db.close()
    return render_template("index.html",
        sources=sources, total=total,
        last_scan=_last_scan or "Starting...",
        scan_count=_scan_count,
    )

@app.route("/api/deals")
def api_deals():
    source    = request.args.get("source", "")
    search    = request.args.get("search", "")
    max_price = request.args.get("maxPrice", type=float)
    sort      = request.args.get("sort", "ts")   # "ts" | "score"
    limit     = min(request.args.get("limit", 50, type=int), 200)
    offset    = request.args.get("offset", 0, type=int)

    query  = "SELECT * FROM deals WHERE 1=1"
    params = []
    if source:
        query += " AND source=?";     params.append(source)
    if search:
        query += " AND title LIKE ?"; params.append(f"%{search}%")
    if max_price is not None:
        query += " AND (price IS NULL OR price<=?)"; params.append(max_price)

    if sort == "score":
        query += " ORDER BY score DESC, ts DESC"
    else:
        query += " ORDER BY ts DESC"
    query += " LIMIT ? OFFSET ?"
    params += [limit, offset]

    db   = get_db()
    rows = [dict(r) for r in db.execute(query, params)]
    db.close()
    return jsonify(rows)

@app.route("/api/stats")
def api_stats():
    db    = get_db()
    total = db.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    today = db.execute("SELECT COUNT(*) FROM deals WHERE ts >= date('now')").fetchone()[0]
    live  = db.execute("SELECT COUNT(*) FROM deals WHERE pulse='live'").fetchone()[0]
    sources = [r[0] for r in db.execute("SELECT DISTINCT source FROM deals")]
    db.close()
    return jsonify({"total": total, "today": today, "live": live,
                    "sources": len(sources), "last_scan": _last_scan,
                    "scan_count": _scan_count})

@app.route("/api/scan", methods=["POST"])
def api_scan():
    scan()
    return jsonify({"ok": True, "last_scan": _last_scan})

@app.route("/api/confirm/<path:deal_id>", methods=["POST"])
def api_confirm(deal_id):
    """Increment the 'I bought it' counter for a deal and recalculate its score."""
    db = get_db()
    db.execute("INSERT INTO confirmations(deal_id) VALUES(?)", (deal_id,))
    db.execute("UPDATE deals SET confirm_count = confirm_count + 1 WHERE id=?", (deal_id,))
    row = db.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    new_count = 0
    if row:
        new_count  = row["confirm_count"]
        new_score  = calculate_score(
            row["discount"], row["price"], row["source"],
            row["reason"] or "", new_count
        )
        db.execute("UPDATE deals SET score=? WHERE id=?", (new_score, deal_id))
    db.commit()
    db.close()
    return jsonify({"ok": True, "confirm_count": new_count})

@app.route("/api/price_history/<path:deal_id>")
def api_price_history(deal_id):
    """Return price history entries for a deal, oldest first."""
    db   = get_db()
    rows = [dict(r) for r in db.execute(
        "SELECT price, ts FROM price_history WHERE deal_id=? ORDER BY ts ASC LIMIT 50",
        (deal_id,)
    )]
    db.close()
    return jsonify(rows)

# ── Application startup ────────────────────────────────────────────────────────
# Runs on import — compatible with both `python app.py` and gunicorn.
# gunicorn with --workers 1 loads this once; scheduler runs in that process.

init_db()

_scheduler = BackgroundScheduler()
_scheduler.add_job(scan,        "interval", seconds=CHECK_INTERVAL)
_scheduler.add_job(pulse_check, "interval", seconds=PULSE_INTERVAL)
_scheduler.start()
atexit.register(lambda: _scheduler.shutdown(wait=False))

# First scan runs in a background thread so the server starts immediately
threading.Thread(target=scan, daemon=True).start()

if __name__ == "__main__":
    print(f"\n  Pennysnipe running at http://localhost:{PORT}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
