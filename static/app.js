// Pennysnipe — frontend
const POLL_MS          = 30_000;
const PAGE_SIZE        = 20;
const IN_FEED_AD_EVERY = 8;   // insert an ad slot every N deals

let offset       = 0;
let currentDeals = [];
let knownIds     = new Set();
let filters      = { source: "", search: "", maxPrice: "", sort: "ts" };

// Confirmed deal IDs this session (prevents double-click spam)
const confirmedThisSession = new Set();

// ── Theme cycling ──────────────────────────────────────────────────────────────
const THEME_CYCLE = ["dark", "light", "sunrise"];
const THEME_ICON  = { dark: "☀", light: "🌅", sunrise: "🌙" }; // icon = "switch to next"

function initTheme() {
  const saved = localStorage.getItem("ps_theme") || "dark";
  document.body.className = saved;
  updateThemeBtn(saved);
}

function updateThemeBtn(current) {
  const nextIdx = (THEME_CYCLE.indexOf(current) + 1) % THEME_CYCLE.length;
  const next    = THEME_CYCLE[nextIdx];
  const btn     = document.getElementById("themeToggle");
  btn.textContent = THEME_ICON[next] || "☀";
  btn.title = `Switch to ${next} theme`;
}

function cycleTheme() {
  const current = document.body.className;
  const idx     = THEME_CYCLE.indexOf(current);
  const next    = THEME_CYCLE[(idx + 1) % THEME_CYCLE.length];
  document.body.className = next;
  localStorage.setItem("ps_theme", next);
  updateThemeBtn(next);
}

// ── Source → badge colour ──────────────────────────────────────────────────────
function badgeClass(source) {
  if (!source) return "badge-green";
  const s = source.toLowerCase();
  if (s.includes("reddit"))  return "badge-blue";
  if (s.includes("slick") || s.includes("deal") || s.includes("woot")) return "badge-green";
  if (s.includes("bing"))    return "badge-purple";
  return "badge-yellow";
}

// ── Score badge ────────────────────────────────────────────────────────────────
function scoreClass(score) {
  if (score >= 65) return "score-hot";
  if (score >= 35) return "score-good";
  return "score-low";
}
function scoreIcon(score) {
  if (score >= 65) return "⚡";
  if (score >= 35) return "★";
  return "·";
}

// ── Pulse badge ────────────────────────────────────────────────────────────────
const PULSE_META = {
  live:        { cls: "pulse-live",       icon: "🟢", label: "Live"      },
  expired:     { cls: "pulse-expired",    icon: "🔴", label: "Expired"   },
  unverified:  { cls: "pulse-unverified", icon: "🟡", label: "Checking"  },
};
function pulseMeta(pulse) {
  return PULSE_META[pulse] || PULSE_META.unverified;
}

// ── Time ago ───────────────────────────────────────────────────────────────────
function timeAgo(ts) {
  if (!ts) return "";
  const s = Math.floor((Date.now() - new Date(ts + "Z").getTime()) / 1000);
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// ── HTML escape ────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

// ── Sparkline (SVG) ────────────────────────────────────────────────────────────
function drawSparkline(container, history) {
  if (!history || history.length < 2) {
    container.style.display = "none";
    return;
  }
  const prices = history.map(h => h.price);
  const min    = Math.min(...prices);
  const max    = Math.max(...prices);
  const range  = max - min || 0.01;
  const W = 100, H = 28, PAD = 3;

  const pts = prices.map((p, i) => {
    const x = PAD + (i / (prices.length - 1)) * (W - PAD * 2);
    const y = H - PAD - ((p - min) / range) * (H - PAD * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const last = pts[pts.length - 1].split(",");
  const trend = prices[prices.length - 1] < prices[0] ? "↓" : prices[prices.length - 1] > prices[0] ? "↑" : "→";

  container.style.display = "flex";
  container.innerHTML = `
    <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" class="sparkline-svg">
      <polyline points="${pts.join(" ")}"
        fill="none" stroke="var(--accent)" stroke-width="1.5"
        stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="var(--accent)"/>
    </svg>
    <div class="spark-meta">
      <span class="spark-label">Price history (${history.length} points)</span>
      <span class="spark-range">${trend} $${min.toFixed(2)} – $${max.toFixed(2)}</span>
    </div>
  `;
}

// Lazy-load sparklines using IntersectionObserver
const sparkObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    const wrap   = entry.target;
    const dealId = wrap.dataset.dealId;
    sparkObserver.unobserve(wrap);
    fetch(`/api/price_history/${encodeURIComponent(dealId)}`)
      .then(r => r.json())
      .then(h => drawSparkline(wrap, h))
      .catch(() => { wrap.style.display = "none"; });
  });
}, { rootMargin: "150px" });

