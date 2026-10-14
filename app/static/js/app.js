import { api, el, setCsrf, setUnauthorizedHandler, showStatus } from "./api.js";

/* ---------- theme (localStorage holds ONLY visual preference) ---------- */
const THEME_KEY = "dts-theme";
function applyTheme(t) {
  document.documentElement.dataset.theme = t === "dark" ? "dark" : "light";
  const sun = document.getElementById("theme-icon-sun");
  const moon = document.getElementById("theme-icon-moon");
  const btn = document.getElementById("theme-toggle");
  const dark = document.documentElement.dataset.theme === "dark";
  if (sun) sun.hidden = dark;
  if (moon) moon.hidden = !dark;
  if (btn) btn.setAttribute("aria-pressed", String(dark));
}
function initTheme() {
  let t = "light";
  try { t = localStorage.getItem(THEME_KEY) || "light"; } catch { t = "light"; }
  applyTheme(t);
}
document.getElementById("theme-toggle")?.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, next); } catch {}
  applyTheme(next);
});
initTheme();

/* ---------- shell wiring ---------- */
const app = document.getElementById("app");
const nav = document.getElementById("nav");
let me = null;

function toast(msg, kind = "") {
  const box = document.getElementById("toasts");
  if (!box) { showStatus(msg, kind); return; }
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.setAttribute("role", kind === "err" ? "alert" : "status");
  const s = document.createElement("span");
  s.textContent = msg;
  const x = document.createElement("button");
  x.type = "button"; x.textContent = "×"; x.setAttribute("aria-label", "Dismiss");
  x.addEventListener("click", () => t.remove());
  t.append(s, x);
  box.appendChild(t);
  showStatus(msg, kind === "err" ? "error" : kind === "ok" ? "success" : "");
  setTimeout(() => t.remove(), 5200);
}

function confirmDialog(message, confirmLabel = "Confirm") {
  return new Promise((resolve) => {
    const back = document.getElementById("modal-backdrop");
    const desc = document.getElementById("modal-desc");
    const ok = document.getElementById("modal-confirm");
    const cancel = document.getElementById("modal-cancel");
    desc.textContent = message;
    ok.textContent = confirmLabel;
    back.hidden = false;
    const done = (v) => { back.hidden = true; ok.onclick = null; cancel.onclick = null; back.onclick = null; document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") done(false); };
    ok.onclick = () => done(true);
    cancel.onclick = () => done(false);
    back.onclick = (e) => { if (e.target === back) done(false); };
    document.addEventListener("keydown", onKey);
    cancel.focus();
  });
}

function setCrumb(page, proj = "") {
  const cp = document.getElementById("crumb-page");
  const cj = document.getElementById("crumb-proj");
  const sep = document.getElementById("crumb-proj-sep");
  if (cp) cp.textContent = page;
  if (cj) cj.textContent = proj || "";
  if (sep) sep.hidden = !proj;
}
function setActiveNav(route) {
  nav?.querySelectorAll("a[data-route]").forEach((a) => {
    a.classList.toggle("active", a.dataset.route === route);
    if (a.dataset.route === route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}
function initialsOf(u) {
  const s = (u.display_name || u.username || "?").trim();
  const parts = s.split(/\s+/);
  const ini = (parts[0]?.[0] || "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "");
  return ini.toUpperCase();
}
function syncUserChrome() {
  const su = document.getElementById("side-user");
  const search = document.getElementById("top-search-form");
  const prof = document.getElementById("profile-wrap");
  const side = document.getElementById("sidebar");
  const menu = document.getElementById("menu-btn");
  if (!me) {
    if (su) su.hidden = true;
    if (search) search.hidden = true;
    if (prof) prof.hidden = true;
    if (nav) nav.hidden = true;
    if (side) side.hidden = true;
    if (menu) menu.hidden = true;
    return;
  }
  if (su) su.hidden = false;
  if (search) search.hidden = false;
  if (prof) prof.hidden = false;
  if (nav) nav.hidden = false;
  if (side) side.hidden = false;
  if (menu) menu.hidden = false;
  const ini = initialsOf(me);
  const a1 = document.getElementById("user-initials");
  const a2 = document.getElementById("profile-initials");
  const nm = document.getElementById("user-name");
  const rl = document.getElementById("user-role");
  if (a1) a1.textContent = ini;
  if (a2) a2.textContent = ini;
  if (nm) nm.textContent = me.display_name || me.username;
  if (rl) rl.textContent = me.global_role === "admin" ? "Administrator" : "Member";
}

setUnauthorizedHandler(() => {
  me = null; setCsrf(null);
  if (nav) nav.hidden = true;
  syncUserChrome();
  if (!location.hash.startsWith("#/login")) location.hash = "#/login";
});

async function doLogout() {
  if (!(await confirmDialog("Log out of this session?", "Logout"))) return;
  try { await api("/api/v1/auth/logout", { method: "POST" }); } catch {}
  me = null; setCsrf(null);
  if (nav) nav.hidden = true;
  syncUserChrome();
  closeMobileNav();
  location.hash = "#/login";
}
document.getElementById("logout-btn")?.addEventListener("click", doLogout);
document.getElementById("profile-logout")?.addEventListener("click", () => {
  document.getElementById("profile-menu").hidden = true;
  doLogout();
});
document.getElementById("profile-btn")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const m = document.getElementById("profile-menu");
  m.hidden = !m.hidden;
  document.getElementById("profile-btn").setAttribute("aria-expanded", String(!m.hidden));
});
document.addEventListener("click", (e) => {
  const m = document.getElementById("profile-menu");
  if (m && !m.hidden && !e.target.closest(".profile-wrap")) m.hidden = true;
});
function closeMobileNav() { document.body.classList.remove("nav-open"); document.getElementById("scrim").hidden = true; }
document.getElementById("menu-btn")?.addEventListener("click", () => {
  const open = document.body.classList.toggle("nav-open");
  document.getElementById("scrim").hidden = !open;
  document.getElementById("menu-btn").setAttribute("aria-expanded", String(open));
});
document.getElementById("scrim")?.addEventListener("click", closeMobileNav);
document.getElementById("collapse-btn")?.addEventListener("click", () => {
  const c = document.body.classList.toggle("side-collapsed");
  document.getElementById("collapse-btn").setAttribute("aria-expanded", String(!c));
});
nav?.addEventListener("click", (e) => { if (e.target.closest("a") && window.innerWidth <= 900) closeMobileNav(); });

document.getElementById("top-search-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = document.getElementById("top-search").value.trim();
  if (!q) return;
  location.hash = `#/search/${encodeURIComponent(q)}`;
});

/* ---------- shared UI primitives (safe: user text via textContent only) ---------- */
function requireAuth() {
  if (!me) { location.hash = "#/login"; return false; }
  return true;
}
function clearApp() { app.textContent = ""; }
function skeletonList(n = 4) {
  const w = el("div", null);
  for (let i = 0; i < n; i++) {
    const s = el("div", null, { class: "card skel" });
    s.style.minHeight = "58px";
    w.appendChild(s);
  }
  return w;
}
function emptyState(title, desc, actionHref = null, actionLabel = null) {
  const d = el("div", null, { class: "empty" });
  d.appendChild(el("h3", title));
  d.appendChild(el("p", desc, { class: "muted" }));
  if (actionHref && actionLabel) {
    const a = document.createElement("a");
    a.href = actionHref; a.className = "btn"; a.textContent = actionLabel;
    d.appendChild(a);
  }
  return d;
}
function errorBox(msg) {
  const d = el("div", null, { class: "card" });
  d.appendChild(el("p", msg, { class: "error" }));
  return d;
}
function badge(text) {
  const cls = String(text).replace(/\s/g, "");
  return el("span", text, { class: `badge ${cls}` });
}
function pageHead(title, desc, actions = []) {
  const w = el("div", null, { class: "page-head" });
  const t = el("div", null);
  t.appendChild(el("h1", title));
  if (desc) t.appendChild(el("p", desc, { class: "muted" }));
  w.appendChild(t);
  if (actions.length) {
    const aw = el("div", null, { class: "form-actions" });
    actions.forEach((a) => aw.appendChild(a));
    w.appendChild(aw);
  }
  return w;
}
function barChart(entries, colorClass = "") {
  const wrap = el("div", null, { class: "chart" });
  wrap.setAttribute("role", "img");
  const total = entries.reduce((s, [, v]) => s + v, 0) || 1;
  const labels = entries.map(([k, v]) => `${k}: ${v}`).join(", ");
  wrap.setAttribute("aria-label", `Distribution — ${labels}`);
  for (const [k, v] of entries) {
    const row = el("div", null, { class: "bar-row" });
    row.appendChild(el("span", k));
    const track = el("div", null, { class: "bar-track" });
    const fill = el("div", null, { class: `bar-fill ${colorClass}` });
    fill.style.width = `${Math.max(2, Math.round((v / total) * 100))}%`;
    track.appendChild(fill);
    row.append(track, el("strong", String(v)));
    wrap.appendChild(row);
  }
  if (!entries.length) wrap.appendChild(el("p", "No data yet.", { class: "muted" }));
  return wrap;
}
function fmtDate(v) {
  if (!v) return "–";
  try { return new Date(v).toLocaleString(); } catch { return String(v); }
}
function btn(label, kind = "") {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = label; b.className = kind ? `btn ${kind}` : "btn";
  return b;
}
function passwordField(input) {
  const wrap = el("div", null, { class: "pw-wrap" });
  const showBtn = document.createElement("button");
  showBtn.type = "button";
  showBtn.className = "pw-toggle";
  showBtn.setAttribute("aria-label", "Show password");
  showBtn.setAttribute("aria-pressed", "false");
  showBtn.title = "Show password";
  const NS = "http://www.w3.org/2000/svg";
  const mkSvg = (off) => {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("width", "18"); s.setAttribute("height", "18");
    s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "2"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true");
    if (!off) {
      const p = document.createElementNS(NS, "path"); p.setAttribute("d", "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z");
      const c = document.createElementNS(NS, "circle"); c.setAttribute("cx", "12"); c.setAttribute("cy", "12"); c.setAttribute("r", "3");
      s.append(p, c);
    } else {
      const p1 = document.createElementNS(NS, "path"); p1.setAttribute("d", "M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94");
      const p2 = document.createElementNS(NS, "path"); p2.setAttribute("d", "M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19");
      const p3 = document.createElementNS(NS, "path"); p3.setAttribute("d", "M14.12 14.12a3 3 0 1 1-4.24-4.24");
      const l = document.createElementNS(NS, "line"); l.setAttribute("x1", "1"); l.setAttribute("y1", "1"); l.setAttribute("x2", "23"); l.setAttribute("y2", "23");
      s.append(p1, p2, p3, l);
    }
    return s;
  };
  const eye = mkSvg(false);
  const eyeOff = mkSvg(true);
  eyeOff.style.display = "none";
  showBtn.append(eye, eyeOff);
  showBtn.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    eye.style.display = show ? "none" : "";
    eyeOff.style.display = show ? "" : "none";
    showBtn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    showBtn.setAttribute("aria-pressed", String(show));
    showBtn.title = show ? "Hide password" : "Show password";
    input.focus();
  });
  wrap.append(input, showBtn);
  return wrap;
}

