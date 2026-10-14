/* Shared API helper: same-origin cookies, CSRF, safe errors, 401 handling */
let csrfToken = null;
let onUnauthorized = null;

export function setCsrf(t) { csrfToken = t; }
export function getCsrf() { return csrfToken; }
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

async function parseBody(res) {
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try { return await res.json(); } catch { return null; }
  }
  try { return await res.text(); } catch { return null; }
}

export async function api(path, { method = "GET", body = null, form = null, csrf = true } = {}) {
  const headers = {};
  const opts = { method, credentials: "same-origin", headers };
  if (body !== null) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  if (form !== null) { opts.body = form; } // multipart; browser sets boundary
  if (csrf && ["POST", "PATCH", "PUT", "DELETE"].includes(method)) {
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }
  const res = await fetch(path, opts);
  if (res.status === 401 && onUnauthorized) { onUnauthorized(); }
  if (!res.ok) {
    const data = await parseBody(res);
    let msg = `Request failed (${res.status})`;
    const d = data && data.detail;
    if (typeof d === "string" && d) msg = d;
    else if (Array.isArray(d) && d.length) {
      msg = d.map((e) => {
        const loc = Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body").join(".") : "";
        const m = e.msg || "Invalid value";
        return loc ? `${loc}: ${m}` : m;
      }).join("; ");
    }
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return await res.json();
  return await res.text();
}

export function el(tag, text, attrs = {}) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = String(text);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else e.setAttribute(k, v);
  }
  return e;
}

export function showStatus(msg, kind = "") {
  const s = document.getElementById("status");
  s.textContent = "";
  if (msg) {
    const p = document.createElement("p");
    p.textContent = msg;
    p.className = kind;
    s.appendChild(p);
  }
}
