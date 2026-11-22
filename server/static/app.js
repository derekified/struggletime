const { esc, el, toast, modal, field, btn, pill, empty, tabs, avatar, icon, iconBtn, selectField, comboField, pathField, pieChart } = UI;
const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => Number(n || 0).toFixed(d);
const timeFmt = () => { try { return localStorage.getItem("st_timefmt") || "auto"; } catch (_) { return "auto"; } };
const fmtH = (h) => {
  const n = Number(h || 0);
  const mode = timeFmt();
  if (mode === "hours") return `${fmt(n, 2)}h`;
  if (mode === "hm") {
    const hrs = Math.floor(n);
    const mins = Math.round((n - hrs) * 60);
    if (hrs <= 0) return `${mins}m`;
    return mins ? `${hrs}h ${mins}m` : `${hrs}h`;
  }
  if (n < 1 / 60) return "0m";
  if (n < 1) return `${Math.round(n * 60)}m`;
  return `${fmt(n, 1)}h`;
};
function prettyChip(obj) {
  if (!obj) return "(none)";
  try { return JSON.stringify(obj, null, 2); } catch (_) { return String(obj); }
}
/* ── state (all lets before any paint/fetch side effects) ─────────────── */
const token = () => localStorage.getItem("st_token") || "";
const setToken = (t) => { if (t) localStorage.setItem("st_token", t); else localStorage.removeItem("st_token"); };
const authHdr = () => (token() ? { Authorization: "Bearer " + token() } : {});
const TRANSIENT_HTTP = new Set([0, 408, 425, 429, 500, 502, 503, 504]);
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

let selected = "", authStatus = null, meProfile = null, ghReposCache = null, ghReposAt = 0;
let lbPeriod = "week", lbQuery = "";
let analytics = null, projectsCache = [];
let meLoaded = false, authStatusLoaded = false, booted = false;
let softRefreshQueued = false;
let pageLoadGen = 0;
let refreshChain = Promise.resolve();
let refreshRunning = false;
let apiKeyPlain = "";
let daemonLinkInfo = null;
let activePage = "landing";
let authReady = null; // Promise gate — page loaders await this
let settingsFilled = false; // must init before early showPage → paintSettingsForm

/* ── fetch layer ─────────────────────────────────────────────────────── */
async function j(u, o = {}) {
  const { cookieOnly, noCookieRetry, timeout = 15000, retries = 2, ...fetchOpts } = o;
  const method = (fetchOpts.method || "GET").toUpperCase();
  const canRetry = method === "GET" || method === "HEAD";
  let last = [0, { error: "network error" }];
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    try {
      const headers = { ...(fetchOpts.headers || {}) };
      if (!cookieOnly) Object.assign(headers, authHdr());
      else delete headers.Authorization;
      const r = await fetch(u, {
        credentials: "same-origin",
        ...fetchOpts,
        headers,
        signal: fetchOpts.signal || ctrl.signal,
      });
      const status = r.status;
      const body = await r.json().catch(() => ({}));
      // Some routes are Slack-session-only (e.g. /v1/auth/api-key). A 401 there must
      // NOT wipe localStorage — that blanked every subsequent /v1 fetch mid-refresh.
      // Retry cookie-only once; only loadMe clears a truly dead bearer.
      if (status === 401 && token() && !cookieOnly && !noCookieRetry) {
        const cookieAttempt = await j(u, { ...o, cookieOnly: true, noCookieRetry: true, retries: 0 });
        if (cookieAttempt[0] === 200) return cookieAttempt;
        return [status, body];
      }
      if (canRetry && TRANSIENT_HTTP.has(status) && attempt < retries) {
        last = [status, body?.error ? body : { error: `HTTP ${status}` }];
        await sleep(180 * (attempt + 1) + Math.floor(Math.random() * 120));
        continue;
      }
      return [status, body];
    } catch (err) {
      const timedOut = err && (err.name === "AbortError" || err.code === 20);
      last = [0, { error: timedOut ? "request timed out" : "network error" }];
      if (canRetry && attempt < retries) {
        await sleep(220 * (attempt + 1) + Math.floor(Math.random() * 150));
        continue;
      }
      return last;
    } finally {
      clearTimeout(timer);
    }
  }
  return last;
}

function fetchError(status, body, fallback) {
  return (body && body.error) || (status === 0 ? "network/timeout" : status ? `HTTP ${status}` : fallback);
}

function hydrateNavIcons(root = document) {
  root.querySelectorAll(".nav-ico[data-glyph]").forEach((node) => {
    if (node.dataset.ready) return;
    const g = node.dataset.glyph;
    const url = `https://icons.hackclub.com/api/icons/black/${encodeURIComponent(g)}`;
    node.style.setProperty("--hc-url", `url(${url})`);
    node.dataset.ready = "1";
  });
}

const STALE_FILTER = new Set(["untracked", "local-daemon", "local_daemon", ""]);
function sanitizeFilter(v, fallback = "all") {
  const s = String(v == null ? "" : v).trim();
  if (!s || STALE_FILTER.has(s)) return fallback;
  return s;
}
["st_filter_language", "st_filter_os", "st_filter_editor", "st_filter_category"].forEach((k) => {
  try {
    if (STALE_FILTER.has(String(localStorage.getItem(k) || "").trim())) localStorage.removeItem(k);
  } catch (_) {}
});
const filters = {
  range: localStorage.getItem("st_range") || localStorage.getItem("st_def_range") || "7d",
  project: "all",
  language: sanitizeFilter(localStorage.getItem("st_filter_language"), "all"),
  os: sanitizeFilter(localStorage.getItem("st_filter_os"), "all"),
  editor: sanitizeFilter(localStorage.getItem("st_filter_editor"), "all"),
  category: sanitizeFilter(localStorage.getItem("st_filter_category"), "all"),
};
const projSearch = {
  q: "",
  range: localStorage.getItem("st_proj_range") || "7d",
  status: localStorage.getItem("st_proj_status") || "active",
};
const TZ_OFFSET = -new Date().getTimezoneOffset();
const KNOWN = ["landing", "login", "home", "projects", "builders", "docs", "review", "admin", "settings"];
const PROTECTED = new Set(["home", "projects", "review", "admin", "settings"]);
const STAFF_PAGES = new Set(["review", "admin"]);

/** Guests cannot stay on PROTECTED pages. Slack sessions that still need a handle may use /settings (claim UI). */
function allowProtectedPage(page) {
  if (!PROTECTED.has(page)) return true;
  if (!meLoaded) return true;
  if (pendingAuthCode()) return true;
  if (isSignedIn()) return true;
  if (page === "settings" && hasSession()) return true;
  return false;
}

function paintDangerBtn(btn, label, glyph = "delete") {
  if (!btn) return;
  btn.replaceChildren(icon(glyph, { size: 14 }), document.createTextNode(label));
}
function resetDeleteAccountBtn() {
  const btnEl = $("btn-delete-account");
  if (!btnEl) return;
  if (btnEl._unlockTick) { clearInterval(btnEl._unlockTick); btnEl._unlockTick = null; }
  delete btnEl.dataset.unlocked;
  btnEl.disabled = false;
  paintDangerBtn(btnEl, "delete account");
}

function pathToPage(pathname) {
  const raw = (pathname || "/").replace(/\/$/, "") || "/";
  let page = raw === "/" ? "landing" : raw.slice(1);
  if (page === "dashboard") page = "home";
  if (page === "fraud") page = "admin";
  if (page === "signout" || page === "logout") return page;
  return KNOWN.includes(page) ? page : "landing";
}

function isSignedIn() {
  // Prefer profile from /v1/auth/me (cookie or bearer). localStorage alone is not enough.
  return !!(meProfile && meProfile.handle);
}
function hasSession() {
  return !!(meProfile && (meProfile.handle || meProfile.needs_handle));
}
function isStaff() {
  return !!(meProfile && (meProfile.is_staff || meProfile.can_review || meProfile.can_admin));
}
function canReview() {
  return !!(meProfile && meProfile.can_review);
}
function canAdmin() {
  return !!(meProfile && meProfile.can_admin);
}

function pendingAuthCode() {
  return new URLSearchParams(location.search).has("auth");
}

function paintNavAuth() {
  const ok = isSignedIn();
  const session = hasSession();
  const staff = isStaff();
  document.querySelectorAll('#top-nav a[data-auth="required"]').forEach((a) => { a.hidden = !ok; });
  document.querySelectorAll('#top-nav a[data-auth="staff"]').forEach((a) => {
    const needAdmin = a.dataset.nav === "admin";
    a.hidden = needAdmin ? !canAdmin() : !staff;
  });
  const shell = $("app-shell");
  if (shell) {
    // guest-mode = no Slack session at all (hide avatar / streak / name)
    shell.classList.toggle("guest-mode", !session);
    shell.classList.toggle("authed", !!ok);
  }
  const topAuth = $("top-auth");
  if (topAuth) {
    // Never show the avatar circle for guests / loading; only when painted .signed.
    topAuth.hidden = !session || !topAuth.classList.contains("signed");
  }
}

function clearClientSession() {
  apiKeyPlain = "";
  daemonLinkInfo = null;
  setToken("");
  meProfile = null;
  meLoaded = true;
  authReady = null;
  authStatusLoaded = false;
  ghReposCache = null;
  ghReposAt = 0;
  projectsCache = [];
  window._projects = [];
  analytics = null;
  selected = "";
  fakeStatsMode = false;
  filtersBeforeFake = null;
  try { sessionStorage.removeItem("st_fake_stats"); } catch (_) {}
  paintFakeBadge();
  if ($("nav-stats")) $("nav-stats").hidden = true;
  if ($("nav-identity")) $("nav-identity").hidden = true;
  if ($("top-auth")) $("top-auth").hidden = true;
  if ($("nav-flag")) $("nav-flag").textContent = "";
  if ($("nav-streak")) $("nav-streak").textContent = "";
  if ($("nav-goal")) $("nav-goal").textContent = "";
  if ($("nav-hours")) $("nav-hours").textContent = "";
  if ($("who-name")) { $("who-name").textContent = ""; $("who-name").href = "/login"; }
  if ($("acct-state")) $("acct-state").textContent = "";
  if ($("set-display-name")) $("set-display-name").value = "";
  if ($("set-username")) $("set-username").value = "";
  if ($("set-email")) $("set-email").value = "";
  if ($("set-country")) $("set-country").value = "";
  if ($("set-timezone")) $("set-timezone").value = "";
  if ($("set-lb-opt-out")) $("set-lb-opt-out").checked = false;
  if ($("set-show-lb")) $("set-show-lb").checked = true;
  if ($("delete-confirm")) $("delete-confirm").value = "";
  if ($("delete-confirm-phrase")) $("delete-confirm-phrase").value = "";
  resetDeleteAccountBtn();
  if ($("settings-main")) $("settings-main").hidden = true;
  if ($("signout-form")) $("signout-form").style.display = "none";
  if ($("gh-connected")) $("gh-connected").hidden = true;
  if ($("gh-form")) $("gh-form").hidden = true;
  if ($("gh-check")) $("gh-check").textContent = "";
  paintTopUser("", "", "/login", "sign in");
  paintWhoAction("sign in", "/login", "door-enter");
  paintNavAuth();
  paintHeroCta();
}

async function doLogout() {
  try { await j("/v1/auth/logout", { method: "POST" }); } catch (_) {}
  clearClientSession();
  navigate("/");
}

activePage = pathToPage(location.pathname);

function showPage(page, { push = false, load = true } = {}) {
  if (page === "signout" || page === "logout") {
    doLogout();
    return;
  }
  if (!KNOWN.includes(page)) page = "landing";
  // Only bounce after /v1/auth/me has resolved. Cookie sessions often have no
  // localStorage token — redirecting earlier sent /home → / and left data blank.
  if (!allowProtectedPage(page)) {
    page = "landing";
    push = true;
  }
  if (STAFF_PAGES.has(page) && meLoaded && isSignedIn()) {
    if (page === "admin" && !canAdmin()) { page = "home"; push = true; }
    else if (page === "review" && !isStaff()) { page = "home"; push = true; }
  }
  activePage = page;
  const path = page === "landing" ? "/" : "/" + page;
  if (push) history.pushState({ page }, "", path);
  document.querySelectorAll(".page").forEach((node) => node.classList.toggle("on", node.id === "page-" + page));
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === page));
  document.querySelectorAll("[data-top]").forEach((a) => a.classList.toggle("on", a.dataset.top === page));
  const shell = $("app-shell");
  shell.classList.toggle("landing-mode", page === "landing");
  shell.classList.toggle("login-mode", page === "login");
  paintNavAuth();
  paintLogin();
  if (page === "settings") paintSettingsForm();
  if (page === "docs") { location.href = "/docs/"; return; }
  if (isSignedIn()) refreshApiKeyStatus();
  else paintDaemonBanners();
  if (load) loadActivePageData();
}

function navigate(url) {
  const u = new URL(url, location.origin);
  if (u.origin !== location.origin) { location.href = url; return; }
  const page = pathToPage(u.pathname);
  if (u.search) history.pushState({ page }, "", u.pathname + u.search);
  else history.pushState({ page }, "", page === "landing" ? "/" : "/" + page);
  // apply query without full reload
  if (u.searchParams.get("signed_out") || u.searchParams.get("oauth") || u.searchParams.get("auth")) {
    activePage = page;
    document.querySelectorAll(".page").forEach((node) => node.classList.toggle("on", node.id === "page-" + page));
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === page));
    document.querySelectorAll("[data-top]").forEach((a) => a.classList.toggle("on", a.dataset.top === page));
    $("app-shell").classList.toggle("landing-mode", page === "landing");
    $("app-shell").classList.toggle("login-mode", page === "login");
  }
  showPage(page, { push: false });
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-nav-link], a[data-nav], a[data-top], a.brand, a.top-user, a#who-action");
  if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
  const href = a.getAttribute("href");
  if (!href || href.startsWith("http") || href.startsWith("mailto:") || href.startsWith("#")) return;
  if (href.startsWith("/v1/")) return; // oauth / api starts
  if (href === "/docs" || href.startsWith("/docs/")) return; // Fumadocs static site (full load)
  e.preventDefault();
  navigate(href);
});
window.addEventListener("popstate", () => showPage(pathToPage(location.pathname), { push: false }));

// Paint shell visibility only — full showPage/paintSettingsForm wait until COUNTRIES and
// other late consts exist (avoids TDZ that blanked /settings fetches).
(function paintShellEarly() {
  let page = activePage;
  if (!KNOWN.includes(page)) page = "landing";
  activePage = page;
  document.querySelectorAll(".page").forEach((node) => node.classList.toggle("on", node.id === "page-" + page));
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === page));
  document.querySelectorAll("[data-top]").forEach((a) => a.classList.toggle("on", a.dataset.top === page));
  const shell = $("app-shell");
  if (shell) {
    shell.classList.toggle("landing-mode", page === "landing");
    shell.classList.toggle("login-mode", page === "login");
  }
})();
hydrateNavIcons();

const DASH_CARD_DEFAULTS = [
  "projects", "modes", "velocity", "classifier", "decay", "timeline", "editor", "os",
  "afternoons", "rhythm", "pace", "assist", "languages", "chain", "sharecard", "friction", "ledger",
];
function loadDashPrefs() {
  let cards = DASH_CARD_DEFAULTS.slice();
  let hidden = [];
  try {
    const raw = JSON.parse(localStorage.getItem("st_dash_cards") || "null");
    if (Array.isArray(raw) && raw.length) {
      const known = new Set(DASH_CARD_DEFAULTS);
      cards = raw.filter((id) => known.has(id));
      for (const id of DASH_CARD_DEFAULTS) if (!cards.includes(id)) cards.push(id);
    }
    const hid = JSON.parse(localStorage.getItem("st_dash_hidden") || "null");
    if (Array.isArray(hid)) hidden = hid.filter((id) => DASH_CARD_DEFAULTS.includes(id));
  } catch (_) {}
  return {
    metric: localStorage.getItem("st_metric_mode") || "weighted",
    calib: localStorage.getItem("st_calib_preset") || "balanced",
    cards,
    hidden: new Set(hidden),
  };
}
let dashPrefs = loadDashPrefs();

const THEME_ALIAS = {
  lime: "neon", ember: "classic", ocean: "nord", forest: "gruvbox-dark", ink: "midnight",
  obsidian: "amoled", paper: "github-light", crt: "neon", nordic: "nord", sand: "gruvbox-dark",
  midnight: "midnight",
};
const THEME_IDS = ["classic","midnight","neon","catppuccin-mocha","catppuccin-latte","gruvbox-dark","github-dark","github-light","nord","rose-pine","rose-pine-dawn","amoled"];
function resolveTheme(raw) {
  let v = (raw || "").trim() || "classic";
  if (THEME_ALIAS[v]) v = THEME_ALIAS[v];
  if (!THEME_IDS.includes(v)) v = "classic";
  return v;
}
const theme = resolveTheme(localStorage.getItem("st_theme"));
document.documentElement.setAttribute("data-theme", theme);
try { localStorage.setItem("st_theme", theme); } catch (_) {}
const density = localStorage.getItem("st_density") || "comfortable";
document.documentElement.setAttribute("data-density", density);
if (!localStorage.getItem("st_range") && localStorage.getItem("st_def_range")) {
  try {
    const def = localStorage.getItem("st_def_range");
    localStorage.setItem("st_range", def);
    if (def) filters.range = def;
  } catch (_) {}
}

async function mountHeroShot() {
  const box = $("hero-preview");
  if (!box || box.querySelector("svg.hero-shot")) return;
  try {
    const r = await fetch("/img/fake-stats-preview.svg");
    if (!r.ok) return;
    box.innerHTML = await r.text();
    const node = box.querySelector("svg");
    if (!node) return;
    node.classList.add("hero-shot");
    node.setAttribute("width", "720");
    node.setAttribute("height", "460");
    node.removeAttribute("xmlns");
  } catch (_) { /* keep <object> fallback */ }
}
mountHeroShot();

const HERO_SCENES = {
  struggle: { label: "fixing a bug · 1.00×", struggle: 78, dump: 14, idle: 8 },
  dump: { label: "unread AI paste · 0.20×", struggle: 12, dump: 70, idle: 18 },
  silent: { label: "reading an error · ~0.42×", struggle: 38, dump: 22, idle: 40 },
  idle: { label: "away from keyboard · 0.00×", struggle: 4, dump: 8, idle: 88 },
};
function paintHeroLive(scene = "struggle") {
  const spec = HERO_SCENES[scene] || HERO_SCENES.struggle;
  if ($("hero-live-label")) $("hero-live-label").textContent = spec.label;
  if ($("hero-live-struggle")) $("hero-live-struggle").style.width = spec.struggle + "%";
  if ($("hero-live-dump")) $("hero-live-dump").style.width = spec.dump + "%";
  if ($("hero-live-idle")) $("hero-live-idle").style.width = spec.idle + "%";
  document.querySelectorAll("[data-hero-scene]").forEach((btn) => {
    btn.classList.toggle("on", btn.getAttribute("data-hero-scene") === scene);
  });
}
function mountHeroLive() {
  const root = $("hero-live");
  if (!root || root.dataset.ready) return;
  root.dataset.ready = "1";
  root.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-hero-scene]");
    if (!btn) return;
    paintHeroLive(btn.getAttribute("data-hero-scene"));
  });
  let i = 0;
  const order = ["struggle", "dump", "silent", "idle"];
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let paused = false;
  root.addEventListener("pointerenter", () => { paused = true; });
  root.addEventListener("pointerleave", () => { paused = false; });
  if (!reduce) {
    root._heroTimer = setInterval(() => {
      if (paused || document.hidden || activePage !== "landing") return;
      i = (i + 1) % order.length;
      paintHeroLive(order[i]);
    }, 4200);
  }
  paintHeroLive("struggle");
}
mountHeroLive();