async function refreshMe() {
  try {
    const data = await api("/api/v1/auth/me");
    me = data.user; setCsrf(data.csrf_token);
    if (nav) nav.hidden = false;
    const adminLink = document.getElementById("nav-admin");
    const adminSec = document.getElementById("nav-admin-section");
    if (adminLink) adminLink.hidden = me.global_role !== "admin";
    if (adminSec) adminSec.hidden = me.global_role !== "admin";
    syncUserChrome();
    return true;
  } catch { me = null; if (nav) nav.hidden = true; syncUserChrome(); return false; }
}

/* ---------- screens ---------- */
async function screenLogin() {
  setCrumb("Login"); setActiveNav("");
  const _s = document.getElementById("top-search-form");
  const _p = document.getElementById("profile-wrap");
  const _side = document.getElementById("sidebar");
  const _menu = document.getElementById("menu-btn");
  if (_s) _s.hidden = true;
  if (_p) _p.hidden = true;
  if (_side) _side.hidden = true;
  if (_menu) _menu.hidden = true;
  if (nav) nav.hidden = true;
  closeMobileNav();
  clearApp();
  const wrap = el("div", null, { class: "login-wrap" });
  const split = el("div", null, { class: "login-split" });
  const side = el("aside", null, { class: "login-side" });
  side.setAttribute("aria-hidden", "false");
  const decor = el("div", null, { class: "login-decor", "aria-hidden": "true" });
  for (const cls of ["login-grid", "orb-a", "orb-b", "orb-c", "cube-a", "cube-b"]) decor.appendChild(el("span", null, { class: cls }));
  side.appendChild(decor);
  const sBrand = el("div", null, { class: "login-brand" });
  const mark = el("span", "◉", { class: "brand-mark" });
  mark.setAttribute("aria-hidden", "true");
  const sBt = el("div", null);
  sBt.appendChild(el("strong", "Defect Tracker"));
  sBt.appendChild(el("p", "Quality, tracked together."));
  sBrand.append(mark, sBt);
  side.appendChild(sBrand);
  side.appendChild(el("h2", "Ship with confidence"));
  side.appendChild(el("p", "Track defects, run QA triage, and collaborate on fixes — in one calm workspace."));
  const pts = document.createElement("ul");
  pts.className = "login-points";
  for (const [t, d] of [["✓", "Capture reproductions with severity & priority"], ["✓", "Triage from Open to Closed with full history"], ["✓", "Comment, attach evidence, and export reports"]]) {
    const li = document.createElement("li");
    li.append(el("span", t, { class: "dot", "aria-hidden": "true" }), el("span", d));
    pts.appendChild(li);
  }
  side.appendChild(pts);
  const main = el("div", null, { class: "login-main" });
  const card = el("div", null, { class: "card login-card" });
  const brand = el("div", null, { class: "login-brand" });
  const mb = el("div", null);
  mb.appendChild(el("h1", "Welcome back"));
  mb.appendChild(el("p", "Sign in to continue to your workspace.", { class: "muted" }));
  brand.appendChild(mb);
  card.appendChild(brand);
  const f = document.createElement("form");
  f.noValidate = false;
  const l1 = el("label", null); l1.htmlFor = "li"; l1.append("Username or email ", (() => { const s = el("span", "*", { class: "req" }); return s; })());
  const i1 = document.createElement("input"); i1.id = "li"; i1.required = true; i1.autocomplete = "username"; i1.placeholder = "admin or admin@example.com";
  const l2 = el("label", null); l2.htmlFor = "lp"; l2.append("Password ", (() => { const s = el("span", "*", { class: "req" }); return s; })());
  const i2 = document.createElement("input"); i2.id = "lp"; i2.type = "password"; i2.required = true; i2.autocomplete = "current-password"; i2.placeholder = "••••••••••••";
  const pwWrap = passwordField(i2);
  const sub = document.createElement("button"); sub.type = "submit"; sub.className = "btn"; sub.style.width = "100%"; sub.style.marginTop = "12px";
  const spin = el("span", null, { class: "spinner" }); spin.hidden = true;
  const lbl = el("span", "Sign in");
  sub.append(spin, lbl);
  const err = el("p", "", { class: "field-err", role: "alert" });
  f.append(l1, i1, l2, pwWrap, sub, err);
  f.addEventListener("submit", async (e) => {
    e.preventDefault(); sub.disabled = true; spin.hidden = false; err.textContent = "";
    try {
      const data = await api("/api/v1/auth/login", { method: "POST", csrf: false, body: { username_or_email: i1.value, password: i2.value } });
      me = data.user; setCsrf(data.csrf_token);
      if (nav) nav.hidden = false;
      const adminLink = document.getElementById("nav-admin");
      const adminSec = document.getElementById("nav-admin-section");
      if (adminLink) adminLink.hidden = me.global_role !== "admin";
      if (adminSec) adminSec.hidden = me.global_role !== "admin";
      syncUserChrome();
      toast(`Welcome, ${me.display_name || me.username}`, "ok");
      location.hash = "#/dashboard";
    } catch (ex) { err.textContent = ex.message; }
    finally { sub.disabled = false; spin.hidden = true; }
  });
  card.appendChild(f);
  card.appendChild(el("p", "Ask an administrator for an account", { class: "muted" }));
  const det = document.createElement("details");
  const sum = el("summary", "Reset password with token");
  const rf = document.createElement("form");
  const rt = document.createElement("input"); rt.placeholder = "Reset token"; rt.required = true; rt.setAttribute("aria-label", "Reset token"); rt.style.marginTop = "8px";
  const np = document.createElement("input"); np.type = "password"; np.placeholder = "New password (12+ chars)"; np.required = true; np.minLength = 12; np.setAttribute("aria-label", "New password");
  const npWrap = passwordField(np);
  const rb = document.createElement("button"); rb.className = "btn secondary"; rb.textContent = "Reset password"; rb.style.marginTop = "8px";
  const re = el("p", "", { class: "field-err", role: "alert" });
  rf.append(rt, npWrap, rb, re);
  rf.addEventListener("submit", async (e) => {
    e.preventDefault(); re.textContent = ""; re.className = "field-err";
    try { await api("/api/v1/auth/reset-password", { method: "POST", csrf: false, body: { token: rt.value, new_password: np.value } }); re.textContent = "Password reset. You can log in now."; re.className = "success"; }
    catch (ex) { re.textContent = ex.message; }
  });
  det.append(sum, rf); card.appendChild(det);
  main.appendChild(card);
  split.append(side, main);
  wrap.appendChild(split);
  app.appendChild(wrap);
}

function statCard(label, value, sub, tint, icon) {
  const c = el("div", null, { class: "card stat-card" });
  const top = el("div", null, { class: "stat-top" });
  const ic = el("span", icon, { class: `stat-ico ${tint}`, "aria-hidden": "true" });
  const k = el("span", label, { class: "stat-sub" });
  top.append(ic, k);
  c.append(top, el("div", String(value), { class: "stat-val" }), el("p", label, { class: "stat-label" }));
  if (sub) c.appendChild(el("p", sub, { class: "stat-sub" }));
  return c;
}

