// background.js - Chrome service worker and Firefox event page (one file, MV3).
// Commands arrive only through the native messaging port (protocol: plan section 3); this file
// is the trust boundary of plan section 4: allowlisted hosts, own tabs only, no checkout.
"use strict";
// ponytail: Chrome loads dom.js with importScripts; Firefox lists it in manifest background.scripts
if (typeof importScripts === "function") importScripts("dom.js");

const api = globalThis.browser ?? globalThis.chrome;
const HOST = "com.github.fbialogrecki.djdigger";
const PROTOCOL = 1;
const INSTANCE = crypto.randomUUID();
const manifest = api.runtime.getManifest();
const ACTION_TIMEOUT_MS = 15000;
const GOTO_TIMEOUT_MS = 30000;
// Window used for the hidden pass. ponytail: spike R2 decides between "normal" (unfocused)
// and "minimized", which throttles timers.
const HIDDEN_STATE = "normal";
const hiddenProps = () => (HIDDEN_STATE === "normal" ? { focused: false } : { state: HIDDEN_STATE });

class Err extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- allowlist (allowlist.json)

let allow;
const parse = (url) => {
  try {
    return new URL(url);
  } catch {
    return null;
  }
};
// The allowlist group a URL belongs to, or null: https only, default port, no credentials,
// exact host / "www." / subdomain boundaries exactly as links.py and store_urls.py.
function groupOf(url) {
  const u = typeof url === "string" ? parse(url) : url;
  if (!u || !allow.schemes.includes(u.protocol.slice(0, -1)) || u.username || u.password || u.port) {
    return null;
  }
  for (const g of Object.values(allow.groups)) {
    for (const h of g.hosts) {
      if (u.hostname === h || (g.www && u.hostname === "www." + h) || (g.subdomains && u.hostname.endsWith("." + h))) {
        return g;
      }
    }
  }
  return null;
}
function cleanPath(u) {
  let p = u.pathname;
  try {
    p = decodeURIComponent(p);
  } catch {
    // keep the raw path
  }
  p = p.toLowerCase().replace(/\/{2,}/g, "/");
  return p.length > 1 ? p.replace(/\/$/, "") : p;
}
const isForbidden = (g, p) => g.forbiddenPaths.some((f) => p === f || p.startsWith(f + "/"));
// Non-allowlisted URLs (OAuth popups carry code= and state=) are reduced to their host.
function safeUrl(url) {
  const u = parse(url);
  if (!u) return "";
  if (url === "about:blank" || groupOf(u)) return u.href;
  return u.protocol.startsWith("http") ? `${u.protocol}//${u.hostname}/` : u.protocol;
}
const redactMessage = (m) => String(m ?? "").replace(/https?:\/\/[^\s"')]+/g, safeUrl);
// A URL the extension may navigate an own tab to.
function navigable(url) {
  const u = parse(url);
  const g = u && groupOf(u);
  if (!g) throw new Err("refused", `host is not allowlisted: ${safeUrl(url)}`);
  const p = cleanPath(u);
  if (g.allowedPaths && !g.allowedPaths.includes(p)) throw new Err("refused", `path is not allowed on ${u.hostname}`);
  if (isForbidden(g, p)) throw new Err("forbidden", `checkout and payment pages are never opened: ${safeUrl(url)}`);
  return u.href;
}

// ---------------------------------------------------------------- registry of owned tabs

const owned = new Map(); // tabId -> {popup}
const downloads = new Map(); // attributed downloadId -> tabId
let win = null; // {id, visible}
let spare = null; // blank tab of a fresh window, reused by the first tab.new
const persist = () =>
  api.storage.session
    .set({ djdRegistry: { win, spare, owned: [...owned], downloads: [...downloads] } })
    .catch(() => {});
const setStatus = (s) => api.storage.session.set({ djdStatus: { instance: INSTANCE, since: Date.now(), ...s } }).catch(() => {});

async function restore() {
  const { djdRegistry: r } = await api.storage.session.get("djdRegistry");
  if (!r) return;
  for (const [id, v] of r.owned) {
    if (await api.tabs.get(id).catch(() => null)) owned.set(id, v);
  }
  if (r.win && (await api.windows.get(r.win.id).catch(() => null))) win = r.win;
  if (owned.has(r.spare)) spare = r.spare;
  for (const [id, tabId] of r.downloads) downloads.set(id, tabId);
}
async function closeAll() {
  const ids = [...owned.keys()];
  owned.clear();
  win = null;
  spare = null;
  for (const id of ids) await api.tabs.remove(id).catch(() => {});
  persist();
}
function need(tabId) {
  if (!owned.has(tabId)) throw new Err("not_owned", "tab is not owned by dj-digger");
}
async function getTab(tabId) {
  need(tabId);
  try {
    return await api.tabs.get(tabId);
  } catch {
    throw new Err("tab_closed", "tab no longer exists");
  }
}
const currentUrl = (tab) => tab.url || tab.pendingUrl || "";
const isBlank = (url) => url === "" || url === "about:blank";
// dom.*, tab.html and tab.capture need an allowlisted page that allows page access; navigation
// (goto, reload) also accepts a blank tab, which has nothing to read.
async function pageTab(tabId, { nav = false } = {}) {
  const tab = await getTab(tabId);
  const url = currentUrl(tab);
  const g = groupOf(url);
  if (g ? !nav && g.dom === false : !(nav && isBlank(url))) throw new Err("refused", `tab is on ${safeUrl(url)}`);
  return tab;
}

// ---------------------------------------------------------------- native port and events

let port = null;
let failures = 0;
const send = (m) => {
  try {
    port?.postMessage(m);
  } catch {
    // the disconnect handler reconnects
  }
};
const emit = (e) => send(e);

async function browserInfo() {
  if (api.runtime.getBrowserInfo) {
    const i = await api.runtime.getBrowserInfo();
    return { name: i.name.toLowerCase(), version: i.version };
  }
  const ua = navigator.userAgent;
  const m = ua.match(/(Edg|OPR)\/([\d.]+)/) ?? ua.match(/(Chrome)\/([\d.]+)/);
  return { name: { Edg: "edge", OPR: "opera", Chrome: "chrome" }[m?.[1]] ?? "unknown", version: m?.[2] ?? "" };
}
async function hello() {
  const info = await browserInfo();
  const permissions = await api.permissions.contains({ origins: manifest.host_permissions });
  send({
    event: "hello", browser: info.name, version: manifest.version, browserVersion: info.version,
    protocol: PROTOCOL, instance: INSTANCE, permissions,
  });
}
function connect() {
  if (port) return;
  let p;
  try {
    p = api.runtime.connectNative(HOST);
  } catch (e) {
    return retry(e);
  }
  port = p;
  p.onMessage.addListener((m) => {
    failures = 0;
    onMessage(m);
  });
  p.onDisconnect.addListener(() => {
    const e = api.runtime.lastError ?? p.error;
    port = null;
    retry(e);
  });
  setStatus({ connected: true });
  hello().catch(() => {});
}
function retry(e) {
  setStatus({ connected: false, error: redactMessage(e?.message) });
  setTimeout(connect, Math.min(30000, 1000 * 2 ** failures++));
}

// ---------------------------------------------------------------- commands

const running = new Map(); // command id -> AbortController
const queues = new Map(); // tabId -> tail of that tab's serial queue
const UNQUEUED = new Set(["tab.close", "tab.info", "tab.focus"]); // never stuck behind a wait

function enqueue(tabId, job) {
  const tail = (queues.get(tabId) ?? Promise.resolve()).then(job);
  queues.set(tabId, tail);
  tail.then(() => queues.get(tabId) === tail && queues.delete(tabId));
}

function onMessage(m) {
  if (!m || typeof m !== "object") return;
  if (m.cmd === "cancel") {
    running.get(m.args?.id)?.abort();
    return send({ id: m.id, ok: true, result: null });
  }
  const args = m.args && typeof m.args === "object" ? m.args : {};
  const ac = new AbortController();
  running.set(m.id, ac);
  const job = async () => {
    try {
      await ready;
      if (ac.signal.aborted) throw new Err("cancelled", "command cancelled");
      if (!Object.hasOwn(commands, m.cmd)) throw new Err("unknown_cmd", `unknown command ${m.cmd}`);
      const result = await commands[m.cmd](args, { id: m.id, signal: ac.signal });
      send({ id: m.id, ok: true, result: result ?? null });
    } catch (e) {
      send({ id: m.id, ok: false, error: { code: e.code ?? "internal", message: redactMessage(e.message) } });
    } finally {
      running.delete(m.id);
    }
  };
  if (typeof args.tabId === "number" && !UNQUEUED.has(m.cmd)) enqueue(args.tabId, job);
  else job();
}

// Waits for check() while honouring cancel and a deadline.
async function pollUntil(check, timeoutMs, signal, what) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    if (signal.aborted) throw new Err("cancelled", "command cancelled");
    if (check()) return;
    if (Date.now() >= end) throw new Err("timeout", `timeout waiting for ${what}`);
    await sleep(50);
  }
}