function applyTheme(v) {
  v = resolveTheme(v);
  localStorage.setItem("st_theme", v);
  document.documentElement.setAttribute("data-theme", v);
  ["theme-sel", "dash-theme-sel"].forEach((id) => { if ($(id)) $(id).value = v; });
  document.querySelectorAll("[data-theme-pick]").forEach((btn) => {
    const on = btn.getAttribute("data-theme-pick") === v;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
}
function applyDensity(v) {
  localStorage.setItem("st_density", v);
  document.documentElement.setAttribute("data-density", v);
  ["density-sel", "dash-density-sel"].forEach((id) => { if ($(id)) $(id).value = v; });
}
function persistMetricMode(v) {
  dashPrefs.metric = v;
  localStorage.setItem("st_metric_mode", v);
  ["metric-mode-sel", "set-metric-mode"].forEach((id) => { if ($(id)) $(id).value = v; });
  paintMetricHint();
  if (analytics) paintHomeAnalytics(analytics);
}
function persistCalib(v) {
  dashPrefs.calib = v;
  localStorage.setItem("st_calib_preset", v);
  ["calib-preset-sel", "set-calib-preset"].forEach((id) => { if ($(id)) $(id).value = v; });
  renderCalibPreview();
}
function paintMetricHint() {
  const elHint = $("dash-metric-hint");
  if (!elHint) return;
  elHint.textContent = ({
    weighted: "Weighted hours favor real recovery loops over dumps and idle time.",
    raw: "Clock hours from the tracker, before StruggleTime adjusts for real fixing vs idle or unread pastes.",
    active: "Active span only: the busy part of each bucket, not the idle tail.",
    compare: "Weighted next to raw, so the discount is easy to see.",
  })[dashPrefs.metric] || elHint.textContent;
}
function saveDashCards() {
  try {
    localStorage.setItem("st_dash_cards", JSON.stringify(dashPrefs.cards));
    localStorage.setItem("st_dash_hidden", JSON.stringify([...dashPrefs.hidden]));
  } catch (_) {}
  applyDashLayout();
}
const CARD_SPAN_PREF = {
  projects: 6, modes: 6, velocity: 4, classifier: 4, decay: 4,
  timeline: 4, editor: 4, os: 4, afternoons: 12, rhythm: 12,
  pace: 4, assist: 4, languages: 4, chain: 6, sharecard: 6,
  friction: 12, ledger: 12,
};
const SPAN_CLASSES = ["span-3", "span-4", "span-5", "span-6", "span-7", "span-8", "span-12"];
function setCardSpan(node, span) {
  const s = Math.max(3, Math.min(12, span | 0));
  node.classList.remove(...SPAN_CLASSES);
  node.classList.add(`span-${s}`);
  node.dataset.span = String(s);
}
function reflowDashSpans(visible) {
  // Pack preferred widths into rows of 12; stretch the last card in each row
  // so toggles/reorders never leave a narrow orphan column.
  let row = [];
  let used = 0;
  const flush = () => {
    if (!row.length) return;
    const leftover = 12 - used;
    if (leftover > 0) {
      const last = row[row.length - 1];
      setCardSpan(last.node, last.span + leftover);
    }
    row = [];
    used = 0;
  };
  for (const node of visible) {
    const id = node.getAttribute("data-card");
    let pref = CARD_SPAN_PREF[id] || 4;
    if (pref >= 12) {
      flush();
      setCardSpan(node, 12);
      continue;
    }
    if (used + pref > 12) flush();
    if (pref > 12 - used) pref = 12 - used;
    setCardSpan(node, pref);
    row.push({ node, span: pref });
    used += pref;
    if (used >= 12) flush();
  }
  flush();
}
function applyDashLayout() {
  const bento = $("dash-bento");
  if (!bento) return;
  const map = {};
  bento.querySelectorAll("[data-card]").forEach((node) => { map[node.getAttribute("data-card")] = node; });
  const visible = [];
  for (const id of dashPrefs.cards) {
    const node = map[id];
    if (!node) continue;
    const hide = dashPrefs.hidden.has(id);
    node.hidden = hide;
    bento.appendChild(node);
    if (!hide) visible.push(node);
  }
  reflowDashSpans(visible);
}
function renderCardToggles() {
  const box = $("card-toggle-list");
  if (!box) return;
  const labels = {
    projects: "Projects", modes: "Modes", velocity: "Struggle → ship", classifier: "Dump vs recovery",
    decay: "Silent & idle", timeline: "Timeline", editor: "Editor", os: "OS", afternoons: "Multi-project afternoons",
    rhythm: "Rhythm", pace: "Pace", assist: "Assist", languages: "Languages", chain: "History check",
    sharecard: "Summary card", friction: "Friction timeline", ledger: "Time log",
  };
  box.replaceChildren(...dashPrefs.cards.map((id, idx) => {
    const row = el("label", { className: "card-toggle-row" }, [
      el("input", {
        type: "checkbox", checked: !dashPrefs.hidden.has(id),
        onChange: (e) => {
          if (e.target.checked) dashPrefs.hidden.delete(id);
          else dashPrefs.hidden.add(id);
          saveDashCards();
        },
      }),
      el("span", { text: labels[id] || id }),
      el("span", { className: "card-order" }, [
        el("button", {
          type: "button", className: "ui-icon-btn", disabled: idx === 0, "aria-label": "Move up",
          onClick: () => {
            if (idx <= 0) return;
            const arr = dashPrefs.cards;
            [arr[idx - 1], arr[idx]] = [arr[idx], arr[idx - 1]];
            saveDashCards();
            renderCardToggles();
          },
        }, ["↑"]),
        el("button", {
          type: "button", className: "ui-icon-btn", disabled: idx === dashPrefs.cards.length - 1, "aria-label": "Move down",
          onClick: () => {
            if (idx >= dashPrefs.cards.length - 1) return;
            const arr = dashPrefs.cards;
            [arr[idx + 1], arr[idx]] = [arr[idx], arr[idx + 1]];
            saveDashCards();
            renderCardToggles();
          },
        }, ["↓"]),
      ]),
    ]);
    return row;
  }));
}
function calibScenarioWeights(preset) {
  // Educational preview multipliers relative to server defaults (does not change ingest).
  const base = [
    { id: "struggle", label: "Struggle loop (reedit + fail→pass)", w: 1.0 },
    { id: "human", label: "Human edits, unverified", w: 0.70 },
    { id: "dump", label: "Unverified multi-file dump", w: 0.20 },
    { id: "silent5", label: "Silent debug ≤5 min", w: 0.70 },
    { id: "silent8", label: "Silent debug ~8 min", w: 0.42 },
    { id: "silent12", label: "Silent debug ≥12 min", w: 0.0 },
    { id: "idle", label: "Idle after active span (≥12 min quiet)", w: 0.0 },
  ];
  const scale = {
    balanced: {},
    struggle: { struggle: 1.0, human: 0.85, dump: 0.15, silent5: 0.75, silent8: 0.45, silent12: 0, idle: 0 },
    strict: { struggle: 1.0, human: 0.55, dump: 0.10, silent5: 0.55, silent8: 0.25, silent12: 0, idle: 0 },
    lenient: { struggle: 1.0, human: 0.75, dump: 0.25, silent5: 0.85, silent8: 0.55, silent12: 0.15, idle: 0.05 },
  }[preset] || {};
  return base.map((s) => ({ ...s, w: scale[s.id] != null ? scale[s.id] : s.w }));
}
function renderCalibPreview() {
  const box = $("calib-preview-rows");
  if (!box) return;
  const rows = calibScenarioWeights(dashPrefs.calib);
  box.replaceChildren(...rows.map((s) => el("div", { className: "calib-row" }, [
    el("span", { className: "calib-label", text: s.label }),
    el("div", { className: "calib-bar" }, [
      el("i", { style: `width:${Math.max(2, s.w * 100)}%` }),
    ]),
    el("b", { className: "tabular", text: s.w.toFixed(2) }),
  ])));
}
function setDashCustomizeOpen(open) {
  const drawer = $("dash-customize");
  const btn = $("btn-dash-customize");
  if (!drawer) return;
  drawer.hidden = !open;
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    renderCardToggles();
    renderCalibPreview();
  }
}

if ($("theme-sel")) {
  $("theme-sel").value = theme;
  $("theme-sel").addEventListener("change", (e) => applyTheme(e.target.value));
}
const themeGrid = $("theme-preview-grid");
if (themeGrid && !themeGrid.dataset.ready) {
  themeGrid.dataset.ready = "1";
  themeGrid.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-theme-pick]");
    if (!btn) return;
    applyTheme(btn.getAttribute("data-theme-pick"));
  });
  applyTheme(theme);
}
if ($("density-sel")) {
  $("density-sel").value = density;
  $("density-sel").addEventListener("change", (e) => applyDensity(e.target.value));
}
if ($("timefmt-sel")) {
  $("timefmt-sel").value = timeFmt();
  $("timefmt-sel").addEventListener("change", (e) => {
    localStorage.setItem("st_timefmt", e.target.value);
    softRefresh();
  });
}
if ($("def-range-sel")) {
  $("def-range-sel").value = localStorage.getItem("st_def_range") || localStorage.getItem("st_range") || "7d";
  $("def-range-sel").addEventListener("change", (e) => {
    localStorage.setItem("st_def_range", e.target.value);
    filters.range = e.target.value;
    localStorage.setItem("st_range", e.target.value);
    if ($("dash-range-sel")) $("dash-range-sel").value = e.target.value;
    if (activePage === "home") loadAnalytics();
  });
}
["metric-mode-sel", "set-metric-mode"].forEach((id) => {
  if (!$(id)) return;
  $(id).value = dashPrefs.metric;
  $(id).addEventListener("change", (e) => persistMetricMode(e.target.value));
});
["calib-preset-sel", "set-calib-preset"].forEach((id) => {
  if (!$(id)) return;
  $(id).value = dashPrefs.calib;
  $(id).addEventListener("change", (e) => persistCalib(e.target.value));
});
if ($("dash-theme-sel")) {
  $("dash-theme-sel").value = theme;
  $("dash-theme-sel").addEventListener("change", (e) => applyTheme(e.target.value));
}
if ($("dash-density-sel")) {
  $("dash-density-sel").value = density;
  $("dash-density-sel").addEventListener("change", (e) => applyDensity(e.target.value));
}
if ($("dash-range-sel")) {
  $("dash-range-sel").value = localStorage.getItem("st_def_range") || localStorage.getItem("st_range") || "7d";
  $("dash-range-sel").addEventListener("change", (e) => {
    localStorage.setItem("st_def_range", e.target.value);
    filters.range = e.target.value;
    localStorage.setItem("st_range", e.target.value);
    if ($("def-range-sel")) $("def-range-sel").value = e.target.value;
    loadAnalytics();
  });
}
if ($("btn-dash-customize")) {
  $("btn-dash-customize").addEventListener("click", () => {
    const drawer = $("dash-customize");
    setDashCustomizeOpen(!!(drawer && drawer.hidden));
  });
}
if ($("btn-dash-customize-close")) $("btn-dash-customize-close").addEventListener("click", () => setDashCustomizeOpen(false));
if ($("btn-dash-share")) $("btn-dash-share").addEventListener("click", () => shareDashboardSummary());
paintMetricHint();
applyDashLayout();
renderCalibPreview();

if (activePage === "signout" || activePage === "logout") {
  showPage(activePage);
}

function oauthMsg(code) {
  return ({
    claim_handle: "Signed in with Hack Club Slack. Grab a handle next.",
    slack_linked: "Signed in with Hack Club Slack.",
    github_linked: "GitHub connected.",
    github_failed: "GitHub sign-in didn’t work. Try again?",
    slack_failed: "Hack Club Slack sign-in didn’t work. Try again?",
    bad_state: "That sign-in link expired. Start again from Hack Club Slack.",
    sign_in_with_slack_first: "Sign in with Hack Club Slack first.",
  })[code] || (code ? code.replaceAll("_", " ") : "");
}

function flagEmoji(cc) {
  if (!cc || !/^[A-Za-z]{2}$/.test(cc)) return "";
  const u = cc.toUpperCase();
  return String.fromCodePoint(...[...u].map((c) => 127397 + c.charCodeAt(0)));
}

const COUNTRIES = [
  ["", "select country"],
  ["AF", "Afghanistan"],
  ["AL", "Albania"],
  ["DZ", "Algeria"],
  ["AD", "Andorra"],
  ["AO", "Angola"],
  ["AI", "Anguilla"],
  ["AQ", "Antarctica"],
  ["AG", "Antigua & Barbuda"],
  ["AR", "Argentina"],
  ["AM", "Armenia"],
  ["AW", "Aruba"],
  ["AU", "Australia"],
  ["AT", "Austria"],
  ["AZ", "Azerbaijan"],
  ["BS", "Bahamas"],
  ["BH", "Bahrain"],
  ["BD", "Bangladesh"],
  ["BB", "Barbados"],
  ["BY", "Belarus"],
  ["BE", "Belgium"],
  ["BZ", "Belize"],
  ["BJ", "Benin"],
  ["BM", "Bermuda"],
  ["BT", "Bhutan"],
  ["BO", "Bolivia"],
  ["BA", "Bosnia & Herzegovina"],
  ["BW", "Botswana"],
  ["BV", "Bouvet Island"],
  ["BR", "Brazil"],
  ["GB", "Britain (UK)"],
  ["IO", "British Indian Ocean Territory"],
  ["BN", "Brunei"],
  ["BG", "Bulgaria"],
  ["BF", "Burkina Faso"],
  ["BI", "Burundi"],
  ["KH", "Cambodia"],
  ["CM", "Cameroon"],
  ["CA", "Canada"],
  ["CV", "Cape Verde"],
  ["BQ", "Caribbean NL"],
  ["KY", "Cayman Islands"],
  ["CF", "Central African Rep."],
  ["TD", "Chad"],
  ["CL", "Chile"],
  ["CN", "China"],
  ["CX", "Christmas Island"],
  ["CC", "Cocos (Keeling) Islands"],
  ["CO", "Colombia"],
  ["KM", "Comoros"],
  ["CD", "Congo (Dem. Rep.)"],
  ["CG", "Congo (Rep.)"],
  ["CK", "Cook Islands"],
  ["CR", "Costa Rica"],
  ["HR", "Croatia"],
  ["CU", "Cuba"],
  ["CW", "Curaçao"],
  ["CY", "Cyprus"],
  ["CZ", "Czech Republic"],
  ["CI", "Côte d’Ivoire"],
  ["DK", "Denmark"],
  ["DJ", "Djibouti"],
  ["DM", "Dominica"],
  ["DO", "Dominican Republic"],
  ["TL", "East Timor"],
  ["EC", "Ecuador"],
  ["EG", "Egypt"],
  ["SV", "El Salvador"],
  ["GQ", "Equatorial Guinea"],
  ["ER", "Eritrea"],
  ["EE", "Estonia"],
  ["SZ", "Eswatini (Swaziland)"],
  ["ET", "Ethiopia"],
  ["FK", "Falkland Islands"],
  ["FO", "Faroe Islands"],
  ["FJ", "Fiji"],
  ["FI", "Finland"],
  ["FR", "France"],
  ["GF", "French Guiana"],
  ["PF", "French Polynesia"],
  ["TF", "French S. Terr."],
  ["GA", "Gabon"],
  ["GM", "Gambia"],
  ["GE", "Georgia"],
  ["DE", "Germany"],
  ["GH", "Ghana"],
  ["GI", "Gibraltar"],
  ["GR", "Greece"],
  ["GL", "Greenland"],
  ["GD", "Grenada"],
  ["GP", "Guadeloupe"],
  ["GU", "Guam"],
  ["GT", "Guatemala"],
  ["GG", "Guernsey"],
  ["GN", "Guinea"],
  ["GW", "Guinea-Bissau"],
  ["GY", "Guyana"],
  ["HT", "Haiti"],
  ["HM", "Heard Island & McDonald Islands"],
  ["HN", "Honduras"],
  ["HK", "Hong Kong"],
  ["HU", "Hungary"],
  ["IS", "Iceland"],
  ["IN", "India"],
  ["ID", "Indonesia"],
  ["IR", "Iran"],
  ["IQ", "Iraq"],
  ["IE", "Ireland"],
  ["IM", "Isle of Man"],
  ["IL", "Israel"],
  ["IT", "Italy"],
  ["JM", "Jamaica"],
  ["JP", "Japan"],
  ["JE", "Jersey"],
  ["JO", "Jordan"],
  ["KZ", "Kazakhstan"],
  ["KE", "Kenya"],
  ["KI", "Kiribati"],
  ["KP", "Korea (North)"],
  ["KR", "Korea (South)"],
  ["KW", "Kuwait"],
  ["KG", "Kyrgyzstan"],
  ["LA", "Laos"],
  ["LV", "Latvia"],
  ["LB", "Lebanon"],
  ["LS", "Lesotho"],
  ["LR", "Liberia"],
  ["LY", "Libya"],
  ["LI", "Liechtenstein"],
  ["LT", "Lithuania"],
  ["LU", "Luxembourg"],
  ["MO", "Macau"],
  ["MG", "Madagascar"],
  ["MW", "Malawi"],
  ["MY", "Malaysia"],
  ["MV", "Maldives"],
  ["ML", "Mali"],
  ["MT", "Malta"],
  ["MH", "Marshall Islands"],
  ["MQ", "Martinique"],
  ["MR", "Mauritania"],
  ["MU", "Mauritius"],
  ["YT", "Mayotte"],
  ["MX", "Mexico"],
  ["FM", "Micronesia"],
  ["MD", "Moldova"],
  ["MC", "Monaco"],
  ["MN", "Mongolia"],
  ["ME", "Montenegro"],
  ["MS", "Montserrat"],
  ["MA", "Morocco"],
  ["MZ", "Mozambique"],
  ["MM", "Myanmar (Burma)"],
  ["NA", "Namibia"],
  ["NR", "Nauru"],
  ["NP", "Nepal"],
  ["NL", "Netherlands"],
  ["NC", "New Caledonia"],
  ["NZ", "New Zealand"],
  ["NI", "Nicaragua"],
  ["NE", "Niger"],
  ["NG", "Nigeria"],
  ["NU", "Niue"],
  ["NF", "Norfolk Island"],
  ["MK", "North Macedonia"],
  ["MP", "Northern Mariana Islands"],
  ["NO", "Norway"],
  ["OM", "Oman"],
  ["PK", "Pakistan"],
  ["PW", "Palau"],
  ["PS", "Palestine"],
  ["PA", "Panama"],
  ["PG", "Papua New Guinea"],
  ["PY", "Paraguay"],
  ["PE", "Peru"],
  ["PH", "Philippines"],
  ["PN", "Pitcairn"],
  ["PL", "Poland"],
  ["PT", "Portugal"],
  ["PR", "Puerto Rico"],
  ["QA", "Qatar"],
  ["RO", "Romania"],
  ["RU", "Russia"],
  ["RW", "Rwanda"],
  ["RE", "Réunion"],
  ["AS", "Samoa (American)"],
  ["WS", "Samoa (western)"],
  ["SM", "San Marino"],
  ["ST", "Sao Tome & Principe"],
  ["SA", "Saudi Arabia"],
  ["SN", "Senegal"],
  ["RS", "Serbia"],
  ["SC", "Seychelles"],
  ["SL", "Sierra Leone"],
  ["SG", "Singapore"],
  ["SK", "Slovakia"],
  ["SI", "Slovenia"],
  ["SB", "Solomon Islands"],
  ["SO", "Somalia"],
  ["ZA", "South Africa"],
  ["GS", "South Georgia & the South Sandwich Islands"],
  ["SS", "South Sudan"],
  ["ES", "Spain"],
  ["LK", "Sri Lanka"],
  ["BL", "St Barthelemy"],
  ["SH", "St Helena"],
  ["KN", "St Kitts & Nevis"],
  ["LC", "St Lucia"],
  ["SX", "St Maarten (Dutch)"],
  ["MF", "St Martin (French)"],
  ["PM", "St Pierre & Miquelon"],
  ["VC", "St Vincent"],
  ["SD", "Sudan"],
  ["SR", "Suriname"],
  ["SJ", "Svalbard & Jan Mayen"],
  ["SE", "Sweden"],
  ["CH", "Switzerland"],
  ["SY", "Syria"],
  ["TW", "Taiwan"],
  ["TJ", "Tajikistan"],
  ["TZ", "Tanzania"],
  ["TH", "Thailand"],
  ["TG", "Togo"],
  ["TK", "Tokelau"],
  ["TO", "Tonga"],
  ["TT", "Trinidad & Tobago"],
  ["TN", "Tunisia"],
  ["TR", "Turkey"],
  ["TM", "Turkmenistan"],
  ["TC", "Turks & Caicos Is"],
  ["TV", "Tuvalu"],
  ["UG", "Uganda"],
  ["UA", "Ukraine"],
  ["AE", "United Arab Emirates"],
  ["US", "United States"],
  ["UY", "Uruguay"],
  ["UM", "US minor outlying islands"],
  ["UZ", "Uzbekistan"],
  ["VU", "Vanuatu"],
  ["VA", "Vatican City"],
  ["VE", "Venezuela"],
  ["VN", "Vietnam"],
  ["VG", "Virgin Islands (UK)"],
  ["VI", "Virgin Islands (US)"],
  ["WF", "Wallis & Futuna"],
  ["EH", "Western Sahara"],
  ["YE", "Yemen"],
  ["ZM", "Zambia"],
  ["ZW", "Zimbabwe"],
  ["AX", "Åland Islands"],
];