async function screenDashboard() {
  if (!requireAuth()) return;
  setCrumb("Dashboard"); setActiveNav("dashboard");
  clearApp();
  app.appendChild(pageHead("Dashboard", "Live quality overview across projects you can access."));
  const sk = skeletonList(3); app.appendChild(sk);
  try {
    const d = await api("/api/v1/dashboard/summary");
    sk.remove();
    const open = d.by_status["Open"] || 0;
    const prog = d.by_status["In Progress"] || 0;
    const resolved = (d.by_status["Closed"] || 0) + (d.by_status["Fixed"] || 0) + (d.by_status["Ready for Test"] || 0);
    const crit = d.by_severity["Critical"] || 0;
    const g = el("div", null, { class: "grid" });
    g.append(
      statCard("Total", d.total_visible, `${d.assigned_to_me} assigned to me`, "tint-indigo", "◈"),
      statCard("Open", open, "Needs triage", "tint-blue", "○"),
      statCard("In Progress", prog, "Actively being fixed", "tint-violet", "◐"),
      statCard("Resolved / Closed", resolved, "Fixed, ready or closed", "tint-green", "●"),
      statCard("Critical", crit, `${d.overdue} overdue`, "tint-red", "!")
    );
    app.appendChild(g);
    const two = el("div", null, { class: "grid" });
    two.style.gridTemplateColumns = "repeat(auto-fit,minmax(300px,1fr))";
    const cStatus = el("div", null, { class: "card" });
    cStatus.appendChild(el("h2", "Defects by status"));
    const order = ["Open", "In Progress", "Fixed", "Ready for Test", "Reopened", "Closed", "Rejected"];
    const se = order.filter((k) => d.by_status[k]).map((k) => [k, d.by_status[k]]);
    Object.keys(d.by_status).filter((k) => !order.includes(k)).forEach((k) => se.push([k, d.by_status[k]]));
    cStatus.appendChild(barChart(se));
    const leg1 = el("div", null, { class: "legend" });
    leg1.appendChild(el("span", `Total ${d.total_visible}`));
    cStatus.appendChild(leg1);
    const cSev = el("div", null, { class: "card" });
    cSev.appendChild(el("h2", "Severity & priority"));
    cSev.appendChild(el("h3", "Severity"));
    cSev.appendChild(barChart(Object.entries(d.by_severity), "red"));
    cSev.appendChild(el("h3", "Priority"));
    cSev.appendChild(barChart(Object.entries(d.by_priority), "cyan"));
    two.append(cStatus, cSev);
    app.appendChild(two);

    const cols = el("div", null, { class: "grid" });
    cols.style.gridTemplateColumns = "repeat(auto-fit,minmax(300px,1fr))";
    const rec = el("div", null, { class: "card" });
    rec.appendChild(el("h2", "Recent activity"));
    if (!d.recently_updated.length) rec.appendChild(el("p", "Nothing yet. New and updated defects will appear here.", { class: "muted" }));
    else {
      const ul = document.createElement("ul"); ul.className = "timeline";
      for (const b of d.recently_updated.slice(0, 8)) {
        const li = document.createElement("li");
        const dot = el("span", null, { class: "tl-dot", "aria-hidden": "true" });
        const body = el("div", null, { class: "tl-body" });
        const a = document.createElement("a"); a.href = `#/bugs/${b.id}`; a.className = "mono"; a.textContent = b.public_key;
        body.append(a, document.createTextNode(` ${b.title} `), badge(b.status));
        const meta = el("small", `Updated ${fmtDate(b.updated_at)}`);
        body.appendChild(document.createElement("br")); body.appendChild(meta);
        li.append(dot, body); ul.appendChild(li);
      }
      rec.appendChild(ul);
    }
    const mine = el("div", null, { class: "card" });
    mine.appendChild(el("h2", "My assigned defects"));
    let assigned = [];
    try { assigned = await api("/api/v1/dashboard/assigned?page=1&page_size=6"); } catch { assigned = []; }
    if (!assigned.length) mine.appendChild(el("p", "Nothing assigned to you.", { class: "muted" }));
    else {
      const tw = el("div", null, { class: "table-wrap" });
      const t = document.createElement("table");
      const hr = document.createElement("tr");
      ["ID", "Title", "Status", "Priority"].forEach((h) => hr.appendChild(el("th", h)));
      t.appendChild(hr);
      for (const b of assigned) {
        const r = document.createElement("tr");
        const tdK = document.createElement("td"); const a = document.createElement("a"); a.href = `#/bugs/${b.id}`; a.className = "mono"; a.textContent = b.public_key; tdK.appendChild(a);
        const tdT = document.createElement("td"); const ta = document.createElement("a"); ta.href = `#/bugs/${b.id}`; ta.textContent = b.title; tdT.appendChild(ta);
        const tdS = document.createElement("td"); tdS.appendChild(badge(b.status));
        const tdP = document.createElement("td"); tdP.appendChild(badge(b.priority));
        r.append(tdK, tdT, tdS, tdP); t.appendChild(r);
      }
      tw.appendChild(t); mine.appendChild(tw);
      const more = document.createElement("a"); more.href = "#/assigned"; more.textContent = "View all assigned →"; more.style.display = "inline-block"; more.style.marginTop = "8px";
      mine.appendChild(more);
    }
    cols.append(rec, mine);
    app.appendChild(cols);

    const pp = el("div", null, { class: "card" });
    pp.appendChild(el("h2", "Projects"));
    if (!d.per_project.length) pp.appendChild(el("p", "No projects visible.", { class: "muted" }));
    else {
      const pg = el("div", null, { class: "proj-grid" });
      for (const p of d.per_project) {
        const c = el("div", null, { class: "proj-card" });
        const top = el("div", null, { class: "proj-meta" });
        top.appendChild(el("span", p.project_key, { class: "proj-key" }));
        top.appendChild(el("span", `${p.count} defects`));
        c.append(top, el("h3", p.name));
        const a = document.createElement("a"); a.href = `#/projects/${p.project_id}`; a.className = "btn secondary sm"; a.textContent = "Open project";
        c.appendChild(a);
        pg.appendChild(c);
      }
      pp.appendChild(pg);
    }
    app.appendChild(pp);
  } catch (e) { app.appendChild(errorBox(e.message)); }
}

async function fetchProjectsCached() {
  return api("/api/v1/projects?include_archived=true");
}

async function screenProjects() {
  if (!requireAuth()) return;
  setCrumb("Projects"); setActiveNav("projects");
  clearApp();
  const isAdmin = me.global_role === "admin";
  const headBtns = [];
  const refresh = btn("Refresh", "secondary"); refresh.type = "button";
  headBtns.push(refresh);
  app.appendChild(pageHead("Projects", "All workspaces you can access, with live counts.", headBtns));
  if (isAdmin) {
    const create = el("div", null, { class: "card" });
    create.appendChild(el("h2", "Create project"));
    const f = document.createElement("form");
    const grid = el("div", null, { class: "form-grid" });
    const w1 = el("div", null); const l1 = el("label", null); l1.htmlFor = "pk"; l1.textContent = "Project key ";
    l1.appendChild(el("span", "*", { class: "req" }));
    const k = document.createElement("input"); k.id = "pk"; k.placeholder = "PAY"; k.required = true; k.setAttribute("aria-label", "Project key"); k.maxLength = 16;
    w1.append(l1, k);
    const w2 = el("div", null); const l2 = el("label", null); l2.htmlFor = "pn"; l2.textContent = "Name ";
    l2.appendChild(el("span", "*", { class: "req" }));
    const n = document.createElement("input"); n.id = "pn"; n.placeholder = "Payments"; n.required = true; n.setAttribute("aria-label", "Project name");
    w2.append(l2, n);
    grid.append(w1, w2);
    const l3 = el("label", "Description"); l3.htmlFor = "pd";
    const dsc = document.createElement("textarea"); dsc.id = "pd"; dsc.placeholder = "What does this project track?"; dsc.setAttribute("aria-label", "Description");
    const row = el("div", null, { class: "form-actions" });
    const cb = document.createElement("button"); cb.className = "btn"; cb.textContent = "Create project"; cb.type = "submit";
    const ce = el("p", "", { class: "field-err", role: "alert" });
    row.appendChild(cb);
    f.append(grid, l3, dsc, row, ce);
    f.addEventListener("submit", async (e) => {
      e.preventDefault(); cb.disabled = true; ce.textContent = "";
      try { await api("/api/v1/projects", { method: "POST", body: { project_key: k.value, name: n.value, description: dsc.value } }); toast("Project created", "ok"); await load(); f.reset(); }
      catch (ex) { ce.textContent = ex.message; } finally { cb.disabled = false; }
    });
    create.appendChild(f);
    app.appendChild(create);
  }
  const listCard = el("div", null, { class: "card" });
  listCard.appendChild(el("h2", "All projects"));
  const list = el("div", null);
  listCard.appendChild(list);
  app.appendChild(listCard);
  async function load() {
    list.textContent = "";
    list.appendChild(skeletonList(3));
    try {
      const items = await fetchProjectsCached();
      list.textContent = "";
      if (!items.length) { list.appendChild(emptyState("No projects yet", "Ask an administrator to create your first project.")); return; }
      const grid = el("div", null, { class: "proj-grid" });
      for (const p of items) {
        const c = el("div", null, { class: `proj-card${p.status === "archived" ? " is-archived" : ""}` });
        const top = el("div", null, { class: "proj-meta" });
        top.appendChild(el("span", p.project_key, { class: "proj-key" }));
        top.appendChild(badge(p.status === "archived" ? "archived" : (p.my_role || "member")));
        c.append(top, el("h3", p.name));
        c.appendChild(el("p", p.description || "No description.", { class: "muted" }));
        const row = el("div", null, { class: "form-actions" });
        const a = document.createElement("a"); a.href = `#/projects/${p.id}`; a.className = "btn secondary sm"; a.textContent = "Open";
        const b = document.createElement("a"); b.href = `#/projects/${p.id}/bugs`; b.className = "btn ghost sm"; b.textContent = "Defects";
        row.append(a, b);
        c.appendChild(row);
        grid.appendChild(c);
      }
      list.appendChild(grid);
    } catch (e) { list.textContent = ""; list.appendChild(el("p", e.message, { class: "error" })); }
  }
  refresh.addEventListener("click", load);
  await load();
}

