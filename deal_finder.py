"""
Deal Finder - Monitors RSS feeds + Twitter/X (via Nitter) for extreme discounts.
Logs matching deals to deals.log and prints colored output to console.
Runs continuously on a configurable interval.
"""

import feedparser
import time
import re
import json
import os
import sys
import ssl
import threading
import urllib.request
import urllib.parse
import urllib.error
import io
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Tuple, List, Dict

# Force UTF-8 output on Windows so box-drawing / emoji chars don't crash
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from rich.console import Console
from rich.theme import Theme
from rich.rule import Rule
from rich.text import Text
from rich import box
from rich.table import Table

# ══════════════════════════════════════════════════════════════════════════════
#  THEME SELECTION  — change ACTIVE_THEME to any key below
# ══════════════════════════════════════════════════════════════════════════════

THEMES: Dict[str, Dict[str, str]] = {
    "default": {
        "header":   "bold white on blue",
        "deal":     "bold bright_green",
        "reason":   "bold yellow",
        "source":   "cyan",
        "link":     "underline bright_cyan",
        "scan":     "bold white",
        "warn":     "yellow",
        "error":    "bold red",
        "nothing":  "dim white",
        "footer":   "dim white",
    },
    "hacker": {
        "header":   "bold bright_green on black",
        "deal":     "bold bright_green",
        "reason":   "bright_green",
        "source":   "green",
        "link":     "underline green",
        "scan":     "bright_green",
        "warn":     "yellow",
        "error":    "bold red",
        "nothing":  "dim green",
        "footer":   "dim green",
    },
    "neon": {
        "header":   "bold bright_magenta on black",
        "deal":     "bold bright_magenta",
        "reason":   "bold bright_yellow",
        "source":   "bright_cyan",
        "link":     "underline bright_cyan",
        "scan":     "bold bright_white",
        "warn":     "bright_yellow",
        "error":    "bold bright_red",
        "nothing":  "dim magenta",
        "footer":   "dim cyan",
    },
    "minimal": {
        "header":   "bold white",
        "deal":     "bold white",
        "reason":   "white",
        "source":   "white",
        "link":     "underline white",
        "scan":     "white",
        "warn":     "white",
        "error":    "bold white",
        "nothing":  "dim white",
        "footer":   "dim white",
    },
    "retro": {
        "header":   "bold yellow on dark_red",
        "deal":     "bold bright_yellow",
        "reason":   "bold orange3",
        "source":   "orange3",
        "link":     "underline yellow",
        "scan":     "bold yellow",
        "warn":     "orange3",
        "error":    "bold red",
        "nothing":  "dim yellow",
        "footer":   "dim orange3",
    },
}

ACTIVE_THEME = "default"   # ← change this: "default", "hacker", "neon", "minimal", "retro"

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

CHECK_INTERVAL = 30  # seconds between scans (2 minutes)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE    = os.path.join(SCRIPT_DIR, "deals.log")
SEEN_FILE   = os.path.join(SCRIPT_DIR, "seen_deals.json")

MIN_DISCOUNT_PERCENT = 80   # flag anything "X% off" where X >= this
MAX_PRICE            = 10.00 # flag anything priced at or under this (USD); set None to disable

# Triggers — genuine price errors and penny deals only
EXTREME_KEYWORDS = [
    "price error", "price mistake", "mispriced", "pricing error",
    "glitch", "accidental", "$0.01", "0.01", "1 cent", "one cent",
    "penny deal", "99% off", "98% off", "97% off", "96% off", "95% off",
]