// ── Confirm deal ───────────────────────────────────────────────────────────────
async function confirmDeal(dealId, btn) {
  if (confirmedThisSession.has(dealId)) return;
  confirmedThisSession.add(dealId);
  btn.disabled = true;

  try {
    const res  = await fetch(`/api/confirm/${encodeURIComponent(dealId)}`, { method: "POST" });
    const data = await res.json();
    const count = data.confirm_count || 1;
    btn.classList.add("confirmed");
    btn.innerHTML = `✓ Bought it <span class="confirm-count">${count}</span>`;
  } catch {
    btn.disabled = false;
    confirmedThisSession.delete(dealId);
  }
}

// ── Build a deal card element ──────────────────────────────────────────────────
function buildCard(deal) {
  const card = document.createElement("div");
  card.className = "deal-card" + (deal.pulse === "expired" ? " is-expired" : "");
  card.dataset.id = deal.id;

  const score   = deal.score ?? 0;
  const pm      = pulseMeta(deal.pulse);
  const count   = deal.confirm_count || 0;
  const alreadyConfirmed = confirmedThisSession.has(deal.id);
  const confirmLabel = alreadyConfirmed
    ? `✓ Bought it${count ? ` <span class="confirm-count">${count}</span>` : ""}`
    : `✓ Bought it${count ? ` <span class="confirm-count">${count}</span>` : ""}`;

  card.innerHTML = `
    <div class="deal-top">
      <span class="source-badge ${badgeClass(deal.source)}">${escHtml(deal.source)}</span>
      <span class="score-badge ${scoreClass(score)}" title="Deal score: ${score}/100">${scoreIcon(score)} ${score}</span>
      <span class="pulse-badge ${pm.cls}" title="Link status">${pm.icon} ${pm.label}</span>
      <span class="deal-time">${timeAgo(deal.ts)}</span>
    </div>
    <a class="deal-title" href="${escHtml(deal.link || "#")}" target="_blank" rel="noopener noreferrer">
      ${escHtml(deal.title)}
    </a>
    <div class="deal-bottom">
      <span class="deal-reason">${escHtml(deal.reason)}</span>
      <button class="confirm-btn${alreadyConfirmed ? " confirmed" : ""}"
              data-id="${escHtml(deal.id)}"
              ${alreadyConfirmed ? "disabled" : ""}>
        ${confirmLabel}
      </button>
      <a class="deal-link" href="${escHtml(deal.link || "#")}" target="_blank" rel="noopener noreferrer">
        View deal &#8599;
      </a>
    </div>
    <div class="sparkline-wrap" data-deal-id="${escHtml(deal.id)}" style="display:none"></div>
  `;

  // Attach confirm handler
  const confirmBtn = card.querySelector(".confirm-btn");
  confirmBtn.addEventListener("click", () => confirmDeal(deal.id, confirmBtn));

  // Register sparkline lazy-loader
  const sparkWrap = card.querySelector(".sparkline-wrap");
  sparkObserver.observe(sparkWrap);

  return card;
}

// ── In-feed ad card ────────────────────────────────────────────────────────────
function buildInFeedAd() {
  const wrap = document.createElement("div");
  wrap.className = "deal-card in-feed-ad";
  wrap.innerHTML = `
    <div class="in-feed-ad-wrap">
      <span class="in-feed-ad-label">ADVERTISEMENT</span>
      <!-- ADSENSE SLOT — replace with your responsive in-feed ad unit -->
      <!--
      <ins class="adsbygoogle" style="display:block" data-ad-format="fluid"
           data-ad-layout-key="-fb+5w+4e-db+86"
           data-ad-client="ca-pub-XXXXXXXXXXXXXXXX"
           data-ad-slot="XXXXXXXXXX"></ins>
      <script>(adsbygoogle = window.adsbygoogle || []).push({});<\/script>
      -->
      <div class="ad-placeholder">Ad — In-feed (responsive)</div>
    </div>
  `;
  return wrap;
}

// ── Render deals into grid ─────────────────────────────────────────────────────
function renderDeals(deals, append = false) {
  const grid = document.getElementById("dealGrid");
  if (!append) { grid.innerHTML = ""; currentDeals = []; }

  if (!deals.length && !append) {
    grid.innerHTML = '<div class="empty-state">No deals found matching your filters yet — check back soon!</div>';
    return;
  }

  deals.forEach(deal => {
    currentDeals.push(deal);
    knownIds.add(deal.id);

    // Insert in-feed ad every N deals (skip first position)
    if ((currentDeals.length - 1) > 0 && (currentDeals.length - 1) % IN_FEED_AD_EVERY === 0) {
      grid.appendChild(buildInFeedAd());
    }
    grid.appendChild(buildCard(deal));
  });

  document.getElementById("loadMore").classList.toggle("hidden", deals.length < PAGE_SIZE);
}