async function screenProjectDetail(id) {
  if (!requireAuth()) return;
  setCrumb("Project", `#${id}`); setActiveNav("projects");
  clearApp();
  app.appendChild(skeletonList(3));
  try {
    const p = await api(`/api/v1/projects/${id}`);
    const members = await api(`/api/v1/projects/${id}/members`);
    let bugCount = 0;
    try { const lb = await api(`/api/v1/projects/${id}/bugs?page=1&page_size=1`); bugCount = lb.total; } catch {}
    clearApp();
    setCrumb("Project", `${p.project_key} — ${p.name}`);
    const back = document.createElement("a"); back.href = "#/projects"; back.textContent = "← All projects"; back.className = "btn ghost sm";
    app.appendChild(back);
    app.appendChild(pageHead(`${p.project_key} — ${p.name}`, p.description || "No description."));
    const meta = el("div", null, { class: "card" });
    const kv = document.createElement("dl"); kv.className = "kv";
    const addKv = (k, v, isBadge = false) => {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd");
      if (isBadge) dd.appendChild(badge(v)); else dd.textContent = v;
      kv.append(dt, dd);
    };
    meta.appendChild(kv);
    addKv("Status", p.status, true);
    addKv("Defects", String(bugCount));
    addKv("Members", String(members.length));
    addKv("My role", p.my_role || "–");
    app.appendChild(meta);
    if (me.global_role === "admin" || p.my_role === "manager") {
      const ec = el("div", null, { class: "card" });
      ec.appendChild(el("h2", "Project settings"));
      const f = document.createElement("form");
      const ln = el("label", "Name"); ln.htmlFor = "ep-name";
      const n = document.createElement("input"); n.id = "ep-name"; n.value = p.name;
      const ld = el("label", "Description"); ld.htmlFor = "ep-desc";
      const d = document.createElement("textarea"); d.id = "ep-desc"; d.value = p.description || "";
      const row = el("div", null, { class: "form-actions" });
      const b = document.createElement("button"); b.className = "btn"; b.textContent = "Save changes"; b.type = "submit";
      const arc = btn(p.status === "archived" ? "Restore project" : "Archive project", "secondary"); arc.type = "button";
      const e = el("p", "", { class: "field-err", role: "alert" });
      row.append(b, arc);
      f.append(ln, n, ld, d, row, e);
      f.addEventListener("submit", async (ev) => {
        ev.preventDefault(); b.disabled = true; e.textContent = "";
        try { await api(`/api/v1/projects/${id}`, { method: "PATCH", body: { name: n.value, description: d.value } }); toast("Project saved", "ok"); }
        catch (ex) { e.textContent = ex.message; } finally { b.disabled = false; }
      });
      arc.addEventListener("click", async () => {
        const act = p.status === "archived" ? "restore" : "archive";
        if (!(await confirmDialog(p.status === "archived" ? "Restore this project?" : "Archive this project? Defect edits will be blocked while archived.", act === "restore" ? "Restore" : "Archive"))) return;
        try { await api(`/api/v1/projects/${id}/${act}`, { method: "POST" }); toast("Done", "ok"); location.reload(); }
        catch (ex) { toast(ex.message, "err"); }
      });
      ec.appendChild(f);
      app.appendChild(ec);
    }
    const dc = el("div", null, { class: "card" });
    dc.appendChild(el("h2", "Defects"));
    const row = el("div", null, { class: "form-actions" });
    const va = document.createElement("a"); va.href = `#/projects/${id}/bugs`; va.className = "btn"; va.textContent = "View defects";
    const ca = document.createElement("a"); ca.href = `#/projects/${id}/bugs/new`; ca.className = "btn secondary"; ca.textContent = "Create defect";
    row.append(va, ca); dc.appendChild(row);
    app.appendChild(dc);
    const mc = el("div", null, { class: "card" });
    mc.appendChild(el("h2", "Members"));
    if (!members.length) mc.appendChild(el("p", "No members yet.", { class: "muted" }));
    else {
      const tw = el("div", null, { class: "table-wrap" });
      const t = document.createElement("table");
      const hr = document.createElement("tr");
      ["Member", "Role", "Actions"].forEach((h) => hr.appendChild(el("th", h)));
      t.appendChild(hr);
      for (const m of members) {
        const r = document.createElement("tr");
        const tdU = document.createElement("td");
        const cell = el("div", null, { class: "user-cell" });
        const av = el("span", (m.display_name || m.username || "?").trim().charAt(0).toUpperCase(), { class: "avatar sm", "aria-hidden": "true" });
        const tx = el("div", null);
        tx.appendChild(el("div", m.username, { class: "cell-main" }));
        tx.appendChild(el("div", m.display_name || "", { class: "cell-sub" }));
        cell.append(av, tx); tdU.appendChild(cell);
        const tdR = document.createElement("td"); tdR.appendChild(badge(m.role));
        const td = document.createElement("td");
        if (me.global_role === "admin" || p.my_role === "manager") {
          const sel = document.createElement("select");
          sel.style.maxWidth = "150px";
          for (const ro of ["manager", "developer", "reporter", "viewer"]) { const o = document.createElement("option"); o.value = ro; o.textContent = ro; if (m.role === ro) o.selected = true; sel.appendChild(o); }
          sel.setAttribute("aria-label", `Role for ${m.username}`);
          sel.addEventListener("change", async () => {
            if (!(await confirmDialog(`Change ${m.username} to ${sel.value}?`, "Change role"))) { sel.value = m.role; return; }
            try { await api(`/api/v1/projects/${id}/members/${m.user_id}`, { method: "PATCH", body: { role: sel.value } }); toast("Role updated", "ok"); }
            catch (ex) { toast(ex.message, "err"); sel.value = m.role; }
          });
          const rm = btn("Remove", "danger"); rm.classList.add("sm");
          rm.addEventListener("click", async () => {
            if (!(await confirmDialog(`Remove ${m.username} from this project?`, "Remove"))) return;
            try { await api(`/api/v1/projects/${id}/members/${m.user_id}`, { method: "DELETE" }); toast("Member removed", "ok"); location.reload(); }
            catch (ex) { toast(ex.message, "err"); }
          });
          td.append(sel, rm);
        } else td.textContent = "–";
        r.append(tdU, tdR, td); t.appendChild(r);
      }
      tw.appendChild(t); mc.appendChild(tw);
    }
    if (me.global_role === "admin" || p.my_role === "manager") {
      const f = document.createElement("form");
      f.appendChild(el("h3", "Add member"));
      const grid = el("div", null, { class: "form-grid" });
      const w1 = el("div", null);
      const li = el("label", "Username or email"); li.htmlFor = "am-u";
      const inp = document.createElement("input"); inp.id = "am-u"; inp.placeholder = "username or email"; inp.required = true;
      w1.append(li, inp);
      const w2 = el("div", null);
      const lr = el("label", "Role"); lr.htmlFor = "am-r";
      const sel = document.createElement("select"); sel.id = "am-r";
      for (const ro of ["manager", "developer", "reporter", "viewer"]) { const o = document.createElement("option"); o.value = ro; o.textContent = ro; sel.appendChild(o); }
      w2.append(lr, sel);
      grid.append(w1, w2);
      const b = document.createElement("button"); b.className = "btn"; b.textContent = "Add member"; b.type = "submit";
      const e = el("p", "", { class: "field-err", role: "alert" });
      f.append(grid, b, e);
      f.addEventListener("submit", async (ev) => {
        ev.preventDefault(); b.disabled = true; e.textContent = "";
        try { await api(`/api/v1/projects/${id}/members`, { method: "POST", body: { username_or_email: inp.value, role: sel.value } }); toast("Member added", "ok"); location.reload(); }
        catch (ex) { e.textContent = ex.message; } finally { b.disabled = false; }
      });
      mc.appendChild(f);
    }
    app.appendChild(mc);
  } catch (e) {
    clearApp();
    if (e.status === 404) { setCrumb("Not found"); app.appendChild(emptyState("Project not found", "It may have been removed or you lack access.", "#/projects", "Back to projects")); }
    else app.appendChild(errorBox(e.message));
  }
}

