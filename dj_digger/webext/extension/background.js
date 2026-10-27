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
const HIDDEN_STATE = "minimized"; // spike R2 on Wayland: an unfocused window still pops up
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
// Beatport serves every route under a locale prefix too (/de/checkout/…), so the bare path counts.
const isForbidden = (g, p) => {
  const bare = p.replace(/^\/[a-z]{2}(?:-[a-z]{2})?(?=\/|$)/, "");
  return g.forbiddenPaths.some((f) => [p, bare].some((q) => q === f || q.startsWith(f + "/")));
};
// Non-allowlisted URLs (OAuth popups carry code= and state=) are reduced to their host; an allowlisted
// one loses the query parameters that carry a credential (an OAuth callback back at the gate's site, the
// jwt on ToneDen's download link).
const SECRET_PARAMS = ["code", "state", "jwt", "token", "access_token", "oauth_token"];
function safeUrl(url) {
  const u = parse(url);
  if (!u) return "";
  if (url === "about:blank") return u.href;
  if (groupOf(u)) {
    if (SECRET_PARAMS.some((k) => u.searchParams.has(k))) for (const k of SECRET_PARAMS) u.searchParams.delete(k);
    return u.href;
  }
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
// Background sessions (carts, plan F10/Z1): a virtual window whose tabs are inactive tabs of the
// person's own last focused window ({background: true, host}); nothing new appears on screen, and
// closing the session closes only its tabs, never the person's window.
const owned = new Map(); // tabId -> {popup, windowId}; a popup belongs to its opener's window
const downloads = new Map(); // attributed downloadId -> tabId
// Offscreen sessions (Chromium, plan F4b): a virtual window has a negative id and no browser window, a
// virtual tab is an iframe of offscreen.html. -1 is the browser's "no tab" (webRequest reports the
// requests of the offscreen document as tab -1), so virtual ids start at -2.
const frames = new Map(); // virtual tabId -> {nonce, windowId, url, loads, port, pending, seq}
let nextVirtual = -2;
const isVirtual = (id) => id < 0;
const closing = new Set(); // tabs the checkout guard is already closing
const persist = () =>
  api.storage.session
    .set({ djdRegistry: { windows: [...windows], owned: [...owned], downloads: [...downloads] } })
    .catch(() => {});
const setStatus = (s) => api.storage.session.set({ djdStatus: { instance: INSTANCE, since: Date.now(), ...s } }).catch(() => {});

// A restarted background has a new INSTANCE, so the app has ended every session of the old one: the
// windows it left behind belong to nobody and are closed (a window of a live session is never here).
async function restore() {
  await closeOffscreen(); // a previous instance's offscreen document and frame script
  const { djdRegistry: r } = await api.storage.session.get("djdRegistry");
  if (!r) return;
  for (const [id] of r.owned ?? []) if (!isVirtual(id)) await api.tabs.remove(id).catch(() => {});
  for (const [id, w] of r.windows ?? []) {
    if (!isVirtual(id)) await api.windows.remove(id).catch(() => {});
    else if (w?.ownsHost) await api.windows.remove(w.host).catch(() => {}); // a window it had to make
  }
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
  const w = windows.get(windowId);
  if (!w) return; // not ours, or already dropped: never touch it
  windows.delete(windowId);
  const ids = [...owned].filter(([, v]) => v.windowId === windowId).map(([id]) => id);
  for (const id of ids) {
    owned.delete(id);
    closing.delete(id);
    killFrame(id);
    emit({ event: "tab.removed", tabId: id });
  }
  emit({ event: "window.removed", windowId });
  persist();
  if (w.offscreen) {
    // its frames go with the document; a provider tab a frame opened is a real tab
    await Promise.all(ids.filter((id) => !isVirtual(id)).map((id) => api.tabs.remove(id).catch(() => {})));
    if (![...windows.values()].some((x) => x.offscreen)) await closeOffscreen();
    return;
  }
  await Promise.all(ids.map((id) => api.tabs.remove(id).catch(() => {})));
  if (w.background && !w.ownsHost) return; // the person's window stays
  await api.windows.remove(w.background ? w.host : windowId).catch(() => {}); // already gone is fine
}
function need(tabId) {
  if (!owned.has(tabId)) throw new Err("not_owned", "tab is not owned by dj-digger");
}
// A command that needs a real browser tab or window: a virtual one has none.
function offscreenRefused() {
  return new Err("offscreen", "not available in the offscreen window; it runs without a browser window");
}
function realTab(tabId) {
  need(tabId);
  if (isVirtual(tabId)) throw offscreenRefused();
}
const vtab = (id) => {
  const f = frames.get(id);
  if (!f) throw new Err("tab_closed", "tab no longer exists");
  return { id, windowId: f.windowId, url: f.url, status: f.port ? "complete" : "loading" };
};
async function getTab(tabId) {
  need(tabId);
  if (isVirtual(tabId)) return vtab(tabId);
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

// ---------------------------------------------------------------- offscreen sessions (plan F4b)

// The hidden gate pass runs in iframes of offscreen.html (Chromium: no browser window at all), for the
// allowlist groups marked "frames" (Hypeddit, ToneDen).
// Two kinds of port reach the one onConnect listener below, and nothing else does:
//   "offscreen"  from offscreen.html itself: background -> it {op:"load"|"remove", nonce, url}
//   <nonce>      from frame.js, the content script of a gate framed by offscreen.html:
//                background -> frame {id, op:"run"|"html"|"cancel", ...}, frame -> background {id, res}
//                or {op:"open", url}: the page's window.open, honoured only for popupUrl()
// A frame's port is admitted only when it has no tab, comes from a frame below the top one, from an
// allowlisted URL of a "frames" group, under the nonce of a live virtual tab (admit()); everything it
// sends is untrusted page data.
const OFFSCREEN_PORT = "offscreen";
const FRAME_SCRIPTS = ["djd-frame", "djd-frame-main"];
const PORT_WAIT_MS = 5000;
let offscreenPort = null; // the offscreen document's port
let offscreenStart = null; // promise of the running or finished start
let offscreenHello = null;

const tellOffscreen = (m) => {
  try {
    offscreenPort?.postMessage(m);
  } catch {
    // its disconnect handler ends the sessions
  }
};

async function startOffscreen() {
  const matches = Object.values(allow.groups).filter((g) => g.frames).flatMap((g) => g.hosts.flatMap((h) => [
    `https://${h}/*`, ...(g.www ? [`https://www.${h}/*`] : []), ...(g.subdomains ? [`https://*.${h}/*`] : []),
  ]));
  const connected = new Promise((r) => (offscreenHello = r));
  await api.scripting.unregisterContentScripts({ ids: FRAME_SCRIPTS }).catch(() => {});
  await api.scripting.registerContentScripts([
    { id: FRAME_SCRIPTS[0], matches, js: ["dom.js", "frame.js"], allFrames: true, runAt: "document_idle", persistAcrossSessions: false },
    // page world, before the page's own scripts: no popups and no sound from a framed gate
    { id: FRAME_SCRIPTS[1], matches, js: ["frame-main.js"], allFrames: true, runAt: "document_start", world: "MAIN", persistAcrossSessions: false },
  ]);
  await api.offscreen.createDocument({
    url: "offscreen.html", reasons: ["IFRAME_SCRIPTING"],
    justification: "Runs download gates (Hypeddit, ToneDen) in hidden frames, since a hidden browser window cannot be made on every desktop.",
  });
  await Promise.race([connected, sleep(PORT_WAIT_MS).then(() => Promise.reject(new Err("offscreen", "offscreen document did not start")))]);
}
// True when offscreen sessions work here (not in Firefox); a failed start is cleaned up and means false.
let offscreenWhyNot = ""; // why the last start was refused, told to the app with offscreen:false
async function openOffscreen() {
  if (!api.offscreen?.createDocument || !api.scripting?.registerContentScripts) {
    offscreenWhyNot = "this browser has no offscreen documents";
    return false;
  }
  try {
    await (offscreenStart ??= startOffscreen());
    return true;
  } catch (e) {
    offscreenWhyNot = redactMessage(e?.message ?? String(e));
    await closeOffscreen();
    return false;
  }
}
async function closeOffscreen() {
  offscreenStart = null;
  offscreenPort = null;
  await api.scripting?.unregisterContentScripts?.({ ids: FRAME_SCRIPTS }).catch(() => {});
  await api.offscreen?.closeDocument().catch(() => {});
}

function newFrame(windowId, url) {
  const tabId = nextVirtual--;
  const nonce = crypto.randomUUID();
  frames.set(tabId, { nonce, windowId, url: "about:blank", loads: 0, port: null, pending: new Map(), seq: 0 });
  owned.set(tabId, { popup: false, windowId });
  persist();
  tellOffscreen({ op: "load", nonce, url });
  return tabId;
}
// The frame is removed and anything waiting on it is released; the caller reports and unregisters it.
function killFrame(tabId) {
  const f = frames.get(tabId);
  if (!f) return;
  frames.delete(tabId);
  tellOffscreen({ op: "remove", nonce: f.nonce });
  f.port?.disconnect();
  f.port = null;
  for (const done of f.pending.values()) done(undefined);
}

// Decides who a new port is: "offscreen", a frame entry, or null (disconnected at once).
function admit(p) {
  const s = p.sender ?? {};
  const f = [...frames.values()].find((x) => x.nonce === p.name);
  // No tab here: the offscreen document and its frames are not in one. Chrome sets frameId only
  // together with tab, so a port is told by its URL and the frame's nonce, never by frameId.
  if (!s.tab) {
    if (p.name === OFFSCREEN_PORT && s.url === api.runtime.getURL("offscreen.html") && offscreenStart) {
      offscreenPort = p;
      offscreenHello?.();
      return OFFSCREEN_PORT;
    }
    if (f && groupOf(s.url)?.frames) {
      f.port?.disconnect(); // an older document of the same frame
      Object.assign(f, { port: p, url: s.url });
      f.loads++;
      emit({ event: "tab.updated", tabId: [...frames].find(([, x]) => x === f)[0], url: safeUrl(s.url) });
      return f;
    }
  }
  p.disconnect();
  return null;
}
// The popups a framed gate may open (plan F10), each as an inactive tab without an opener, by the group
// of the frame's own page:
// - Hypeddit: Spotify's sign-in for its Connect step; the callback tells the gate through Hypeddit's
//   storage, so no opener is needed.
// - ToneDen: its own sign-in start, /auth/soundcloud or /auth/spotify (it goes on to the provider and
//   back to ToneDen, whose server then knows the fan; the app reloads the gate), and the unlocked file,
//   /api/v1/attachments/<id>/download.
function popupUrl(g, url) {
  const u = parse(url);
  if (!u || u.protocol !== "https:" || u.username || u.password || u.port) return null;
  if (g === allow.groups.hypeddit && u.hostname === "accounts.spotify.com" && u.pathname === "/authorize") {
    const back = parse(u.searchParams.get("redirect_uri") ?? "");
    return back && groupOf(back) === allow.groups.hypeddit ? u.href : null;
  }
  const toneden = /^\/auth\/(soundcloud|spotify)$|^\/api\/v1\/attachments\/\d+\/download$/;
  return g === allow.groups.toneden && groupOf(u) === g && toneden.test(u.pathname) ? u.href : null;
}
async function openFramePopup(f, url) {
  const href = popupUrl(groupOf(f.url), url);
  const opener = [...frames].find(([, x]) => x === f)?.[0];
  if (!href || opener == null) return;
  const host = (await api.windows.getLastFocused({ windowTypes: ["normal"] }).catch(() => null))?.id;
  if (host == null) return; // no window to keep it out of the way in: the row is deferred as before
  const tab = await api.tabs.create({ windowId: host, url: href, active: false }).catch(() => null);
  if (!tab || !frames.has(opener)) return void (tab && api.tabs.remove(tab.id).catch(() => {}));
  owned.set(tab.id, { popup: true, windowId: f.windowId });
  persist();
  mute(tab.id);
  emit({ event: "tab.created", tabId: tab.id, openerTabId: opener, url: safeUrl(href) });
}
function onPortMessage(who, m) {
  if (who === OFFSCREEN_PORT || !m || typeof m !== "object") return;
  if (m.op === "open") return void openFramePopup(who, m.url);
  const done = who.pending.get(m.id);
  who.pending.delete(m.id);
  done?.(m.res !== null && typeof m.res === "object" ? m.res : { ok: false, code: "dom_error", message: "bad reply from the frame" });
}
function onPortGone(who, p) {
  if (who === OFFSCREEN_PORT) {
    if (offscreenPort !== p) return; // closed on purpose
    offscreenPort = null; // the document is gone: so are its frames
    for (const [id, w] of windows) if (w.offscreen) dropWindow(id);
    return;
  }
  if (who.port !== p) return;
  who.port = null;
  for (const done of who.pending.values()) done(undefined);
  who.pending.clear();
}
// The only onConnect listener; the listeners on the port itself are added at once, the
// decision follows when the allowlist is loaded.
api.runtime.onConnect.addListener((p) => {
  const who = ready.then(() => admit(p));
  p.onMessage.addListener((m) => who.then((w) => w && onPortMessage(w, m)));
  p.onDisconnect.addListener(() => who.then((w) => w && onPortGone(w, p)));
});

// inject() for a virtual tab: djDom.run or djDom.html runs in the frame's content script.
async function viaFrame(tabId, op, makeArgs, { budgetMs, signal }) {
  const end = Date.now() + budgetMs;
  for (;;) {
    if (signal.aborted) throw new Err("cancelled", "command cancelled");
    const f = frames.get(tabId);
    if (!f) throw new Err("tab_closed", "tab no longer exists");
    if (f.port) {
      const arg = makeArgs(Math.max(0, end - Date.now()))[0];
      const res = await new Promise((done) => {
        const id = ++f.seq;
        f.pending.set(id, done);
        try {
          f.port.postMessage(op === "html" ? { id, op, maxBytes: arg } : { id, op, spec: arg });
        } catch {
          f.pending.delete(id);
          done(undefined);
        }
      });
      if (res !== undefined) return res;
    }
    if (Date.now() >= end) throw new Err("timeout", "page did not answer");
    await sleep(100); // the document is being replaced: wait for the next one
  }
}
// goto/reload of a virtual tab: ready when the new document's content script has connected.
async function navigateFrame(tabId, url, timeoutMs, ctx) {
  const f = frames.get(tabId);
  if (!f) throw new Err("tab_closed", "tab no longer exists");
  const seen = f.loads;
  tellOffscreen({ op: "load", nonce: f.nonce, url });
  await pollUntil(() => f.loads > seen || !frames.has(tabId), timeoutMs, ctx.signal, "page load");
  if (!frames.has(tabId)) throw new Err("tab_closed", "tab no longer exists");
  return { url: safeUrl(f.url), status: null }; // no webRequest status for a frame
}

// ---------------------------------------------------------------- native port and events

let port = null;
let failures = 0;
let missing = false; // the last disconnect said there is no usable host (NO_HOST)
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
const siteAccess = () => api.permissions.contains({ origins: manifest.host_permissions });
async function hello() {
  const info = await browserInfo();
  const permissions = await siteAccess();
  send({
    event: "hello", browser: info.name, version: manifest.version, browserVersion: info.version,
    protocol: PROTOCOL, instance: INSTANCE, permissions,
  });
}
// Site access granted or taken back in the browser's extension settings: the app learns it live.
const sitesChanged = () => siteAccess().then((granted) => emit({ event: "permissions", granted }), () => {});
api.permissions.onAdded.addListener(sitesChanged);
api.permissions.onRemoved.addListener(sitesChanged);
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
    if (missing) {
      missing = false; // the host was installed meanwhile
      setStatus({ connected: true });
    }
    onMessage(m);
  });
  p.onDisconnect.addListener(() => {
    const e = api.runtime.lastError ?? p.error;
    port = null;
    setPlayer(null);
    retry(e);
  });
  // A missing host only ever disconnects: until a host answers, the status stays "missing" between retries
  // (the relay answers at once, app-offline without the app).
  if (!missing) setStatus({ connected: true });
  hello().catch(() => {});
}
// No usable host: never registered (the app is not installed or never started: Chrome "Specified native
// messaging host not found.", Firefox "No such native application ..."), or registered by an app too old
// to list this extension's ID ("... is forbidden.", Firefox "... does not have permission ..."). The
// welcome page and the panel then offer the download of the desktop app.
const NO_HOST = /not found|no such native application|forbidden|not have permission/i;
function retry(e) {
  const error = redactMessage(e?.message);
  missing = NO_HOST.test(error);
  setStatus({ connected: false, error, missing });
  setTimeout(connect, Math.min(30000, 1000 * 2 ** failures++));
}