function paintTopUser(url, handle, href, title) {
  const topAuth = $("top-auth");
  if (!topAuth) return;
  topAuth.innerHTML = "";
  const show = !!(url || handle);
  topAuth.classList.toggle("signed", show);
  topAuth.hidden = !show;
  if (!show) {
    topAuth.removeAttribute("href");
    topAuth.title = "";
    topAuth.setAttribute("aria-label", "profile");
    return;
  }
  topAuth.href = href || "/settings";
  topAuth.title = title || "";
  topAuth.setAttribute("aria-label", title || "profile");
  if (url) topAuth.appendChild(el("img", { src: url, alt: handle || "profile" }));
  else topAuth.appendChild(el("span", { className: "top-user-fallback", text: String(handle).slice(0, 1).toUpperCase() }));
}

function paintWhoAction(label, href, glyph) {
  const a = $("who-action");
  if (!a) return;
  a.href = href;
  a.innerHTML = "";
  if (glyph) a.append(icon(glyph, { size: 14 }), document.createTextNode(label));
  else a.textContent = label;
}

function paintAccount() {
  const p = meProfile, signed = hasSession();
  const q = new URLSearchParams(location.search).get("oauth");
  const stats = $("nav-stats");
  const ident = $("nav-identity");
  const flagEl = $("nav-flag");
  if (signed && p && p.needs_handle) {
    paintTopUser(p.slack_avatar, p.slack_name, "/login", "claim handle");
    if ($("top-auth")) $("top-auth").hidden = false;
    if (ident) ident.hidden = true;
    if (stats) stats.hidden = true;
    paintWhoAction("claim handle", "/login", "badge-check");
    if ($("acct-state")) $("acct-state").textContent = "Signed in with Slack. Grab a handle next." + (q ? " " + oauthMsg(q) : "");
    if ($("slack-login-box")) $("slack-login-box").hidden = true;
    if ($("claim-box")) $("claim-box").hidden = false;
    if ($("settings-main")) $("settings-main").hidden = true;
    if ($("signout-form")) $("signout-form").style.display = "flex";
  } else if (signed && p && p.handle) {
    paintTopUser(p.slack_avatar, p.handle, "/settings", "@" + p.handle);
    if ($("top-auth")) $("top-auth").hidden = false;
    if (ident) {
      ident.hidden = false;
      $("who-name").textContent = p.display_name || ("@" + p.handle);
      $("who-name").href = "/settings";
      if (flagEl) flagEl.textContent = flagEmoji(p.country) || "";
    }
    if (stats) {
      stats.hidden = false;
      if ($("nav-streak")) $("nav-streak").textContent = `${p.current_streak_days || 0}d`;
      if ($("nav-hours")) $("nav-hours").textContent = fmtH(p.weighted_hours);
      const goal = Number(p.weekly_goal_hours != null ? p.weekly_goal_hours : 5);
      const week = Number(p.weighted_week_hours || 0);
      if ($("nav-goal")) $("nav-goal").textContent = `${week.toFixed(1)}/${goal}h`;
    }
    paintWhoAction("sign out", "/signout", "door-leave");
    if ($("acct-state")) {
      $("acct-state").textContent = `Signed in as @${p.handle}` + (q ? `. ${oauthMsg(q)}` : "");
    }
    if ($("slack-login-box")) $("slack-login-box").hidden = true;
    if ($("claim-box")) $("claim-box").hidden = true;
    if ($("settings-main")) $("settings-main").hidden = false;
    if ($("signout-form")) $("signout-form").style.display = "flex";
    if ($("daemon-cmd")) $("daemon-cmd").textContent = `struggletime-helper start --repo ~/my-project`;
  } else {
    paintTopUser("", "", "/login", "sign in");
    if (ident) ident.hidden = true;
    if (stats) stats.hidden = true;
    if ($("top-auth")) $("top-auth").hidden = true;
    if ($("nav-flag")) $("nav-flag").textContent = "";
    if ($("who-name")) { $("who-name").textContent = ""; $("who-name").href = "/login"; }
    if ($("nav-streak")) $("nav-streak").textContent = "";
    if ($("nav-hours")) $("nav-hours").textContent = "";
    if ($("nav-goal")) $("nav-goal").textContent = "";
    paintWhoAction("sign in", "/login", "door-enter");
    if ($("acct-state")) $("acct-state").textContent = new URLSearchParams(location.search).get("signed_out") ? "You're signed out." : "Sign in to manage your account.";
    if ($("slack-login-box")) $("slack-login-box").hidden = false;
    if ($("claim-box")) $("claim-box").hidden = true;
    if ($("settings-main")) $("settings-main").hidden = true;
    if ($("signout-form")) $("signout-form").style.display = "none";
    daemonLinkInfo = null;
  }
  paintNavAuth();
  if (!allowProtectedPage(activePage)) {
    navigate("/");
    return;
  }
  paintAuthChecks();
  paintLogin();
  paintSettingsForm();
  paintDaemonBanners();
  paintHeroCta();
  hydrateNavIcons();
}

function paintLogin() {
  if (activePage !== "login") return;
  const p = meProfile, signed = hasSession();
  const q = new URLSearchParams(location.search);
  const status = $("login-status");
  if (q.get("signed_out")) status.textContent = "You're signed out.";
  else if (q.get("oauth")) status.innerHTML = `<span class="tag ${String(q.get("oauth")).includes("fail") || String(q.get("oauth")).includes("denied") ? "bad" : "ok"}">${esc(oauthMsg(q.get("oauth")))}</span>`;
  else status.textContent = signed ? "You're signed in." : "Continue with Hack Club Slack to get started.";

  $("login-signed").hidden = !(signed && p && p.handle);
  $("login-slack-box").hidden = !!(signed && (p?.handle || p?.needs_handle));
  $("login-claim-box").hidden = !(signed && p && p.needs_handle);
  if (signed && p && p.handle && !q.get("oauth") && !q.get("stay") && !q.get("signed_out")) {
    navigate("/home");
  }
}

function paintAuthChecks() {
  if (!authStatus || !$("gh-check")) return;
  const p = meProfile || {}, ready = !!p.handle, linked = !!p.github_linked;
  const needsRe = !!p.github_needs_reconnect;
  $("gh-check").innerHTML = "";
  if (linked && !needsRe) {
    $("gh-check").append(pill(true, "connected", "not connected"));
    if (p.github_login) $("gh-check").append(document.createTextNode(" @" + p.github_login));
  } else if (linked && needsRe) {
    $("gh-check").append(pill(false, "reconnect", "reconnect"));
    if (p.github_login) $("gh-check").append(document.createTextNode(" @" + p.github_login + " — token expired"));
  } else {
    $("gh-check").append(
      pill(!!authStatus.github_ready, "ready", "not configured"),
      pill(false, "connected", "not connected"),
    );
  }
  if ($("gh-form")) $("gh-form").hidden = (linked && !needsRe) || !ready;
  if ($("gh-connected")) $("gh-connected").hidden = !(linked && !needsRe);
  if ($("gh-oauth")) $("gh-oauth").disabled = !ready || !authStatus.github_ready || (linked && !needsRe);
  if ($("sl-oauth")) $("sl-oauth").disabled = !authStatus.slack_ready;
  if ($("login-slack")) $("login-slack").disabled = !authStatus.slack_ready;
  if ($("login-slack-hint") && !authStatus.slack_ready) $("login-slack-hint").textContent = "Hack Club Slack OAuth is not configured on this server.";
  const builders = document.querySelector('[data-top="builders"], [data-nav="builders"]');
  if (builders) builders.hidden = !!(p.handle && p.show_leaderboards === false);
}

function paintSettingsForm() {
  if (!$("set-country")) return;
  if (!settingsFilled) {
    $("set-country").innerHTML = COUNTRIES.map(([c, l]) => `<option value="${esc(c)}">${esc(l)}</option>`).join("");
    const zones = (typeof Intl !== "undefined" && Intl.supportedValuesOf)
      ? Intl.supportedValuesOf("timeZone")
      : ["UTC", "America/New_York", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Tokyo"];
    $("set-timezone").innerHTML = '<option value="">select timezone</option>' +
      zones.map((z) => `<option value="${esc(z)}">${esc(z)}</option>`).join("");
    settingsFilled = true;
  }
  const p = meProfile;
  if (!(p && p.handle)) return;
  if ($("set-country")) $("set-country").value = p.country || "";
  if ($("set-timezone")) $("set-timezone").value = p.timezone || "";
  if ($("set-display-name")) $("set-display-name").value = p.display_name || "";
  if ($("set-username")) $("set-username").value = p.handle || "";
  if ($("set-email")) $("set-email").value = p.email || "";
  if ($("set-lb-opt-out")) $("set-lb-opt-out").checked = !!p.lb_opt_out;
  if ($("set-show-lb")) $("set-show-lb").checked = p.show_leaderboards !== false;
  if ($("set-weekly-goal")) $("set-weekly-goal").value = p.weekly_goal_hours != null ? p.weekly_goal_hours : 5;
  if ($("delete-confirm")) $("delete-confirm").placeholder = p.handle || "your-handle";
  refreshApiKeyStatus();
}

async function saveProfile(patch) {
  const [s, r] = await j("/v1/me/profile", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  });
  if (s !== 200) { toast(r.error || "Could not save", { kind: "error" }); return null; }
  meProfile = r; meLoaded = true;
  paintAccount();
  toast("Saved", { kind: "ok" });
  return r;
}

const DAEMON_BANNER_DISMISS_KEY = "st_daemon_banner_dismissed";
const INSTALL_DOCS_URL = "/docs/installation/";
const TRAY_DOCS_URL = "/docs/tray/";
function isDaemonBannerDismissed() {
  try {
    if (localStorage.getItem(DAEMON_BANNER_DISMISS_KEY) === "1") return true;
    // Migrate prior session-only dismissals into permanent localStorage.
    if (sessionStorage.getItem(DAEMON_BANNER_DISMISS_KEY) === "1") {
      localStorage.setItem(DAEMON_BANNER_DISMISS_KEY, "1");
      return true;
    }
  } catch (_) {}
  return false;
}
function dismissDaemonBanner() {
  try { localStorage.setItem(DAEMON_BANNER_DISMISS_KEY, "1"); } catch (_) {}
  if ($("daemon-banner")) $("daemon-banner").hidden = true;
}

function paintDaemonBanners() {
  const p = meProfile || {};
  const info = daemonLinkInfo || {
    has_api_key: !!(p.has_api_key || apiKeyPlain),
    daemon_linked: !!p.daemon_linked,
    recent_buckets_7d: p.recent_buckets_7d || 0,
  };
  const signed = isSignedIn() && p.handle;
  const needKey = signed && !info.has_api_key && !apiKeyPlain;
  const needDaemon = signed && (info.has_api_key || apiKeyPlain) && !info.daemon_linked;
  const show = !!(signed && (needKey || needDaemon));
  const dismissed = isDaemonBannerDismissed();

  const title = needKey
    ? "Generate an API key & install the tracker"
    : "Link the desktop tracker";
  const msg = needKey
    ? "Your dashboard stays empty until a machine is linked. Open Settings → Daemon for install steps."
    : "Install StruggleTime on your computer and paste your API key so your hours show up here.";

  const global = $("daemon-banner");
  if (global) {
    // Guests never see the daemon banner; dismiss is permanent via localStorage.
    global.hidden = !(show && !dismissed && activePage !== "landing" && activePage !== "login");
    if (!global.hidden) {
      if ($("daemon-banner-title")) $("daemon-banner-title").textContent = title;
      if ($("daemon-banner-text")) $("daemon-banner-text").textContent = msg;
      const cta = $("daemon-banner-cta");
      if (cta) {
        if (needKey) {
          cta.textContent = "open settings";
          cta.setAttribute("href", "/settings");
        } else {
          cta.textContent = "open tray docs";
          cta.setAttribute("href", INSTALL_DOCS_URL);
        }
      }
      hydrateNavIcons(global);
    }
  }
  const settingsBanner = $("daemon-banner-settings");
  if (settingsBanner) {
    settingsBanner.hidden = !show;
    if (show) {
      if ($("daemon-banner-settings-title")) $("daemon-banner-settings-title").textContent = title;
      if ($("daemon-banner-settings-msg")) $("daemon-banner-settings-msg").textContent = msg;
      const sCta = $("daemon-banner-settings-cta");
      if (sCta) sCta.setAttribute("href", needKey ? "/settings" : INSTALL_DOCS_URL);
      hydrateNavIcons(settingsBanner);
    }
  }
  const st = $("daemon-link-status");
  if (st) {
    if (!signed) st.textContent = "Daemon status: sign in first";
    else if (needKey) st.textContent = "Daemon status: no API key yet";
    else if (needDaemon) st.textContent = "Daemon status: key ready · waiting for first sync";
    else st.textContent = `Daemon status: linked · ${info.recent_buckets_7d || 0} buckets in the last 7 days`;
  }
  const cmd = $("daemon-cmd");
  if (cmd && p.handle) {
    cmd.textContent = `struggletime-helper start --repo ~/my-project`;
  }
}

function showApiKey(value, { fresh = false } = {}) {
  apiKeyPlain = value || "";
  if (!$("api-key-reveal-row")) return;
  if (!apiKeyPlain) {
    $("api-key-reveal-row").hidden = true;
    if ($("api-key-hint")) $("api-key-hint").hidden = true;
    return;
  }
  $("api-key-value").value = apiKeyPlain;
  $("api-key-value").type = "password";
  $("api-key-reveal-row").hidden = false;
  if ($("api-key-hint")) $("api-key-hint").hidden = false;
  if ($("btn-api-key-reveal")) $("btn-api-key-reveal").textContent = "show";
  if ($("api-key-status")) {
    $("api-key-status").textContent = fresh
      ? "key ready — copy and paste into the tray"
      : "your API key (stays here after refresh)";
  }
}

async function refreshApiKeyStatus() {
  if (!$("api-key-status")) return;
  try {
    if (!isSignedIn()) {
      $("api-key-status").textContent = "sign in to manage your API key";
      if ($("api-key-reveal-row")) $("api-key-reveal-row").hidden = true;
      if ($("api-key-hint")) $("api-key-hint").hidden = true;
      daemonLinkInfo = null;
      return;
    }
    const [s, r] = await j("/v1/auth/api-key");
    if (s !== 200) {
      // Session-only route: API-key bearer gets 401. Fall back to /v1/auth/me fields
      // so daemon banners and settings stay accurate without wiping the session.
      const p = meProfile || {};
      daemonLinkInfo = {
        has_api_key: !!(p.has_api_key || apiKeyPlain),
        daemon_linked: !!p.daemon_linked,
        recent_buckets_7d: p.recent_buckets_7d || 0,
        last_sync_wall: p.last_sync_wall || null,
        install_keys: p.install_keys || 0,
      };
      const needsSlack = /slack/i.test(String(r.error || ""));
      $("api-key-status").textContent = needsSlack
        ? (p.has_api_key ? "key on file · open settings from a Slack session to reveal or rotate" : "sign in with Slack to manage your API key")
        : (r.error || "could not load API key status");
      if ($("api-key-reveal-row") && !apiKeyPlain) $("api-key-reveal-row").hidden = true;
      return;
    }
    daemonLinkInfo = {
      has_api_key: !!r.has_api_key || !!r.configured,
      daemon_linked: !!r.daemon_linked,
      recent_buckets_7d: r.recent_buckets_7d || 0,
      last_sync_wall: r.last_sync_wall || null,
      install_keys: r.install_keys || 0,
    };
    if (r.api_key) {
      showApiKey(r.api_key);
    } else if (apiKeyPlain) {
      showApiKey(apiKeyPlain, { fresh: true });
    } else if (r.configured && r.needs_regenerate) {
      $("api-key-status").textContent = "key on file (legacy) — regenerate once to keep it visible here";
      $("api-key-reveal-row").hidden = true;
      if ($("api-key-hint")) $("api-key-hint").hidden = true;
    } else if (r.configured) {
      $("api-key-status").textContent = "configured · " + (r.prefix || "st_…");
      $("api-key-reveal-row").hidden = true;
      if ($("api-key-hint")) $("api-key-hint").hidden = true;
    } else {
      $("api-key-status").textContent = "no API key yet — generate one to link the tray";
      $("api-key-reveal-row").hidden = true;
      if ($("api-key-hint")) $("api-key-hint").hidden = true;
      apiKeyPlain = "";
    }
  } finally {
    paintDaemonBanners();
  }
}

function ghRepoOptions(repos, { includeCurrent } = {}) {
  const opts = [{ id: "", label: "None — no GitHub repo", search: "none optional" }];
  const seen = new Set([""]);
  for (const repo of repos || []) {
    const url = repo.repo_url || "";
    if (!url || seen.has(url)) continue;
    seen.add(url);
    const name = repo.full_name || repo.name || url;
    opts.push({
      id: url,
      label: `${name}${repo.private ? " (private)" : ""}${repo.already_added ? " · linked" : ""}`,
      search: `${name} ${url}`,
    });
  }
  if (includeCurrent && includeCurrent.trim() && !seen.has(includeCurrent.trim())) {
    opts.push({ id: includeCurrent.trim(), label: includeCurrent.trim(), search: includeCurrent });
  }
  return opts;
}

function projectSelectOptions(list, { allowEmpty } = {}) {
  const active = (list || []).filter((p) => !p.deleted && !p.archived && p.registered !== false);
  const opts = active.map((p) => ({
    id: p.id,
    label: p.name && p.name !== p.id ? `${p.name} (${p.id})` : (p.name || p.id),
    search: `${p.name || ""} ${p.id} ${p.repo_slug || ""} ${p.local_path || ""}`,
  }));
  if (allowEmpty) opts.unshift({ id: "", label: "Select a project…", search: "" });
  return opts;
}