# If any of these appear in the deal text, skip it entirely
EXCLUDE_KEYWORDS = [
    # Food delivery / restaurants
    "uber eats", "doordash", "grubhub", "instacart", "postmates", "seamless",
    "restaurant", "food delivery", "meal kit", "takeout", "takeaway",
    # Coupon / promo code noise
    "promo code", "coupon code", "discount code", "voucher", "code:",
    # Subscriptions / recurring
    "free trial", "per month", "/month", "monthly", "subscription", "annual plan",
    # Gift cards / credit
    "gift card", "e-gift", "egift", "store credit",
    # Shipping-only "deals"
    "free shipping", "free ship", "ship free",
    # Cashback noise
    "% cashback", "cash back", "rakuten", "ibotta",
    # In-store / app-only
    "app only", "in-store only", "in store only",
    # Generic navigation titles — not actual product deals
    "here's the deal", "deals under $", "deal of the day", "deals of the day",
    "today's deals", "lightning deals", "shop deals", "see more deals",
    "all deals", "view deals", "browse deals", "more deals", "best deals",
    "top deals", "hot deals", "weekly deals", "daily deals", "featured deals",
    "weekly ad", "sales ad", "circular",
    # Generic call-to-action titles
    "buy now at amazon", "buy now at", "shop now at", "click here",
    "sign up", "subscribe now", "newsletter",
    # Reddit non-deal posts
    "work is offering", "anyone know", "question:", "discussion:",
    "looking for", "help with", "advice on", "what do you think",
]

# ── RSS feeds ─────────────────────────────────────────────────────────────────

