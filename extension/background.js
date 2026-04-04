// Deal Finder — background service worker
// Scans RSS feeds + Twitter/X (via Nitter) on a timer, stores deals, updates badge.

const SCAN_INTERVAL_MINUTES = 2;
const MAX_DEALS = 300;
const MAX_SEEN  = 6000;
const NITTER_CACHE_MS = 30 * 60 * 1000; // re-probe instances every 30 min

// ── RSS Feeds ──────────────────────────────────────────────────────────────────

const FEEDS = [
  { name: "Slickdeals",           url: "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1" },
  { name: "Slickdeals Price Err", url: "https://slickdeals.net/newsearch.php?q=price+error&searcharea=deals&rss=1" },
  { name: "Slickdeals Tech",      url: "https://slickdeals.net/newsearch.php?forumid=9&mode=frontpage&searcharea=deals&rss=1" },
  { name: "r/deals",              url: "https://www.reddit.com/r/deals/.rss" },
  { name: "r/buildapcsales",      url: "https://www.reddit.com/r/buildapcsales/.rss" },
  { name: "r/GameDeals",          url: "https://www.reddit.com/r/GameDeals/.rss" },
  { name: "r/frugalmalefashion",  url: "https://www.reddit.com/r/frugalmalefashion/.rss" },
  { name: "r/frugal",             url: "https://www.reddit.com/r/frugal/.rss" },
  { name: "r/coupons",            url: "https://www.reddit.com/r/coupons/.rss" },
  { name: "r/consoledeals",       url: "https://www.reddit.com/r/consoledeals/.rss" },
  { name: "DealNews Electronics", url: "https://www.dealnews.com/c142/Electronics/?rss=1" },
  { name: "DealNews Computers",   url: "https://www.dealnews.com/c39/Computers/?rss=1" },
  { name: "DealNews Top Deals",   url: "https://www.dealnews.com/featured-sales/?rss=1" },
  { name: "9to5Toys",             url: "https://9to5toys.com/feed/" },
  { name: "9to5Mac Deals",        url: "https://9to5mac.com/deals/feed/" },
  { name: "Hip2Save",             url: "https://hip2save.com/feed/" },
  { name: "BensBargains",         url: "https://bensbargains.com/rss/" },
  { name: "Brad's Deals",         url: "https://www.bradsdeals.com/feed" },
  { name: "Krazy Coupon Lady",    url: "https://thekrazycouponlady.com/feed" },
  { name: "Woot",                 url: "https://www.woot.com/blog/rss" },
  { name: "TechBargains",         url: "https://www.techbargains.com/rssfeed.cfm" },
];

// ── Twitter/X accounts + search terms to monitor via Nitter ───────────────────

const TWITTER_ACCOUNTS = [
  "slickdeals", "dealnews", "TechDeals", "dealspotr", "9to5toys",
];

const TWITTER_SEARCHES = [
  "price error amazon",
  "price glitch",
  "price mistake",
  "mispriced deal",
  "$0.01",
];

const NITTER_INSTANCES = [
  "nitter.privacydev.net",
  "nitter.poast.org",
  "nitter.cz",
  "nitter.1d4.us",
  "xcancel.com",
  "nitter.net",
];

// ── Deal detection ─────────────────────────────────────────────────────────────

const KEYWORDS = [
  "price error","price mistake","mispriced","glitch","$0.01","0.01",
  "1 cent","penny deal","99% off","98% off","97% off","96% off",
  "95% off","94% off","93% off","92% off","91% off","90% off",
  "free after rebate","free + ship",
];

function extractPrice(text) {
  const ms = [...text.matchAll(/\$\s*(\d+(?:\.\d+)?)/g)];
  return ms.length ? Math.min(...ms.map(m => parseFloat(m[1]))) : null;
}

function extractDiscount(text) {
  const ms = [...text.matchAll(/(\d+)\s*%\s*off/gi)];
  return ms.length ? Math.max(...ms.map(m => parseInt(m[1]))) : null;
}

function isExtremeDeal(title, summary, maxPrice, minDiscount) {
  const combined = (title + " " + summary).toLowerCase();
  for (const kw of KEYWORDS) {
    if (combined.includes(kw)) return { match: true, reason: `keyword: "${kw}"` };
  }
  const pct = extractDiscount(combined);
  if (pct !== null && pct >= minDiscount) return { match: true, reason: `${pct}% off` };
  if (maxPrice != null) {
    const price = extractPrice(combined);
    if (price !== null && price <= maxPrice) return { match: true, reason: `price $${price.toFixed(2)}` };
  }
  return { match: false, reason: "" };
}

