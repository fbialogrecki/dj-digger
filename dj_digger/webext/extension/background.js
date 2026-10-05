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

// One work window per session; several sessions (cart, gate batch, sign-in) run at once, each in its
// own window, and a command only ever touches the window and tabs of its own windowId.
const windows = new Map(); // windowId -> {visible, spare}; spare = blank tab reused by the first tab.new
const owned = new Map(); // tabId -> {popup, windowId}; a popup belongs to its opener's window
const downloads = new Map(); // attributed downloadId -> tabId
const closing = new Set(); // tabs the checkout guard is already closing
const persist = () =>
  api.storage.session
    .set({ djdRegistry: { windows: [...windows], owned: [...owned], downloads: [...downloads] } })
    .catch(() => {});
const setStatus = (s) => api.storage.session.set({ djdStatus: { instance: INSTANCE, since: Date.now(), ...s } }).catch(() => {});

// A restarted background has a new INSTANCE, so the app has ended every session of the old one: the
// windows it left behind belong to nobody and are closed (a window of a live session is never here).
async function restore() {
  const { djdRegistry: r } = await api.storage.session.get("djdRegistry");
  if (!r) return;
  for (const [id] of r.owned ?? []) await api.tabs.remove(id).catch(() => {});
  for (const [id] of r.windows ?? []) await api.windows.remove(id).catch(() => {});
  for (const [id, tabId] of r.downloads ?? []) downloads.set(id, tabId);
  persist();
}
function workWindow(windowId) {
  const w = windows.get(windowId);
  if (!w) throw new Err("no_window", "no such work window, call window.open first");
  return w;
}
// Ends one work window: its tabs (popups elsewhere included) are closed and reported with tab.removed,
// then window.removed. Used for window.close, a window the user closed by hand and app-offline.
async function dropWindow(windowId) {
  if (!windows.delete(windowId)) return; // not ours, or already dropped: never touch it
  const ids = [...owned].filter(([, v]) => v.windowId === windowId).map(([id]) => id);
  for (const id of ids) {
    owned.delete(id);
    closing.delete(id);
    emit({ event: "tab.removed", tabId: id });
  }
  emit({ event: "window.removed", windowId });
  persist();
  await Promise.all(ids.map((id) => api.tabs.remove(id).catch(() => {})));
  await api.windows.remove(windowId).catch(() => {}); // already gone is fine
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
  if (m.event === "app-offline") {
    // the relay lost the app: its sessions are over, leave no work window behind
    for (const id of [...windows.keys()]) dropWindow(id);
    return;
  }
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

const gone = () => {
  throw new Err("no_window", "work window is gone, call window.open again");
};
const domCommand = (op) => (a, ctx) => runDom(op, a, ctx);
const commands = {
  async "window.open"({ visible }) {
    // always a NEW window: the windows of other sessions are none of its business
    const w = await api.windows.create({ url: "about:blank", ...(visible ? { focused: true } : hiddenProps()) });
    const spare = (w.tabs ?? (await api.tabs.query({ windowId: w.id })))[0].id;
    windows.set(w.id, { visible: !!visible, spare });
    owned.set(spare, { popup: false, windowId: w.id });
    persist();
    return { windowId: w.id };
  },
  async "window.show"({ windowId }) {
    const w = workWindow(windowId);
    await api.windows.update(windowId, { state: "normal", focused: true }).catch(gone);
    w.visible = true;
    persist();
    return null;
  },
  async "window.hide"({ windowId }) {
    const w = workWindow(windowId);
    await api.windows.update(windowId, { state: HIDDEN_STATE, ...hiddenProps() }).catch(gone);
    w.visible = false;
    persist();
    return null;
  },
  async "window.close"({ windowId }) {
    await dropWindow(windowId); // not in plan section 3: ends a session; unknown or closed already is fine
    return null;
  },
  async "tab.new"({ windowId, url }) {
    const target = url ? navigable(url) : "about:blank";
    const w = workWindow(windowId);
    let tab;
    try {
      if (w.spare !== null) {
        const id = w.spare;
        w.spare = null;
        tab = target === "about:blank" ? await api.tabs.get(id) : await api.tabs.update(id, { url: target });
      } else {
        tab = await api.tabs.create({ windowId, url: target, active: false });
      }
    } catch {
      throw new Err("no_window", "work window is gone, call window.open again");
    }
    owned.set(tab.id, { popup: false, windowId });
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
  owned.set(tab.id, { popup: true, windowId: owned.get(opener).windowId });
  persist();
  emit({ event: "tab.created", tabId: tab.id, openerTabId: opener, url: safeUrl(tab.pendingUrl || tab.url || "") });
});
on(api.tabs.onUpdated, (tabId, change) => {
  if (owned.has(tabId) && change.url !== undefined) emit({ event: "tab.updated", tabId, url: safeUrl(change.url) });
});
on(api.tabs.onRemoved, (tabId) => {
  if (!owned.delete(tabId)) return;
  for (const w of windows.values()) if (w.spare === tabId) w.spare = null;
  closing.delete(tabId);
  persist();
  emit({ event: "tab.removed", tabId });
});
on(api.windows.onRemoved, dropWindow); // closed by hand: same events as window.close

// Checkout guard: an own tab heading to a forbidden path is closed and reported.
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