// ── Fetch deals from API ───────────────────────────────────────────────────────
async function fetchDeals(reset = false) {
  if (reset) offset = 0;
  const params = new URLSearchParams({
    limit:  PAGE_SIZE,
    offset,
    sort:   filters.sort || "ts",
    ...(filters.source   && { source:   filters.source }),
    ...(filters.search   && { search:   filters.search }),
    ...(filters.maxPrice && { maxPrice: filters.maxPrice }),
  });
  try {
    const res   = await fetch(`/api/deals?${params}`);
    const deals = await res.json();
    renderDeals(deals, !reset);
    offset += deals.length;
  } catch {
    if (!offset) {
      document.getElementById("dealGrid").innerHTML =
        '<div class="empty-state">Could not connect to server.</div>';
    }
  }
}

// ── Poll for new deals ─────────────────────────────────────────────────────────
async function pollForNew() {
  try {
    const res   = await fetch(`/api/deals?limit=10&offset=0&sort=${filters.sort}`);
    const deals = await res.json();
    const fresh = deals.filter(d => !knownIds.has(d.id));
    if (fresh.length) {
      const badge = document.getElementById("newBadge");
      badge.classList.remove("hidden");
      badge.textContent = `▲ ${fresh.length} new deal${fresh.length > 1 ? "s" : ""}!`;
    }
  } catch {}
}

// ── Fetch + update stats ───────────────────────────────────────────────────────
async function updateStats() {
  try {
    const res  = await fetch("/api/stats");
    const data = await res.json();
    document.getElementById("statTotal").textContent     = data.total      ?? "—";
    document.getElementById("statSources").textContent   = data.sources    ?? "—";
    document.getElementById("statLastScan").textContent  = data.last_scan  ?? "—";
    document.getElementById("sideTotal").textContent     = data.total      ?? "—";
    document.getElementById("sideToday").textContent     = data.today      ?? "—";
    document.getElementById("sideLive").textContent      = data.live       ?? "—";
    document.getElementById("sideSources").textContent   = data.sources    ?? "—";
    document.getElementById("sideScanCount").textContent = data.scan_count ?? "—";
  } catch {}
}

// ── Filters ────────────────────────────────────────────────────────────────────
function applyFilters() {
  filters.source   = document.getElementById("sourceFilter").value;
  filters.search   = document.getElementById("searchBox").value.trim();
  filters.maxPrice = document.getElementById("maxPriceInput").value;
  filters.sort     = document.getElementById("sortSelect").value;
  document.getElementById("newBadge").classList.add("hidden");
  fetchDeals(true);
}

function clearFilters() {
  document.getElementById("sourceFilter").value  = "";
  document.getElementById("searchBox").value     = "";
  document.getElementById("maxPriceInput").value = "";
  document.getElementById("sortSelect").value    = "ts";
  filters = { source: "", search: "", maxPrice: "", sort: "ts" };
  document.getElementById("newBadge").classList.add("hidden");
  fetchDeals(true);
}

// ── Init ───────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  fetchDeals(true);
  updateStats();

  document.getElementById("applyFilters").addEventListener("click", applyFilters);
  document.getElementById("clearFilters").addEventListener("click", clearFilters);
  document.getElementById("loadMore").addEventListener("click",  () => fetchDeals(false));
  document.getElementById("themeToggle").addEventListener("click", cycleTheme);

  document.getElementById("newBadge").addEventListener("click", () => {
    fetchDeals(true);
    document.getElementById("newBadge").classList.add("hidden");
  });

  document.getElementById("scanNow").addEventListener("click", async () => {
    const btn = document.getElementById("scanNow");
    btn.textContent = "⟳ Scanning…";
    btn.disabled = true;
    await fetch("/api/scan", { method: "POST" });
    await fetchDeals(true);
    await updateStats();
    btn.textContent = "⟳ Scan";
    btn.disabled = false;
  });

  // Search on Enter
  document.getElementById("searchBox").addEventListener("keydown", e => {
    if (e.key === "Enter") applyFilters();
  });

  // Sort immediately on change
  document.getElementById("sortSelect").addEventListener("change", applyFilters);

  // Auto-poll
  setInterval(pollForNew,   POLL_MS);
  setInterval(updateStats,  POLL_MS);
});