// ---------------------------------------------------------------- player panel (popup.html)

// The app sends {cmd: "player.state", args} (no id, no reply) when its player, its running job or the gates
// waiting for a person change, and on hello. It is kept, checked, in storage.session (djdPlayer; null while
// the app is gone), where the popup reads it, and never leaves the browser; `needs` is also the action's
// badge. The popup asks by runtime.sendMessage; only the popup page of this extension is heard (frames, the
// offscreen document and other content scripts are not; soundcloud.js only for an import), and a checked
// command (command()) goes to the app as {event: "player.command", op, ...}. The context menu's "Dig in
// dj-digger" and soundcloud.js's "Import to DJ digger" send the same import.
const PLAYER_OPS = ["prev", "toggle", "next", "cancel"];
const MAX_ARTWORK = 48 * 1024;
const ARTWORK = /^data:image\/(?:jpeg|png);base64,[A-Za-z0-9+/]+={0,2}$/;
const COLOR = /^#[0-9a-f]{6}$/i;
const cut = (v, n) => (typeof v === "string" ? v.slice(0, n) : "");
const count = (v, max) => (Number.isInteger(v) && v >= 0 && v <= max ? v : 0);
const seconds = (v, max) => (typeof v === "number" && v >= 0 && v <= max ? v : 0); // NaN fails both
function playerState(a) {
  a = a && typeof a === "object" ? a : {};
  const j = a.jobs && typeof a.jobs === "object" ? a.jobs : {};
  const colors = Array.isArray(a.colors) && a.colors.length === 4 && a.colors.every((c) => typeof c === "string" && COLOR.test(c)) ? a.colors : null;
  return {
    title: cut(a.title, 300), artist: cut(a.artist, 300), playing: a.playing === true,
    position: seconds(a.position, 1e6), duration: seconds(a.duration, 1e6), at: seconds(a.at, 1e11),
    seed: Number.isInteger(a.seed) && a.seed >= 0 && a.seed < 2 ** 32 ? a.seed : 7,
    artwork: typeof a.artwork === "string" && a.artwork.length <= MAX_ARTWORK && ARTWORK.test(a.artwork) ? a.artwork : "",
    colors, dark: a.dark === true,
    jobs: { busy: j.busy === true, label: cut(j.label, 120), done: count(j.done, 1e9), total: count(j.total, 1e9) },
    needs: count(a.needs, 999),
  };
}
const quiet = (p) => Promise.resolve(p).catch(() => {}); // Chrome before 123 answers some of these with no promise
function setPlayer(s) {
  api.storage.session.set({ djdPlayer: s }).catch(() => {});
  const needs = s?.needs ?? 0;
  quiet(api.action.setBadgeText({ text: needs ? String(needs) : "" }));
  if (needs) quiet(api.action.setBadgeBackgroundColor({ color: s.colors?.[2] ?? "#ff0094" })); // the neon foot
  showMenus(!!s);
}
// the import entries only while an app is there to take them
const showMenus = (on) => MENUS.forEach((id) => quiet(api.contextMenus.update(id, { visible: on })));