async function consumeAuthCode() {
  const q = new URLSearchParams(location.search);
  const code = q.get("auth");
  if (!code) return false;
  const [s, r] = await j("/v1/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  q.delete("auth");
  const oauth = q.get("oauth");
  q.delete("oauth");
  const clean = location.pathname + (q.toString() ? "?" + q : "");
  history.replaceState({}, "", clean);
  if (s === 200 && r.token) {
    setToken(r.token); meProfile = r; meLoaded = true;
    toast(r.needs_handle ? "Signed in. Grab a handle next." : "Welcome back", { kind: "ok" });
    if (r.handle) {
      navigate("/home");
      return true;
    }
    navigate("/login" + (oauth ? "?oauth=" + encodeURIComponent(oauth) : ""));
    return true;
  }
  toast(r.error || "Sign-in failed — try Slack again", { kind: "error" });
  navigate("/login" + (oauth ? "?oauth=" + encodeURIComponent(oauth) : ""));
  return false;
}

/* ── auth gate (single flight; page loaders await this) ─────────────── */
async function loadMe(force = false) {
  if (meLoaded && !force && meProfile) return meProfile;
  // Always hit /v1/auth/me — cookie session works even with empty localStorage.
  let [s, r] = await j("/v1/auth/me");
  if (s === 401 && token()) {
    setToken("");
    [s, r] = await j("/v1/auth/me", { cookieOnly: true });
  }
  if (s === 401) {
    meProfile = null;
    meLoaded = true;
    if ($("nav-identity")) $("nav-identity").hidden = true;
    if ($("nav-stats")) $("nav-stats").hidden = true;
    if ($("nav-flag")) $("nav-flag").textContent = "";
    return null;
  }
  if (s === 200) {
    meProfile = r;
    meLoaded = true;
    return meProfile;
  }
  // Transient — keep prior session; leave meLoaded false on first failure so ensureAuth retries
  console.warn("loadMe transient", s, r?.error || r);
  return meProfile;
}

async function loadAuthStatus(force = false) {
  if (authStatusLoaded && !force && authStatus) return authStatus;
  const [as, ar] = await j("/v1/auth/status");
  if (as === 200) authStatus = ar;
  authStatusLoaded = true;
  return authStatus;
}

async function ensureAuth(force = false) {
  if (!force && authReady) return authReady;
  const run = (async () => {
    await loadAuthStatus(force);
    await loadMe(force);
    if (!meLoaded) {
      await sleep(250);
      await loadMe(true);
    }
    // Unblock UI: signed-out is fine; infinite blank is not
    if (!meLoaded) {
      meLoaded = true;
      if (!meProfile) meProfile = null;
    }
    return meProfile;
  })();
  authReady = run.catch((err) => {
    authReady = null;
    throw err;
  });
  return authReady;
}

async function fetchGhRepos(force = false) {
  if (!(meProfile && meProfile.github_linked)) return null;
  if (!force && ghReposCache && Date.now() - ghReposAt < 5 * 60 * 1000) return ghReposCache;
  const [s, r] = await j("/v1/me/github-repos");
  if (s !== 200) {
    if (r && r.reconnect) {
      if (meProfile) meProfile.github_linked = false;
      toast(r.error || "Reconnect GitHub in settings", { kind: "error" });
      paintAuthChecks();
    } else {
      toast(r.error || "Could not load GitHub repos", { kind: "error" });
    }
    return null;
  }
  ghReposCache = r; ghReposAt = Date.now();
  return r;
}

function openProjectModal(existing) {
  if (!(meProfile && meProfile.handle)) {
    toast("Sign in with Slack first", { kind: "error" });
    navigate("/login");
    return;
  }
  const nameId = "m-name", archId = "m-archived", ghId = "m-gh", dirId = "m-dir";
  const ghCombo = comboField({
    label: "GitHub repo (optional)",
    id: ghId,
    glyph: "github",
    placeholder: "filter repos…",
    value: existing?.repo_url || "",
    options: ghRepoOptions([], { includeCurrent: existing?.repo_url }),
    hint: meProfile.github_linked
      ? "Pick None to leave the project without a GitHub repo."
      : "Connect GitHub in Settings to choose from your repos — None is always allowed.",
  });
  const dirField = pathField({
    label: "Local directory",
    id: dirId,
    glyph: "docs",
    value: existing?.local_path || "",
    placeholder: "~/code/my-project or C:\\Users\\you\\code\\my-project",
  });
  const kids = [
    field({ label: "Name", id: nameId, value: existing?.name || "", placeholder: "my project", required: true }),
    ghCombo,
    dirField,
  ];
  if (existing) {
    kids.push(el("label", { className: "toggle-row" }, [
      el("input", { type: "checkbox", id: archId, checked: !!existing.archived }),
      el("span", { text: "Archived project" }),
    ]));
    if (existing.repo_url || existing.last_repo_url) {
      const hasLink = !!existing.repo_url;
      kids.push(el("div", { className: "modal-danger", style: "margin-top:14px;padding-top:12px;border-top:1px solid var(--line)" }, [
        el("h3", { style: "margin:0 0 6px;font-size:14px", text: "Delete repo" }),
        el("p", {
          className: "sub",
          text: hasLink
            ? "Unlink the repository from this project. Coding history stays on the project."
            : `Last linked: ${existing.last_repo_url}`,
        }),
        btn(hasLink ? "Delete repo" : "Clear deleted repo memory", {
          className: "ui-btn-danger",
          glyph: "delete",
          onClick: async () => {
            const msg = hasLink
              ? `Delete repo link ${existing.repo_url} from this project?`
              : "Clear the remembered deleted repo URL?";
            if (!confirm(msg)) return;
            const [s, r] = await j("/v1/projects/" + encodeURIComponent(existing.id) + "/delete", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ mode: hasLink ? "repo" : "clear" }),
            });
            if (s !== 200) { toast(r.error || "Could not update repo link", { kind: "error" }); return; }
            toast(hasLink ? "Repo deleted from project" : "Cleared", { kind: "ok" });
            document.querySelector(".ui-modal-backdrop")?.remove();
            refresh();
          },
        }),
      ]));
    }
    const softId = "m-soft-confirm";
    kids.push(el("div", { className: "modal-danger", style: "margin-top:14px;padding-top:12px;border-top:1px solid var(--line)" }, [
      el("h3", { style: "margin:0 0 6px;font-size:14px", text: "Soft-delete project" }),
      el("p", {
        className: "sub",
        text: "Hides this project from your list. Bucket evidence stays for review/audit. Type the project id, wait for the unlock, then confirm.",
      }),
      el("input", {
        className: "ui-input", id: softId, placeholder: existing.id,
        autocomplete: "off", style: "margin:8px 0",
        onInput: () => {
          const softBtn = document.querySelector("[data-soft-delete-btn]");
          if (!softBtn) return;
          if (softBtn._unlockTick) { clearInterval(softBtn._unlockTick); softBtn._unlockTick = null; }
          delete softBtn.dataset.unlocked;
          softBtn.disabled = false;
          paintDangerBtn(softBtn, "Soft-delete project");
        },
      }),
      (() => {
        const softBtn = btn("Soft-delete project", {
        className: "ui-btn-danger",
        glyph: "delete",
        onClick: async (ev) => {
          const typed = (document.getElementById(softId)?.value || "").trim();
          if (typed !== existing.id && typed !== (existing.name || "")) {
            toast("Type the exact project id (or name) to unlock", { kind: "error" });
            return;
          }
          const lockBtn = ev?.currentTarget || ev?.target;
          if (lockBtn && !lockBtn.dataset.unlocked) {
            lockBtn.dataset.unlocked = "1";
            let left = 3;
            lockBtn.disabled = true;
            paintDangerBtn(lockBtn, `unlocked in ${left}s…`);
            if (lockBtn._unlockTick) clearInterval(lockBtn._unlockTick);
            lockBtn._unlockTick = setInterval(() => {
              left -= 1;
              if (left <= 0) {
                clearInterval(lockBtn._unlockTick);
                lockBtn._unlockTick = null;
                lockBtn.disabled = false;
                paintDangerBtn(lockBtn, "Confirm soft-delete");
              } else {
                paintDangerBtn(lockBtn, `unlocked in ${left}s…`);
              }
            }, 1000);
            toast("Safety lock: click again after the countdown", { kind: "info" });
            return;
          }
          if (!confirm(`Soft-delete “${existing.name || existing.id}”? It disappears from your list; evidence is kept.`)) return;
          const [s, r] = await j("/v1/projects/" + encodeURIComponent(existing.id) + "/delete", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: "project", confirm: typed || existing.id }),
          });
          if (s !== 200) { toast(r.error || "Could not soft-delete", { kind: "error" }); return; }
          toast("Project soft-deleted", { kind: "ok" });
          document.querySelector(".ui-modal-backdrop")?.remove();
          if (selected === existing.id) selected = "";
          refresh();
        },
      });
        softBtn.setAttribute("data-soft-delete-btn", "1");
        return softBtn;
      })(),
    ]));
  }
  const body = el("div", {}, kids);
  modal({
    title: existing ? "Edit project" : "Create project",
    glyph: existing ? "edit" : "plus",
    confirmText: existing ? "Save" : "Create & link",
    confirmGlyph: existing ? "checkmark" : "plus",
    cancelGlyph: "view-close",
    body,
    onConfirm: async () => {
      const name = document.getElementById(nameId).value.trim();
      const repo_url = (ghCombo.getValue?.() || "").trim() || null;
      const local_path = dirField.getValue?.() || null;
      if (!name) { toast("Name required", { kind: "error" }); return false; }
      if (existing) {
        const archived = !!document.getElementById(archId)?.checked;
        const [s, r] = await j("/v1/projects/" + encodeURIComponent(existing.id) + "/edit", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, repo_url, archived, local_path }),
        });
        if (s !== 200) { toast(r.error || "Could not save", { kind: "error" }); return false; }
        toast(local_path ? "Project updated · directory saved for the tray" : "Project updated", { kind: "ok" });
      } else {
        const [s, r] = await j("/v1/projects", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, repo_url, local_path }),
        });
        if (s !== 201) { toast(r.error || "Could not create", { kind: "error" }); return false; }
        toast(
          local_path
            ? `Created ${r.id} · directory ready for tray link`
            : (r.repo_slug ? `Created /${String(r.repo_slug).split("/").pop()}` : `Created ${r.id}`),
          { kind: "ok" },
        );
      }
      refresh();
    },
  });
  (async () => {
    if (!meProfile.github_linked) return;
    const data = await fetchGhRepos();
    ghCombo.setOptions?.(
      ghRepoOptions(data?.repos || [], { includeCurrent: existing?.repo_url }),
      existing?.repo_url || "",
    );
  })();
}

/** Link an existing project: project dropdown + optional GitHub + local directory. */
function openLinkProjectModal() {
  if (!(meProfile && meProfile.handle)) {
    toast("Sign in with Slack first", { kind: "error" });
    navigate("/login");
    return;
  }
  const projCombo = comboField({
    label: "Project",
    id: "m-link-project",
    glyph: "briefcase",
    placeholder: "filter projects…",
    options: projectSelectOptions(projectsCache, { allowEmpty: true }),
    value: "",
    hint: "Choose which StruggleTime project this folder belongs to.",
  });
  const ghCombo = comboField({
    label: "GitHub repo (optional)",
    id: "m-link-gh",
    glyph: "github",
    placeholder: "filter repos…",
    options: ghRepoOptions([]),
    value: "",
    hint: "None leaves the project without a GitHub link.",
  });
  const dirField = pathField({
    label: "Local directory",
    id: "m-link-dir",
    glyph: "docs",
    value: "",
    placeholder: "~/code/my-project or C:\\Users\\you\\code\\my-project",
  });
  projCombo.addEventListener?.("change", () => {});
  // Sync defaults when project changes
  const projSel = projCombo.querySelector("select");
  if (projSel) {
    projSel.addEventListener("change", () => {
      const p = (projectsCache || []).find((x) => x.id === projSel.value);
      if (!p) return;
      ghCombo.setValue?.(p.repo_url || "");
      if (p.local_path) dirField.setValue?.(p.local_path);
    });
  }
  modal({
    title: "Link project",
    glyph: "link",
    confirmText: "Link",
    confirmGlyph: "link",
    cancelGlyph: "view-close",
    body: el("div", {}, [projCombo, ghCombo, dirField]),
    onConfirm: async () => {
      const project_id = (projCombo.getValue?.() || "").trim();
      const repo_url = (ghCombo.getValue?.() || "").trim() || null;
      const local_path = dirField.getValue?.() || null;
      if (!project_id) { toast("Pick a project", { kind: "error" }); return false; }
      const existing = (projectsCache || []).find((p) => p.id === project_id);
      const [s, r] = await j("/v1/projects/" + encodeURIComponent(project_id) + "/edit", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: existing?.name || project_id,
          repo_url,
          local_path,
        }),
      });
      if (s !== 200) { toast(r.error || "Could not link", { kind: "error" }); return false; }
      toast(local_path ? `Linked ${project_id} · set the same folder in the tray` : `Linked ${project_id}`, { kind: "ok" });
      refresh();
    },
  });
  (async () => {
    const [ps, pr] = await j("/v1/projects");
    if (ps === 200) {
      projectsCache = pr.projects || [];
      projCombo.setOptions?.(projectSelectOptions(projectsCache, { allowEmpty: true }), "");
    }
    if (meProfile.github_linked) {
      const data = await fetchGhRepos();
      ghCombo.setOptions?.(ghRepoOptions(data?.repos || []), "");
    }
  })();
}

window.claimProject = async (e, id) => {
  e.preventDefault();
  const [s, r] = await j("/v1/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, name: id }) });
  if (s === 201) toast("Claimed " + id, { kind: "ok" }); else toast(r.error || s, { kind: "error" });
  refresh(); return false;
};

async function toggleArchive(p) {
  const next = !p.archived;
  const [s, r] = await j("/v1/projects/" + encodeURIComponent(p.id) + "/archive", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ archived: next }),
  });
  if (s !== 200) { toast(r.error || "Could not update archive", { kind: "error" }); return; }
  toast(next ? "Archived" : "Unarchived", { kind: "ok" });
  refresh();
}

async function restoreProject(p) {
  const [s, r] = await j("/v1/projects/" + encodeURIComponent(p.id) + "/restore", { method: "POST" });
  if (s !== 200) { toast(r.error || "Could not restore", { kind: "error" }); return; }
  toast(r.repo_url ? "Repo restored" : "Restored", { kind: "ok" });
  refresh();
}

async function renderLeaderboard() {
  const box = $("lb-list"); if (!box) return;
  try {
    const [s, r] = await j("/v1/leaderboard?period=" + encodeURIComponent(lbPeriod));
    if (s !== 200) {
      const detail = r?.error || (s === 0 ? "network/timeout" : `HTTP ${s}`);
      box.replaceChildren(el("div", { className: "ui-empty" }, [document.createTextNode(detail || "Could not load leaderboard")]));
      if ($("lb-meta")) $("lb-meta").textContent = "";
      return;
    }
    const total = r.total || 0;
    const builderWord = total === 1 ? "builder" : "builders";
    if ($("lb-meta")) $("lb-meta").textContent = `${r.label || lbPeriod} · ${total} ${builderWord}`;
    let entries = r.entries || [];
    const q = lbQuery.trim().toLowerCase();
    if (q) entries = entries.filter((e) => [e.handle, e.github_login, e.active_project, e.slack_name].filter(Boolean).join(" ").toLowerCase().includes(q));
    if (!entries.length) {
      box.replaceChildren(el("div", { className: "ui-empty" }, [
        document.createTextNode(q ? "No builders match that search." : "No activity in this period."),
      ]));
      return;
    }
    const max = entries[0].weighted_hours || 1;
    const list = el("div", { className: "builders-list" });
    list.append(...entries.map((e) => {
      const flag = flagEmoji(e.country);
      const nameKids = [];
      nameKids.push(el("span", { className: "lb-flag", text: flag || "·", title: e.country || "" }));
      nameKids.push(document.createTextNode("@" + e.handle));
      return el("div", { className: "lb-row" }, [
        el("div", { className: `lb-rank tabular ${e.rank <= 3 ? "top" : ""}`, text: "#" + e.rank }),
        avatar(e.slack_avatar, e.handle),
        el("div", { className: "lb-user" }, [
          el("b", {}, nameKids),
          el("span", { className: "sub", text: [
            e.active_project ? "/" + String(e.active_project).split("/").pop() : null,
            e.streak ? `${e.streak}d streak` : null,
          ].filter(Boolean).join(" · ") || " " }),
        ]),
        el("div", { className: "lb-track", "aria-hidden": "true" }, [
          el("div", { className: "lb-bar" }, [
            el("i", { style: `width:${Math.max(4, Math.min(100, (e.weighted_hours / max) * 100))}%` }),
          ]),
        ]),
        el("div", { className: "lb-hours tabular", text: fmtH(e.weighted_hours) }),
      ]);
    }));
    box.replaceChildren(list);
  } catch (err) {
    console.error("renderLeaderboard failed", err);
    box.replaceChildren(el("div", { className: "ui-empty" }, [document.createTextNode("Could not load leaderboard")]));
  }
}

const REVIEW_REASONS = [
  "NONE", "UNATTENDED_AGENT_DUMP", "IDLE_INFLATION", "NO_TEST_ITERATION",
  "BOILERPLATE_HEAVY", "CHAIN_BROKEN", "GIT_MISMATCH", "UNSIGNED_INSTALL",
];

async function loadReviewQueue() {
  const box = $("review-list"); if (!box) return;
  await ensureAuth();
  if (!isStaff()) {
    box.innerHTML = '<p class="ui-empty">staff only — ask an admin for reviewer access</p>';
    return;
  }
  try {
    const [ss, sr] = await j("/v1/submissions");
    if (ss !== 200) {
      const detail = sr?.error || (ss === 0 ? "network/timeout" : `HTTP ${ss}`);
      box.innerHTML = `<p class="ui-empty">${esc(detail || "could not load queue")}</p>`;
      return;
    }
    const statusF = ($("review-status") && $("review-status").value) || "pending";
    const builderF = (($("review-builder") && $("review-builder").value) || "").trim().toLowerCase();
    const projectF = (($("review-project") && $("review-project").value) || "").trim().toLowerCase();
    const flagsF = ($("review-flags") && $("review-flags").value) || "any";
    let subs = sr.submissions || [];
    if (statusF !== "all") subs = subs.filter((s) => s.status === statusF);
    if (builderF) subs = subs.filter((s) => String(s.user_id || "").toLowerCase().includes(builderF));
    if (projectF) subs = subs.filter((s) => String(s.project_id || "").toLowerCase().includes(projectF));
    if (flagsF === "flagged") subs = subs.filter((s) => (s.flags || []).length);
    if (flagsF === "chain") subs = subs.filter((s) => s.chain_ok === false || (s.flags || []).some((f) => String(f).includes("chain")));
    if (!subs.length) {
      box.innerHTML = '<p class="ui-empty">Nothing matches these filters.</p>';
      return;
    }
    const reasonOpts = REVIEW_REASONS.map((r) => `<option value="${r}">${r}</option>`).join("");
    box.innerHTML = subs.map((s) => {
      const hours = fmt(s.claimed_weighted_hours);
      const flags = (s.flags || []).length ? `<span class="tag bad">${esc((s.flags || []).slice(0, 4).join(", "))}</span>` : "";
      const notes = (s.staff_notes || []).slice(-2);
      const notesHtml = notes.length
        ? `<div class="sub review-notes">${notes.map((n) => esc(`${n.by || "?"}: ${n.text || ""}`)).join(" · ")}</div>`
        : "";
      const evidence = [
        s.bucket_count != null ? `${s.bucket_count} buckets` : null,
        s.chain_ok === false ? "chain broken" : (s.chain_ok ? "chain ok" : null),
        s.evidence_bytes ? `${Math.round(s.evidence_bytes / 1024)} KB meta` : null,
      ].filter(Boolean).join(" · ");
      const pending = s.status === "pending" && canReview();
      const actions = pending
        ? `<div class="review-actions">
            <label class="ui-filter"><span class="ui-filter-label">reason</span>
              <select class="ui-input" data-reason-for="${esc(s.id)}">${reasonOpts}</select>
            </label>
            <label class="ui-filter"><span class="ui-filter-label">approved hours</span>
              <input class="ui-input" type="number" min="0" step="0.01" data-hours-for="${esc(s.id)}" value="${esc(hours)}">
            </label>
            <label class="ui-filter"><span class="ui-filter-label">note</span>
              <input class="ui-input" type="text" data-note-for="${esc(s.id)}" placeholder="optional staff note" maxlength="500">
            </label>
            <button type="button" class="ui-btn" data-review="${esc(s.id)}" data-action="approve">approve</button>
            <button type="button" class="ui-btn ui-btn-ghost" data-review="${esc(s.id)}" data-action="deflate">adjust weight 50%</button>
            <button type="button" class="ui-btn ui-btn-danger" data-review="${esc(s.id)}" data-action="reject">reject</button>
            <button type="button" class="ui-btn ui-btn-ghost" data-review="${esc(s.id)}" data-action="flag">flag</button>
            <button type="button" class="ui-btn ui-btn-ghost" data-review="${esc(s.id)}" data-action="note">add note</button>
            <button type="button" class="ui-btn ui-btn-ghost" data-inspect="${esc(s.id)}">inspect</button>
          </div>`
        : `<div class="review-actions">
            <span class="tag">${esc(s.status)}</span>
            <button type="button" class="ui-btn ui-btn-ghost" data-inspect="${esc(s.id)}">inspect</button>
            ${canReview() ? `<button type="button" class="ui-btn ui-btn-ghost" data-review="${esc(s.id)}" data-action="note">add note</button>` : '<span class="sub">viewer — no review actions</span>'}
          </div>`;
      return `<div class="review-card" data-sub="${esc(s.id)}">
        <b>${esc(s.project_id)}</b> <span class="tag">${esc(s.status)}</span> ${flags}
        <div class="review-meta sub tabular">${hours}h weighted · @${esc(s.user_id || "")}${evidence ? " · " + esc(evidence) : ""}</div>
        ${notesHtml}
        <p class="sub">Review timing flags, active vs idle, and the linked history check. No code or prompts.</p>
        ${actions}
        <div class="review-inspect" id="inspect-${esc(s.id)}" hidden></div>
      </div>`;
    }).join("");
  } catch (err) {
    console.error("loadReviewQueue failed", err);
    box.innerHTML = '<p class="ui-empty">could not load queue</p>';
  }
}

