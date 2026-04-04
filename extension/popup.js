const REFRESH_MS   = 30_000;
const THEME_KEY    = "secretshopper_theme";
const AD_CLOSE_KEY = "secretshopper_ad_closed";

// ── Premium key validation ─────────────────────────────────────────────────────
// Keys starting with "DF-" and at least 15 chars are accepted.
// Replace this with a real server check for production.

function validateKey(key) {
  return typeof key === "string" && key.startsWith("DF-") && key.length >= 15;
}

// ── Theme ──────────────────────────────────────────────────────────────────────

function applyTheme(name) {
  document.body.className = name;
  localStorage.setItem(THEME_KEY, name);
  document.getElementById("themeSelect").value = name;
}

// ── Time ───────────────────────────────────────────────────────────────────────

function timeAgo(iso) {
  const s = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// ── Ads ────────────────────────────────────────────────────────────────────────
// Ads are fetched from chrome.storage where background.js deposits them.
// background.js pulls from Ethical Ads (ethicalads.io) or your own ad server.
// Free users see one ad per session; premium users see none.

function showAd(isPremium) {
  const banner = document.getElementById("adBanner");

  if (isPremium || sessionStorage.getItem(AD_CLOSE_KEY)) {
    banner.classList.add("hidden");
    return;
  }

  chrome.storage.local.get("currentAd", ({ currentAd }) => {
    if (!currentAd || !currentAd.url) {
      banner.classList.add("hidden");
      return;
    }
    const link = document.getElementById("adLink");
    link.textContent = currentAd.text || currentAd.url;
    link.href        = currentAd.url;
    if (currentAd.image) {
      const img = document.createElement("img");
      img.src   = currentAd.image;
      img.style.cssText = "height:20px;vertical-align:middle;margin-right:6px;border-radius:2px;";
      link.prepend(img);
    }
    banner.classList.remove("hidden");
  });

  document.getElementById("adClose").onclick = () => {
    sessionStorage.setItem(AD_CLOSE_KEY, "1");
    banner.classList.add("hidden");
  };
}

// ── Render deals ───────────────────────────────────────────────────────────────

function renderDeals(deals) {
  const list = document.getElementById("dealList");
  list.innerHTML = "";
  document.getElementById("footerCount").textContent =
    `${deals.length} deal${deals.length !== 1 ? "s" : ""} stored`;

  for (const d of deals) {
    const card = document.createElement("div");
    card.className = "deal-card";
    const isTwitter = d.source && d.source.startsWith("Twitter");

    const top = document.createElement("div");
    top.className = "deal-top";

    const badge = document.createElement("span");
    badge.className = "deal-badge" + (isTwitter ? " twitter-badge" : "");
    badge.textContent = isTwitter ? "𝕏" : "DEAL";

    const source = document.createElement("span");
    source.className = "deal-source";
    source.textContent = d.source;

    const when = document.createElement("span");
    when.className = "deal-time";
    when.textContent = d.ts ? timeAgo(d.ts) : "";

    top.append(badge, source, when);

    const titleLink = document.createElement("a");
    titleLink.className = "deal-title";
    titleLink.href = d.link || "#";
    titleLink.target = "_blank";
    titleLink.rel = "noopener noreferrer";
    titleLink.textContent = d.title;

    const reason = document.createElement("div");
    reason.className = "deal-reason";
    reason.textContent = d.reason;

    card.append(top, titleLink, reason);
    list.appendChild(card);
  }
}

// ── Load from storage ──────────────────────────────────────────────────────────

function loadDeals() {
  chrome.storage.local.get(["deals", "unread", "nitterHost", "premium"], r => {
    const deals   = r.deals  || [];
    const now     = new Date().toLocaleTimeString();
    const status  = document.getElementById("status");
    status.className = "ok";
    status.textContent = `${deals.length} deals — last checked ${now}`;

    // Twitter status indicator
    const tw = document.getElementById("twitterStatus");
    if (r.nitterHost) {
      tw.textContent  = `𝕏 Twitter active via ${r.nitterHost}`;
      tw.className    = "twitter-status on";
    } else {
      tw.textContent  = "𝕏 Twitter: no instance available";
      tw.className    = "twitter-status off";
    }

    renderDeals(deals);
    showAd(!!r.premium);
  });
}

// ── Settings ───────────────────────────────────────────────────────────────────

function loadSettings() {
  chrome.storage.local.get(["maxPrice","minDiscount","affiliateTag","licenseKey","premium","getPremiumUrl","kofiUrl"], r => {
    document.getElementById("setMaxPrice").value    = r.maxPrice    ?? 10;
    document.getElementById("setMinDiscount").value = r.minDiscount ?? 80;
    document.getElementById("setAffTag").value      = r.affiliateTag || "";
    document.getElementById("setLicenseKey").value  = r.licenseKey  || "";
    if (r.getPremiumUrl) document.getElementById("getPremiumLink").href = r.getPremiumUrl;
    if (r.kofiUrl)       document.getElementById("kofiLink").href       = r.kofiUrl;
    updatePremiumUI(!!r.premium);
  });
}

function updatePremiumUI(isPremium) {
  const row = document.querySelector(".premium-row");
  const perks = document.querySelector(".premium-perks");
  if (isPremium) {
    row.classList.add("premium-active");
    perks.textContent = "✓ Premium active — ads removed, full history enabled.";
  }
}

function saveSettings() {
  const maxPrice    = parseFloat(document.getElementById("setMaxPrice").value)  || 10;
  const minDiscount = parseInt(document.getElementById("setMinDiscount").value) || 80;
  const affiliateTag = document.getElementById("setAffTag").value.trim();
  const licenseKey   = document.getElementById("setLicenseKey").value.trim();
  const premium      = validateKey(licenseKey);

  chrome.storage.local.set({ maxPrice, minDiscount, affiliateTag, licenseKey, premium }, () => {
    const btn = document.getElementById("saveSettings");
    btn.textContent = premium ? "Saved ✓ (Premium!)" : "Saved ✓";
    updatePremiumUI(premium);
    loadDeals(); // refresh ad visibility
    setTimeout(() => { btn.textContent = "Save"; }, 2000);
  });
}

// ── Scan trigger ───────────────────────────────────────────────────────────────

function triggerScan() {
  chrome.alarms.create("scan", { delayInMinutes: 0.01, periodInMinutes: 2 });
  const btn = document.getElementById("refreshBtn");
  btn.textContent = "…";
  setTimeout(() => { btn.textContent = "⟳"; loadDeals(); }, 4000);
}

// ── Init ───────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  // Theme
  applyTheme(localStorage.getItem(THEME_KEY) || "dark");
  document.getElementById("themeSelect").addEventListener("change", e => applyTheme(e.target.value));

  // Clear badge on open
  chrome.storage.local.set({ unread: 0 });
  chrome.action.setBadgeText({ text: "" });

  // Buttons
  document.getElementById("refreshBtn").addEventListener("click", triggerScan);
  document.getElementById("clearBtn").addEventListener("click", () => {
    chrome.storage.local.set({ unread: 0 });
    chrome.action.setBadgeText({ text: "" });
    document.getElementById("status").textContent = "Marked all as read.";
  });
  document.getElementById("saveSettings").addEventListener("click", saveSettings);
  document.getElementById("settingsBtn").addEventListener("click", () => {
    const p = document.getElementById("settingsPanel");
    p.classList.toggle("hidden");
    if (!p.classList.contains("hidden")) loadSettings();
  });

  loadDeals();
  setInterval(loadDeals, REFRESH_MS);
});