// "Dig in dj-digger": a SoundCloud set, track or profile, from the popup (the active tab's URL, read only
// once the person opened it: activeTab; sets and profiles only), a context-menu click (that link, or that
// page) or soundcloud.js's "Import to DJ digger" (a track or set). importTarget() is the only gate: https,
// exactly soundcloud.com, www. or m., no credentials or port, at most 512 characters, the path
// /<user>/sets/<name>, /<user>/<track>, /<user> or /<user>/<tab>; it returns the canonical
// https://soundcloud.com/... link, without query or fragment.
const SC_HOSTS = ["soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"];
const SC_SET = /^\/([A-Za-z0-9_-]+)\/sets\/([A-Za-z0-9_-]+)\/?$/;
const SC_PROFILE = /^\/([A-Za-z0-9_-]+)(?:\/(?:sets|tracks|albums|popular-tracks|reposts))?\/?$/;
const SC_TRACK = /^\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)\/?$/; // after SC_PROFILE, so a profile tab is never one
const SC_NOT_TRACKS = new Set(["likes", "followers", "following", "comments", "spotlight"]);
// SoundCloud's own pages that look like a profile
const SC_PAGES = new Set(["discover", "feed", "stream", "you", "search", "upload", "charts", "settings", "messages",
  "notifications", "pages", "signin", "logout", "people", "tags", "terms-of-use", "pro", "mobile", "jobs", "imprint"]);