async function inspectSubmission(id) {
  const panel = $("inspect-" + id);
  if (!panel) return;
  if (!panel.hidden && panel.dataset.loaded) { panel.hidden = true; return; }
  panel.hidden = false;
  panel.innerHTML = '<p class="sub">loading bucket breakdown…</p>';
  const [s, r] = await j(`/v1/submissions/${encodeURIComponent(id)}/inspect`);
  if (s !== 200) {
    panel.innerHTML = `<p class="ui-empty">${esc(r.error || "inspect failed")}</p>`;
    return;
  }
  panel.dataset.loaded = "1";
  const vel = r.velocity || {};
  const rows = (r.timeline || []).slice(-24).map((t) => {
    const flagged = (r.anomalies || []).some((a) => a.bucket && String(a.bucket.bucket_id || "").startsWith(String(t.bucket_id || "").slice(0, 8)));
    return `<tr class="${flagged ? "flagged" : ""}">
      <td class="tabular">${esc(String(t.bucket_id || "").slice(0, 8))}</td>
      <td>${esc(t.state || "")}</td>
      <td class="tabular">${esc(String(t.weight))}</td>
      <td class="tabular">${esc(String(t.active_secs || 0))}s / ${esc(String(t.idle_secs || 0))}s</td>
      <td class="tabular">${esc(String(t.friction ?? ""))}</td>
    </tr>`;
  }).join("");
  const anomalies = (r.anomalies || []).slice(0, 6).map((a) => {
    const reasons = (a.reasons || []).join(", ");
    return `<div class="chain-diff-pair">
      <div class="bucket-chip bad"><b>flagged</b><div class="sub">${esc(reasons)}</div><pre class="json-pre">${esc(prettyChip(a.bucket))}</pre></div>
      <div class="bucket-chip ok"><b>neighbor</b><pre class="json-pre">${esc(prettyChip(a.neighbor))}</pre></div>
    </div>`;
  }).join("") || '<p class="sub">No anomalies in this window.</p>';
  panel.innerHTML = `
    <p class="sub tabular">struggle→ship: ${esc(String(vel.struggle_hours ?? "—"))}h / ${esc(String(vel.ship_units ?? 0))} commits
      ${vel.velocity_ratio != null ? `(ratio ${esc(String(vel.velocity_ratio))})` : ""} · chain ${r.chain_ok ? "ok" : "broken"}</p>
    <div class="review-inspect"><table><thead><tr><th>bucket</th><th>state</th><th>w</th><th>active/idle</th><th>friction</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5">no buckets</td></tr>'}</tbody></table></div>
    <h3 style="margin:12px 0 6px;font-size:13px">chain &amp; anomaly diff</h3>
    ${anomalies}`;
}

async function submitReview(id, approvedSecs, reason, note) {
  const body = { approved_weighted_secs: Number(approvedSecs), reason_code: reason };
  if (note) body.note = note;
  const [s, r] = await j(`/v1/submissions/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (s !== 200) { toast(r.error || "review failed", { kind: "error" }); return; }
  toast(`marked ${r.status}`, { kind: "ok" });
  await loadReviewQueue();
}

async function submitReviewSideAction(id, action, { reason, note } = {}) {
  const path = action === "flag" ? "flag" : "note";
  const payload = action === "flag"
    ? { flag: reason && reason !== "NONE" ? reason : "staff_flag", note: note || "" }
    : { note: note || "" };
  if (action === "note" && !payload.note) {
    toast("Type a note first", { kind: "error" }); return;
  }
  const [s, r] = await j(`/v1/submissions/${encodeURIComponent(id)}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (s !== 200) { toast(r.error || `${action} failed`, { kind: "error" }); return; }
  toast(action === "flag" ? "Flagged — still pending" : "Note saved", { kind: "ok" });
  await loadReviewQueue();
}

async function loadAdminPage() {
  const kpi = $("admin-kpi");
  const usersBox = $("admin-users");
  const auditBox = $("admin-audit");
  await ensureAuth();
  if (!canAdmin()) {
    if (usersBox) usersBox.innerHTML = '<p class="ui-empty">admin role required</p>';
    return;
  }
  const q = (($("admin-user-q") && $("admin-user-q").value) || "").trim();
  const [[os, ov], [us, ur], [as, ar]] = await Promise.all([
    j("/v1/admin/overview"),
    j("/v1/admin/users" + (q ? `?q=${encodeURIComponent(q)}` : "")),
    j("/v1/admin/trust-audit"),
  ]);
  if (kpi) {
    if (os !== 200) kpi.innerHTML = `<div class="kpi"><div class="n">—</div><div class="l">${esc(ov?.error || "error")}</div></div>`;
    else {
      const trust = ov.trust_counts || {};
      kpi.innerHTML = [
        ["pending reviews", ov.pending_reviews || 0, "review"],
        ["users", ov.users || 0, null],
        ["blue", trust.blue || 0, null],
        ["green", trust.green || 0, null],
        ["yellow", trust.yellow || 0, null],
        ["red", trust.red || 0, null],
      ].map(([l, n, go]) => `<div class="kpi"${go ? ` data-nav-kpi="${go}" style="cursor:pointer"` : ""}><div class="n tabular">${esc(String(n))}</div><div class="l">${esc(l)}</div></div>`).join("");
    }
  }
  await loadFraudAnomalies();
  if (usersBox) {
    if (us !== 200) usersBox.innerHTML = `<p class="ui-empty">${esc(ur?.error || "could not load")}</p>`;
    else {
      const rows = ur.users || [];
      usersBox.innerHTML = rows.length ? rows.map((u) => {
        const trustOpts = ["blue", "green", "yellow", "red"].map((t) =>
          `<option value="${t}" ${u.trust_level === t ? "selected" : ""}>${t}</option>`).join("");
        const roleOpts = ["user", "viewer", "reviewer", "admin", "superadmin"].map((t) =>
          `<option value="${t}" ${u.admin_level === t ? "selected" : ""}>${t}</option>`).join("");
        return `<div class="admin-user" data-handle="${esc(u.handle)}">
          <div class="meta">
            <div><span class="trust-dot trust-${esc(u.trust_level || "blue")}"></span><b>@${esc(u.handle)}</b>
              <span class="sub">${esc(u.display_name || "")}</span></div>
            <div class="sub">${fmt(u.weighted_hours)}h · ${esc(String(u.buckets || 0))} buckets · ${esc(u.admin_level)}</div>
          </div>
          <div class="actions">
            <select data-trust-for="${esc(u.handle)}" aria-label="trust">${trustOpts}</select>
            <select data-role-for="${esc(u.handle)}" aria-label="role">${roleOpts}</select>
          </div>
        </div>`;
      }).join("") : '<p class="ui-empty">no users</p>';
    }
  }
  if (auditBox) {
    if (as !== 200) auditBox.innerHTML = `<p class="ui-empty">${esc(ar?.error || "could not load")}</p>`;
    else {
      const logs = ar.logs || [];
      auditBox.innerHTML = logs.length
        ? logs.map((l) => `<div class="event"><span class="trust-dot trust-${esc(l.new_level || "blue")}"></span>
            <b>@${esc(l.handle)}</b> <span class="sub">${esc(l.prev_level)} → ${esc(l.new_level)} by @${esc(l.changed_by)}</span>
            <div class="sub">${esc(l.reason || "")} ${esc(l.notes || "")}</div></div>`).join("")
        : '<p class="ui-empty">no trust changes yet</p>';
    }
  }
}

async function loadFraudAnomalies() {
  const box = $("fraud-anomalies");
  if (!box) return;
  const risk = ($("fraud-risk") && $("fraud-risk").value) || "all";
  const window = ($("fraud-window") && $("fraud-window").value) || "7d";
  const [s, r] = await j(`/v1/admin/anomalies?risk=${encodeURIComponent(risk)}&window=${encodeURIComponent(window)}`);
  if ($("fraud-meta")) {
    $("fraud-meta").textContent = s === 200 ? `${r.total || 0} projects` : "";
  }
  if (s !== 200) {
    box.innerHTML = `<p class="ui-empty">${esc(r.error || "could not load anomalies")}</p>`;
    return;
  }
  const findings = r.findings || [];
  if (!findings.length) {
    box.innerHTML = '<p class="ui-empty">No anomalies in this window.</p>';
    return;
  }
  box.innerHTML = findings.map((f) => {
    const sample = (f.sample || [])[0];
    const reasons = sample ? (sample.reasons || []).join(", ") : "";
    const vel = f.velocity || {};
    return `<div class="anomaly-row">
      <div>
        <b>${esc(f.project_id)}</b>
        <span class="sub"> · @${esc(f.owner || "?")} · ${esc(String(f.anomaly_count))} flags · chain ${f.chain_ok ? "ok" : "broken"}</span>
        <div class="sub">${esc(reasons)}${vel.velocity_ratio != null ? ` · velocity ${esc(String(vel.velocity_ratio))}` : ""}</div>
      </div>
      <button type="button" class="ui-btn ui-btn-ghost" data-chain-diff="${esc(f.project_id)}">inspect</button>
    </div>`;
  }).join("");
}

async function shareProjectReceipt(projectId) {
  const [s, r] = await j("/v1/receipts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (s !== 201) { toast(r.error || "receipt failed", { kind: "error" }); return; }
  const url = location.origin + (r.url || `/v1/receipts/${r.token}`);
  const rec = r.receipt || {};
  modal({
    title: "session receipt",
    glyph: "share",
    confirmText: "Copy link",
    cancelText: "Close",
    body: el("div", { className: "receipt-card" }, [
      el("h3", {}, [document.createTextNode(projectId)]),
      el("p", { className: "sub" }, [document.createTextNode(`@${rec.handle || ""} · ${rec.verified_weighted_hours ?? "—"}h verified · chain ${rec.chain_ok ? "ok" : "broken"}`)]),
      el("p", { className: "sub tabular" }, [document.createTextNode(
        `struggle ${rec.struggle?.struggle_hours ?? "—"}h · ship ${rec.struggle?.ship_units ?? 0} · ${rec.privacy || ""}`
      )]),
      el("p", { className: "sub" }, [document.createTextNode(url)]),
    ]),
    onConfirm: async () => {
      try { await navigator.clipboard.writeText(url); toast("Copied", { kind: "ok" }); } catch (_) {}
      return true;
    },
  });
}

function analyticsQS() {
  const p = new URLSearchParams({
    range: filters.range || "7d",
    project: filters.project || "all",
    language: sanitizeFilter(filters.language, "all"),
    os: sanitizeFilter(filters.os, "all"),
    editor: sanitizeFilter(filters.editor, "all"),
    category: sanitizeFilter(filters.category, "all"),
    tz_offset: String(TZ_OFFSET),
  });
  return p.toString();
}

const KONAMI = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "KeyB", "KeyA"];
let konamiBuf = [];
// Real tracker data by default. Never restore sample mode from a prior tab session —
// stale sessionStorage "1" made the dashboard look fake until Konami was toggled off.
try {
  sessionStorage.removeItem("st_fake_stats");
  localStorage.removeItem("st_fake_stats");
  // Sticky demo/dimension filters blank the first real analytics fetch; Konami OFF
  // recovered via resetDashboardFilters — clear them up front so mount matches that path.
  ["st_filter_language", "st_filter_os", "st_filter_editor", "st_filter_category"].forEach((k) => {
    localStorage.removeItem(k);
  });
} catch (_) {}
let fakeStatsMode = false;
let filtersBeforeFake = null;
filters.project = "all";
filters.language = "all";
filters.os = "all";
filters.editor = "all";
filters.category = "all";
if (filters.range === "today") {
  filters.range = "7d";
  try { localStorage.setItem("st_range", "7d"); } catch (_) {}
}

function buildFakeAnalytics() {
  const days = [];
  const now = new Date();
  for (let i = 6; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(now.getDate() - i);
    const iso = d.toISOString().slice(0, 10);
    const hours = [3.2, 5.1, 6.4, 8.8, 7.2, 5.6, 6.3][6 - i];
    days.push({ day: iso, hours });
  }
  const rhythm = Array.from({ length: 7 }, (_, d) => Array.from({ length: 24 }, (_, h) => {
    if (h < 9 || h > 22) return 0;
    const base = [0.1, 0.25, 0.55, 0.9, 0.7, 0.35, 0.2][d];
    const bump = h >= 14 && h <= 18 ? 1.4 : h >= 10 && h <= 12 ? 1.1 : 0.6;
    return Math.round(base * bump * 100) / 100;
  }));
  const projects = [
    { id: "arcade", label: "arcade", hours: 18.2, pct: 43 },
    { id: "shipyard", label: "shipyard", hours: 12.4, pct: 29 },
    { id: "notebook", label: "notebook", hours: 7.1, pct: 17 },
    { id: "helpers", label: "helpers", hours: 4.9, pct: 11 },
  ];
  const languages = [
    { label: "TypeScript", hours: 22.4, pct: 53 },
    { label: "Python", hours: 11.2, pct: 26 },
    { label: "Go", hours: 5.8, pct: 14 },
    { label: "CSS", hours: 3.2, pct: 7 },
  ];
  const editors = [
    { id: "vscode", label: "VS Code", hours: 18.0, pct: 42 },
    { id: "neovim", label: "Neovim", hours: 8.4, pct: 20 },
    { id: "jetbrains", label: "JetBrains", hours: 6.1, pct: 14 },
    { id: "godot", label: "Godot", hours: 4.2, pct: 10 },
    { id: "unity", label: "Unity", hours: 3.0, pct: 7 },
    { id: "blender", label: "Blender", hours: 2.9, pct: 7 },
  ];
  const oses = [
    { label: "Linux", hours: 36.1, pct: 85 },
    { label: "macOS", hours: 6.5, pct: 15 },
  ];
  const categories = [
    { label: "coding", hours: 29.0, pct: 68 },
    { label: "debugging", hours: 8.1, pct: 19 },
    { label: "docs", hours: 3.4, pct: 8 },
    { label: "review", hours: 2.1, pct: 5 },
  ];
  return {
    total_hours: 42.6,
    top_project: projects[0],
    top_language: languages[0],
    top_os: oses[0],
    top_editor: editors[0],
    top_category: categories[0],
    projects, languages, editors, oses, categories,
    series: days,
    rhythm,
    pace: { today_hours: 6.3, baseline_hours: 5.4, delta_pct: 17 },
    modes: { human_hours: 34.1, ai_hours: 8.5, human_pct: 80, ai_pct: 20 },
    recent: projects.slice(0, 4).map((p, i) => ({
      day: days[days.length - 1 - (i % days.length)].day,
      project_id: p.id,
      project_label: p.label,
      state: "CLOSED_LOOP",
      state_label: "closed loop",
      weighted_hours: p.hours / 10,
      editor: editors[i % editors.length].label,
      language: languages[i % languages.length].label,
      os: oses[0].label,
    })),
    filters: {
      ranges: [
        { id: "today", label: "today" }, { id: "7d", label: "7 days" },
        { id: "30d", label: "30 days" }, { id: "all", label: "all time" },
      ],
      projects: [{ id: "all", label: "all projects" }, ...projects.map((p) => ({ id: p.id, label: p.label }))],
      languages: [{ id: "all", label: "all languages" }, ...languages.map((l) => ({ id: l.label.toLowerCase(), label: l.label }))],
      oses: [{ id: "all", label: "all os" }, ...oses.map((o) => ({ id: o.label.toLowerCase(), label: o.label }))],
      editors: [{ id: "all", label: "all editors" }, ...editors.map((e) => ({ id: e.label.toLowerCase(), label: e.label }))],
      categories: [{ id: "all", label: "all categories" }, ...categories.map((c) => ({ id: c.label, label: c.label }))],
    },
  };
}

function paintFakeBadge() {
  const badge = $("fake-stats-badge");
  if (!badge) return;
  badge.hidden = !(fakeStatsMode && activePage === "home");
  if (!badge.hidden) hydrateNavIcons(badge);
}

/**
 * Load whatever the active route needs.
 * All auth-gated pages go through ensureAuth once; navigation cancels stale gens.
 */
async function loadActivePageData() {
  const gen = ++pageLoadGen;
  booted = true;
  softRefreshQueued = false;
  const page = activePage;
  const stillHere = () => activePage === page && gen === pageLoadGen;
  try {
    await ensureAuth();
    if (!stillHere()) return;
    const loaders = {
      home: async () => {
        await loadAnalytics();
        if (!stillHere()) return;
        if (!fakeStatsMode && isSignedIn()) await drawFriction();
      },
      projects: () => loadProjectsView(),
      builders: () => renderLeaderboard(),
      review: () => loadReviewQueue(),
      admin: () => loadAdminPage(),
      docs: () => { location.href = "/docs/"; },
      settings: () => { if (isSignedIn()) return refreshApiKeyStatus(); },
    };
    const loader = loaders[page];
    if (loader) await loader();
  } catch (err) {
    console.error("loadActivePageData failed", err);
  }
  if (!stillHere()) return;
  paintHeroCta();
  paintFakeBadge();
}

function setFakeStatsMode(on) {
  const next = !!on;
  if (next && !fakeStatsMode) {
    filtersBeforeFake = { ...filters };
  }
  fakeStatsMode = next;
  if (!fakeStatsMode && filtersBeforeFake) {
    Object.assign(filters, filtersBeforeFake);
    filtersBeforeFake = null;
  }
  try {
    if (fakeStatsMode) sessionStorage.setItem("st_fake_stats", "1");
    else {
      sessionStorage.removeItem("st_fake_stats");
      localStorage.removeItem("st_fake_stats");
    }
  } catch (_) {}
  paintFakeBadge();
  toast(fakeStatsMode ? "Sample stats on" : "Back to your real stats", { kind: fakeStatsMode ? "info" : "ok", glyph: fakeStatsMode ? "sticker" : "checkmark" });
  // ON → fake paint via loadAnalytics; OFF → real data (same as page load)
  loadActivePageData();
}

function onKonamiKey(e) {
  if (activePage !== "home") return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
  const code = e.code === "KeyB" || e.key === "b" || e.key === "B" ? "KeyB"
    : e.code === "KeyA" || e.key === "a" || e.key === "A" ? "KeyA"
    : e.code;
  if (!KONAMI.includes(code) && !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "KeyB", "KeyA"].includes(code)) {
    konamiBuf = [];
    return;
  }
  konamiBuf.push(code);
  if (konamiBuf.length > KONAMI.length) konamiBuf.shift();
  if (konamiBuf.length === KONAMI.length && konamiBuf.every((k, i) => k === KONAMI[i])) {
    konamiBuf = [];
    setFakeStatsMode(!fakeStatsMode);
  }
}
document.addEventListener("keydown", onKonamiKey);

function coerceFilterValue(options, value) {
  const ids = (options || []).map((o) => String(o.id));
  const v = sanitizeFilter(value, "all");
  if (ids.includes(v)) return v;
  return ids.includes("all") ? "all" : (ids[0] || "all");
}

function renderFilterStrip(meta) {
  const box = $("home-filters"); if (!box) return;
  const f = meta?.filters || {};
  filters.range = coerceFilterValue(f.ranges, filters.range);
  filters.project = coerceFilterValue(f.projects || [{ id: "all", label: "all projects" }], filters.project);
  filters.language = coerceFilterValue(f.languages, filters.language);
  filters.os = coerceFilterValue(f.oses, filters.os);
  filters.editor = coerceFilterValue(f.editors, filters.editor);
  filters.category = coerceFilterValue(f.categories, filters.category);
  // Never persist demo-mode picks into sticky real-data filters
  const persist = (key, v) => {
    if (fakeStatsMode) return;
    try { localStorage.setItem(key, v); } catch (_) {}
  };
  box.replaceChildren(
    selectField({ label: "Range", glyph: "clock", options: f.ranges || [], value: filters.range, onChange: (v) => { filters.range = v; persist("st_range", v); loadAnalytics(); } }),
    selectField({ label: "Project", glyph: "briefcase", options: f.projects || [{ id: "all", label: "all projects" }], value: filters.project, onChange: (v) => { filters.project = v; loadAnalytics(); } }),
    selectField({ label: "Language", glyph: "code", options: f.languages || [], value: filters.language, onChange: (v) => { filters.language = v; persist("st_filter_language", v); loadAnalytics(); } }),
    selectField({ label: "OS", glyph: "laptop", options: f.oses || [], value: filters.os, onChange: (v) => { filters.os = v; persist("st_filter_os", v); loadAnalytics(); } }),
    selectField({ label: "Editor", glyph: "terminal", options: f.editors || [], value: filters.editor, onChange: (v) => { filters.editor = v; persist("st_filter_editor", v); loadAnalytics(); } }),
    selectField({ label: "Category", glyph: "sticker", options: f.categories || [], value: filters.category, onChange: (v) => { filters.category = v; persist("st_filter_category", v); loadAnalytics(); } }),
  );
}