function buildFilterToolbar(state, onApply) {
  const bar = el("div", null, { class: "toolbar" });
  const mkSelect = (label, id, options, val) => {
    const w = el("div", null, { class: "fld" });
    const l = el("label", label); l.htmlFor = id;
    const s = document.createElement("select"); s.id = id;
    for (const [v, t] of options) { const o = document.createElement("option"); o.value = v; o.textContent = t; if (String(v) === String(val)) o.selected = true; s.appendChild(o); }
    w.append(l, s);
    return [w, s];
  };
  const statuses = [["", "Any"], ["Open", "Open"], ["In Progress", "In Progress"], ["Fixed", "Fixed"], ["Ready for Test", "Ready for Test"], ["Reopened", "Reopened"], ["Closed", "Closed"], ["Rejected", "Rejected"]];
  const sevs = [["", "Any"], ["Critical", "Critical"], ["High", "High"], ["Medium", "Medium"], ["Low", "Low"], ["Trivial", "Trivial"]];
  const pris = [["", "Any"], ["P0", "P0"], ["P1", "P1"], ["P2", "P2"], ["P3", "P3"], ["P4", "P4"]];
  const sorts = [["-updated_at", "Updated ↓"], ["updated_at", "Updated ↑"], ["-created_at", "Created ↓"], ["priority", "Priority"], ["severity", "Severity"], ["public_key", "Key"]];
  const [wS, sS] = mkSelect("Status", "f-status", statuses, state.status);
  const [wV, sV] = mkSelect("Severity", "f-sev", sevs, state.severity);
  const [wP, sP] = mkSelect("Priority", "f-pri", pris, state.priority);
  const wQ = el("div", null, { class: "fld" });
  const lQ = el("label", "Search"); lQ.htmlFor = "f-q";
  const iQ = document.createElement("input"); iQ.id = "f-q"; iQ.placeholder = "KEY-123, title, text…"; iQ.value = state.q || "";
  iQ.setAttribute("type", "search");
  wQ.append(lQ, iQ);
  const [wO, sO] = mkSelect("Sort", "f-sort", sorts, state.sort);
  const acts = el("div", null, { class: "actions" });
  const apply = btn("Apply filters"); apply.type = "button";
  const clear = btn("Clear", "secondary"); clear.type = "button";
  acts.append(apply, clear);
  bar.append(wS, wV, wP, wQ, wO, acts);
  const chips = el("div", null, { class: "chip-row" });
  const refreshChips = () => {
    chips.textContent = "";
    const active = [["status", sS.value], ["severity", sV.value], ["priority", sP.value], ["search", iQ.value.trim()]];
    let n = 0;
    for (const [k, v] of active) {
      if (!v) continue; n++;
      const c = el("span", `${k}: ${v}`, { class: "chip" });
      chips.appendChild(c);
    }
    if (!n) chips.appendChild(el("span", "No filters — showing everything.", { class: "muted" }));
  };
  apply.addEventListener("click", () => { refreshChips(); onApply({ status: sS.value, severity: sV.value, priority: sP.value, q: iQ.value.trim(), sort: sO.value }); });
  clear.addEventListener("click", () => { sS.value = ""; sV.value = ""; sP.value = ""; iQ.value = ""; sO.value = "-updated_at"; refreshChips(); onApply({ status: "", severity: "", priority: "", q: "", sort: "-updated_at" }); });
  iQ.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); apply.click(); } });
  refreshChips();
  const wrap = el("div", null);
  wrap.append(bar, chips);
  return wrap;
}

async function screenBugList(projectId) {
  if (!requireAuth()) return;
  setCrumb("Defects", `Project #${projectId}`); setActiveNav("projects");
  clearApp();
  let projName = "";
  try { const p = await api(`/api/v1/projects/${projectId}`); projName = `${p.project_key} — ${p.name}`; setCrumb("Defects", projName); } catch {}
  const headActs = [];
  const create = document.createElement("a"); create.href = `#/projects/${projectId}/bugs/new`; create.className = "btn"; create.textContent = "＋ New defect";
  headActs.push(create);
  app.appendChild(pageHead(projName ? `Defects · ${projName}` : `Defects · #${projectId}`, "Search, filter, sort and page through every defect in this project.", headActs));
  const state = { status: "", severity: "", priority: "", q: "", sort: "-updated_at", page: 1 };
  const tools = buildFilterToolbar(state, (s) => { Object.assign(state, s, { page: 1 }); load(); });
  app.appendChild(tools);
  const info = el("p", "", { class: "muted" });
  app.appendChild(info);
  const list = el("div", null);
  app.appendChild(list);
  const pager = el("div", null, { class: "pager" });
  const prev = btn("← Prev", "secondary"); prev.classList.add("sm");
  const next = btn("Next →", "secondary"); next.classList.add("sm");
  const pg = el("span", "", { class: "muted" });
  pager.append(prev, pg, next);
  app.appendChild(pager);
  async function load() {
    list.textContent = "";
    list.appendChild(skeletonList(4));
    const params = new URLSearchParams({ page: state.page, page_size: 20, sort: state.sort });
    if (state.status) params.set("status", state.status);
    if (state.severity) params.set("severity", state.severity);
    if (state.priority) params.set("priority", state.priority);
    if (state.q) params.set("q", state.q);
    try {
      const data = await api(`/api/v1/projects/${projectId}/bugs?${params}`);
      info.textContent = `${data.total} defect${data.total === 1 ? "" : "s"} found`;
      pg.textContent = `Page ${data.page}`;
      prev.disabled = data.page <= 1;
      list.textContent = "";
      if (!data.items.length) { list.appendChild(emptyState("No defects match", "Try widening the search or clearing filters.")); return; }
      const tw = el("div", null, { class: "table-wrap" });
      const t = document.createElement("table");
      const hr = document.createElement("tr");
      ["ID", "Title", "Status", "Severity", "Priority", "Assignee", "Updated"].forEach((h) => hr.appendChild(el("th", h)));
      t.appendChild(hr);
      for (const b of data.items) {
        const r = document.createElement("tr");
        const tdK = document.createElement("td"); const a = document.createElement("a"); a.href = `#/bugs/${b.id}`; a.className = "mono"; a.textContent = b.public_key; tdK.appendChild(a);
        const tdT = document.createElement("td");
        const ta = document.createElement("a"); ta.href = `#/bugs/${b.id}`; ta.textContent = b.title; ta.className = "cell-main";
        const sub = el("div", (b.description || "").slice(0, 90), { class: "cell-sub" });
        tdT.append(ta, sub);
        const tdS = document.createElement("td"); tdS.appendChild(badge(b.status));
        const tdV = document.createElement("td"); tdV.appendChild(badge(b.severity));
        const tdP = document.createElement("td"); tdP.appendChild(badge(b.priority));
        const tdA = document.createElement("td"); tdA.textContent = b.assignee_id ? `#${b.assignee_id}` : "Unassigned";
        const tdU = document.createElement("td"); tdU.textContent = fmtDate(b.updated_at); tdU.className = "muted";
        r.append(tdK, tdT, tdS, tdV, tdP, tdA, tdU);
        t.appendChild(r);
      }
      tw.appendChild(t);
      list.appendChild(tw);
    } catch (e) { list.textContent = ""; list.appendChild(el("p", e.message, { class: "error" })); }
  }
  prev.addEventListener("click", () => { if (state.page > 1) { state.page--; load(); } });
  next.addEventListener("click", () => { state.page++; load(); });
  await load();
}

async function screenBugNew(projectId) {
  if (!requireAuth()) return;
  setCrumb("New defect", `Project #${projectId}`); setActiveNav("projects");
  clearApp();
  const back = document.createElement("a"); back.href = `#/projects/${projectId}/bugs`; back.textContent = "← Back to defects"; back.className = "btn ghost sm";
  app.appendChild(back);
  app.appendChild(pageHead("Create defect", "Describe the issue clearly so it can be reproduced and fixed."));
  const card = el("div", null, { class: "card" });
  const f = document.createElement("form");
  const sec1 = el("h3", "Basics");
  f.appendChild(sec1);
  const mkField = (id, label, kind, req, placeholder) => {
    const l = el("label", null); l.htmlFor = id; l.textContent = label + " ";
    if (req) l.appendChild(el("span", "*", { class: "req" }));
    const inp = kind === "textarea" ? document.createElement("textarea") : document.createElement("input");
    inp.id = id; if (req) inp.required = true; if (placeholder) inp.placeholder = placeholder;
    f.append(l, inp);
    return inp;
  };
  const title = mkField("nb-title", "Title (5–200 chars)", "input", true, "e.g. Checkout crashes when coupon is empty");
  title.minLength = 5; title.maxLength = 200;
  const desc = mkField("nb-desc", "Description", "textarea", true, "What happened, where, and impact…");
  desc.required = true;
  f.appendChild(el("h3", "Reproduction"));
  const steps = mkField("nb-steps", "Steps to reproduce", "textarea", false, "1. … 2. … 3. …");
  const exp = mkField("nb-exp", "Expected result", "textarea", false, "What should happen");
  const act = mkField("nb-act", "Actual result", "textarea", false, "What actually happens");
  const env = mkField("nb-env", "Environment", "input", false, "OS / browser / app version");
  f.appendChild(el("h3", "Classification"));
  const grid = el("div", null, { class: "form-grid" });
  const mkSel = (label, options, val) => {
    const w = el("div", null);
    const l = el("label", label);
    const s = document.createElement("select");
    for (const o of options) { const op = document.createElement("option"); op.textContent = o; if (o === val) op.selected = true; s.appendChild(op); }
    w.append(l, s);
    return [w, s];
  };
  const [wSev, sev] = mkSel("Severity", ["Critical", "High", "Medium", "Low", "Trivial"], "Medium");
  const [wPri, pri] = mkSel("Priority", ["P0", "P1", "P2", "P3", "P4"], "P2");
  const [wTyp, typ] = mkSel("Type", ["Bug", "Regression", "UI", "Performance", "Security", "Compatibility"], "Bug");
  grid.append(wSev, wPri, wTyp);
  f.appendChild(grid);
  const row = el("div", null, { class: "form-actions" });
  const btnC = document.createElement("button"); btnC.className = "btn"; btnC.textContent = "Create defect"; btnC.type = "submit";
  const cancel = document.createElement("a"); cancel.href = `#/projects/${projectId}/bugs`; cancel.className = "btn secondary"; cancel.textContent = "Cancel";
  const err = el("p", "", { class: "field-err", role: "alert" });
  row.append(btnC, cancel);
  f.append(row, err);
  f.addEventListener("submit", async (e) => {
    e.preventDefault(); btnC.disabled = true; err.textContent = "";
    const t = title.value.trim();
    const de = desc.value.trim();
    if (t.length < 5) { err.textContent = "Title must be at least 5 characters."; title.focus(); btnC.disabled = false; return; }
    if (!de) { err.textContent = "Description is required."; desc.focus(); btnC.disabled = false; return; }
    const body = { title: t, description: de, steps_to_reproduce: steps.value, expected_result: exp.value, actual_result: act.value, environment: env.value, severity: sev.value, priority: pri.value, type: typ.value };
    try { const b = await api(`/api/v1/projects/${projectId}/bugs`, { method: "POST", body }); toast(`Created ${b.public_key}`, "ok"); location.hash = `#/bugs/${b.id}`; }
    catch (ex) { err.textContent = ex.message || "Create failed."; } finally { btnC.disabled = false; }
  });
  card.appendChild(f); app.appendChild(card);
}