RSS_FEEDS: List[Dict[str, str]] = [
    # ── Slickdeals ──────────────────────────────────────────────────────────
    {
        "name": "Slickdeals Front Page",
        "url":  "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    },
    {
        "name": "Slickdeals — Price Errors",
        "url":  "https://slickdeals.net/newsearch.php?q=price+error&searcharea=deals&rss=1",
    },
    {
        "name": "Slickdeals — Computers & Electronics",
        "url":  "https://slickdeals.net/newsearch.php?forumid=9&mode=frontpage&searcharea=deals&rss=1",
    },
    # ── Reddit — all subreddits in ONE request to avoid 429 rate limiting ───
    {
        "name": "Reddit",
        "url":  "https://www.reddit.com/r/deals+buildapcsales+GameDeals+frugalmalefashion+frugal+consoledeals/.rss",
    },
    # ── DealNews ─────────────────────────────────────────────────────────────
    {
        "name": "DealNews Electronics",
        "url":  "https://www.dealnews.com/c142/Electronics/?rss=1",
    },
    {
        "name": "DealNews Computers",
        "url":  "https://www.dealnews.com/c39/Computers/?rss=1",
    },
    # ── Deal blogs / aggregators ─────────────────────────────────────────────
    {
        "name": "9to5Toys",
        "url":  "https://9to5toys.com/feed/",
    },
    {
        "name": "9to5Mac Deals",
        "url":  "https://9to5mac.com/guides/deals/feed/",
    },
    {
        "name": "Hip2Save",
        "url":  "https://hip2save.com/feed/",
    },
    {
        "name": "BensBargains",
        "url":  "https://bensbargains.com/rss/",
    },
    {
        "name": "Money Saving Mom",
        "url":  "https://moneysavingmom.com/feed/",
    },
    {
        "name": "Living Rich With Coupons",
        "url":  "https://www.livingrichwithcoupons.com/feed",
    },
    # ── Bing News (replaces Twitter — indexes social + news deal mentions) ───
    {
        "name": "Bing: price error",
        "url":  "https://www.bing.com/news/search?q=price+error+amazon&format=RSS&mkt=en-US",
    },
    {
        "name": "Bing: price glitch",
        "url":  "https://www.bing.com/news/search?q=price+glitch+deal&format=RSS&mkt=en-US",
    },
    {
        "name": "Bing: $0.01 deal",
        "url":  "https://www.bing.com/news/search?q=%240.01+deal&format=RSS&mkt=en-US",
    },
    {
        "name": "Bing: extreme discount",
        "url":  "https://www.bing.com/news/search?q=extreme+discount+sale+today&format=RSS&mkt=en-US",
    },
    {
        "name": "Bing: mispriced",
        "url":  "https://www.bing.com/news/search?q=mispriced+item+deal&format=RSS&mkt=en-US",
    },
]

# ── Dedup helpers ────────────────────────────────────────────────────────────

def make_uid(source: str, title: str, url: str = "") -> str:
    """Stable dedup key based on source + normalised title, not raw URL."""
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

# ── Local HTTP server (for browser extension) ────────────────────────────────

SERVER_PORT   = 8765        # extension connects to http://localhost:8765/deals
MAX_STORED    = 200         # how many recent deals to keep in memory

# Shared in-memory store — written by main thread, read by HTTP server thread
_recent_deals: List[Dict] = []
_deals_lock   = threading.Lock()


class _DealHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/deals", "/deals/"):
            with _deals_lock:
                payload = json.dumps(_recent_deals, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # silence server access logs


def _start_server():
    try:
        server = HTTPServer(("localhost", SERVER_PORT), _DealHandler)
        server.serve_forever()
    except OSError as e:
        cprint("warn", f"  [WARN] Could not start local server on port {SERVER_PORT}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  THEME / CONSOLE SETUP
# ══════════════════════════════════════════════════════════════════════════════

def build_console(theme_name: str) -> Console:
    t = THEMES.get(theme_name, THEMES["default"])
    rich_theme = Theme({k: v for k, v in t.items()})
    return Console(theme=rich_theme, highlight=False)


console = build_console(ACTIVE_THEME)

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    trimmed = list(seen)[-5000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f)


def log_to_file(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def cprint(style: str, message: str, also_log: bool = False):
    console.print(f"[{style}]{message}[/{style}]")
    if also_log:
        log_to_file(message)


def extract_discount_percent(text: str) -> Optional[int]:
    matches = re.findall(r"(\d+)\s*%\s*off", text, re.IGNORECASE)
    return max(int(m) for m in matches) if matches else None


def extract_price(text: str) -> Optional[float]:
    matches = re.findall(r"\$\s*(\d+(?:\.\d+)?)", text)
    return min(float(m) for m in matches) if matches else None


def is_extreme_deal(title: str, summary: str) -> Tuple[bool, str]:
    combined = (title + " " + summary).lower()

    # Bail out immediately on excluded categories
    for ex in EXCLUDE_KEYWORDS:
        if ex in combined:
            return False, ""

    for kw in EXTREME_KEYWORDS:
        if kw.lower() in combined:
            return True, f'keyword: "{kw}"'

    pct = extract_discount_percent(combined)
    if pct is not None and pct >= MIN_DISCOUNT_PERCENT:
        return True, f"{pct}% off"

    if MAX_PRICE is not None:
        price = extract_price(combined)
        if price is not None and price <= MAX_PRICE:
            return True, f"price ${price:.2f}"

    return False, ""


def print_deal(source: str, title: str, link: str, reason: str):
    console.print()
    console.print(f"  [deal]★ DEAL FOUND[/deal]  [source][{source}][/source]  [reason]({reason})[/reason]")
    console.print(f"  [bold]{title}[/bold]")
    console.print(f"  [link]{link}[/link]")
    log_to_file(f"DEAL [{source}] ({reason}) | {title} | {link}")
    # Push to in-memory store for browser extension
    record = {
        "source": source,
        "title": title,
        "link": link,
        "reason": reason,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with _deals_lock:
        _recent_deals.insert(0, record)
        if len(_recent_deals) > MAX_STORED:
            _recent_deals.pop()


# ══════════════════════════════════════════════════════════════════════════════
#  FEED FETCHING
# ══════════════════════════════════════════════════════════════════════════════

_SSL_CTX = ssl.create_default_context()
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _fetch_bytes(url: str, timeout: int = 10, silent_fail: bool = False, _retry: bool = True) -> Optional[bytes]:
    """Pre-fetch URL with a browser User-Agent. Returns raw bytes or None on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retry:
            cprint("warn", f"  [429] Rate limited on {url[:50]}... waiting 60s then retrying once.")
            time.sleep(60)
            return _fetch_bytes(url, timeout=timeout, silent_fail=silent_fail, _retry=False)
        if not silent_fail:
            cprint("warn", f"  [WARN] fetch failed ({url[:60]}...): {e}")
        return None
    except Exception as e:
        if not silent_fail:
            cprint("warn", f"  [WARN] fetch failed ({url[:60]}...): {e}")
        return None


def _looks_like_xml(data: bytes) -> bool:
    """Return True only if the response body looks like XML/RSS, not an HTML error page."""
    head = data[:200].lstrip()
    return head.startswith(b"<?xml") or head.startswith(b"<rss") or head.startswith(b"<feed")


def fetch_rss(feed_cfg: Dict[str, str], seen: set, silent: bool = False) -> List[Dict]:
    found: List[Dict] = []
    try:
        raw = _fetch_bytes(feed_cfg["url"], silent_fail=silent)
        if raw is None:
            return found

        if not _looks_like_xml(raw):
            if not silent:
                cprint("warn", f"  [WARN] {feed_cfg['name']}: response is not XML (likely blocked/HTML page)")
            return found

        parsed = feedparser.parse(raw)
        if parsed.bozo and not parsed.entries:
            if not silent:
                cprint("warn", f"  [WARN] {feed_cfg['name']}: {parsed.bozo_exception}")
            return found

        for entry in parsed.entries:
            title   = entry.get("title", "")
            link    = strip_tracking(entry.get("link", ""))
            summary = entry.get("summary", "") or entry.get("description", "")
            uid     = make_uid(feed_cfg["name"], title, link)
            if not uid or uid in seen:
                continue
            seen.add(uid)
            is_deal, reason = is_extreme_deal(title, summary)
            if is_deal:
                found.append({"source": feed_cfg["name"], "title": title, "link": link, "reason": reason})

    except Exception as e:
        if not silent:
            cprint("error", f"  [ERROR] {feed_cfg['name']}: {e}")
    return found


# ── HTML scrapers (for sites with dead RSS feeds) ────────────────────────────

SCRAPE_SITES: List[Dict[str, str]] = [
    {"name": "Woot",             "url": "https://www.woot.com"},
    {"name": "DealNews",         "url": "https://www.dealnews.com/"},
    {"name": "TechBargains",     "url": "https://www.techbargains.com/"},
    {"name": "Brad's Deals",     "url": "https://www.bradsdeals.com/"},
    {"name": "Krazy Coupon Lady", "url": "https://thekrazycouponlady.com/deals"},
]

_STRIP_TAGS = re.compile(r"<[^>]+>")


def scrape_deals(site: Dict[str, str], seen: set) -> List[Dict]:
    """Scrape a deal site's HTML and return matching extreme deals."""
    raw = _fetch_bytes(site["url"], silent_fail=True)
    if not raw:
        return []
    html = raw.decode("utf-8", errors="replace")
    base = urllib.parse.urlparse(site["url"])
    found: List[Dict] = []

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
        if uid in seen:
            continue
        seen.add(uid)

        is_deal, reason = is_extreme_deal(title, context)
        if is_deal:
            found.append({"source": site["name"], "title": title,
                          "link": strip_tracking(href), "reason": reason})
    return found


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run():
    # Start local HTTP server for browser extension
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()

    console.print(Rule(f"[header] Deal Finder — Theme: {ACTIVE_THEME} [/header]"))
    cprint("scan",   f"  Min discount : {MIN_DISCOUNT_PERCENT}%")
    cprint("scan",   f"  Max price    : ${MAX_PRICE}")
    cprint("scan",   f"  Interval     : {CHECK_INTERVAL}s ({CHECK_INTERVAL//60}m {CHECK_INTERVAL%60}s)")
    cprint("scan",   f"  Log file     : {LOG_FILE}")
    cprint("scan",   f"  Extension    : http://localhost:{SERVER_PORT}/deals")
    console.print(Rule())

    seen  = load_seen()
    cycle = 0

    while True:
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")
        console.print(Rule(f"[scan] Scan #{cycle} — {now} [/scan]"))
        total_found = 0

        # — RSS feeds — 2s delay between each to avoid rate limits
        for feed_cfg in RSS_FEEDS:
            time.sleep(2)
            new_deals = fetch_rss(feed_cfg, seen)
            total_found += len(new_deals)
            for d in new_deals:
                print_deal(d["source"], d["title"], d["link"], d["reason"])

        # — HTML scrapers (sites with dead RSS) —
        for site in SCRAPE_SITES:
            scraped = scrape_deals(site, seen)
            total_found += len(scraped)
            for d in scraped:
                print_deal(d["source"], d["title"], d["link"], d["reason"])

        if total_found == 0:
            cprint("nothing", f"  No new extreme deals found.")

        save_seen(seen)
        cprint("footer", f"  Next scan in {CHECK_INTERVAL//60}m {CHECK_INTERVAL%60}s ...\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        console.print("\n[warn]Stopped by user.[/warn]")
        sys.exit(0)