function kpiCard(glyph, label, value) {
  return el("div", { className: "kpi" }, [
    el("div", { className: "kpi-ico" }, [icon(glyph, { size: 16 })]),
    el("div", { className: "n", text: value }),
    el("div", { className: "l", text: label }),
  ]);
}

function metricHours(item, mode = dashPrefs.metric) {
  if (!item) return 0;
  if (mode === "raw") return Number(item.raw_hours != null ? item.raw_hours : item.hours || 0);
  if (mode === "active") return Number(item.active_hours != null ? item.active_hours : item.hours || 0);
  return Number(item.weighted_hours != null ? item.weighted_hours : item.hours || 0);
}

function analyticsPrimaryHours(a) {
  if (!a) return 0;
  if (dashPrefs.metric === "raw") return Number(a.raw_hours || 0);
  if (dashPrefs.metric === "active") return Number(a.active_hours || 0);
  return Number(a.total_hours || a.weighted_hours || 0);
}

function renderKpis(a) {
  const box = $("kpi-strip"); if (!box) return;
  const goal = Number((meProfile && meProfile.weekly_goal_hours != null) ? meProfile.weekly_goal_hours : 5);
  const week = Number((meProfile && meProfile.weighted_week_hours) || a.week_hours || 0);
  const pct = goal > 0 ? Math.min(100, Math.round((week / goal) * 100)) : 0;
  const primary = analyticsPrimaryHours(a);
  const kids = [];
  if (dashPrefs.metric === "compare") {
    kids.push(kpiCard("timer", "Weighted", fmtH(a.total_hours || a.weighted_hours)));
    kids.push(kpiCard("history", "Raw clock", fmtH(a.raw_hours)));
    kids.push(kpiCard("stopwatch", "Active span", fmtH(a.active_hours)));
  } else {
    const label = dashPrefs.metric === "raw" ? "Raw clock" : dashPrefs.metric === "active" ? "Active span" : "Weighted";
    kids.push(kpiCard("timer", label, fmtH(primary)));
  }
  kids.push(
    kpiCard("trophy", "Weekly goal", `${week.toFixed(1)}h / ${goal}h (${pct}%)`),
    kpiCard("briefcase", "Project", a.top_project ? a.top_project.label : "none yet"),
    kpiCard("code", "Language", a.top_language ? a.top_language.label : "none yet"),
    kpiCard("laptop", "OS", a.top_os ? a.top_os.label : "none yet"),
    kpiCard("terminal", "Editor", a.top_editor ? editorDisplay(a.top_editor.id || a.top_editor.label).label : "none yet"),
  );
  box.replaceChildren(...kids);
}

function compactEmpty(msg) {
  return el("p", { className: "ui-empty compact", text: msg });
}

function renderRanked(box, items, emptyMsg) {
  if (!box) return;
  if (!items?.length) { box.replaceChildren(compactEmpty(emptyMsg)); return; }
  const ranked = items.slice().sort((a, b) => metricHours(b) - metricHours(a));
  const max = metricHours(ranked[0]) || 1;
  box.replaceChildren(...ranked.slice(0, 8).map((it, i) => {
    const h = metricHours(it);
    const compare = dashPrefs.metric === "compare"
      ? el("div", { className: "sub wrap-text", text: `w ${fmtH(it.weighted_hours ?? it.hours)} · raw ${fmtH(it.raw_hours ?? it.hours)}` })
      : null;
    return el("div", { className: "rank-row" }, [
      el("div", { className: "idx", text: String(i + 1).padStart(2, "0") }),
      el("div", { className: "rank-main" }, [
        el("b", { text: it.label }),
        compare,
        el("div", { className: "bar" }, [el("i", { style: `width:${Math.max(4, (h / max) * 100)}%` })]),
      ]),
      el("b", { className: "rank-h", text: fmtH(h) }),
    ]);
  }));
}

const EDITOR_DISPLAY = {
  neovim: { label: "Neovim", glyph: "terminal" },
  helix: { label: "Helix", glyph: "terminal" },
  jetbrains: { label: "JetBrains", glyph: "code" },
  godot: { label: "Godot", glyph: "waveform" },
  unity: { label: "Unity", glyph: "waveform" },
  kicad: { label: "KiCad", glyph: "diagram-3" },
  blender: { label: "Blender", glyph: "photo" },
  freecad: { label: "FreeCAD", glyph: "photo" },
  openscad: { label: "OpenSCAD", glyph: "code" },
  xcode: { label: "Xcode", glyph: "laptop" },
  arduino: { label: "Arduino", glyph: "cpu" },
  aseprite: { label: "Aseprite", glyph: "sticker" },
  krita: { label: "Krita", glyph: "sticker" },
  gimp: { label: "GIMP", glyph: "photo" },
  figma: { label: "Figma", glyph: "edit" },
  jupyter: { label: "Jupyter", glyph: "code" },
  rstudio: { label: "RStudio", glyph: "code" },
  obsidian: { label: "Obsidian", glyph: "docs" },
  vscode: { label: "VS Code", glyph: "code" },
  cursor: { label: "Cursor", glyph: "code" },
  vscodium: { label: "VSCodium", glyph: "code" },
  windsurf: { label: "Windsurf", glyph: "code" },
  zed: { label: "Zed", glyph: "code" },
};

function editorDisplay(raw) {
  const id = String(raw || "").toLowerCase().trim();
  const hit = EDITOR_DISPLAY[id];
  if (hit) return { id, label: hit.label, glyph: hit.glyph };
  if (!id) return { id: "", label: "unknown", glyph: "terminal" };
  return { id, label: id, glyph: "terminal" };
}

function renderSeg(box, items, emptyMsg) {
  if (!box) return;
  if (!items?.length) { box.replaceChildren(compactEmpty(emptyMsg)); return; }
  const isEditorBox = box.id === "break-editor";
  box.replaceChildren(...items.slice(0, 6).map((it) => {
    const disp = isEditorBox ? editorDisplay(it.id || it.label) : null;
    const labelKids = disp
      ? [icon(disp.glyph, { size: 14, className: "seg-ico" }), document.createTextNode(disp.label)]
      : [document.createTextNode(it.label)];
    return el("div", { className: "seg" }, [
      el("div", { className: "seg-top" }, [
        el("b", { className: disp ? "seg-editor-label" : "" }, labelKids),
        el("span", { className: "seg-meta", text: `${fmtH(it.hours)} · ${it.pct || 0}%` }),
      ]),
      el("div", { className: "track" }, [el("i", { style: `width:${Math.max(3, it.pct || 0)}%` })]),
    ]);
  }));
}

function renderModePie(a) {
  const box = $("mode-pie"); if (!box) return;
  const cats = (a.categories || []).filter((c) => (c.hours || 0) > 0);
  box.replaceChildren(pieChart(cats.length ? cats : a.categories || [], { size: 148, hole: 0.55 }));
}

function renderSeries(a) {
  const box = $("series-chart"); if (!box) return;
  const series = a.series || [];
  if (!series.length) {
    box.classList.add("is-empty");
    box.replaceChildren(compactEmpty("nothing here yet"));
    return;
  }
  box.classList.remove("is-empty");
  const max = Math.max(...series.map((s) => s.hours), 0.01);
  box.replaceChildren(...series.map((s) => el("div", { className: "series-col" }, [
    el("div", { className: "col", style: `height:${Math.max(4, (s.hours / max) * 120)}px`, title: `${s.day}: ${fmtH(s.hours)}` }),
    el("div", { className: "lbl", text: s.day.slice(5) }),
  ])));
}

function renderRhythm(a) {
  const box = $("rhythm-grid"); if (!box) return;
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const rhythm = a.rhythm || [];
  let peak = 0;
  for (const row of rhythm) for (const v of row) peak = Math.max(peak, v);
  if (!peak) {
    box.classList.add("is-empty");
    box.replaceChildren(compactEmpty("quiet week so far"));
    return;
  }
  box.classList.remove("is-empty");
  const kids = [el("div", {})];
  for (let h = 0; h < 24; h++) kids.push(el("div", { className: "hour-lbl", text: h % 3 === 0 ? (h === 0 ? "12a" : h < 12 ? h + "a" : h === 12 ? "12p" : (h - 12) + "p") : "" }));
  for (let d = 0; d < 7; d++) {
    kids.push(el("div", { className: "rh", text: days[d] }));
    for (let h = 0; h < 24; h++) {
      const v = (rhythm[d] && rhythm[d][h]) || 0;
      const op = 0.14 + 0.86 * (v / peak);
      kids.push(el("div", {
        className: "cell",
        title: `${days[d]} ${h}:00 · ${fmt(v, 2)}h`,
        style: v > 0 ? `background:var(--accent);opacity:${op}` : "",
      }));
    }
  }
  box.replaceChildren(...kids);
}

function renderPaceModes(a) {
  const pace = a.pace || {}, modes = a.modes || {};
  const pp = $("pace-panel");
  if (pp) {
    const delta = pace.delta_pct || 0;
    pp.replaceChildren(
      el("div", { className: "pace-big", text: fmtH(pace.today_hours) }),
      el("div", { className: "sub", text: `${delta >= 0 ? "+" : ""}${fmt(delta, 0)}% compared with your usual day` }),
      el("div", { className: "pace-bar" }, [
        el("i", { style: `width:${Math.min(100, Math.max(4, pace.baseline_hours ? (pace.today_hours / Math.max(pace.baseline_hours, 0.01)) * 50 : (pace.today_hours ? 50 : 0)))}%` }),
      ]),
    );
  }
  const mp = $("mode-panel");
  if (mp) {
    mp.replaceChildren(
      el("div", { className: "mode-bar" }, [
        el("div", { className: "human", style: `width:${modes.human_pct || 0}%` }),
        el("div", { className: "ai", style: `width:${modes.ai_pct || 0}%` }),
      ]),
      el("div", { className: "mode-legend" }, [
        el("span", {}, [icon("person", { size: 14 }), " ", el("b", { text: fmtH(modes.human_hours) })]),
        el("span", {}, [icon("bolt", { size: 14 }), " ", el("b", { text: fmtH(modes.ai_hours) })]),
      ]),
    );
  }
}

function renderRecentBuckets(a) {
  const box = $("recent-buckets"); if (!box) return;
  const rows = a.recent || [];
  if (!rows.length) {
    box.replaceChildren(compactEmpty("no logged buckets in this range"));
    return;
  }
  box.replaceChildren(...rows.slice(0, 24).map((b) => {
    const sg = b.struggle || {};
    const struggleBits = [
      sg.reedits ? `${sg.reedits} re-edits` : null,
      sg.fail_to_pass ? `${sg.fail_to_pass} fail→pass` : null,
      sg.git_pulses ? `${sg.git_pulses} git pulses` : null,
      sg.build_cycles ? `${sg.build_cycles} build cycles` : null,
      sg.recovery_edits ? `${sg.recovery_edits} recovery` : null,
    ].filter(Boolean);
    const idleBits = [
      b.active_secs != null ? `active ${b.active_secs}s` : null,
      b.idle_secs != null ? `idle ${b.idle_secs}s` : null,
      b.weight != null ? `w ${Number(b.weight).toFixed(2)}` : null,
    ].filter(Boolean);
    const chain = b.hash_short ? `∑ ${b.hash_short}` : null;
    const h = dashPrefs.metric === "raw" ? b.raw_hours : b.weighted_hours;
    return el("div", { className: "rank-row" }, [
      el("div", { className: "idx", text: (b.day || "").slice(5) || "—" }),
      el("div", { className: "rank-main" }, [
        el("b", { text: b.project_label || b.project_id || "project" }),
        el("div", { className: "sub wrap-text", text: [b.state_label || b.state, b.editor, b.language, b.os, chain, b.signed === false ? "unsigned" : null].filter(Boolean).join(" · ") }),
        struggleBits.length ? el("div", { className: "sub wrap-text", text: struggleBits.join(" · ") }) : null,
        idleBits.length ? el("div", { className: "sub wrap-text", text: idleBits.join(" · ") }) : null,
      ]),
      el("b", { className: "rank-h", text: fmtH(h) }),
    ]);
  }));
}

function renderVelocity(a) {
  const box = $("velocity-panel"); if (!box) return;
  const v = a.velocity || {};
  if (!a.bucket_count) { box.replaceChildren(compactEmpty("no buckets yet")); return; }
  box.replaceChildren(
    el("div", { className: "feature-stat" }, [
      el("b", { className: "feature-n tabular", text: fmtH(v.struggle_hours) }),
      el("span", { className: "sub", text: "weighted struggle" }),
    ]),
    el("div", { className: "feature-stat" }, [
      el("b", { className: "feature-n tabular", text: String(v.commits || 0) }),
      el("span", { className: "sub", text: "commit anchors" }),
    ]),
    el("div", { className: "feature-stat" }, [
      el("b", { className: "feature-n tabular", text: v.velocity_ratio != null ? String(v.velocity_ratio) : "—" }),
      el("span", { className: "sub", text: "h per commit" }),
    ]),
    el("p", { className: "sub wrap-text", text: `${v.reedits || 0} re-edits · ${v.fail_to_pass || 0} fail→pass · ${v.git_pulses || 0} git pulses` }),
  );
}

function renderClassifier(a) {
  const box = $("classifier-panel"); if (!box) return;
  const c = a.classifier || {};
  if (!a.bucket_count) { box.replaceChildren(compactEmpty("no buckets yet")); return; }
  const total = (c.struggle_hours || 0) + (c.dump_hours || 0) + (c.silent_hours || 0) + (c.idle_hours || 0) || 1;
  box.replaceChildren(
    el("div", { className: "mode-bar classifier-bar" }, [
      el("div", { className: "human", style: `width:${Math.max(2, (c.struggle_hours || 0) / total * 100)}%`, title: "struggle / recovery" }),
      el("div", { className: "ai", style: `width:${Math.max(0, (c.dump_hours || 0) / total * 100)}%`, title: "unverified dump" }),
    ]),
    el("div", { className: "mode-legend" }, [
      el("span", { text: `struggle ${fmtH(c.struggle_hours)}` }),
      el("span", { text: `dump ${fmtH(c.dump_hours)}` }),
    ]),
    el("p", { className: "sub wrap-text", text: `Recovery ${fmtH(c.recovery_hours)} · silent ${fmtH(c.silent_hours)} · idle ${fmtH(c.idle_hours)}. Bulk dumps stay discounted until you show edit/test follow-up.` }),
  );
}

function renderDecay(a) {
  const box = $("decay-panel"); if (!box) return;
  const d = a.decay || {};
  if (!a.bucket_count) { box.replaceChildren(compactEmpty("no buckets yet")); return; }
  const raw = Number(d.raw_hours || 0) || 1;
  const decayRows = [
    ["active", d.active_hours, "var(--accent)"],
    ["silent decay", d.silent_hours, "var(--mut)"],
    ["idle zero", d.idle_hours, "var(--dump)"],
  ].map(([label, hours, color]) => el("div", { className: "decay-row" }, [
    el("span", { text: label }),
    el("div", { className: "calib-bar" }, [
      el("i", { style: `width:${Math.max(2, (Number(hours) || 0) / raw * 100)}%;background:${color}` }),
    ]),
    el("b", { className: "tabular", text: fmtH(hours) }),
  ]));
  box.replaceChildren(
    el("div", { className: "decay-stack" }, decayRows),
    el("p", { className: "sub wrap-text", text: d.note || "5-12 minute silent-debug decay. Idle cutoffs stop rewarding AFK." }),
  );
}

function renderAfternoons(a) {
  const box = $("afternoon-panel"); if (!box) return;
  const rows = a.afternoons || [];
  if (!rows.length) {
    box.replaceChildren(compactEmpty("no multi-project afternoons in this range"));
    return;
  }
  box.replaceChildren(...rows.slice(0, 8).map((row) => el("div", { className: "rank-row" }, [
    el("div", { className: "idx", text: (row.day || "").slice(5) || "—" }),
    el("div", { className: "rank-main" }, [
      el("b", { text: `${row.project_count} projects · ${row.window || "afternoon"}` }),
      el("div", {
        className: "sub wrap-text",
        text: (row.projects || []).map((p) => `${p.label} ${p.pct}%`).join(" · "),
      }),
      el("div", { className: "bar afternoon-bar" }, (row.projects || []).map((p) =>
        el("i", { style: `width:${Math.max(3, p.pct || 0)}%`, title: `${p.label}: ${fmtH(p.hours)}` }))),
    ]),
    el("b", { className: "rank-h", text: fmtH(row.weighted_hours) }),
  ])));
}

function renderChainPanel(a) {
  const box = $("chain-panel"); if (!box) return;
  const ch = a.chain || {};
  if (!a.bucket_count) { box.replaceChildren(compactEmpty("no buckets yet")); return; }
  box.replaceChildren(
    el("div", { className: `tag ${ch.ok ? "ok" : "bad"}`, text: ch.ok ? "chain ok" : `chain breaks · ${ch.break_count}` }),
    el("p", { className: "sub wrap-text", text: `${ch.bucket_count || 0} buckets across ${ch.projects_checked || 0} projects · ${ch.unsigned_buckets || 0} unsigned` }),
    el("p", { className: "sub wrap-text", text: "After you sync, the tracker picks up the server’s latest history tip so offline time reconnects without rewriting older entries." }),
  );
}

function buildSummaryText(a) {
  const handle = (meProfile && meProfile.handle) || "builder";
  const v = a.velocity || {};
  const c = a.classifier || {};
  const ch = a.chain || {};
  const tops = (a.projects || []).slice(0, 3).map((p) => `${p.label} ${fmtH(metricHours(p))}`).join(", ") || "none";
  return [
    `StruggleTime @${handle} · ${a.range_label || a.range || "range"}`,
    `Weighted ${fmtH(a.total_hours || a.weighted_hours)} · raw ${fmtH(a.raw_hours)} · active ${fmtH(a.active_hours)}`,
    `Struggle ${fmtH(v.struggle_hours)} · dumps ${fmtH(c.dump_hours)} · commits ${v.commits || 0}`,
    `Top: ${tops}`,
    `Chain ${ch.ok ? "ok" : "broken"} · ${a.bucket_count || 0} buckets · metadata only (no code/prompts/paths)`,
  ].join("\n");
}

function renderSummaryCard(a) {
  const box = $("summary-card"); if (!box) return;
  if (!a.bucket_count) { box.replaceChildren(compactEmpty("nothing to summarize yet")); return; }
  const text = buildSummaryText(a);
  box.replaceChildren(
    el("pre", { className: "summary-pre json-pre", text }),
    el("button", {
      type: "button", className: "ui-btn ui-btn-ghost",
      onClick: async () => {
        try {
          await navigator.clipboard.writeText(text);
          toast("Summary copied", { kind: "ok" });
        } catch (_) {
          toast("Could not copy. Select the text manually.", { kind: "error" });
        }
      },
    }, [icon("copy", { size: 14 }), "copy summary"]),
  );
}

async function shareDashboardSummary() {
  if (!analytics) {
    toast("Load the dashboard first", { kind: "error" });
    return;
  }
  const text = buildSummaryText(analytics);
  try {
    await navigator.clipboard.writeText(text);
    toast("Privacy-safe summary copied", { kind: "ok" });
  } catch (_) {
    modal({
      title: "share summary",
      glyph: "share",
      confirmText: "Close",
      cancelText: null,
      body: el("pre", { className: "summary-pre json-pre", text }),
    });
  }
}

function paintHomeAnalytics(a) {
  analytics = a;
  try {
    applyDashLayout();
    paintMetricHint();
    renderFilterStrip(a);
    renderKpis(a);
    renderRanked($("rank-projects"), a.projects, "no projects in this range");
    renderModePie(a);
    renderSeg($("break-lang"), a.languages, "no language data yet");
    renderSeg($("break-editor"), a.editors, "no editor data yet");
    renderSeg($("break-os"), a.oses, "no os data yet");
    renderSeries(a);
    renderRhythm(a);
    renderPaceModes(a);
    renderRecentBuckets(a);
    renderVelocity(a);
    renderClassifier(a);
    renderDecay(a);
    renderAfternoons(a);
    renderChainPanel(a);
    renderSummaryCard(a);
    paintFakeBadge();
    hydrateNavIcons(document.getElementById("page-home") || document);
  } catch (err) {
    console.error("paintHomeAnalytics failed", err);
    toast("Dashboard render failed — check console", { kind: "error" });
    if ($("kpi-strip")) $("kpi-strip").replaceChildren(compactEmpty("could not render analytics"));
  }
}

function profileHasActivity() {
  const p = meProfile;
  if (!p) return false;
  return Number(p.weighted_hours || 0) > 0 || Number(p.buckets || 0) > 0;
}