// Runs an injected function in the tab, retrying while a navigation tears the frame down.
const TRANSIENT = /frame.*(removed|not found)|no frame|invalidated|destroyed|receiving end|navigat/i;
async function inject(tabId, func, makeArgs, { budgetMs, world, signal }) {
  const end = Date.now() + budgetMs;
  for (;;) {
    if (signal.aborted) throw new Err("cancelled", "command cancelled");
    try {
      const target = { tabId };
      const [r] = await api.scripting.executeScript({ target, func, args: makeArgs(Math.max(0, end - Date.now())), ...(world && { world }) });
      if (r?.result !== undefined) return r.result;
    } catch (e) {
      if (/cannot access|permission/i.test(e.message)) throw new Err("no_permission", redactMessage(e.message));
      if (!TRANSIENT.test(e.message)) throw e;
    }
    if (Date.now() >= end) throw new Err("timeout", "page did not answer");
    await sleep(100);
  }
}

const WAITING = new Set(["attr", "text", "value", "click", "fill", "waitVisible"]);
async function runDom(op, a, ctx) {
  await pageTab(a.tabId);
  if (!Array.isArray(a.locator)) throw new Err("bad_args", "locator must be a list of steps");
  const waits = WAITING.has(op);
  const token = String(ctx.id);
  const cancelInPage = () =>
    api.scripting
      .executeScript({ target: { tabId: a.tabId }, func: (t) => (globalThis.__djdCancel ||= new Set()).add(t), args: [token] })
      .catch(() => {});
  ctx.signal.addEventListener("abort", cancelInPage, { once: true });
  try {
    const res = await inject(
      a.tabId,
      djDom.run,
      (remaining) => [{
        op, locator: a.locator, name: a.name, value: a.value, attrs: a.attrs, text: a.text, limit: a.limit,
        timeoutMs: waits ? remaining : 0, deny: allow.clickDenylist, token,
      }],
      { budgetMs: waits ? (a.timeoutMs ?? ACTION_TIMEOUT_MS) : 1500, signal: ctx.signal }
    );
    if (!res.ok) throw new Err(res.code, res.message);
    return res.result;
  } finally {
    ctx.signal.removeEventListener("abort", cancelInPage);
  }
}