function importTarget(url) {
  const u = typeof url === "string" && url.length <= 512 ? parse(url) : null;
  if (!u || u.protocol !== "https:" || u.username || u.password || u.port || !SC_HOSTS.includes(u.hostname)) return null;
  const set = SC_SET.exec(u.pathname);
  if (set) return { kind: "set", url: `https://soundcloud.com/${set[1]}/sets/${set[2]}` };
  const profile = SC_PROFILE.exec(u.pathname);
  const track = profile ? null : SC_TRACK.exec(u.pathname);
  const user = (profile ?? track)?.[1];
  if (!user || SC_PAGES.has(user.toLowerCase())) return null;
  if (profile) return { kind: "profile", url: `https://soundcloud.com/${user}` };
  return SC_NOT_TRACKS.has(track[2]) ? null : { kind: "track", url: `https://soundcloud.com/${user}/${track[2]}` };
}
// What the popup may ask of the app, checked; null for anything else.
function command(m) {
  if (PLAYER_OPS.includes(m.op)) return { op: m.op };
  if (m.op === "seek" && typeof m.fraction === "number" && m.fraction >= 0 && m.fraction <= 1) return { op: "seek", fraction: m.fraction };
  if (m.op === "gates" && (m.choice === "open" || m.choice === "skip")) return { op: "gates", choice: m.choice };
  const target = m.op === "import" ? importTarget(m.url) : null;
  return target && { op: "import", url: target.url };
}
// soundcloud.js, in the top frame of a soundcloud.com tab: an import and nothing else, answered with
// whether the app was there to take it
const fromSoundCloud = (sender) => !!sender.tab && sender.frameId === 0 && parse(sender.url)?.origin === "https://soundcloud.com";
api.runtime.onMessage.addListener((m, sender, reply) => {
  if (sender.id !== api.runtime.id || !m || typeof m !== "object") return;
  if (fromSoundCloud(sender)) {
    const c = m.op === "import" ? command(m) : null;
    if (!c) return;
    api.storage.session.get("djdPlayer").then(({ djdPlayer }) => !!(djdPlayer && port), () => false).then((app) => {
      if (app) send({ event: "player.command", ...c });
      reply({ ok: app });
    });
    return true; // answered once storage says whether the app is there
  }
  if (sender.url !== api.runtime.getURL("popup.html")) return;
  if (m.op === "page") { // what the popup may offer: a set or a profile
    const kind = importTarget(m.url)?.kind;
    return void reply({ kind: kind === "set" || kind === "profile" ? kind : null });
  }
  const c = command(m);
  if (c) send({ event: "player.command", ...c });
});
// Two entries: "page" is shown only where no link is under the pointer, so they never appear together.
const MENUS = ["djd-dig-link", "djd-dig-page"];
const SC_PATTERNS = SC_HOSTS.map((h) => `https://${h}/*`);
// Made on install, update and browser start; one that is already there (a duplicate id) is left as it is.
function createMenus() {
  const done = () => void api.runtime.lastError;
  api.contextMenus.create({ id: MENUS[0], title: "Dig in dj-digger", contexts: ["link"], targetUrlPatterns: SC_PATTERNS, visible: false }, done);
  api.contextMenus.create({ id: MENUS[1], title: "Dig in dj-digger", contexts: ["page"], documentUrlPatterns: SC_PATTERNS, visible: false }, done);
  api.storage.session.get("djdPlayer").then(({ djdPlayer }) => showMenus(!!djdPlayer), () => {});
}
api.contextMenus.onClicked.addListener((info) => {
  const c = command({ op: "import", url: info.menuItemId === MENUS[0] ? info.linkUrl : info.pageUrl });
  if (c && MENUS.includes(info.menuItemId)) send({ event: "player.command", ...c });
});
api.runtime.onStartup.addListener(createMenus);

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
    setPlayer(null);
    return;
  }
  if (m.cmd === "player.state") return setPlayer(playerState(m.args)); // no id, no reply
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
  if (isVirtual(tabId)) return viaFrame(tabId, func === djDom.html ? "html" : "run", makeArgs, { budgetMs, signal });
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
    isVirtual(a.tabId)
      ? frames.get(a.tabId)?.port?.postMessage({ op: "cancel", token })
      : api.scripting
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
const ABORT_GRACE_MS = 3000;
// A navigation superseded by another (a redirect, the site's own next one): Chromium says net::ERR_ABORTED,
// Firefox "Error code <nsresult>" for NS_BINDING_ABORTED, NS_BINDING_REDIRECTED and NS_ERROR_ABORT.
const isAbort = (error) =>
  /^net::ERR_ABORTED$|NS_BINDING_(ABORTED|REDIRECTED)|NS_ERROR_ABORT|^Error code (2152398850|2152398859|2147500036)$/
    .test(error ?? "");