function resetDashboardFilters({ range = "7d" } = {}) {
  filters.range = range;
  filters.project = "all";
  filters.language = "all";
  filters.os = "all";
  filters.editor = "all";
  filters.category = "all";
  try {
    localStorage.setItem("st_range", range);
    ["st_filter_language", "st_filter_os", "st_filter_editor", "st_filter_category"].forEach((k) => localStorage.removeItem(k));
  } catch (_) {}
}

function filtersAreDefault() {
  return (filters.project || "all") === "all"
    && sanitizeFilter(filters.language, "all") === "all"
    && sanitizeFilter(filters.os, "all") === "all"
    && sanitizeFilter(filters.editor, "all") === "all"
    && sanitizeFilter(filters.category, "all") === "all";
}

function analyticsHours(a) {
  return Number(a?.total_hours || a?.weighted_hours || 0);
}

async function loadAnalytics() {
  // fakeStatsMode ON → sample; OFF (default / Konami off) → real API paint
  if (fakeStatsMode && activePage === "home") {
    paintHomeAnalytics(buildFakeAnalytics());
    return;
  }
  if ($("kpi-strip") && !analytics) {
    $("kpi-strip").replaceChildren(compactEmpty("loading session…"));
  }
  await ensureAuth();
  if (!isSignedIn()) {
    if ($("kpi-strip")) $("kpi-strip").replaceChildren(compactEmpty("sign in to see your hours"));
    if ($("recent-buckets")) $("recent-buckets").replaceChildren(compactEmpty("sign in to see buckets"));
    if ($("rank-projects")) $("rank-projects").replaceChildren(compactEmpty("sign in to see projects"));
    return;
  }
  if ($("kpi-strip") && !analytics) {
    $("kpi-strip").replaceChildren(compactEmpty("loading hours…"));
  }
  let [s, a] = await j("/v1/me/analytics?" + analyticsQS());
  // Sticky filters can zero analytics while streak still looks fine — recover once.
  let empty = s === 200 && a && analyticsHours(a) === 0;
  if (empty && (profileHasActivity() || !filtersAreDefault())) {
    resetDashboardFilters({ range: filters.range === "today" ? "7d" : (filters.range || "7d") });
    const [s2, a2] = await j("/v1/me/analytics?" + analyticsQS());
    if (s2 === 200 && a2 && analyticsHours(a2) > 0) {
      s = s2; a = a2;
      toast("Cleared filters that hid your hours", { kind: "info" });
    } else if (s2 === 200 && a2 && analyticsHours(a2) === 0 && filters.range !== "all") {
      resetDashboardFilters({ range: "all" });
      const [s3, a3] = await j("/v1/me/analytics?" + analyticsQS());
      if (s3 === 200 && a3 && analyticsHours(a3) > 0) {
        s = s3; a = a3;
        toast("Cleared filters that hid your hours", { kind: "info" });
      }
    }
    empty = s === 200 && a && analyticsHours(a) === 0;
  }
  if (empty && filters.range === "today") {
    const [s2, a2] = await j("/v1/me/analytics?" + new URLSearchParams({
      range: "7d", project: "all", language: "all", os: "all", editor: "all", category: "all",
      tz_offset: String(TZ_OFFSET),
    }).toString());
    if (s2 === 200 && a2 && analyticsHours(a2) > 0) {
      resetDashboardFilters({ range: "7d" });
      s = s2; a = a2;
      toast("Showing last 7 days — nothing in today yet", { kind: "info" });
    }
  }
  if (s !== 200 || !a || typeof a !== "object") {
    const detail = (a && a.error) || (s === 0 ? "network/timeout" : `HTTP ${s}`);
    console.warn("loadAnalytics failed", s, a);
    // Keep last good paint on transient blips — don't blank the dashboard
    if (analytics && TRANSIENT_HTTP.has(s) && s !== 401) {
      toast(`Dashboard refresh failed (${detail})`, { kind: "error" });
      return;
    }
    if ($("kpi-strip")) {
      $("kpi-strip").replaceChildren(compactEmpty(
        s === 0 ? `could not reach server (${detail})` : s === 401 ? "sign in to see your hours" : `could not load analytics (${detail})`
      ));
    }
    if ($("recent-buckets")) $("recent-buckets").replaceChildren(compactEmpty("could not load buckets"));
    if ($("rank-projects")) $("rank-projects").replaceChildren(compactEmpty(s === 401 ? "sign in to see projects" : "could not load projects"));
    return;
  }
  paintHomeAnalytics(a);
}

function repoName(p) {
  if (p.repo_slug) return "/" + String(p.repo_slug).split("/").pop();
  return p.registered === false ? "unregistered" : "no repo";
}

function projectStatus(p) {
  if (p.deleted) return "deleted";
  if (p.archived) return "archived";
  if (!p.repo_url && p.last_repo_url) return "deleted";
  return "active";
}

function renderProjectCards(list, rangeAnalytics) {
  const box = $("project-grid"); if (!box) return;
  if (!(meProfile && meProfile.handle)) {
    box.replaceChildren(el("div", { className: "ui-panel" }, [
      empty("Sign in to see your projects."),
      el("div", { style: "margin-top:10px" }, [el("a", { className: "ui-btn", href: "/login" }, [icon("slack", { size: 16 }), "sign in"])]),
    ]));
    return;
  }
  const hoursMap = Object.fromEntries((rangeAnalytics?.projects || []).map((p) => [p.id, p.hours]));
  let items = list.slice();
  const status = projSearch.status || "active";
  if (status === "active") items = items.filter((p) => projectStatus(p) === "active");
  else if (status === "archived") items = items.filter((p) => projectStatus(p) === "archived");
  else if (status === "deleted") items = items.filter((p) => projectStatus(p) === "deleted");
  const q = projSearch.q.trim().toLowerCase();
  if (q) items = items.filter((p) => [p.name, p.id, p.repo_slug, p.last_repo_url].filter(Boolean).join(" ").toLowerCase().includes(q));
  const total = items.reduce((s, p) => s + (hoursMap[p.id] ?? p.weighted_hours ?? 0), 0);
  if ($("proj-total")) $("proj-total").textContent = `${fmtH(total)} · ${items.length} projects · ${status}`;
  if (!items.length) {
    box.replaceChildren(el("div", { className: "ui-panel" }, [
      empty(status === "deleted" ? "No deleted repos here." : status === "archived" ? "No archived projects." : "No projects yet."),
      status === "active"
        ? el("div", { style: "margin-top:10px" }, [btn("Create project", { glyph: "plus", onClick: () => openProjectModal(null) })])
        : null,
    ]));
    return;
  }
  box.replaceChildren(...items.map((p) => {
    const hours = hoursMap[p.id] ?? p.weighted_hours ?? 0;
    const slug = repoName(p);
    const buckets = p.buckets ? `${p.buckets} buckets` : "no buckets";
    const st = projectStatus(p);
    const canRestore = !!p.deleted || (!p.repo_url && !!p.last_repo_url);
    return el("article", { className: `proj-card${p.archived ? " is-archived" : ""}${st === "deleted" ? " is-deleted" : ""}` }, [
      el("div", { className: "title-row" }, [
        el("div", { className: "title-copy" }, [
          el("h3", { text: p.name || p.id }),
          el("div", { className: "slug", text: slug }),
        ]),
        el("div", { className: "hours", text: fmtH(hours) }),
      ]),
      el("div", { className: "tags" }, [
        el("span", { className: "tag", text: p.registered === false ? "unclaimed" : "tracked" }),
        st !== "active" ? el("span", { className: "tag", text: st === "deleted" ? "deleted repo" : "archived" }) : null,
      ]),
      el("div", { className: "meta-row" }, [
        el("span", { className: "meta-item" }, [icon("card-list", { size: 14 }), el("span", { text: buckets })]),
        el("span", { className: "meta-item" }, [
          icon(p.repo_url ? "github" : (p.last_repo_url ? "delete" : "private"), { size: 14 }),
          el("span", { text: p.repo_url ? "repo linked" : (p.last_repo_url ? "repo deleted" : "no repo") }),
        ]),
        el("span", { className: "meta-item" }, [
          icon(p.local_path ? "docs" : "private", { size: 14 }),
          el("span", { text: p.local_path ? "dir linked" : "no dir", title: p.local_path || "" }),
        ]),
        el("span", { className: "meta-item" }, [
          icon("history", { size: 14 }),
          el("span", { text: hours > 0 ? "active" : "idle" }),
        ]),
      ]),
      el("div", { className: "actions" }, [
        p.registered === false
          ? iconBtn({ glyph: "plus", title: "Claim", onClick: (e) => claimProject(e, p.id) })
          : iconBtn({ glyph: "edit", title: "Edit project", onClick: () => openProjectModal(p) }),
        p.repo_url
          ? iconBtn({ glyph: "external", title: "Open repo", onClick: () => window.open(p.repo_url, "_blank", "noopener") })
          : iconBtn({ glyph: "link", title: "Link project / directory", onClick: () => openLinkProjectModal() }),
        iconBtn({ glyph: "dashboard-meter", title: "Open on dashboard", onClick: () => {
          filters.project = p.id; localStorage.setItem("st_range", filters.range); navigate("/home");
        } }),
        iconBtn({ glyph: "link", title: "Share privacy-safe receipt", onClick: () => shareProjectReceipt(p.id) }),
        canRestore
          ? iconBtn({
              glyph: "view-reload",
              title: p.deleted ? "Restore project" : "Restore deleted repo",
              onClick: () => restoreProject(p),
            })
          : iconBtn({
              glyph: p.archived ? "view-reload" : "archive",
              title: p.archived ? "Unarchive" : "Archive",
              onClick: () => toggleArchive(p),
            }),
      ]),
    ]);
  }));
}

async function loadProjectsView() {
  const grid = $("project-grid");
  try {
    if (grid && !projectsCache.length) {
      grid.replaceChildren(el("div", { className: "ui-panel" }, [empty("loading session…")]));
    }
    await ensureAuth();
    if (!isSignedIn()) {
      renderProjectCards([], null);
      return;
    }
    const [ps, pr] = await j("/v1/projects");
    if (ps === 401) {
      console.warn("projects 401 — session missing");
      renderProjectCards([], null);
      return;
    }
    if (ps !== 200) {
      const detail = (pr && pr.error) || `HTTP ${ps}`;
      console.warn("projects failed", ps, pr);
      if (projectsCache.length && TRANSIENT_HTTP.has(ps)) {
        toast(`Projects refresh failed (${detail})`, { kind: "error" });
        renderProjectCards(projectsCache, analytics);
        return;
      }
      if (grid) {
        grid.replaceChildren(el("div", { className: "ui-panel" }, [
          empty(detail || "Could not load projects"),
        ]));
      }
      return;
    }
    projectsCache = pr.projects || [];
    window._projects = projectsCache;
    let range = projSearch.range || "7d";
    let [as, ar] = await j(`/v1/me/analytics?range=${encodeURIComponent(range)}&project=all&language=all&os=all&editor=all&category=all&tz_offset=${TZ_OFFSET}`);
    // Same streak-vs-empty trap: today+TZ can zero project hours while cards still exist
    if (as === 200 && ar && Number(ar.total_hours || 0) === 0 && range === "today" && projectsCache.some((p) => Number(p.weighted_hours || p.buckets || 0) > 0)) {
      range = "7d";
      projSearch.range = "7d";
      try { localStorage.setItem("st_proj_range", "7d"); } catch (_) {}
      if ($("proj-range")) $("proj-range").value = "7d";
      [as, ar] = await j(`/v1/me/analytics?range=7d&project=all&language=all&os=all&editor=all&category=all&tz_offset=${TZ_OFFSET}`);
    }
    renderProjectCards(projectsCache, as === 200 ? ar : null);
  } catch (err) {
    console.error("loadProjectsView failed", err);
    if (projectsCache.length) {
      toast("Projects refresh failed", { kind: "error" });
      renderProjectCards(projectsCache, analytics);
      return;
    }
    if (grid) {
      grid.replaceChildren(el("div", { className: "ui-panel" }, [
        empty("Could not load projects"),
      ]));
    }
  }
}

async function drawFriction() {
  if (!$("fr-chart")) return;
  const statesBox = $("fr-states");
  if (!selected) {
    $("fr-chart").innerHTML = '<p class="ui-empty compact">pick a project with buckets</p>';
    if (statesBox) statesBox.replaceChildren();
    return;
  }
  const [s, fr] = await j("/v1/projects/" + encodeURIComponent(selected) + "/friction");
  if (s !== 200 || !fr || !Array.isArray(fr.timeline) || !fr.timeline.length) {
    const detail = (fr && fr.error) || (s === 401 ? "sign in to view friction" : s && s !== 200 ? `could not load (${s})` : "no buckets yet");
    $("fr-chart").innerHTML = `<p class="ui-empty compact">${esc(detail)}</p>`;
    if (statesBox) statesBox.replaceChildren();
    return;
  }
  const n = fr.timeline.length;
  const leftPad = 36, bottomPad = 28, topPad = 8;
  const w = Math.max(560, leftPad + n * 52 + 8), h = 120, bw = 32;
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--bar").trim() || "#d7ff6a";
  const dump = getComputedStyle(document.documentElement).getPropertyValue("--dump").trim() || "#57534c";
  const mute = getComputedStyle(document.documentElement).getPropertyValue("--mut").trim()
    || getComputedStyle(document.documentElement).getPropertyValue("--muted").trim()
    || "#78716c";
  const axis = `
    <text x="12" y="${topPad + h / 2}" fill="currentColor" font-size="10" opacity=".55"
      transform="rotate(-90 12 ${topPad + h / 2})" text-anchor="middle">friction</text>
    <text x="${leftPad - 4}" y="${topPad + 10}" text-anchor="end" fill="currentColor" font-size="9" opacity=".45">1</text>
    <text x="${leftPad - 4}" y="${topPad + h}" text-anchor="end" fill="currentColor" font-size="9" opacity=".45">0</text>
    <line x1="${leftPad}" y1="${topPad}" x2="${leftPad}" y2="${topPad + h}" stroke="currentColor" stroke-opacity=".18"/>
    <line x1="${leftPad}" y1="${topPad + h}" x2="${w - 8}" y2="${topPad + h}" stroke="currentColor" stroke-opacity=".18"/>
    <text x="${(leftPad + w) / 2}" y="${topPad + h + 24}" text-anchor="middle" fill="currentColor" font-size="10" opacity=".5">bucket (oldest → newest)</text>`;
  const bars = fr.timeline.map((t, i) => {
    const bh = Math.max(3, (Number(t.friction) || 0) * 95);
    const fill = t.state === "OPEN_LOOP_AGENT_DUMP" ? dump : (t.state === "SILENT_DEBUG" ? mute : accent);
    const tip = [
      t.state || "?",
      `friction ${Number(t.friction || 0).toFixed(2)}`,
      `weight ${Number(t.weight || 0).toFixed(2)}`,
      `active ${t.active_secs || 0}s / idle ${t.idle_secs || 0}s`,
      t.reedits ? `${t.reedits} re-edits` : null,
      t.fail_to_pass ? `${t.fail_to_pass} fail→pass` : null,
      t.git_pulses ? `${t.git_pulses} git` : null,
      t.bucket_id ? String(t.bucket_id).slice(0, 8) : "",
    ].filter(Boolean).join(" · ");
    const x = leftPad + i * 52 + 8;
    return `<g class="fr-bar" data-i="${i}" style="cursor:pointer">
      <rect x="${x}" y="${topPad + h - bh}" width="${bw}" height="${bh}" rx="4" fill="${fill}">
        <title>${esc(tip)}</title>
      </rect>
      <text x="${x + bw / 2}" y="${topPad + h + 14}" text-anchor="middle" fill="currentColor" font-size="9" opacity=".55">${i + 1}</text>
    </g>`;
  }).join("");
  $("fr-chart").innerHTML = `<svg width="${w}" height="${h + bottomPad + topPad}" viewBox="0 0 ${w} ${h + bottomPad + topPad}" role="img" aria-label="friction timeline">${axis}${bars}</svg>`;
  const inspect = $("fr-inspect");
  $("fr-chart").querySelectorAll(".fr-bar").forEach((g) => {
    g.addEventListener("click", () => {
      const i = Number(g.getAttribute("data-i"));
      const t = fr.timeline[i];
      if (!t) return;
      toast(`${t.state || "bucket"} · friction ${Number(t.friction || 0).toFixed(2)} · w ${Number(t.weight || 0).toFixed(2)}`, { kind: "info", glyph: "diagram-3" });
      if (inspect) {
        inspect.hidden = false;
        const files = (t.files || []).slice(0, 8).map((f) => f.file_id || "?").join(", ");
        inspect.replaceChildren(
          el("div", { className: "fr-inspect-grid" }, [
            el("div", {}, [el("b", { text: "state" }), el("div", { className: "sub wrap-text", text: t.state || "?" })]),
            el("div", {}, [el("b", { text: "weight" }), el("div", { className: "sub tabular", text: String(Number(t.weight || 0).toFixed(2)) })]),
            el("div", {}, [el("b", { text: "friction" }), el("div", { className: "sub tabular", text: String(Number(t.friction || 0).toFixed(2)) })]),
            el("div", {}, [el("b", { text: "active / idle" }), el("div", { className: "sub tabular", text: `${t.active_secs || 0}s / ${t.idle_secs || 0}s` })]),
            el("div", {}, [el("b", { text: "commits / fails" }), el("div", { className: "sub tabular", text: `${t.commits || 0} / ${t.exec_failures || 0}` })]),
            el("div", {}, [el("b", { text: "re-edits" }), el("div", { className: "sub tabular", text: String(t.reedits || 0) })]),
            el("div", {}, [el("b", { text: "fail→pass" }), el("div", { className: "sub tabular", text: String(t.fail_to_pass || 0) })]),
            el("div", {}, [el("b", { text: "git pulses" }), el("div", { className: "sub tabular", text: String(t.git_pulses || 0) })]),
            el("div", {}, [el("b", { text: "bucket" }), el("div", { className: "sub wrap-text", text: String(t.bucket_id || "").slice(0, 16) })]),
          ]),
          files ? el("p", { className: "sub wrap-text", text: `file ids: ${files}` }) : el("p", { className: "sub", text: "No file ids in this bucket." }),
        );
      }
    });
  });
  if (statesBox) {
    const counts = {};
    for (const t of fr.timeline) counts[t.state || "unknown"] = (counts[t.state || "unknown"] || 0) + 1;
    const kids = Object.entries(counts).map(([st, n]) =>
      el("span", { className: "tag", text: `${(st || "").replace(/_/g, " ").toLowerCase()} · ${n}` }));
    if (fr.chain_ok === false) kids.unshift(el("span", { className: "tag bad", text: "chain broken" }));
    else if (fr.chain_ok === true) kids.unshift(el("span", { className: "tag ok", text: "chain ok" }));
    if ((fr.commit_anchors || []).length) kids.push(el("span", { className: "tag", text: `${fr.commit_anchors.length} commit anchors` }));
    statesBox.replaceChildren(...kids);
  }
  if ($("fr-legend")) {
    $("fr-legend").textContent = fr.chain_ok === false
      ? "History check failed on this project (an older entry may have been edited). Review timing signals only; paths stay on your machine."
      : "Click a bar for re-edits, fail→pass, git plumbing, and active/idle. Commit anchors mark verified recovery.";
  }
  if (inspect) { inspect.hidden = true; inspect.replaceChildren(); }
}

async function softRefresh() {
  // Alias kept for call sites — always the Konami-OFF / real-data page loader.
  if (!booted) {
    softRefreshQueued = true;
    return;
  }
  await loadActivePageData();
}

function paintHeroCta() {
  const primary = $("hero-primary");
  const secondary = $("hero-secondary");
  if (primary) {
    if (meProfile?.handle) {
      primary.href = "/home";
      primary.innerHTML = "";
      primary.append(icon("home", { size: 16 }), document.createTextNode("open your dashboard"));
    } else {
      primary.href = "/login";
      primary.innerHTML = "";
      primary.append(icon("slack", { size: 16 }), document.createTextNode("continue with hack club slack"));
    }
  }
  if (secondary) {
    secondary.href = "/docs/";
    secondary.innerHTML = "";
    secondary.append(icon("docs", { size: 16 }), document.createTextNode("read the docs"));
  }
}