// Navigation: listeners go in before the navigation starts. Ready = DOMContentLoaded of the
// top frame after its commit; status = last main_frame onCompleted; errors from webNavigation.
async function navigate(tabId, start, timeoutMs, ctx) {
  const st = { committed: false, dcl: false, status: null, error: null };
  const top = (d) => d.tabId === tabId && d.frameId === 0;
  const onCommitted = (d) => top(d) && (st.committed = true);
  const onDcl = (d) => top(d) && st.committed && (st.dcl = true);
  const onError = (d) => top(d) && (st.error = d.error);
  const onCompleted = (d) => (st.status = d.statusCode);
  const filter = { urls: manifest.host_permissions, tabId, types: ["main_frame"] };
  api.webNavigation.onCommitted.addListener(onCommitted);
  api.webNavigation.onDOMContentLoaded.addListener(onDcl);
  api.webNavigation.onErrorOccurred.addListener(onError);
  api.webRequest.onCompleted.addListener(onCompleted, filter);
  try {
    await start();
    await pollUntil(() => st.error || st.dcl, timeoutMs, ctx.signal, "page load");
    if (st.error) throw new Err("navigation_failed", st.error);
    if (st.status === null) await sleep(300); // onCompleted can trail DOMContentLoaded
    return { url: safeUrl(currentUrl(await getTab(tabId))), status: st.status };
  } finally {
    api.webNavigation.onCommitted.removeListener(onCommitted);
    api.webNavigation.onDOMContentLoaded.removeListener(onDcl);
    api.webNavigation.onErrorOccurred.removeListener(onError);
    api.webRequest.onCompleted.removeListener(onCompleted);
  }
}

