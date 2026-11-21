const { esc, el, toast, modal, field, btn, pill, empty, tabs, avatar, icon, iconBtn, selectField, comboField, pathField, pieChart } = UI;
const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => Number(n || 0).toFixed(d);
const fmtH = (h) => {
  const n = Number(h || 0);
  if (n < 1 / 60) return "0m";
  if (n < 1) return `${Math.round(n * 60)}m`;
  return `${fmt(n, 1)}h`;
};
const token = () => localStorage.getItem("st_token") || "";
const setToken = (t) => { if (t) localStorage.setItem("st_token", t); else localStorage.removeItem("st_token"); };
const authHdr = () => (token() ? { Authorization: "Bearer " + token() } : {});
async function j(u, o = {}) {
  const { cookieOnly, noCookieRetry, timeout = 12000, ...fetchOpts } = o;
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
    let status = r.status;
    let body = await r.json().catch(() => ({}));
    // Stale localStorage bearer must not blank projects/analytics when the Slack cookie is valid
    if (status === 401 && token() && !cookieOnly && !noCookieRetry) {
      setToken("");
      return j(u, { ...o, cookieOnly: true, noCookieRetry: true });
    }
    return [status, body];
  } catch (_) {
    return [0, { error: "network error" }];
  } finally {
    clearTimeout(timer);
  }
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

let selected = "", authStatus = null, meProfile = null, ghReposCache = null, ghReposAt = 0;
let lbPeriod = "week", lbQuery = "";
let analytics = null, projectsCache = [];
let meLoaded = false, authStatusLoaded = false, booted = false;
let softRefreshQueued = false;
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
  range: localStorage.getItem("st_range") || "7d",
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

function mountDocs(into) {
  const root = typeof into === "string" ? $(into) : into;
  const tpl = $("st-docs-template");
  if (!root || !tpl) return;
  root.replaceChildren(tpl.content.cloneNode(true));
  hydrateNavIcons(root);
  const tabs = [...root.querySelectorAll(".docs-rail a.settings-tab[href^='#']")];
  const setActive = (id) => {
    tabs.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + id));
  };
  if (tabs[0]) setActive(tabs[0].getAttribute("href").slice(1));
  tabs.forEach((a) => {
    a.addEventListener("click", (e) => {
      const id = a.getAttribute("href").slice(1);
      const target = root.querySelector("#" + CSS.escape(id));
      if (!target) return;
      e.preventDefault();
      setActive(id);
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      try { history.replaceState(null, "", "#" + id); } catch (_) {}
    });
  });
  root.querySelectorAll("[data-docs-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const text = btn.getAttribute("data-docs-copy") || "";
      try {
        await navigator.clipboard.writeText(text);
        toast("Clone command copied", { kind: "ok" });
      } catch (_) {
        toast(text, { kind: "ok" });
      }
    });
  });
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
  const staff = isStaff();
  document.querySelectorAll('#top-nav a[data-auth="required"]').forEach((a) => { a.hidden = !ok; });
  document.querySelectorAll('#top-nav a[data-auth="staff"]').forEach((a) => {
    const needAdmin = a.dataset.nav === "admin";
    a.hidden = needAdmin ? !canAdmin() : !staff;
  });
  const shell = $("app-shell");
  if (shell) shell.classList.toggle("guest-mode", !ok);
}