async function screenBugDetail(id) {
  if (!requireAuth()) return;
  setCrumb("Defect", `#${id}`); setActiveNav("");
  clearApp();
  app.appendChild(skeletonList(4));
  try {
    const b = await api(`/api/v1/bugs/${id}`);
    const comments = await api(`/api/v1/bugs/${id}/comments`);
    const history = await api(`/api/v1/bugs/${id}/history`);
    let attachments = [];
    try { attachments = await api(`/api/v1/bugs/${id}/attachments`); } catch {}
    let projKey = `#${b.project_id}`;
    try { const p = await api(`/api/v1/projects/${b.project_id}`); projKey = p.project_key; setCrumb("Defect", `${p.project_key}-${b.public_key || ""}`.replace(/^#/, "")); } catch {}
    clearApp();
    setCrumb("Defect", `${b.public_key}`);
    const back = document.createElement("a"); back.href = `#/projects/${b.project_id}/bugs`; back.textContent = `← ${projKey} defects`; back.className = "btn ghost sm";
    app.appendChild(back);
    const headRow = el("div", null, { class: "page-head" });
    const ht = el("div", null);
    const h1 = el("h1", `${b.public_key}  ${b.title}`);
    h1.style.overflowWrap = "anywhere";
    ht.appendChild(h1);
    const chips = el("div", null, { class: "chip-row" });
    chips.append(badge(b.status), badge(b.severity), badge(b.priority), badge(b.type));
    if (b.is_archived) chips.appendChild(badge("archived"));
    ht.appendChild(chips);
    headRow.appendChild(ht);
    app.appendChild(headRow);

    const grid = el("div", null, { class: "detail-grid" });
    const left = el("div", null);
    const overview = el("div", null, { class: "card" });
    overview.appendChild(el("h2", "Overview"));
    const kv = document.createElement("dl"); kv.className = "kv";
    const kvAdd = (k, v) => { const dt = document.createElement("dt"); dt.textContent = k; const dd = document.createElement("dd"); dd.textContent = v; kv.append(dt, dd); };
    kvAdd("Project", `${projKey} (#${b.project_id})`);
    kvAdd("Reporter", `#${b.reporter_id}`);
    kvAdd("Assignee", b.assignee_id ? `#${b.assignee_id}` : "Unassigned");
    kvAdd("Version", `v${b.version}`);
    kvAdd("Created", fmtDate(b.created_at));
    kvAdd("Updated", fmtDate(b.updated_at));
    if (b.due_date) kvAdd("Due", String(b.due_date));
    if (b.resolution) kvAdd("Resolution", b.resolution);
    overview.appendChild(kv);
    overview.appendChild(el("h3", "Description"));
    overview.appendChild(el("p", b.description));
    if (b.steps_to_reproduce) { overview.appendChild(el("h3", "Steps to reproduce")); overview.appendChild(el("p", b.steps_to_reproduce)); }
    if (b.expected_result) { overview.appendChild(el("h3", "Expected")); overview.appendChild(el("p", b.expected_result)); }
    if (b.actual_result) { overview.appendChild(el("h3", "Actual")); overview.appendChild(el("p", b.actual_result)); }
    if (b.environment) { overview.appendChild(el("h3", "Environment")); overview.appendChild(el("p", b.environment)); }
    left.appendChild(overview);

    const cc = el("div", null, { class: "card" });
    cc.appendChild(el("h2", `Comments (${comments.length})`));
    if (!comments.length) cc.appendChild(el("p", "No comments yet. Start the discussion.", { class: "muted" }));
    for (const c of comments) {
      const d = el("div", null, { class: "comment" });
      const head = el("div", `#${c.id} · by #${c.author_id} · ${fmtDate(c.created_at)}`, { class: "comment-head" });
      const body = el("p", c.body);
      const del = btn("Delete", "ghost"); del.classList.add("sm");
      del.addEventListener("click", async () => {
        if (!(await confirmDialog("Delete this comment? This is soft-deleted and audited.", "Delete"))) return;
        try { await api(`/api/v1/comments/${c.id}`, { method: "DELETE" }); toast("Comment deleted", "ok"); location.reload(); }
        catch (ex) { toast(ex.message, "err"); }
      });
      d.append(head, body, del);
      cc.appendChild(d);
    }
    const cf = document.createElement("form");
    const lct = el("label", "Add a comment"); lct.htmlFor = "c-new";
    const cta = document.createElement("textarea"); cta.id = "c-new"; cta.required = true; cta.placeholder = "Write a clear, kind update…";
    const cb = document.createElement("button"); cb.className = "btn"; cb.textContent = "Add comment"; cb.type = "submit";
    cf.append(lct, cta, cb);
    cf.addEventListener("submit", async (e) => {
      e.preventDefault(); cb.disabled = true;
      try { await api(`/api/v1/bugs/${id}/comments`, { method: "POST", body: { body: cta.value } }); toast("Comment added", "ok"); location.reload(); }
      catch (ex) { toast(ex.message, "err"); } finally { cb.disabled = false; }
    });
    cc.appendChild(cf);
    left.appendChild(cc);

    const atc = el("div", null, { class: "card" });
    atc.appendChild(el("h2", `Attachments (${attachments.length})`));
    if (!attachments.length) atc.appendChild(el("p", "No files yet. PNG, JPG, WebP, PDF, TXT, LOG up to 10 MB.", { class: "muted" }));
    for (const a of attachments) {
      const p = el("p", null);
      const link = document.createElement("a"); link.href = `/api/v1/attachments/${a.id}/download`; link.textContent = `${a.original_filename} (${a.size_bytes} bytes)`; link.className = "mono";
      const del = btn("Delete", "ghost"); del.classList.add("sm"); del.style.marginLeft = "8px";
      del.addEventListener("click", async () => {
        if (!(await confirmDialog("Delete this attachment?", "Delete"))) return;
        try { await api(`/api/v1/attachments/${a.id}`, { method: "DELETE" }); toast("Attachment deleted", "ok"); location.reload(); }
        catch (ex) { toast(ex.message, "err"); }
      });
      p.append(link, del); atc.appendChild(p);
    }
    const uf = document.createElement("form");
    const lf = el("label", "Upload evidence"); lf.htmlFor = "f-up";
    const fi = document.createElement("input"); fi.id = "f-up"; fi.type = "file"; fi.required = true;
    const ub = document.createElement("button"); ub.className = "btn secondary"; ub.textContent = "Upload"; ub.type = "submit";
    uf.append(lf, fi, ub);
    uf.addEventListener("submit", async (e) => {
      e.preventDefault(); ub.disabled = true;
      try {
        const fd = new FormData(); fd.append("file", fi.files[0]);
        await api(`/api/v1/bugs/${id}/attachments`, { method: "POST", form: fd }); toast("Uploaded", "ok"); location.reload();
      } catch (ex) { toast(ex.message, "err"); } finally { ub.disabled = false; }
    });
    atc.appendChild(uf);
    left.appendChild(atc);

    const hc = el("div", null, { class: "card" });
    hc.appendChild(el("h2", "Activity history"));
    if (!history.length) hc.appendChild(el("p", "No history.", { class: "muted" }));
    else {
      const ul = document.createElement("ul"); ul.className = "timeline";
      for (const h of history.slice().reverse().slice(0, 20)) {
        const li = document.createElement("li");
        li.appendChild(el("span", null, { class: "tl-dot", "aria-hidden": "true" }));
        const bd = el("div", null, { class: "tl-body" });
        bd.appendChild(el("div", `${h.event_type} · by ${h.actor_id ?? "system"}`));
        bd.appendChild(el("small", `${fmtDate(h.created_at)} · ${JSON.stringify(h.changes_json)}`));
        li.appendChild(bd); ul.appendChild(li);
      }
      hc.appendChild(ul);
    }
    left.appendChild(hc);

    const right = el("div", null);
    const act = el("div", null, { class: "card" });
    act.appendChild(el("h2", "Manage"));
    const f = document.createElement("form");
    f.appendChild(el("h3", "Edit fields"));
    const lt = el("label", "Title"); lt.htmlFor = "e-title";
    const title = document.createElement("input"); title.id = "e-title"; title.value = b.title;
    const ld = el("label", "Description"); ld.htmlFor = "e-desc";
    const desc = document.createElement("textarea"); desc.id = "e-desc"; desc.value = b.description;
    const gl = el("div", null, { class: "form-grid" });
    const mkS = (lbl, opts, cur) => {
      const w = el("div", null); const l = el("label", lbl);
      const s = document.createElement("select");
      for (const o of opts) { const op = document.createElement("option"); op.textContent = o; if (o === cur) op.selected = true; s.appendChild(op); }
      w.append(l, s); return [w, s];
    };
    const [wSev, sev] = mkS("Severity", ["Critical", "High", "Medium", "Low", "Trivial"], b.severity);
    const [wPri, pri] = mkS("Priority", ["P0", "P1", "P2", "P3", "P4"], b.priority);
    gl.append(wSev, wPri);
    const sb = document.createElement("button"); sb.className = "btn"; sb.textContent = `Save (v${b.version})`; sb.type = "submit"; sb.style.width = "100%";
    const ee = el("p", "", { class: "field-err", role: "alert" });
    f.append(lt, title, ld, desc, gl, sb, ee);
    f.addEventListener("submit", async (e) => {
      e.preventDefault(); sb.disabled = true; ee.textContent = "";
      try { await api(`/api/v1/bugs/${id}`, { method: "PATCH", body: { title: title.value, description: desc.value, severity: sev.value, priority: pri.value, version: b.version } }); toast("Saved", "ok"); location.reload(); }
      catch (ex) { ee.textContent = ex.message; } finally { sb.disabled = false; }
    });
    act.appendChild(f);
    const allowed = { "Open": ["In Progress", "Rejected"], "In Progress": ["Fixed", "Open", "Rejected"], "Fixed": ["Ready for Test", "Reopened"], "Ready for Test": ["Closed", "Reopened"], "Reopened": ["In Progress", "Rejected"], "Closed": ["Reopened"], "Rejected": ["Reopened"] };
    act.appendChild(el("h3", "Status transition"));
    if ((allowed[b.status] || []).length) {
      const ts = document.createElement("select"); ts.setAttribute("aria-label", "Next status");
      for (const s of (allowed[b.status] || [])) { const o = document.createElement("option"); o.textContent = s; ts.appendChild(o); }
      const tb = document.createElement("button"); tb.className = "btn secondary"; tb.textContent = "Transition"; tb.type = "button"; tb.style.width = "100%"; tb.style.marginTop = "8px";
      tb.addEventListener("click", async () => {
        if (!(await confirmDialog(`Move from ${b.status} to ${ts.value}?`, "Transition"))) return;
        tb.disabled = true;
        try { await api(`/api/v1/bugs/${id}/transition`, { method: "POST", body: { to_status: ts.value, version: b.version } }); toast("Status updated", "ok"); location.reload(); }
        catch (ex) { toast(ex.message, "err"); } finally { tb.disabled = false; }
      });
      act.append(ts, tb);
    } else act.appendChild(el("p", "No transitions available.", { class: "muted" }));
    act.appendChild(el("h3", "Assignment"));
    let mem = [];
    try { mem = await api(`/api/v1/projects/${b.project_id}/members`); } catch { mem = []; }
    const asel = document.createElement("select"); asel.setAttribute("aria-label", "Assignee");
    const none = document.createElement("option"); none.value = ""; none.textContent = "Unassigned"; asel.appendChild(none);
    for (const m of (mem || [])) { const o = document.createElement("option"); o.value = m.user_id; o.textContent = `${m.username} (${m.role})`; if (String(m.user_id) === String(b.assignee_id)) o.selected = true; asel.appendChild(o); }
    const ab = document.createElement("button"); ab.className = "btn secondary"; ab.textContent = "Update assignee"; ab.type = "button"; ab.style.width = "100%"; ab.style.marginTop = "8px";
    ab.addEventListener("click", async () => {
      if (!(await confirmDialog("Change the assignee?", "Assign"))) return;
      try { await api(`/api/v1/bugs/${id}/assign`, { method: "POST", body: { assignee_id: asel.value ? Number(asel.value) : null, version: b.version } }); toast("Assignee updated", "ok"); location.reload(); }
      catch (ex) { toast(ex.message, "err"); }
    });
    act.append(asel, ab);
    act.appendChild(el("h3", "Danger zone"));
    const arch = btn(b.is_archived ? "Restore defect" : "Archive defect", "danger"); arch.style.width = "100%";
    arch.addEventListener("click", async () => {
      if (!(await confirmDialog(b.is_archived ? "Restore this defect?" : "Archive this defect? It will be hidden from default lists.", b.is_archived ? "Restore" : "Archive"))) return;
      try { await api(`/api/v1/bugs/${id}/${b.is_archived ? "restore" : "archive"}`, { method: "POST" }); toast("Done", "ok"); location.reload(); }
      catch (ex) { toast(ex.message, "err"); }
    });
    act.appendChild(arch);
    right.appendChild(act);
    grid.append(left, right);
    app.appendChild(grid);
  } catch (e) { clearApp(); app.appendChild(errorBox(e.message)); }
}

async function screenAssigned() {
  if (!requireAuth()) return;
  setCrumb("My Assigned"); setActiveNav("assigned");
  clearApp();
  app.appendChild(pageHead("My assigned defects", "Everything currently assigned to you across visible projects."));
  const w = el("div", null, { class: "card" });
  w.appendChild(skeletonList(3));
  app.appendChild(w);
  try {
    const items = await api("/api/v1/dashboard/assigned?page=1&page_size=50");
    w.textContent = "";
    w.appendChild(el("h2", `Assigned (${items.length})`));
    if (!items.length) { w.appendChild(emptyState("All clear", "Nothing is assigned to you right now.")); return; }
    const tw = el("div", null, { class: "table-wrap" });
    const t = document.createElement("table");
    const hr = document.createElement("tr");
    ["ID", "Title", "Status", "Priority", "Severity", "Updated"].forEach((h) => hr.appendChild(el("th", h)));
    t.appendChild(hr);
    for (const b of items) {
      const r = document.createElement("tr");
      const k = document.createElement("td"); const a = document.createElement("a"); a.href = `#/bugs/${b.id}`; a.className = "mono"; a.textContent = b.public_key; k.appendChild(a);
      const ti = document.createElement("td"); const ta = document.createElement("a"); ta.href = `#/bugs/${b.id}`; ta.textContent = b.title; ti.appendChild(ta);
      const s = document.createElement("td"); s.appendChild(badge(b.status));
      const pr = document.createElement("td"); pr.appendChild(badge(b.priority));
      const sv = document.createElement("td"); sv.appendChild(badge(b.severity));
      const up = document.createElement("td"); up.textContent = fmtDate(b.updated_at); up.className = "muted";
      r.append(k, ti, s, pr, sv, up); t.appendChild(r);
    }
    tw.appendChild(t); w.appendChild(tw);
  } catch (e) { w.textContent = ""; w.appendChild(el("p", e.message, { class: "error" })); }
}

async function screenSearch(q) {
  if (!requireAuth()) return;
  const query = decodeURIComponent(q || "").trim();
  setCrumb("Search", query); setActiveNav("");
  clearApp();
  app.appendChild(pageHead(`Search: ${query}`, "Matching defects across all projects you can access."));
  const w = el("div", null, { class: "card" });
  w.appendChild(skeletonList(3));
  app.appendChild(w);
  try {
    const summary = await api("/api/v1/dashboard/summary");
    const projs = summary.per_project || [];
    const out = [];
    for (const p of projs.slice(0, 25)) {
      try {
        const r = await api(`/api/v1/projects/${p.project_id}/bugs?q=${encodeURIComponent(query)}&page=1&page_size=10`);
        for (const b of r.items) out.push({ ...b, _proj: p.project_key });
      } catch {}
      if (out.length >= 40) break;
    }
    w.textContent = "";
    w.appendChild(el("h2", `${out.length} result${out.length === 1 ? "" : "s"}`));
    if (!out.length) { w.appendChild(emptyState("No matches", `Nothing found for “${query}”.`)); return; }
    const tw = el("div", null, { class: "table-wrap" });
    const t = document.createElement("table");
    const hr = document.createElement("tr");
    ["ID", "Project", "Title", "Status", "Priority"].forEach((h) => hr.appendChild(el("th", h)));
    t.appendChild(hr);
    for (const b of out.slice(0, 40)) {
      const r = document.createElement("tr");
      const k = document.createElement("td"); const a = document.createElement("a"); a.href = `#/bugs/${b.id}`; a.className = "mono"; a.textContent = b.public_key; k.appendChild(a);
      r.append(k, el("td", b._proj));
      const ti = document.createElement("td"); const ta = document.createElement("a"); ta.href = `#/bugs/${b.id}`; ta.textContent = b.title; ti.appendChild(ta);
      r.appendChild(ti);
      const s = document.createElement("td"); s.appendChild(badge(b.status)); r.appendChild(s);
      const pr = document.createElement("td"); pr.appendChild(badge(b.priority)); r.appendChild(pr);
      t.appendChild(r);
    }
    tw.appendChild(t); w.appendChild(tw);
  } catch (e) { w.textContent = ""; w.appendChild(el("p", e.message, { class: "error" })); }
}

async function screenReports() {
  if (!requireAuth()) return;
  setCrumb("Reports"); setActiveNav("reports");
  clearApp();
  app.appendChild(pageHead("Reports", "Export the exact filtered set you see in the UI. Formula cells are escaped."));
  const c = el("div", null, { class: "card" });
  c.appendChild(el("h2", "CSV export"));
  c.appendChild(el("p", "Respects project access and the same filters as the defect list.", { class: "muted" }));
  const f = document.createElement("form");
  const grid = el("div", null, { class: "form-grid" });
  const w1 = el("div", null); const l1 = el("label", "Project ID (optional)"); l1.htmlFor = "rp-p";
  const pid = document.createElement("input"); pid.id = "rp-p"; pid.placeholder = "e.g. 1"; pid.inputMode = "numeric";
  w1.append(l1, pid);
  const w2 = el("div", null); const l2 = el("label", "Status (optional)"); l2.htmlFor = "rp-s";
  const st = document.createElement("select"); st.id = "rp-s";
  for (const o of ["", "Open", "In Progress", "Fixed", "Ready for Test", "Reopened", "Closed", "Rejected"]) { const op = document.createElement("option"); op.value = o; op.textContent = o || "Any"; st.appendChild(op); }
  w2.append(l2, st);
  grid.append(w1, w2);
  const b = document.createElement("button"); b.className = "btn"; b.textContent = "Download CSV"; b.type = "submit";
  f.append(grid, b);
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    const qs = new URLSearchParams();
    if (pid.value.trim()) qs.set("project_id", pid.value.trim());
    if (st.value) qs.set("status", st.value);
    window.location.href = `/api/v1/reports/bugs.csv?${qs.toString()}`;
    toast("CSV download started", "ok");
  });
  c.appendChild(f); app.appendChild(c);
}

async function screenAdmin() {
  if (!requireAuth()) return;
  if (me.global_role !== "admin") { setCrumb("Access denied"); clearApp(); app.appendChild(emptyState("Access denied", "Administration is restricted to global admins.", "#/dashboard", "Back to dashboard")); return; }
  setCrumb("Administration"); setActiveNav("admin");
  clearApp();
  app.appendChild(pageHead("User administration", "Create accounts, manage roles, and handle access."));
  const c = el("div", null, { class: "card" });
  c.appendChild(el("h2", "Create user"));
  const f = document.createElement("form");
  const grid = el("div", null, { class: "form-grid" });
  const mk = (id, label, type = "text") => {
    const w = el("div", null); const l = el("label", label); l.htmlFor = id;
    const i = document.createElement("input"); i.id = id; i.required = true; if (type) i.type = type;
    w.append(l, i); return [w, i];
  };
  const [wu, u] = mk("au-u", "Username"); u.placeholder = "jdoe"; u.minLength = 3;
  const [we, em] = mk("au-e", "Email", "email"); em.placeholder = "jdoe@example.com";
  const [wd, dn] = mk("au-d", "Display name"); dn.placeholder = "Jane Doe";
  const [wp, pw] = mk("au-p", "Password (12+ chars)", "password"); pw.minLength = 12;
  grid.append(wu, we, wd, wp);
  const lr = el("label", "Global role"); lr.htmlFor = "au-r";
  const ro = document.createElement("select"); ro.id = "au-r";
  for (const r of ["user", "admin"]) { const o = document.createElement("option"); o.value = r; o.textContent = r; ro.appendChild(o); }
  const b = document.createElement("button"); b.className = "btn"; b.textContent = "Create user"; b.type = "submit";
  const e = el("p", "", { class: "field-err", role: "alert" });
  f.append(grid, lr, ro, b, e);
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault(); b.disabled = true; e.textContent = "";
    try { await api("/api/v1/admin/users", { method: "POST", body: { username: u.value, email: em.value, display_name: dn.value, password: pw.value, global_role: ro.value } }); toast("User created", "ok"); f.reset(); await load(); }
    catch (ex) { e.textContent = ex.message; } finally { b.disabled = false; }
  });
  c.appendChild(f); app.appendChild(c);
  const lc = el("div", null, { class: "card" });
  lc.appendChild(el("h2", "Accounts"));
  const list = el("div", null); lc.appendChild(list); app.appendChild(lc);
  async function load() {
    list.textContent = ""; list.appendChild(skeletonList(3));
    try {
      const data = await api("/api/v1/admin/users?page=1&page_size=100");
      list.textContent = "";
      if (!data.items.length) { list.appendChild(el("p", "No users.", { class: "muted" })); return; }
      const tw = el("div", null, { class: "table-wrap" });
      const t = document.createElement("table");
      const hr = document.createElement("tr");
      ["User", "Role", "Status", "Actions"].forEach((h) => hr.appendChild(el("th", h)));
      t.appendChild(hr);
      for (const usr of data.items) {
        const r = document.createElement("tr");
        const tdU = document.createElement("td");
        const cell = el("div", null, { class: "user-cell" });
        cell.appendChild(el("span", (usr.display_name || usr.username || "?").trim().charAt(0).toUpperCase(), { class: "avatar sm", "aria-hidden": "true" }));
        const tx = el("div", null);
        tx.appendChild(el("div", usr.username, { class: "cell-main mono" }));
        tx.appendChild(el("div", `${usr.display_name || ""} · ${usr.email}`, { class: "cell-sub" }));
        cell.appendChild(tx); tdU.appendChild(cell);
        const tdR = document.createElement("td"); tdR.appendChild(badge(usr.global_role));
        const tdA = document.createElement("td"); tdA.appendChild(badge(usr.is_active ? "Closed" : "Rejected"));
        tdA.firstChild.textContent = usr.is_active ? "Active" : "Disabled";
        const td = document.createElement("td");
        const wrap = el("div", null, { class: "form-actions" }); wrap.style.margin = "0";
        const dis = btn(usr.is_active ? "Disable" : "Enable", "secondary"); dis.classList.add("sm");
        dis.addEventListener("click", async () => {
          if (!(await confirmDialog(`${usr.is_active ? "Disable" : "Enable"} ${usr.username}? ${usr.is_active ? "Their sessions will be revoked immediately." : ""}`, usr.is_active ? "Disable" : "Enable"))) return;
          try { await api(`/api/v1/admin/users/${usr.id}/${usr.is_active ? "disable" : "enable"}`, { method: "POST" }); toast("Updated", "ok"); await load(); }
          catch (ex) { toast(ex.message, "err"); }
        });
        const rst = btn("Reset PW", "ghost"); rst.classList.add("sm");
        rst.title = "Generate a one-time reset token";
        rst.addEventListener("click", async () => {
          if (!(await confirmDialog(`Generate a one-time reset token for ${usr.username}? Deliver it securely.`, "Generate"))) return;
          try {
            const dd = await api(`/api/v1/admin/users/${usr.id}/password-reset`, { method: "POST" });
            const shown = window.prompt("One-time reset token (copy now, expires in 1h):", dd.reset_token);
            if (shown !== null) toast("Token generated — deliver securely", "ok");
          } catch (ex) { toast(ex.message, "err"); }
        });
        wrap.append(dis, rst); td.appendChild(wrap);
        r.append(tdU, tdR, tdA, td); t.appendChild(r);
      }
      tw.appendChild(t); list.appendChild(tw);
    } catch (ex) { list.textContent = ""; list.appendChild(el("p", ex.message, { class: "error" })); }
  }
  await load();
}