// ── Affiliate links ────────────────────────────────────────────────────────────

function applyAffiliate(url, tag) {
  if (!tag || !url) return url;
  try {
    const u = new URL(url);
    if (u.hostname.includes("amazon.com") || u.hostname.includes("amzn.")) {
      u.searchParams.set("tag", tag);
      return u.toString();
    }
  } catch {}
  return url;
}

// ── XML parser (no DOMParser in service workers) ───────────────────────────────

function looksLikeXml(text) {
  const h = text.trimStart().slice(0, 200);
  return h.startsWith("<?xml") || h.startsWith("<rss") || h.startsWith("<feed");
}

function getTag(block, tag) {
  let m = block.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`, "i"));
  if (m) return m[1].trim();
  m = block.match(new RegExp(`<${tag}[^>]*>([^<]*)<\\/${tag}>`, "i"));
  if (m) return decodeEnt(m[1].trim());
  m = block.match(new RegExp(`<${tag}[^>]*\\shref=["']([^"']+)["'][^>]*/?>`, "i"));
  if (m) return m[1].trim();
  return "";
}

function decodeEnt(s) {
  return s.replace(/&amp;/g,"&").replace(/&lt;/g,"<").replace(/&gt;/g,">")
          .replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&apos;/g,"'");
}

function parseFeed(text) {
  const items = [];
  const re = /<(item|entry)[\s>][\s\S]*?<\/\1>/gi;
  let m;
  while ((m = re.exec(text)) !== null) {
    const b = m[0];
    items.push({
      title:   getTag(b, "title"),
      link:    getTag(b, "link"),
      summary: getTag(b, "description") || getTag(b, "summary") || getTag(b, "content"),
      id:      getTag(b, "guid") || getTag(b, "id"),
    });
  }
  return items;
}

// ── Fetch helpers ──────────────────────────────────────────────────────────────

const HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
};

async function fetchText(url) {
  try {
    const res = await fetch(url, { headers: HEADERS });
    if (!res.ok) return null;
    return await res.text();
  } catch { return null; }
}

async function fetchFeed(feed, seenSet, maxPrice, minDiscount, affiliateTag) {
  const text = await fetchText(feed.url);
  if (!text || !looksLikeXml(text)) return [];
  const items = parseFeed(text);
  const deals = [];
  for (const item of items) {
    const uid = item.id || item.link || item.title;
    if (!uid || seenSet.has(uid)) continue;
    seenSet.add(uid);
    const { match, reason } = isExtremeDeal(item.title, item.summary, maxPrice, minDiscount);
    if (match) {
      deals.push({
        id: uid, source: feed.name, title: item.title,
        link: applyAffiliate(item.link, affiliateTag), reason,
        ts: new Date().toISOString(),
      });
    }
  }
  return deals;
}

// ── Nitter: find a working instance ───────────────────────────────────────────

async function findWorkingNitter() {
  // Check cache first
  const cached = await chrome.storage.local.get(["nitterHost", "nitterCachedAt"]);
  if (cached.nitterHost && cached.nitterCachedAt && (Date.now() - cached.nitterCachedAt) < NITTER_CACHE_MS) {
    return cached.nitterHost;
  }

  for (const host of NITTER_INSTANCES) {
    const text = await fetchText(`https://${host}/slickdeals/rss`);
    if (text && looksLikeXml(text)) {
      await chrome.storage.local.set({ nitterHost: host, nitterCachedAt: Date.now() });
      return host;
    }
  }

  // Clear stale cache if nothing works
  await chrome.storage.local.remove(["nitterHost", "nitterCachedAt"]);
  return null;
}

async function fetchTwitterFeeds(host, seenSet, maxPrice, minDiscount, affiliateTag) {
  const deals = [];
  const enc = s => encodeURIComponent(s);

  for (const account of TWITTER_ACCOUNTS) {
    const found = await fetchFeed(
      { name: `Twitter @${account}`, url: `https://${host}/${account}/rss` },
      seenSet, maxPrice, minDiscount, affiliateTag
    );
    deals.push(...found);
  }

  for (const query of TWITTER_SEARCHES) {
    const found = await fetchFeed(
      { name: `Twitter: ${query}`, url: `https://${host}/search/rss?q=${enc(query)}&f=tweets` },
      seenSet, maxPrice, minDiscount, affiliateTag
    );
    deals.push(...found);
  }

  return deals;
}