function clearClientSession() {
  apiKeyPlain = "";
  daemonLinkInfo = null;
  setToken("");
  meProfile = null;
  meLoaded = true;
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
  if ($("nav-flag")) $("nav-flag").textContent = "";
  if ($("nav-streak")) $("nav-streak").textContent = "0d";
  if ($("nav-goal")) $("nav-goal").textContent = "0/5h";
  if ($("nav-hours")) $("nav-hours").textContent = "0h";
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

let activePage = pathToPage(location.pathname);

function showPage(page, { push = false, load = true } = {}) {
  if (page === "signout" || page === "logout") {
    doLogout();
    return;
  }
  if (!KNOWN.includes(page)) page = "landing";
  // Only bounce after /v1/auth/me has resolved. Cookie sessions often have no
  // localStorage token — redirecting earlier sent /home → / and left data blank.
  if (PROTECTED.has(page) && meLoaded && !isSignedIn() && !pendingAuthCode()) {
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
  if (page === "docs") mountDocs("docs-page-mount");
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
  e.preventDefault();
  navigate(href);
});
window.addEventListener("popstate", () => showPage(pathToPage(location.pathname), { push: false }));

showPage(activePage, { push: false });
hydrateNavIcons();

const theme = localStorage.getItem("st_theme") || "lime";
document.documentElement.setAttribute("data-theme", theme);

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

if ($("theme-sel")) {
  $("theme-sel").value = theme;
  $("theme-sel").addEventListener("change", (e) => {
    localStorage.setItem("st_theme", e.target.value);
    document.documentElement.setAttribute("data-theme", e.target.value);
  });
}

if (activePage === "signout" || activePage === "logout") {
  showPage(activePage);
}

function oauthMsg(code) {
  return ({
    claim_handle: "Signed in with Slack. Grab a handle next.",
    slack_linked: "Signed in with Slack.",
    github_linked: "GitHub connected.",
    github_failed: "GitHub sign-in didn’t work. Try again?",
    slack_failed: "Slack sign-in didn’t work. Try again?",
    bad_state: "That sign-in link expired. Start again from Slack.",
    sign_in_with_slack_first: "Sign in with Slack first.",
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
  topAuth.href = href;
  topAuth.title = title;
  topAuth.setAttribute("aria-label", title);
  topAuth.classList.toggle("signed", !!url || !!handle);
  topAuth.innerHTML = "";
  if (url) topAuth.appendChild(el("img", { src: url, alt: handle || "profile" }));
  else if (handle) topAuth.appendChild(el("span", { className: "top-user-fallback", text: String(handle).slice(0, 1).toUpperCase() }));
  else topAuth.appendChild(el("span", { className: "top-user-fallback", text: "in" }));
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
    paintWhoAction("sign in", "/login", "door-enter");
    if ($("acct-state")) $("acct-state").textContent = new URLSearchParams(location.search).get("signed_out") ? "You're signed out." : "Sign in to manage your account.";
    if ($("slack-login-box")) $("slack-login-box").hidden = false;
    if ($("claim-box")) $("claim-box").hidden = true;
    if ($("settings-main")) $("settings-main").hidden = true;
    if ($("signout-form")) $("signout-form").style.display = "none";
    daemonLinkInfo = null;
  }
  paintNavAuth();
  if (PROTECTED.has(activePage) && meLoaded && !isSignedIn() && !pendingAuthCode()) {
    navigate("/");
    return;
  }
  paintAuthChecks();
  fillAttachProjects();
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
  else status.textContent = signed ? "You're signed in." : "Continue with Slack to get started.";

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
  if ($("login-slack-hint") && !authStatus.slack_ready) $("login-slack-hint").textContent = "Slack OAuth is not configured on this server.";
  const builders = document.querySelector('[data-top="builders"], [data-nav="builders"]');
  if (builders) builders.hidden = !!(p.handle && p.show_leaderboards === false);
}

let settingsFilled = false;
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

let apiKeyPlain = "";
let daemonLinkInfo = null;

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
  const show = needKey || needDaemon;
  let dismissed = false;
  try { dismissed = sessionStorage.getItem("st_daemon_banner_dismissed") === "1"; } catch (_) {}

  const title = needKey
    ? "Generate an API key & install the daemon"
    : "Install and link the desktop daemon";
  const msg = needKey
    ? "Your dashboard stays empty until a machine is linked. Open Settings → Daemon for install steps."
    : "API key is ready, but nothing has synced yet. Paste the key into the tray and start tracking a project.";

  const global = $("daemon-banner");
  if (global) {
    global.hidden = !(show && !dismissed && activePage !== "landing" && activePage !== "login");
    if (!global.hidden) {
      if ($("daemon-banner-title")) $("daemon-banner-title").textContent = title;
      if ($("daemon-banner-text")) $("daemon-banner-text").textContent = msg;
      const cta = $("daemon-banner-cta");
      if (cta) {
        cta.textContent = needKey ? "open settings" : "install steps";
        cta.setAttribute("href", "/settings");
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
  if (!isSignedIn()) {
    $("api-key-status").textContent = "sign in to manage your API key";
    if ($("api-key-reveal-row")) $("api-key-reveal-row").hidden = true;
    if ($("api-key-hint")) $("api-key-hint").hidden = true;
    daemonLinkInfo = null;
    paintDaemonBanners();
    return;
  }
  const [s, r] = await j("/v1/auth/api-key");
  if (s !== 200) {
    $("api-key-status").textContent = r.error || "could not load API key status";
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
  paintDaemonBanners();
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

async function loadMe(force = false) {
  if (meLoaded && !force && meProfile) return;
  // Always hit /v1/auth/me — cookie session works even with empty localStorage.
  // Stale bearer in localStorage must not mask a valid st_session cookie.
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
    return;
  }
  if (s === 200) meProfile = r;
  else if (!meProfile) meProfile = null;
  meLoaded = true;
}

async function loadAuthStatus(force = false) {
  if (authStatusLoaded && !force && authStatus) return;
  const [as, ar] = await j("/v1/auth/status");
  if (as === 200) authStatus = ar;
  authStatusLoaded = true;
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
    placeholder: "/home/you/code/my-project",
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
    placeholder: "/home/you/code/my-project",
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
      box.replaceChildren(empty(r?.error || "Could not load leaderboard"));
      if ($("lb-meta")) $("lb-meta").textContent = "";
      return;
    }
    if ($("lb-meta")) $("lb-meta").textContent = `${r.label || lbPeriod} · ${r.total || 0} builders`;
    let entries = r.entries || [];
    const q = lbQuery.trim().toLowerCase();
    if (q) entries = entries.filter((e) => [e.handle, e.github_login, e.active_project, e.slack_name].filter(Boolean).join(" ").toLowerCase().includes(q));
    if (!entries.length) { box.replaceChildren(empty("No activity in this period.")); return; }
    const max = entries[0].weighted_hours || 1;
    box.replaceChildren(...entries.map((e) => {
      const flag = flagEmoji(e.country);
      const nameKids = [];
      if (flag) nameKids.push(el("span", { className: "lb-flag", text: flag, title: e.country || "" }));
      nameKids.push(document.createTextNode("@" + e.handle));
      return el("div", { className: "lb-row" }, [
        el("div", { className: `lb-rank ${e.rank <= 3 ? "top" : ""}`, text: "#" + e.rank }),
        avatar(e.slack_avatar, e.handle),
        el("div", { className: "lb-user" }, [
          el("b", {}, nameKids),
          el("span", { className: "sub", text: [
            e.active_project ? "/" + String(e.active_project).split("/").pop() : null,
            e.streak ? `${e.streak}d streak` : null,
          ].filter(Boolean).join(" · ") || " " }),
          el("div", { className: "lb-bar" }, [el("i", { style: `width:${Math.max(4, (e.weighted_hours / max) * 100)}%` })]),
        ]),
        el("div", { className: "lb-hours", text: fmt(e.weighted_hours) + "h" }),
      ]);
    }));
  } catch (err) {
    console.error("renderLeaderboard failed", err);
    box.replaceChildren(empty("Could not load leaderboard"));
  }
}

async function loadReviewQueue() {
  const box = $("review-list"); if (!box) return;
  if (!isStaff()) {
    box.innerHTML = '<p class="ui-empty">staff only — ask an admin for reviewer access</p>';
    return;
  }
  try {
    const [ss, sr] = await j("/v1/submissions");
    if (ss !== 200) {
      box.innerHTML = `<p class="ui-empty">${esc(sr?.error || "could not load queue")}</p>`;
      return;
    }
    const subs = (sr.submissions || []).filter((s) => s.status === "pending");
    if (!subs.length) {
      box.innerHTML = '<p class="ui-empty">Nothing pending review.</p>';
      return;
    }
    box.innerHTML = subs.map((s) => {
      const hours = fmt(s.claimed_weighted_hours);
      const flags = (s.flags || []).length ? `<span class="tag bad">${esc((s.flags || []).slice(0, 2).join(", "))}</span>` : "";
      const actions = canReview()
        ? `<div class="admin-user actions" style="margin-top:8px">
            <button type="button" class="ui-btn" data-review="${esc(s.id)}" data-hours="${esc(String(s.claimed_weighted_secs || 0))}" data-action="approve">approve</button>
            <button type="button" class="ui-btn ui-btn-ghost" data-review="${esc(s.id)}" data-hours="${esc(String((s.claimed_weighted_secs || 0) * 0.5))}" data-action="deflate">deflate 50%</button>
            <button type="button" class="ui-btn ui-btn-danger" data-review="${esc(s.id)}" data-hours="0" data-action="reject">reject</button>
          </div>`
        : `<p class="sub">viewer — no review actions</p>`;
      return `<div class="event" data-sub="${esc(s.id)}">
        <b>${esc(s.project_id)}</b> <span class="tag">${esc(s.status)}</span> ${flags}
        <span class="sub"> ${hours}h · @${esc(s.user_id || "")}</span>
        ${actions}
      </div>`;
    }).join("");
  } catch (err) {
    console.error("loadReviewQueue failed", err);
    box.innerHTML = '<p class="ui-empty">could not load queue</p>';
  }
}

async function submitReview(id, approvedSecs, reason) {
  const [s, r] = await j(`/v1/submissions/${id}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved_weighted_secs: Number(approvedSecs), reason_code: reason }),
  });
  if (s !== 200) { toast(r.error || "review failed", { kind: "error" }); return; }
  toast(`marked ${r.status}`, { kind: "ok" });
  await loadReviewQueue();
}

async function loadAdminPage() {
  const kpi = $("admin-kpi");
  const usersBox = $("admin-users");
  const auditBox = $("admin-audit");
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
        ["pending", ov.pending_reviews || 0],
        ["users", ov.users || 0],
        ["green", trust.green || 0],
        ["yellow", trust.yellow || 0],
        ["red", trust.red || 0],
      ].map(([l, n]) => `<div class="kpi"><div class="n tabular">${esc(String(n))}</div><div class="l">${esc(l)}</div></div>`).join("");
    }
  }
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
// Real telemetry by default. Never restore sample mode from a prior tab session —
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
    { label: "VS Code", hours: 31.0, pct: 73 },
    { label: "Neovim", hours: 8.4, pct: 20 },
    { label: "Cursor", hours: 3.2, pct: 7 },
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
 * Konami OFF ends here with fakeStatsMode=false — that is the working real-data path.
 * Boot and every navigation must use this same function.
 */
async function loadActivePageData() {
  booted = true;
  softRefreshQueued = false;
  try {
    if (activePage === "home") {
      await loadAnalytics();
      if (!fakeStatsMode && isSignedIn()) await drawFriction();
    } else if (activePage === "projects") {
      await loadProjectsView();
    } else if (activePage === "builders") {
      await renderLeaderboard();
    } else if (activePage === "review") {
      await loadReviewQueue();
    } else if (activePage === "admin") {
      await loadAdminPage();
    } else if (activePage === "docs") {
      mountDocs("docs-page-mount");
    }
  } catch (err) {
    console.error("loadActivePageData failed", err);
  }
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

function renderKpis(a) {
  const box = $("kpi-strip"); if (!box) return;
  const goal = Number((meProfile && meProfile.weekly_goal_hours != null) ? meProfile.weekly_goal_hours : 5);
  const week = Number((meProfile && meProfile.weighted_week_hours) || a.week_hours || 0);
  const pct = goal > 0 ? Math.min(100, Math.round((week / goal) * 100)) : 0;
  box.replaceChildren(
    kpiCard("timer", "Time", fmtH(a.total_hours)),
    kpiCard("trophy", "Weekly goal", `${week.toFixed(1)}h / ${goal}h (${pct}%)`),
    kpiCard("briefcase", "Project", a.top_project ? a.top_project.label : "none yet"),
    kpiCard("code", "Language", a.top_language ? a.top_language.label : "none yet"),
    kpiCard("laptop", "OS", a.top_os ? a.top_os.label : "none yet"),
    kpiCard("terminal", "Editor", a.top_editor ? a.top_editor.label : "none yet"),
  );
}

function compactEmpty(msg) {
  return el("p", { className: "ui-empty compact", text: msg });
}

function renderRanked(box, items, emptyMsg) {
  if (!box) return;
  if (!items?.length) { box.replaceChildren(compactEmpty(emptyMsg)); return; }
  const max = items[0].hours || 1;
  box.replaceChildren(...items.slice(0, 8).map((it, i) => el("div", { className: "rank-row" }, [
    el("div", { className: "idx", text: String(i + 1).padStart(2, "0") }),
    el("div", { className: "rank-main" }, [
      el("b", { text: it.label }),
      el("div", { className: "bar" }, [el("i", { style: `width:${Math.max(4, (it.hours / max) * 100)}%` })]),
    ]),
    el("b", { className: "rank-h", text: fmtH(it.hours) }),
  ])));
}

function renderSeg(box, items, emptyMsg) {
  if (!box) return;
  if (!items?.length) { box.replaceChildren(compactEmpty(emptyMsg)); return; }
  box.replaceChildren(...items.slice(0, 6).map((it) => el("div", { className: "seg" }, [
    el("div", { className: "seg-top" }, [
      el("b", { text: it.label }),
      el("span", { className: "seg-meta", text: `${fmtH(it.hours)} · ${it.pct || 0}%` }),
    ]),
    el("div", { className: "track" }, [el("i", { style: `width:${Math.max(3, it.pct || 0)}%` })]),
  ])));
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
  box.replaceChildren(...rows.slice(0, 24).map((b) => el("div", { className: "rank-row" }, [
    el("div", { className: "idx", text: (b.day || "").slice(5) || "—" }),
    el("div", { className: "rank-main" }, [
      el("b", { text: b.project_label || b.project_id || "project" }),
      el("div", { className: "sub", text: [b.state_label || b.state, b.editor, b.language, b.os].filter(Boolean).join(" · ") }),
    ]),
    el("b", { className: "rank-h", text: fmtH(b.weighted_hours) }),
  ])));
}

function paintHomeAnalytics(a) {
  analytics = a;
  try {
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
  let [s, a] = await j("/v1/me/analytics?" + analyticsQS());
  // Sticky filters can zero analytics while streak still looks fine — recover once.
  let empty = s === 200 && a && analyticsHours(a) === 0;
  if (empty && (profileHasActivity() || !filtersAreDefault())) {
    resetDashboardFilters({ range: filters.range === "today" ? "7d" : (filters.range || "7d") });
    if (filtersAreDefault() && filters.range !== "all" && analyticsHours(a) === 0) {
      // still empty on defaults — try all-time once
    }
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
    if ($("kpi-strip")) {
      $("kpi-strip").replaceChildren(compactEmpty(
        s === 0 ? "could not reach server" : s === 401 ? "sign in to see your hours" : "could not load analytics"
      ));
    }
    if ($("recent-buckets")) $("recent-buckets").replaceChildren(compactEmpty("could not load buckets"));
    if ($("rank-projects")) $("rank-projects").replaceChildren(compactEmpty(s === 401 ? "sign in to see projects" : "could not load projects"));
    console.warn("loadAnalytics failed", s, a);
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
    const [ps, pr] = await j("/v1/projects");
    if (ps === 401) {
      console.warn("projects 401 — session missing");
      renderProjectCards([], null);
      return;
    }
    if (ps !== 200) {
      console.warn("projects failed", ps, pr);
      if (grid) {
        grid.replaceChildren(el("div", { className: "ui-panel" }, [
          empty(pr.error || "Could not load projects"),
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
    if (grid) {
      grid.replaceChildren(el("div", { className: "ui-panel" }, [
        empty("Could not load projects"),
      ]));
    }
  }
}

async function drawFriction() {
  if (!$("fr-chart")) return;
  if (!selected) { $("fr-chart").innerHTML = '<p class="ui-empty compact">nothing here yet</p>'; return; }
  const [s, fr] = await j("/v1/projects/" + encodeURIComponent(selected) + "/friction");
  if (s !== 200 || !fr || !Array.isArray(fr.timeline) || !fr.timeline.length) {
    $("fr-chart").innerHTML = '<p class="ui-empty compact">nothing here yet</p>';
    return;
  }
  const w = Math.max(560, fr.timeline.length * 52), h = 120, bw = 32;
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--bar").trim() || "#d7ff6a";
  const dump = getComputedStyle(document.documentElement).getPropertyValue("--dump").trim() || "#57534c";
  $("fr-chart").innerHTML = `<svg width="100%" viewBox="0 0 ${w} ${h + 20}">` + fr.timeline.map((t, i) => {
    const bh = Math.max(3, (Number(t.friction) || 0) * 95);
    return `<rect x="${i * 52 + 8}" y="${h - bh}" width="${bw}" height="${bh}" rx="4" fill="${t.state === "OPEN_LOOP_AGENT_DUMP" ? dump : accent}"></rect>`;
  }).join("") + "</svg>";
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
      primary.append(icon("slack", { size: 16 }), document.createTextNode("continue with slack"));
    }
  }
  if (secondary) {
    secondary.href = "/docs";
    secondary.innerHTML = "";
    secondary.append(icon("docs", { size: 16 }), document.createTextNode("read the docs"));
  }
}

async function refresh({ forceAuth = false } = {}) {
  await loadAuthStatus(forceAuth);
  await loadMe(forceAuth);
  paintAccount();

  booted = true;
  if (PROTECTED.has(activePage) && meLoaded && !isSignedIn() && !pendingAuthCode()) {
    showPage("landing", { push: true, load: false });
  }

  const [os] = await j("/v1/overview");
  const [ps, pr] = await j("/v1/projects");
  if ($("live-txt")) {
    if (os !== 200) { $("live-txt").textContent = "●"; $("live-txt").style.color = "var(--danger)"; }
    else { $("live-txt").textContent = "●"; $("live-txt").style.color = "var(--accent)"; }
  }

  // Never wipe a good cache with an auth failure (looks like projects were deleted)
  if (ps === 200) {
    projectsCache = pr.projects || [];
    window._projects = projectsCache;
  }
  const projs = projectsCache;
  fillAttachProjects();
  const withData = projs.filter((p) => p.buckets > 0);
  if (!selected && withData.length) selected = withData[0].id;
  if ($("proj-sel")) {
    $("proj-sel").innerHTML = withData.length
      ? withData.map((p) => `<option value="${esc(p.id)}" ${p.id === selected ? "selected" : ""}>${esc(p.name || p.id)}</option>`).join("")
      : '<option value="">no projects yet</option>';
  }

  // Real data for the active page — same function Konami OFF uses
  await loadActivePageData();
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
  if (!(authStatus && authStatus.slack_ready)) { toast("Slack OAuth not configured", { kind: "error" }); return; }
  location.href = "/v1/auth/slack/start";
});
if ($("login-slack")) $("login-slack").addEventListener("click", () => {
  if (!(authStatus && authStatus.slack_ready)) { toast("Slack OAuth not configured", { kind: "error" }); return; }
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

function openDaemonSettingsTab() {
  if (activePage !== "settings") navigate("/settings");
  const tab = document.querySelector('[data-settings-tab="daemon"]');
  if (tab) tab.click();
  const panel = $("settings-daemon-panel");
  if (panel) setTimeout(() => panel.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}
if ($("daemon-banner-settings-cta")) {
  $("daemon-banner-settings-cta").addEventListener("click", (e) => {
    e.preventDefault();
    openDaemonSettingsTab();
  });
}
if ($("daemon-banner-cta")) {
  $("daemon-banner-cta").addEventListener("click", (e) => {
    e.preventDefault();
    openDaemonSettingsTab();
  });
}
if ($("daemon-banner-dismiss")) {
  $("daemon-banner-dismiss").addEventListener("click", () => {
    try { sessionStorage.setItem("st_daemon_banner_dismissed", "1"); } catch (_) {}
    if ($("daemon-banner")) $("daemon-banner").hidden = true;
  });
}

document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    const id = tab.dataset.settingsTab;
    document.querySelectorAll("[data-settings-tab]").forEach((t) => t.classList.toggle("on", t === tab));
    document.querySelectorAll(".settings-panel").forEach((p) => {
      const on = p.id === "settings-" + id;
      p.classList.toggle("on", on);
      p.hidden = !on;
    });
    if (id === "docs") mountDocs("settings-docs-mount");
    hydrateNavIcons($("settings-main") || document);
  });
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
if ($("btn-delete-account")) $("btn-delete-account").addEventListener("click", async () => {
  const confirm = ($("delete-confirm").value || "").trim();
  if (!meProfile?.handle || confirm !== meProfile.handle) {
    toast("Type your handle to confirm", { kind: "error" }); return;
  }
  const [s, r] = await j("/v1/me/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm }),
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
  if (!confirm("Revoke the API key? The tray will stop authenticating until you set a new one.")) return;
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

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-review]");
  if (!btn) return;
  e.preventDefault();
  const id = btn.getAttribute("data-review");
  const hours = btn.getAttribute("data-hours");
  const action = btn.getAttribute("data-action");
  const reason = action === "approve" ? "NONE"
    : action === "reject" ? "IDLE_INFLATION"
    : "NO_TEST_ITERATION";
  submitReview(id, hours, reason);
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

(async () => {
  // Exchange OAuth code before any auth-gated redirect can strip ?auth=
  await consumeAuthCode();
  await refresh({ forceAuth: true });
  // refresh() already awaited loadActivePageData(). If we landed on login/hero while
  // signed in, move to dashboard and load real data again (Konami-OFF path).
  if (isSignedIn() && (activePage === "login" || activePage === "landing")) {
    showPage("home", { push: true, load: false });
    await loadActivePageData();
  } else if (PROTECTED.has(activePage) && meLoaded && !isSignedIn()) {
    showPage("landing", { push: true, load: false });
  } else {
    // Ensure real data once more after auth (Konami-OFF path) even if refresh raced
    await loadActivePageData();
  }
  setInterval(() => refresh({ forceAuth: false }), 15000);
})();