async function screenPassword() {
  if (!requireAuth()) return;
  setCrumb("Change password"); setActiveNav("password");
  clearApp();
  app.appendChild(pageHead("Change password", "Your other sessions will be revoked for safety."));
  const c = el("div", null, { class: "card" });
  c.style.maxWidth = "520px";
  const f = document.createElement("form");
  const l1 = el("label", "Current password"); l1.htmlFor = "pw-cur";
  const cur = document.createElement("input"); cur.id = "pw-cur"; cur.type = "password"; cur.required = true; cur.autocomplete = "current-password";
  const l2 = el("label", "New password (12–128 chars)"); l2.htmlFor = "pw-new";
  const nw = document.createElement("input"); nw.id = "pw-new"; nw.type = "password"; nw.required = true; nw.minLength = 12; nw.maxLength = 128; nw.autocomplete = "new-password";
  const b = document.createElement("button"); b.className = "btn"; b.textContent = "Change password"; b.type = "submit"; b.style.width = "100%"; b.style.marginTop = "12px";
  const e = el("p", "", { class: "field-err", role: "alert" });
  f.append(l1, cur, l2, nw, b, e);
  f.addEventListener("submit", async (ev) => {
    ev.preventDefault(); b.disabled = true; e.textContent = "";
    try {
      const dd = await api("/api/v1/auth/change-password", { method: "POST", body: { current_password: cur.value, new_password: nw.value } });
      const { setCsrf: sc } = await import("./api.js");
      if (dd.csrf_token) sc(dd.csrf_token);
      toast("Password changed — other sessions revoked", "ok");
      f.reset();
    } catch (ex) { e.textContent = ex.message; } finally { b.disabled = false; }
  });
  c.appendChild(f); app.appendChild(c);
}