// ── Main scan ──────────────────────────────────────────────────────────────────

async function scan() {
  const stored = await chrome.storage.local.get(["deals","seen","maxPrice","minDiscount","affiliateTag","unread"]);
  const seenSet      = new Set(stored.seen || []);
  const maxPrice     = stored.maxPrice     ?? 10.0;
  const minDiscount  = stored.minDiscount  ?? 80;
  const affiliateTag = stored.affiliateTag || "";
  const existing     = stored.deals        || [];
  let unread         = stored.unread       || 0;

  const newDeals = [];

  // RSS feeds
  for (const feed of FEEDS) {
    const found = await fetchFeed(feed, seenSet, maxPrice, minDiscount, affiliateTag);
    newDeals.push(...found);
  }

  // Twitter via Nitter
  const nitterHost = await findWorkingNitter();
  if (nitterHost) {
    const twitterDeals = await fetchTwitterFeeds(nitterHost, seenSet, maxPrice, minDiscount, affiliateTag);
    newDeals.push(...twitterDeals);
  }

  if (newDeals.length > 0) {
    const allDeals = [...newDeals, ...existing].slice(0, MAX_DEALS);
    const allSeen  = [...seenSet].slice(-MAX_SEEN);
    unread += newDeals.length;

    await chrome.storage.local.set({ deals: allDeals, seen: allSeen, unread });

    chrome.action.setBadgeText({ text: unread > 99 ? "99+" : String(unread) });
    chrome.action.setBadgeBackgroundColor({ color: "#4ade80" });

    chrome.notifications.create(`deal-${Date.now()}`, {
      type: "basic",
      iconUrl: "icons/icon48.png",
      title: `Deal Finder: ${newDeals.length} new deal${newDeals.length > 1 ? "s" : ""}!`,
      message: newDeals[0].title,
      priority: 2,
    });
  }
}

// ── Ad fetching ────────────────────────────────────────────────────────────────
// Supports Ethical Ads (ethicalads.io) out of the box.
// Set adNetwork = "ethical" and adKeyword = your topic (e.g. "frontend")
// Or set adNetwork = "custom" and adServerUrl = your own endpoint returning
// JSON like: { "text": "...", "url": "...", "image": "..." }

async function fetchAd() {
  const { adNetwork, adKeyword, adServerUrl } = await chrome.storage.local.get(
    ["adNetwork", "adKeyword", "adServerUrl"]
  );

  try {
    if (adNetwork === "ethical") {
      // Ethical Ads API — sign up free at ethicalads.io
      const kw  = adKeyword || "backend";
      const res = await fetch(
        `https://server.ethicalads.io/api/v1/decision/?publisher=YOUR_PUBLISHER_ID&ad_types=text&keywords=${encodeURIComponent(kw)}`,
        { headers: { "Accept": "application/json" } }
      );
      if (!res.ok) return;
      const data = await res.json();
      const ad   = data?.results?.[0];
      if (ad) {
        await chrome.storage.local.set({
          currentAd: { text: ad.text, url: ad.link, image: ad.image }
        });
      }
    } else if (adNetwork === "custom" && adServerUrl) {
      // Your own ad server — return JSON: { text, url, image }
      const res = await fetch(adServerUrl, { headers: { "Accept": "application/json" } });
      if (!res.ok) return;
      const ad = await res.json();
      if (ad?.url) await chrome.storage.local.set({ currentAd: ad });
    }
  } catch { /* silently skip if ad fetch fails */ }
}

// ── Lifecycle ──────────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("scan",    { periodInMinutes: SCAN_INTERVAL_MINUTES });
  chrome.alarms.create("fetchAd", { periodInMinutes: 60 }); // refresh ad hourly
  chrome.storage.local.get(["maxPrice", "minDiscount"], r => {
    if (r.maxPrice    == null) chrome.storage.local.set({ maxPrice: 10.0 });
    if (r.minDiscount == null) chrome.storage.local.set({ minDiscount: 80 });
  });
  scan();
  fetchAd();
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "scan")    scan();
  if (alarm.name === "fetchAd") fetchAd();
});