const domCommand = (op) => (a, ctx) => runDom(op, a, ctx);
const commands = {
  async "window.open"({ visible }) {
    await closeAll(); // leftovers of a session that never ended
    const w = await api.windows.create({ url: "about:blank", ...(visible ? { focused: true } : hiddenProps()) });
    win = { id: w.id, visible: !!visible };
    spare = (w.tabs ?? (await api.tabs.query({ windowId: w.id })))[0].id;
    owned.set(spare, { popup: false });
    persist();
    return { windowId: w.id };
  },
  async "window.show"() {
    if (!win) throw new Err("no_window", "no work window");
    await api.windows.update(win.id, { state: "normal", focused: true });
    win.visible = true;
    persist();
    return null;
  },
  async "window.hide"() {
    if (!win) throw new Err("no_window", "no work window");
    await api.windows.update(win.id, { state: HIDDEN_STATE, ...hiddenProps() });
    win.visible = false;
    persist();
    return null;
  },
  async "window.close"() {
    await closeAll(); // not in plan section 3: ends a session (closing the last tab would also do it)
    return null;
  },
  async "tab.new"({ url }) {
    const target = url ? navigable(url) : "about:blank";
    if (!win) throw new Err("no_window", "call window.open first");
    let tab;
    try {
      if (spare !== null) {
        const id = spare;
        spare = null;
        tab = target === "about:blank" ? await api.tabs.get(id) : await api.tabs.update(id, { url: target });
      } else {
        tab = await api.tabs.create({ windowId: win.id, url: target, active: false });
      }
    } catch {
      throw new Err("no_window", "work window is gone, call window.open again");
    }
    owned.set(tab.id, { popup: false });
    persist();
    return { tabId: tab.id };
  },
  async "tab.goto"({ tabId, url, timeoutMs }, ctx) {
    const target = navigable(url);
    await pageTab(tabId, { nav: true });
    return navigate(tabId, () => api.tabs.update(tabId, { url: target }), timeoutMs ?? GOTO_TIMEOUT_MS, ctx);
  },
  async "tab.reload"({ tabId, timeoutMs }, ctx) {
    await pageTab(tabId, { nav: true });
    return navigate(tabId, () => api.tabs.reload(tabId), timeoutMs ?? GOTO_TIMEOUT_MS, ctx);
  },
  async "tab.close"({ tabId }) {
    need(tabId);
    await api.tabs.remove(tabId).catch(() => {}); // already gone is fine; onRemoved cleans up
    return null;
  },
  async "tab.info"({ tabId }) {
    const tab = await getTab(tabId);
    const url = currentUrl(tab);
    return {
      tabId, url: safeUrl(url), host: parse(url)?.hostname ?? "", status: tab.status ?? null,
      windowId: tab.windowId, openerTabId: tab.openerTabId ?? null,
    };
  },
  async "tab.focus"({ tabId }) {
    const tab = await getTab(tabId);
    await api.tabs.update(tabId, { active: true });
    await api.windows.update(tab.windowId, { focused: true });
    return null;
  },
  async "tab.html"({ tabId, maxBytes }, ctx) {
    await pageTab(tabId);
    const limit = Number.isInteger(maxBytes) && maxBytes > 0 ? maxBytes : 2_000_000;
    return inject(tabId, djDom.html, () => [limit], { budgetMs: 3000, signal: ctx.signal });
  },
  async "tab.capture"({ tabId }) {
    // Best effort, diagnostics only: needs the tab active in a visible window.
    const tab = await pageTab(tabId);
    await api.tabs.update(tabId, { active: true });
    const png = await api.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    return { png: png.replace(/^data:image\/png;base64,/, "") };
  },
  "dom.count": domCommand("count"),
  "dom.visible": domCommand("visible"),
  "dom.attr": domCommand("attr"),
  "dom.text": domCommand("text"),
  "dom.value": domCommand("value"),
  "dom.all": domCommand("all"),
  "dom.click": domCommand("click"),
  "dom.fill": domCommand("fill"),
  "dom.waitVisible": domCommand("waitVisible"),
  async "dom.call"({ tabId, name }, ctx) {
    if (typeof name !== "string" || !Object.hasOwn(djDom.main, name)) throw new Err("bad_args", "unknown function");
    await pageTab(tabId);
    const result = await inject(tabId, djDom.main[name], () => [], { budgetMs: 3000, world: "MAIN", signal: ctx.signal });
    if (JSON.stringify(result ?? null).length > 100_000) throw new Err("too_large", "page returned too much data");
    return result; // untrusted: the page can replace the globals the function reads
  },
  async "cookie.soundcloudToken"() {
    const c = await api.cookies.get({ url: "https://soundcloud.com/", name: "oauth_token" });
    return c ? c.value : null;
  },
  async "cookie.clearGateDownload"() {
    const g = allow.groups.hypeddit;
    let removed = 0;
    for (const h of g.hosts.flatMap((x) => [x, "www." + x])) {
      if (await api.cookies.remove({ url: `https://${h}/`, name: "filedownloading" })) removed++;
    }
    return { removed };
  },
  async "download.discard"({ downloadId }) {
    if (!downloads.has(downloadId)) throw new Err("not_owned", "download was not attributed to an own tab");
    await api.downloads.removeFile(downloadId).catch(() => {}); // already moved or deleted
    await api.downloads.erase({ id: downloadId });
    downloads.delete(downloadId);
    persist();
    return null;
  },
};