function router() {
  const h = location.hash || "#/dashboard";
  const status = document.getElementById("status");
  if (status) status.textContent = "";
  const _foot = document.getElementById("app-foot");
  if (h.startsWith("#/login")) {
    if (_foot) _foot.hidden = true;
    return screenLogin();
  }
  if (_foot) _foot.hidden = false;
  if (h.startsWith("#/dashboard")) return screenDashboard();
  let m;
  if ((m = h.match(/^#\/search\/(.+)$/))) return screenSearch(m[1]);
  if ((m = h.match(/^#\/projects\/(\d+)\/bugs\/new$/))) return screenBugNew(m[1]);
  if ((m = h.match(/^#\/projects\/(\d+)\/bugs$/))) return screenBugList(m[1]);
  if ((m = h.match(/^#\/projects\/(\d+)$/))) return screenProjectDetail(m[1]);
  if (h === "#/projects") return screenProjects();
  if ((m = h.match(/^#\/bugs\/(\d+)$/))) return screenBugDetail(m[1]);
  if (h === "#/assigned") return screenAssigned();
  if (h === "#/reports") return screenReports();
  if (h === "#/admin") return screenAdmin();
  if (h === "#/password") return screenPassword();
  clearApp(); setCrumb("Not found");
  app.appendChild(emptyState("Page not found", "The link may be wrong or you lack access.", "#/dashboard", "Go to dashboard"));
}

window.addEventListener("hashchange", router);

(async function init() {
  const ok = await refreshMe();
  if (!ok && !location.hash.startsWith("#/login")) location.hash = "#/login";
  if (!location.hash) location.hash = "#/dashboard";
  router();
})();