// Same document: origin and path (a redirect elsewhere is not the target).
const sameDoc = (a, b) => {
  const x = parse(a);
  const y = parse(b);
  return !!x && !!y && x.origin === y.origin && x.pathname === y.pathname;
};
async function navigate(tabId, start, target, timeoutMs, ctx) {
  const st = { committed: false, dcl: false, status: null, error: null };
  const top = (d) => d.tabId === tabId && d.frameId === 0;
  const onCommitted = (d) => top(d) && (st.committed = true);
  const onDcl = (d) => top(d) && st.committed && (st.dcl = true);
  const onError = (d) => top(d) && Object.assign(st, { error: d.error, errorAt: Date.now() });
  const onCompleted = (d) => (st.status = d.statusCode);
  const filter = { urls: manifest.host_permissions, tabId, types: ["main_frame"] };
  api.webNavigation.onCommitted.addListener(onCommitted);
  api.webNavigation.onDOMContentLoaded.addListener(onDcl);
  api.webNavigation.onErrorOccurred.addListener(onError);
  api.webRequest.onCompleted.addListener(onCompleted, filter);
  try {
    await start();
    // An aborted navigation is often just superseded by the site's own next one (Beatport track to
    // track): wait a little for that one to load before reporting the abort.
    const settled = () =>
      st.dcl || (st.error && (!isAbort(st.error) || Date.now() - st.errorAt > ABORT_GRACE_MS));
    const end = Date.now() + timeoutMs;
    let retried = false;
    for (;;) {
      await pollUntil(settled, end - Date.now(), ctx.signal, "page load");
      if (st.dcl) break;
      if (!isAbort(st.error)) throw new Err("navigation_failed", st.error);
      // The grace is over with no further event: ask the tab. Firefox aborts the first navigation of a
      // new window (its initial about:blank) and may then load the page, or nothing, without telling us.
      const tab = await getTab(tabId);
      const url = currentUrl(tab);
      if (tab.status === "complete" && sameDoc(url, target)) break; // it loaded, the events went missing
      if (tab.status === "complete" && isBlank(url) && !retried) {
        retried = true; // the navigation never started: once more
        await start();
      } else if (tab.status !== "loading") {
        throw new Err("navigation_failed", st.error);
      }
      st.errorAt = Date.now(); // still loading (or retried): another grace, within the timeout
    }
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
// Own tabs never make a sound: SoundCloud popups autoplay (spike R1).
const mute = (tabId) => api.tabs.update(tabId, { muted: true }).catch(() => {});
const commands = {
  async "window.open"({ visible, offscreen, background }) {
    if (background && !visible) {
      // the person's own window; a browser without one gets a new window (nothing else to use)
      let host = (await api.windows.getLastFocused({ windowTypes: ["normal"] }).catch(() => null))?.id;
      const ownsHost = host == null;
      if (ownsHost) host = (await api.windows.create({ url: "about:blank", focused: false })).id;
      const windowId = nextVirtual--;
      windows.set(windowId, { visible: false, spare: null, background: true, host, ownsHost });
      persist();
      return { windowId, offscreen: false, background: true };
    }
    // offscreen (hidden pass, Chromium): a virtual window without any browser window; elsewhere the
    // reply says offscreen:false and the session gets the minimized window as before
    if (offscreen && !visible && (await openOffscreen())) {
      const windowId = nextVirtual--;
      windows.set(windowId, { visible: false, spare: null, offscreen: true });
      persist();
      return { windowId, offscreen: true };
    }
    // always a NEW window: the windows of other sessions are none of its business
    const w = await api.windows.create({ url: "about:blank", ...(visible ? { focused: true } : hiddenProps()) });
    // Wayland ignores focused:false and may map a window before minimizing it (spike R2)
    if (!visible) await api.windows.update(w.id, hiddenProps()).catch(() => {});
    const spare = (w.tabs ?? (await api.tabs.query({ windowId: w.id })))[0].id;
    mute(spare);
    windows.set(w.id, { visible: !!visible, spare });
    owned.set(spare, { popup: false, windowId: w.id });
    persist();
    return { windowId: w.id, offscreen: false, ...(offscreen && !visible ? { offscreenError: offscreenWhyNot } : {}) };
  },
  async "window.show"({ windowId }) {
    const w = workWindow(windowId);
    if (w.offscreen) throw offscreenRefused();
    if (w.background) return null; // its tabs are already in the person's window: tab.focus shows one
    await api.windows.update(windowId, { state: "normal", focused: true }).catch(gone);
    w.visible = true;
    persist();
    return null;
  },
  async "window.hide"({ windowId }) {
    const w = workWindow(windowId);
    if (w.offscreen) throw offscreenRefused();
    if (w.background) return null;
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
    if (w.offscreen) return { tabId: newFrame(windowId, url ? target : null) };
    let tab;
    try {
      if (w.spare !== null) {
        const id = w.spare;
        w.spare = null;
        tab = target === "about:blank" ? await api.tabs.get(id) : await api.tabs.update(id, { url: target });
      } else {
        tab = await api.tabs.create({ windowId: w.background ? w.host : windowId, url: target, active: false });
      }
    } catch {
      throw new Err("no_window", "work window is gone, call window.open again");
    }
    owned.set(tab.id, { popup: false, windowId });
    persist();
    mute(tab.id);
    return { tabId: tab.id };
  },
  async "tab.goto"({ tabId, url, timeoutMs }, ctx) {
    const target = navigable(url);
    await pageTab(tabId, { nav: true });
    if (isVirtual(tabId)) return navigateFrame(tabId, target, timeoutMs ?? GOTO_TIMEOUT_MS, ctx);
    return navigate(tabId, () => api.tabs.update(tabId, { url: target }), target, timeoutMs ?? GOTO_TIMEOUT_MS, ctx);
  },
  async "tab.reload"({ tabId, timeoutMs }, ctx) {
    const tab = await pageTab(tabId, { nav: true });
    if (isVirtual(tabId)) return navigateFrame(tabId, navigable(frames.get(tabId).url), timeoutMs ?? GOTO_TIMEOUT_MS, ctx);
    return navigate(tabId, () => api.tabs.reload(tabId), currentUrl(tab), timeoutMs ?? GOTO_TIMEOUT_MS, ctx);
  },
  async "tab.close"({ tabId }) {
    need(tabId);
    if (isVirtual(tabId)) {
      killFrame(tabId);
      owned.delete(tabId);
      persist();
      emit({ event: "tab.removed", tabId });
      return null;
    }
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
  // Hands a tab over to the person (the finished cart): no longer owned, so nothing here closes,
  // drives or guards it any more - checkout from it is theirs.
  async "tab.release"({ tabId }) {
    realTab(tabId);
    // a window made for a browser that had none now holds the person's tab: it is theirs, never closed here
    const w = windows.get(owned.get(tabId).windowId);
    if (w?.background) w.ownsHost = false;
    owned.delete(tabId);
    persist();
    emit({ event: "tab.removed", tabId });
    return null;
  },
  async "tab.focus"({ tabId }) {
    realTab(tabId);
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
    realTab(tabId);
    if (windows.get(owned.get(tabId).windowId)?.background) {
      throw new Err("background", "a background tab is not brought forward for a screenshot");
    }
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
    realTab(tabId); // MAIN-world functions cannot run in an offscreen frame
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
  // The app downloads one gate file at a time; it names the tab right before that click, so a
  // CDN download with only an origin for a referrer is that tab's even when others share the origin.
  // Development: re-read the extension's files from disk (an unpacked install) without a trip to
  // chrome://extensions. The reply goes out first; the work windows close as on any restart.
  async "extension.reload"() {
    setTimeout(() => api.runtime.reload(), 200);
    return null;
  },
  async "download.expect"({ tabId }) {
    need(tabId);
    expected = { tabId, t: Date.now() };
    return null;
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

// A popup of an own tab is the opener's session's: muted, and closed with its window.
function adopt(tab, opener, url) {
  if (opener == null || !owned.has(opener) || owned.has(tab.id)) return;
  const windowId = owned.get(opener).windowId;
  owned.set(tab.id, { popup: true, windowId });
  persist();
  mute(tab.id);
  // a popup window of a hidden session is hidden too (never the person's window a background session uses)
  const w = windows.get(windowId);
  if (tab.windowId !== windowId && w?.visible === false && !w.background) {
    api.windows.update(tab.windowId, hiddenProps()).catch(() => {});
  }
  emit({ event: "tab.created", tabId: tab.id, openerTabId: opener, url: safeUrl(url) });
}
on(api.tabs.onCreated, (tab) => adopt(tab, tab.openerTabId, tab.pendingUrl || tab.url || ""));
// Firefox gives a window.open popup in a window of its own no openerTabId; webNavigation names its
// source tab in both browsers (Chromium: whichever comes first adopts it).
on(api.webNavigation.onCreatedNavigationTarget, (d) => {
  if (owned.has(d.sourceTabId)) api.tabs.get(d.tabId).then((tab) => adopt(tab, d.sourceTabId, d.url), () => {});
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
on(api.windows.onRemoved, (windowId) => {
  dropWindow(windowId); // closed by hand: same events as window.close
  for (const [id, w] of windows) if (w.background && w.host === windowId) dropWindow(id);
});

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

let expected = null; // {tabId, t} named by download.expect
const blocked = new Set();
function block(downloadId, tabId, reason) {
  if (blocked.has(downloadId)) return;
  blocked.add(downloadId);
  emit({ event: "download.blocked", downloadId, tabId, reason });
}
on(api.downloads.onCreated, async (item) => {
  let tabs = new Set(
    recent.filter((r) => Date.now() - r.t < 60000 && (r.url === item.url || r.url === item.finalUrl)).map((r) => r.tabId)
  );
  // Spike R5: a gate's file comes from a CDN outside host_permissions, so webRequest never
  // sees it. Fall back to the page that started it: the one own (non-popup) tab on the
  // referrer's allowlisted origin. The referrer is often cut to the origin, so match that.
  const from = parse(item.referrer || "");
  if (!tabs.size && from && groupOf(from)) {
    const own = [...owned]
      .filter(([, o]) => !o.popup)
      .map(([id]) => (isVirtual(id) ? Promise.resolve(frames.has(id) ? vtab(id) : null) : api.tabs.get(id).catch(() => null)));
    tabs = new Set((await Promise.all(own)).filter((t) => t && parse(t.url)?.origin === from.origin).map((t) => t.id));
    if (tabs.size > 1 && expected && tabs.has(expected.tabId) && Date.now() - expected.t < 60000) {
      tabs = new Set([expected.tabId]);
    }
  }
  if (tabs.size !== 1) return; // not attributable: ignored, never reported as a row's file
  if (expected && tabs.has(expected.tabId)) expected = null;
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
  createMenus();
  if (d.reason === "install") api.tabs.create({ url: api.runtime.getURL("welcome.html") });
});