// ---------------------------------------------------------------- browser events

// Registered synchronously (a service worker must), but handled once the allowlist and the
// registry are loaded.
const on = (event, fn, ...extra) => event.addListener((...a) => ready.then(() => fn(...a)), ...extra);

on(api.tabs.onCreated, (tab) => {
  const opener = tab.openerTabId;
  if (opener == null || !owned.has(opener) || owned.has(tab.id)) return; // popup of an own tab
  owned.set(tab.id, { popup: true });
  persist();
  emit({ event: "tab.created", tabId: tab.id, openerTabId: opener, url: safeUrl(tab.pendingUrl || tab.url || "") });
});
on(api.tabs.onUpdated, (tabId, change) => {
  if (owned.has(tabId) && change.url !== undefined) emit({ event: "tab.updated", tabId, url: safeUrl(change.url) });
});
on(api.tabs.onRemoved, (tabId) => {
  if (!owned.delete(tabId)) return;
  if (spare === tabId) spare = null;
  closing.delete(tabId);
  persist();
  emit({ event: "tab.removed", tabId });
});
on(api.windows.onRemoved, (windowId) => {
  if (win?.id === windowId) {
    win = null;
    spare = null;
  }
});

// Checkout guard: an own tab heading to a forbidden path is closed and reported.
const closing = new Set();
function guard(d) {
  if (!owned.has(d.tabId) || closing.has(d.tabId)) return;
  const u = parse(d.url);
  const g = u && groupOf(u);
  if (!g || !isForbidden(g, cleanPath(u))) return;
  closing.add(d.tabId);
  api.tabs.remove(d.tabId).catch(() => {});
  emit({ event: "tab.error", tabId: d.tabId, code: "forbidden_path", url: safeUrl(d.url) });
}
on(api.webNavigation.onBeforeNavigate, guard);
on(api.webNavigation.onCommitted, guard); // also catches a redirect into a forbidden path