async function refresh({ forceAuth = false } = {}) {
  // Serialize overlapping refresh/interval/navigation so a slow response can't blank a newer paint
  const run = async () => {
    refreshRunning = true;
    try {
      if (forceAuth) authReady = null;
      await ensureAuth(forceAuth);
      paintAccount();
      if (isSignedIn()) await refreshApiKeyStatus();
      else { daemonLinkInfo = null; paintDaemonBanners(); }

      booted = true;
      if (!allowProtectedPage(activePage)) {
        showPage("landing", { push: true, load: false });
      }

      const [[os], [ps, pr]] = await Promise.all([
        j("/v1/overview"),
        isSignedIn() ? j("/v1/projects") : Promise.resolve([401, {}]),
      ]);
      if ($("live-txt")) {
        $("live-txt").textContent = "●";
        $("live-txt").style.color = os === 200 ? "var(--accent)" : "var(--danger)";
      }

      // Never wipe a good cache with an auth failure (looks like projects were deleted)
      if (ps === 200) {
        projectsCache = pr.projects || [];
        window._projects = projectsCache;
      } else if (TRANSIENT_HTTP.has(ps) && projectsCache.length) {
        console.warn("projects refresh transient", ps, pr?.error);
      }
      const withData = projectsCache.filter((p) => p.buckets > 0);
      if (!selected && withData.length) selected = withData[0].id;
      if ($("proj-sel")) {
        $("proj-sel").innerHTML = withData.length
          ? withData.map((p) => `<option value="${esc(p.id)}" ${p.id === selected ? "selected" : ""}>${esc(p.name || p.id)}</option>`).join("")
          : '<option value="">no projects yet</option>';
      }

      await loadActivePageData();
    } finally {
      refreshRunning = false;
    }
  };
  const queued = refreshChain.then(run, run);
  refreshChain = queued.catch(() => {});
  return queued;
}

/* wire controls */
if ($("proj-sel")) $("proj-sel").addEventListener("change", (e) => { selected = e.target.value; drawFriction(); });
if ($("c-handle")) $("c-handle").addEventListener("input", async (e) => {
  const h = e.target.value.trim().toLowerCase();
  if (h.length < 2) { $("create-out").textContent = ""; return; }
  const [s, r] = await j("/v1/handles/" + encodeURIComponent(h));
  $("create-out").textContent = s === 200 ? (r.available ? `@${h} is available` : `@${h} is taken`) : "";
});
if ($("claim-form")) $("claim-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const h = $("c-handle").value.trim().toLowerCase();
  const [s, r] = await j("/v1/auth/claim-handle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ handle: h }) });
  if (s === 201) {
    meProfile = r; meLoaded = true; toast("@" + h + " is yours", { kind: "ok" });
    await refresh({ forceAuth: true });
    navigate("/home");
  } else toast(r.error || s, { kind: "error" });
});
if ($("signout-form")) $("signout-form").addEventListener("submit", async (e) => {
  e.preventDefault(); await doLogout();
});
if ($("login-signout")) $("login-signout").addEventListener("submit", async (e) => {
  e.preventDefault(); await doLogout();
});
if ($("sl-login-form")) $("sl-login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!(authStatus && authStatus.slack_ready)) { toast("Hack Club Slack OAuth not configured", { kind: "error" }); return; }
  location.href = "/v1/auth/slack/start";
});
if ($("login-slack")) $("login-slack").addEventListener("click", () => {
  if (!(authStatus && authStatus.slack_ready)) { toast("Hack Club Slack OAuth not configured", { kind: "error" }); return; }
  location.href = "/v1/auth/slack/start";
});
if ($("login-claim-form")) $("login-claim-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const h = $("login-handle").value.trim().toLowerCase();
  const [s, r] = await j("/v1/auth/claim-handle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ handle: h }) });
  if (s === 201) {
    meProfile = r; meLoaded = true; toast("@" + h + " is yours", { kind: "ok" });
    paintAccount();
    navigate("/home");
  } else { $("login-claim-out").textContent = r.error || String(s); toast(r.error || s, { kind: "error" }); }
});
if ($("login-handle")) $("login-handle").addEventListener("input", async (e) => {
  const h = e.target.value.trim().toLowerCase();
  if (h.length < 2) { $("login-claim-out").textContent = ""; return; }
  const [s, r] = await j("/v1/handles/" + encodeURIComponent(h));
  $("login-claim-out").textContent = s === 200 ? (r.available ? `@${h} is available` : `@${h} is taken`) : "";
});
if ($("gh-form")) $("gh-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!(meProfile && meProfile.handle)) { toast("Claim a handle first", { kind: "error" }); return; }
  // Prefer cookie session; only pass localStorage token when it looks like a web session (not st_ API key).
  const t = token();
  const q = t && !t.startsWith("st_") ? ("?session=" + encodeURIComponent(t)) : "";
  location.href = "/v1/auth/github/start" + q;
});
if ($("btn-create-project")) $("btn-create-project").addEventListener("click", () => openProjectModal(null));
if ($("btn-link-project")) $("btn-link-project").addEventListener("click", () => openLinkProjectModal());
if ($("proj-search")) $("proj-search").addEventListener("input", (e) => { projSearch.q = e.target.value; renderProjectCards(projectsCache, analytics); });
if ($("proj-range")) {
  $("proj-range").value = projSearch.range;
  $("proj-range").addEventListener("change", async (e) => {
    projSearch.range = e.target.value; localStorage.setItem("st_proj_range", projSearch.range);
    await loadProjectsView();
  });
}
if ($("proj-status")) {
  $("proj-status").value = projSearch.status;
  $("proj-status").addEventListener("change", (e) => {
    projSearch.status = e.target.value;
    localStorage.setItem("st_proj_status", projSearch.status);
    renderProjectCards(projectsCache, analytics);
  });
}

if ($("lb-tabs")) {
  $("lb-tabs").appendChild(tabs([
    { value: "today", label: "24h" },
    { value: "week", label: "7d" },
  ], { value: lbPeriod, onChange: (v) => { lbPeriod = v; renderLeaderboard(); } }));
}
if ($("lb-search-slot")) {
  $("lb-search-slot").appendChild(UI.searchInput({ placeholder: "Find a builder…", onInput: (v) => { lbQuery = v; renderLeaderboard(); } }));
}

function openInstallDocs() {
  location.href = INSTALL_DOCS_URL;
}
if ($("daemon-banner-settings-cta")) {
  $("daemon-banner-settings-cta").addEventListener("click", (e) => {
    e.preventDefault();
    openInstallDocs();
  });
}
if ($("daemon-banner-cta")) {
  $("daemon-banner-cta").addEventListener("click", (e) => {
    const href = $("daemon-banner-cta").getAttribute("href") || INSTALL_DOCS_URL;
    if (href === "/settings" || href.startsWith("/settings")) {
      e.preventDefault();
      navigate("/settings");
      return;
    }
    e.preventDefault();
    location.href = href;
  });
}
if ($("daemon-banner-dismiss")) {
  $("daemon-banner-dismiss").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dismissDaemonBanner();
  });
}
if ($("btn-github-disconnect")) {
  $("btn-github-disconnect").addEventListener("click", async () => {
    if (!confirm("Disconnect GitHub? Project repo links stay; reconnect anytime from settings.")) return;
    const [s, r] = await j("/v1/me/github/disconnect", { method: "POST" });
    if (s !== 200) { toast(r.error || "Could not disconnect GitHub", { kind: "error" }); return; }
    if (r && r.handle) { meProfile = r; meLoaded = true; }
    else if (meProfile) {
      meProfile.github_linked = false;
      meProfile.github_login = null;
    }
    ghReposCache = null;
    ghReposAt = 0;
    paintAccount();
    paintAuthChecks();
    toast("GitHub disconnected", { kind: "ok" });
  });
}

function setSettingsSection(id) {
  if (id === "daemon") {
    location.href = TRAY_DOCS_URL;
    return;
  }
  if (id === "docs") {
    location.href = "/docs/";
    return;
  }
  document.querySelectorAll("[data-settings-tab]").forEach((t) => t.classList.toggle("on", t.dataset.settingsTab === id));
  document.querySelectorAll(".settings-panel").forEach((p) => {
    const on = p.id === "settings-" + id;
    p.classList.toggle("on", on);
    p.hidden = !on;
  });
  const sel = $("settings-rail-select");
  if (sel && sel.value !== id) sel.value = id;
  hydrateNavIcons($("settings-main") || document);
}

document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
  tab.addEventListener("click", () => setSettingsSection(tab.dataset.settingsTab));
});
if ($("settings-rail-select")) {
  $("settings-rail-select").addEventListener("change", (e) => setSettingsSection(e.target.value));
}

function setMobileNavOpen(open) {
  document.body.classList.toggle("nav-open", !!open);
  const btn = $("nav-toggle");
  if (btn) {
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  }
}
if ($("nav-toggle")) {
  $("nav-toggle").addEventListener("click", () => {
    setMobileNavOpen(!document.body.classList.contains("nav-open"));
  });
}
if ($("nav-backdrop")) {
  $("nav-backdrop").addEventListener("click", () => setMobileNavOpen(false));
}
document.querySelectorAll("#top-nav a").forEach((a) => {
  a.addEventListener("click", () => setMobileNavOpen(false));
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setMobileNavOpen(false);
});
window.addEventListener("resize", () => {
  if (window.innerWidth > 720) setMobileNavOpen(false);
});

if ($("btn-save-region")) $("btn-save-region").addEventListener("click", () => saveProfile({
  country: $("set-country").value || null,
  timezone: $("set-timezone").value || null,
}));
if ($("btn-save-identity")) $("btn-save-identity").addEventListener("click", () => saveProfile({
  display_name: $("set-display-name").value.trim() || null,
  email: $("set-email").value.trim() || null,
  weekly_goal_hours: Number(($("set-weekly-goal") && $("set-weekly-goal").value) || 5),
}));
if ($("btn-save-privacy")) $("btn-save-privacy").addEventListener("click", () => saveProfile({
  lb_opt_out: !!$("set-lb-opt-out").checked,
  show_leaderboards: !!$("set-show-lb").checked,
}));
["delete-confirm", "delete-confirm-phrase"].forEach((id) => {
  if ($(id)) $(id).addEventListener("input", () => resetDeleteAccountBtn());
});
if ($("btn-delete-account")) $("btn-delete-account").addEventListener("click", async (ev) => {
  const handleConfirm = ($("delete-confirm") && $("delete-confirm").value || "").trim();
  const phrase = ($("delete-confirm-phrase") && $("delete-confirm-phrase").value || "").trim();
  if (!meProfile?.handle || handleConfirm !== meProfile.handle) {
    toast("Type your exact handle to confirm", { kind: "error" }); return;
  }
  if (phrase !== "DELETE") {
    toast("Type DELETE in capitals to unlock account deletion", { kind: "error" }); return;
  }
  const btnEl = ev.currentTarget;
  if (btnEl && !btnEl.dataset.unlocked) {
    btnEl.dataset.unlocked = "1";
    btnEl.disabled = true;
    let left = 3;
    paintDangerBtn(btnEl, `unlocked in ${left}s…`);
    if (btnEl._unlockTick) clearInterval(btnEl._unlockTick);
    btnEl._unlockTick = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(btnEl._unlockTick);
        btnEl._unlockTick = null;
        btnEl.disabled = false;
        paintDangerBtn(btnEl, "confirm delete account");
      } else {
        paintDangerBtn(btnEl, `unlocked in ${left}s…`);
      }
    }, 1000);
    toast("Safety lock: click again after the countdown. This removes your account and sessions.", { kind: "info" });
    return;
  }
  if (!window.confirm(`Permanently delete @${meProfile.handle}? Sessions and project list go away. Bucket evidence may be retained for staff audit. This cannot be undone.`)) return;
  const [s, r] = await j("/v1/me/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: handleConfirm }),
  });
  if (s !== 200) { toast(r.error || "Delete failed", { kind: "error" }); return; }
  clearClientSession();
  toast("Account deleted", { kind: "ok" });
  navigate("/");
});

if ($("btn-api-key-regen")) $("btn-api-key-regen").addEventListener("click", async () => {
  if (!isSignedIn()) { toast("Sign in first", { kind: "error" }); return; }
  if (apiKeyPlain && !confirm("Regenerate? The previous key stops working immediately.")) return;
  if (!apiKeyPlain && !confirm("Generate a new StruggleTime API key? Any previous key will stop working.")) return;
  const [s, r] = await j("/v1/auth/api-key", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: true }),
  });
  if (s !== 200) { toast(r.error || "Could not generate key", { kind: "error" }); return; }
  showApiKey(r.api_key || "", { fresh: true });
  if (meProfile) {
    meProfile.has_api_key = true;
    meProfile.daemon_linked = !!r.daemon_linked;
  }
  toast("API key ready — paste it into the tray", { kind: "ok" });
  await refreshApiKeyStatus();
});
if ($("btn-api-key-revoke")) $("btn-api-key-revoke").addEventListener("click", async () => {
  if (!confirm("Revoke the API key? The tray will stop syncing until you generate and paste a new one.")) return;
  if (!confirm("Second check: revoke now? Any machine still using the old key will get 401s.")) return;
  const [s, r] = await j("/v1/auth/api-key/revoke", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  if (s !== 200) { toast(r.error || "Could not revoke", { kind: "error" }); return; }
  apiKeyPlain = "";
  if (meProfile) {
    meProfile.has_api_key = false;
    meProfile.daemon_linked = false;
  }
  toast("API key revoked", { kind: "ok" });
  await refreshApiKeyStatus();
});
if ($("btn-api-key-reveal")) $("btn-api-key-reveal").addEventListener("click", () => {
  const inp = $("api-key-value"); if (!inp) return;
  const show = inp.type === "password";
  inp.type = show ? "text" : "password";
  $("btn-api-key-reveal").textContent = show ? "hide" : "show";
});
if ($("btn-api-key-copy")) $("btn-api-key-copy").addEventListener("click", async () => {
  const v = apiKeyPlain || ($("api-key-value") && $("api-key-value").value) || "";
  if (!v) { toast("Generate a key first", { kind: "error" }); return; }
  try {
    await navigator.clipboard.writeText(v);
    toast("Copied", { kind: "ok" });
  } catch (_) {
    $("api-key-value").type = "text"; $("api-key-value").select();
    toast("Select the key and copy manually", { kind: "ok" });
  }
});

document.addEventListener("click", async (e) => {
  const kpiNav = e.target.closest("[data-nav-kpi]");
  if (kpiNav) {
    e.preventDefault();
    navigate("/" + kpiNav.getAttribute("data-nav-kpi"));
    return;
  }
  const inspectBtn = e.target.closest("[data-inspect]");
  if (inspectBtn) {
    e.preventDefault();
    await inspectSubmission(inspectBtn.getAttribute("data-inspect"));
    return;
  }
  const chainBtn = e.target.closest("[data-chain-diff]");
  if (chainBtn) {
    e.preventDefault();
    const pid = chainBtn.getAttribute("data-chain-diff");
    const [s, r] = await j(`/v1/projects/${encodeURIComponent(pid)}/chain-diff`);
    if (s !== 200) { toast(r.error || "inspect failed", { kind: "error" }); return; }
    const pairs = (r.pairs || []).slice(0, 8).map((a) =>
      el("div", { className: "chain-diff-pair" }, [
        el("div", { className: "bucket-chip bad" }, [
          el("b", { text: "flagged" }),
          el("div", { className: "sub", text: (a.reasons || []).join(", ") || "anomaly" }),
          el("pre", { className: "json-pre", text: prettyChip(a.bucket) }),
        ]),
        el("div", { className: "bucket-chip ok" }, [
          el("b", { text: "neighbor" }),
          el("pre", { className: "json-pre", text: prettyChip(a.neighbor) }),
        ]),
      ])
    );
    modal({
      title: "chain & anomaly diff — " + pid,
      glyph: "private",
      confirmText: "Close",
      cancelText: "",
      wide: true,
      body: el("div", { style: "min-width:0" }, [
        el("p", { className: "sub", text: r.chain_ok === false
          ? `Chain broken · ${(r.chain_errors || []).slice(0, 3).join("; ") || "see pairs below"}`
          : `Chain ok · ${r.bucket_count || 0} buckets · ${(r.pairs || []).length} anomalies` }),
        ...(pairs.length ? pairs : [el("p", { className: "sub", text: "No anomalies in this window." })]),
      ]),
      onConfirm: () => true,
    });
    return;
  }
  const receiptBtn = e.target.closest("[data-receipt]");
  if (receiptBtn) {
    e.preventDefault();
    await shareProjectReceipt(receiptBtn.getAttribute("data-receipt"));
    return;
  }
  const btn = e.target.closest("[data-review]");
  if (!btn) return;
  e.preventDefault();
  const id = btn.getAttribute("data-review");
  const action = btn.getAttribute("data-action");
  const reasonSel = document.querySelector(`[data-reason-for="${CSS.escape(id)}"]`);
  const hoursInp = document.querySelector(`[data-hours-for="${CSS.escape(id)}"]`);
  const noteInp = document.querySelector(`[data-note-for="${CSS.escape(id)}"]`);
  let reason = (reasonSel && reasonSel.value) || "NONE";
  let hours = hoursInp ? Number(hoursInp.value) * 3600 : 0;
  const note = (noteInp && noteInp.value || "").trim();
  if (action === "note" || action === "flag") {
    if (action === "flag" && !confirm("Flag this submission without resolving it?")) return;
    await submitReviewSideAction(id, action, { reason, note });
    return;
  }
  if (action === "approve") {
    if (!reason || reason === "NONE") reason = "NONE";
    if (!hours && hoursInp) hours = Number(hoursInp.value) * 3600;
  } else if (action === "deflate") {
    hours = hours * 0.5;
    if (reason === "NONE") reason = "NO_TEST_ITERATION";
  } else if (action === "reject") {
    hours = 0;
    if (reason === "NONE") reason = "IDLE_INFLATION";
  }
  if (!confirm(`${action} this submission?`)) return;
  submitReview(id, hours, reason, note);
});

document.addEventListener("change", async (e) => {
  const trustSel = e.target.closest("[data-trust-for]");
  if (trustSel) {
    const handle = trustSel.getAttribute("data-trust-for");
    const [s, r] = await j("/v1/admin/trust", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handle, trust_level: trustSel.value, reason: "manual" }),
    });
    if (s !== 200) toast(r.error || "trust update failed", { kind: "error" });
    else { toast(`@${handle} → ${r.trust_level}`, { kind: "ok" }); loadAdminPage(); }
    return;
  }
  const roleSel = e.target.closest("[data-role-for]");
  if (roleSel) {
    const handle = roleSel.getAttribute("data-role-for");
    const [s, r] = await j("/v1/admin/role", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handle, admin_level: roleSel.value }),
    });
    if (s !== 200) toast(r.error || "role update failed", { kind: "error" });
    else { toast(`@${handle} → ${r.admin_level}`, { kind: "ok" }); loadAdminPage(); }
  }
});

let adminSearchTimer = null;
if ($("admin-user-q")) {
  $("admin-user-q").addEventListener("input", () => {
    clearTimeout(adminSearchTimer);
    adminSearchTimer = setTimeout(() => { if (activePage === "admin") loadAdminPage(); }, 250);
  });
}
["review-status", "review-flags"].forEach((id) => {
  if ($(id)) $(id).addEventListener("change", () => { if (activePage === "review") loadReviewQueue(); });
});
let reviewFilterTimer = null;
["review-builder", "review-project"].forEach((id) => {
  if ($(id)) $(id).addEventListener("input", () => {
    clearTimeout(reviewFilterTimer);
    reviewFilterTimer = setTimeout(() => { if (activePage === "review") loadReviewQueue(); }, 200);
  });
});
["fraud-risk", "fraud-window"].forEach((id) => {
  if ($(id)) $(id).addEventListener("change", () => { if (activePage === "admin") loadFraudAnomalies(); });
});
if ($("fraud-refresh")) {
  $("fraud-refresh").addEventListener("click", () => loadFraudAnomalies());
}

/* ── boot (side effects last — after all decls) ─────────────────────── */
(async function boot() {
  await consumeAuthCode();
  await refresh({ forceAuth: true });
  // Stay on the hero when signed-in users reload `/`. Only bounce `/login`
  // (post-auth claim / already-signed-in). OAuth still navigates via consumeAuthCode.
  if (isSignedIn() && activePage === "login") {
    showPage("home", { push: true, load: false });
    await loadActivePageData();
  } else if (!allowProtectedPage(activePage)) {
    showPage("landing", { push: true, load: false });
  }
  setInterval(() => {
    if (refreshRunning || document.hidden) return;
    refresh({ forceAuth: false });
  }, 15000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh({ forceAuth: false });
  });
})();