// Download attribution (plan D5): a response seen in an own tab with the download's URL.
const recent = []; // {url, tabId, t}
const note = (d, url) => {
  if (!owned.has(d.tabId)) return;
  recent.push({ url, tabId: d.tabId, t: Date.now() });
  if (recent.length > 200) recent.shift();
};
const responses = { urls: manifest.host_permissions };
on(api.webRequest.onResponseStarted, (d) => note(d, d.url), responses);
on(api.webRequest.onBeforeRedirect, (d) => {
  note(d, d.url);
  note(d, d.redirectUrl);
}, responses);

const blocked = new Set();
function block(downloadId, tabId, reason) {
  if (blocked.has(downloadId)) return;
  blocked.add(downloadId);
  emit({ event: "download.blocked", downloadId, tabId, reason });
}
on(api.downloads.onCreated, (item) => {
  const tabs = new Set(
    recent.filter((r) => Date.now() - r.t < 60000 && (r.url === item.url || r.url === item.finalUrl)).map((r) => r.tabId)
  );
  if (tabs.size !== 1) return; // not attributable: ignored, never reported as a row's file
  const [tabId] = tabs;
  downloads.set(item.id, tabId);
  persist();
  emit({ event: "download.created", downloadId: item.id, tabId, danger: item.danger });
  if (item.danger !== "safe") return block(item.id, tabId, `danger: ${item.danger}`);
  // ponytail: a "where to save" prompt is recognised by a filename still missing after 5 s (spike R5)
  setTimeout(async () => {
    const [now] = await api.downloads.search({ id: item.id }).catch(() => []);
    if (now?.state === "in_progress" && !now.filename && !now.paused) block(item.id, tabId, "save_as_dialog");
  }, 5000);
});
on(api.downloads.onChanged, async (delta) => {
  const tabId = downloads.get(delta.id);
  if (tabId === undefined) return;
  if (delta.danger && delta.danger.current !== "safe") return block(delta.id, tabId, `danger: ${delta.danger.current}`);
  const state = delta.state?.current;
  if (state === "interrupted") {
    emit({ event: "download.failed", downloadId: delta.id, error: delta.error?.current ?? "interrupted" });
  } else if (state === "complete") {
    const [item] = await api.downloads.search({ id: delta.id });
    if (item.danger !== "safe") return block(delta.id, tabId, `danger: ${item.danger}`);
    emit({
      event: "download.done", downloadId: delta.id, path: item.filename, filename: item.filename,
      bytes: item.fileSize >= 0 ? item.fileSize : item.totalBytes, mime: item.mime,
    });
  }
});

// ---------------------------------------------------------------- start

const ready = (async () => {
  allow = await (await fetch(api.runtime.getURL("allowlist.json"))).json();
  await restore();
  api.alarms.create("djd-reconnect", { periodInMinutes: 0.5 });
  connect();
})();
api.alarms.onAlarm.addListener(() => ready.then(connect));
api.runtime.onInstalled.addListener((d) => {
  if (d.reason === "install") api.tabs.create({ url: api.runtime.getURL("welcome.html") });
});
