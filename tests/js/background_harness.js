// Runs the real background.js against a fake browser API and checks the work-window registry:
// several windows at once, per-window close, windows closed by hand, app-offline, leftovers.
// Usage: node background_harness.js <extension dir>; exits non-zero with the failed assertion.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const dir = process.argv[2];
const read = (name) => fs.readFileSync(path.join(dir, name), "utf8");
const tick = () => new Promise((r) => setTimeout(r, 20));

function event() {
  const fns = [];
  return { addListener: (f) => fns.push(f), removeListener() {}, fire: (...a) => fns.forEach((f) => f(...a)) };
}

// A runtime port as the background sees it: what it posted is kept, what the peer sends is fired.
function fakePort(name, sender) {
  const p = { name, sender, posted: [], disconnected: false, onMessage: event(), onDisconnect: event() };
  p.postMessage = (m) => p.posted.push(JSON.parse(JSON.stringify(m)));
  p.disconnect = () => (p.disconnected = true);
  return p;
}

function fakeBrowser({ offscreen = true, failOffscreen = false } = {}) {
  const session = {};
  const tabs = new Map();
  const wins = new Set();
  let nextTab = 1;
  let nextWin = 1;
  const calls = [];
  const sent = [];
  const e = {};
  for (const name of ["tabs.onCreated", "tabs.onUpdated", "tabs.onRemoved", "windows.onRemoved", "alarms.onAlarm",
    "webNavigation.onBeforeNavigate", "webNavigation.onCommitted", "webNavigation.onDOMContentLoaded",
    "webNavigation.onErrorOccurred", "webNavigation.onCreatedNavigationTarget", "webRequest.onResponseStarted", "webRequest.onBeforeRedirect",
    "webRequest.onCompleted", "downloads.onCreated", "downloads.onChanged", "runtime.onInstalled", "runtime.onConnect", "runtime.onMessage",
    "runtime.onStartup", "contextMenus.onClicked", "permissions.onAdded", "permissions.onRemoved"]) {
    e[name] = event();
  }
  const port = { onMessage: event(), onDisconnect: event(), postMessage: (m) => sent.push(JSON.parse(JSON.stringify(m))) };
  const newTab = (windowId, extra = {}) => {
    const tab = { id: nextTab++, windowId, url: "about:blank", ...extra };
    tabs.set(tab.id, tab);
    return tab;
  };
  const dropTab = (id, closing) => {
    const tab = tabs.get(id);
    tabs.delete(id);
    e["tabs.onRemoved"].fire(id, { windowId: tab.windowId, isWindowClosing: closing });
  };
  const off = { docs: 0, closed: 0, port: null, scripts: [], unregistered: 0 };
  const user = { window: null, created: [] }; // the person's last focused window; windows made
  const ui = { badge: "", badgeColor: null, menus: {}, sites: true };
  const api = {
    scripting: {
      registerContentScripts: async (list) => {
        for (const sc of list) {
          if (off.scripts.some((x) => x.id === sc.id)) throw new Error("duplicate script id");
          off.scripts.push(JSON.parse(JSON.stringify(sc)));
        }
      },
      unregisterContentScripts: async ({ ids }) => {
        off.unregistered++;
        if (!off.scripts.some((x) => ids.includes(x.id))) throw new Error("nonexistent script");
        off.scripts = off.scripts.filter((x) => !ids.includes(x.id));
      },
    },
    ...(offscreen && {
      offscreen: {
        createDocument: async (opts) => {
          if (failOffscreen) throw new Error("no offscreen");
          if (off.docs > off.closed) throw new Error("Only a single offscreen document may be created.");
          off.docs++;
          off.opts = opts;
          setTimeout(() => {
            off.port = fakePort("offscreen", { url: "offscreen.html" }); // no tab, so Chrome sets no frameId
            e["runtime.onConnect"].fire(off.port);
          }, 5);
        },
        closeDocument: async () => {
          if (off.docs <= off.closed) throw new Error("no offscreen document");
          off.closed++;
        },
      },
    }),
    runtime: {
      onConnect: e["runtime.onConnect"],
      onMessage: e["runtime.onMessage"],
      id: "djd",
      getManifest: () => JSON.parse(read("manifest.json")),
      getURL: (n) => n,
      connectNative: () => port,
      onInstalled: e["runtime.onInstalled"],
      onStartup: e["runtime.onStartup"],
    },
    // the badge and the context menu as the browser keeps them
    action: {
      setBadgeText: async ({ text }) => void (ui.badge = text),
      setBadgeBackgroundColor: async ({ color }) => void (ui.badgeColor = color),
    },
    contextMenus: {
      create: (props, done) => {
        if (ui.menus[props.id]) api.runtime.lastError = { message: "duplicate id" };
        else ui.menus[props.id] = JSON.parse(JSON.stringify(props));
        done?.();
        delete api.runtime.lastError;
      },
      update: async (id, props) => {
        if (!ui.menus[id]) throw new Error("no such menu");
        Object.assign(ui.menus[id], props);
      },
      onClicked: e["contextMenus.onClicked"],
    },
    permissions: { contains: async () => ui.sites, onAdded: e["permissions.onAdded"], onRemoved: e["permissions.onRemoved"] },
    alarms: { create() {}, onAlarm: e["alarms.onAlarm"] },
    storage: {
      session: {
        get: async (k) => (k in session ? { [k]: JSON.parse(JSON.stringify(session[k])) } : {}),
        set: async (o) => Object.assign(session, JSON.parse(JSON.stringify(o))),
      },
    },
    windows: {
      create: async () => {
        const id = nextWin++;
        wins.add(id);
        user.created.push(id);
        return { id, tabs: [newTab(id)] };
      },
      getLastFocused: async () => (wins.has(user.window) ? { id: user.window } : Promise.reject(new Error("no window"))),
      get: async (id) => (wins.has(id) ? { id } : Promise.reject(new Error("no window"))),
      update: async (id, props) => {
        calls.push(["windows.update", id, JSON.parse(JSON.stringify(props))]);
        return wins.has(id) ? { id } : Promise.reject(new Error("no window"));
      },
      remove: async (id) => {
        calls.push(["windows.remove", id]);
        if (!wins.has(id)) throw new Error("no window");
        for (const t of [...tabs.values()].filter((t) => t.windowId === id)) dropTab(t.id, true);
        wins.delete(id);
        e["windows.onRemoved"].fire(id);
      },
      onRemoved: e["windows.onRemoved"],
    },
    tabs: {
      get: async (id) => tabs.get(id) ?? Promise.reject(new Error("no tab")),
      query: async ({ windowId }) => [...tabs.values()].filter((t) => t.windowId === windowId),
      create: async ({ windowId, url, active }) => {
        if (!wins.has(windowId)) throw new Error("no window");
        return newTab(windowId, { url, active });
      },
      update: async (id, { url, muted, active }) =>
        Object.assign(tabs.get(id), url ? { url } : {}, muted ? { muted } : {}, active ? { active } : {}),
      remove: async (id) => {
        calls.push(["tabs.remove", id]);
        if (!tabs.has(id)) throw new Error("no tab");
        dropTab(id, false);
        // a browser closes the window with its last tab
        const w = [...wins].filter((w) => ![...tabs.values()].some((t) => t.windowId === w));
        for (const id of w) {
          wins.delete(id);
          e["windows.onRemoved"].fire(id);
        }
      },
      onCreated: e["tabs.onCreated"], onUpdated: e["tabs.onUpdated"], onRemoved: e["tabs.onRemoved"],
    },
    webNavigation: Object.fromEntries(["onBeforeNavigate", "onCommitted", "onDOMContentLoaded", "onErrorOccurred", "onCreatedNavigationTarget"]
      .map((n) => [n, e["webNavigation." + n]])),
    webRequest: Object.fromEntries(["onResponseStarted", "onBeforeRedirect", "onCompleted"]
      .map((n) => [n, e["webRequest." + n]])),
    downloads: { onCreated: e["downloads.onCreated"], onChanged: e["downloads.onChanged"], search: async () => [] },
    cookies: {},
  };
  return { api, e, port, sent, calls, tabs, wins, newTab, session, off, user, ui };
}

async function start(fake = fakeBrowser()) {
  const sandbox = {
    browser: fake.api, console, URL, Promise, Date, setTimeout, clearTimeout, AbortController, JSON,
    crypto: { randomUUID: ((n) => () => (n++ ? `nonce-${n}` : "instance"))(0) }, navigator: { userAgent: "Chrome/121.0" },
    fetch: async () => ({ json: async () => JSON.parse(read("allowlist.json")) }),
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(read("dom.js"), sandbox);
  vm.runInContext(read("background.js"), sandbox);
  await tick();
  let id = 0;
  const call = async (cmd, args = {}, waitMs = 2000) => {
    const mine = ++id;
    fake.port.onMessage.fire({ id: mine, cmd, args });
    for (let i = 0; i < waitMs / 20; i++) {
      const reply = fake.sent.find((m) => m.id === mine);
      if (reply) return reply;
      await tick();
    }
    throw new Error(`no reply to ${cmd}`);
  };
  const events = (name) => fake.sent.filter((m) => m.event === name);
  return { ...fake, call, events };
}

(async () => {
  // two sessions, two windows; neither opening closes the other
  let x = await start();
  const a = (await x.call("window.open", { visible: false })).result.windowId;
  const b = (await x.call("window.open", { visible: true })).result.windowId;
  assert.notEqual(a, b);
  assert.deepEqual(x.calls, [["windows.update", a, { state: "minimized" }]]); // hidden one minimized again
  x.calls.length = 0;
  const ta = (await x.call("tab.new", { windowId: a })).result.tabId;
  const tb = (await x.call("tab.new", { windowId: b, url: "https://bandcamp.com/" })).result.tabId;
  const tb2 = (await x.call("tab.new", { windowId: b })).result.tabId;
  assert.equal(x.tabs.get(ta).windowId, a);
  assert.equal(x.tabs.get(tb2).windowId, b);
  assert.equal((await x.call("tab.new", { windowId: 999 })).error.code, "no_window");
  assert.equal((await x.call("tab.new", {})).error.code, "no_window");
  assert.equal((await x.call("window.show", { windowId: 999 })).error.code, "no_window");
  assert.equal((await x.call("window.hide", { windowId: a })).ok, true);

  // a popup of A's tab opened in a window of its own still belongs to A
  const stray = x.newTab(77, { openerTabId: ta, url: "https://accounts.spotify.com/x?code=1" });
  x.wins.add(77);
  x.e["tabs.onCreated"].fire(stray);
  await tick();
  assert.deepEqual(x.events("tab.created").map((m) => [m.tabId, m.openerTabId]), [[stray.id, ta]]);
  assert.equal((await x.call("tab.info", { tabId: stray.id })).ok, true);
  // own tabs are muted, and a hidden session's popup window is minimized
  assert.ok([ta, tb, tb2, stray.id].every((id) => x.tabs.get(id).muted));
  assert.deepEqual(x.calls.filter((c) => c[1] === 77), [["windows.update", 77, { state: "minimized" }]]);
  x.wins.add(78);
  const user = x.newTab(78); // the user's own window and tab: never ours
  assert.equal((await x.call("tab.close", { tabId: user.id })).error.code, "not_owned");

  // closing A closes A's tabs (popup included), reports them, and leaves B alone
  assert.equal((await x.call("window.close", { windowId: a })).ok, true);
  assert.deepEqual(x.events("tab.removed").map((m) => m.tabId).sort(), [ta, stray.id].sort());
  assert.deepEqual(x.events("window.removed"), [{ event: "window.removed", windowId: a }]);
  assert.ok(x.tabs.has(tb) && x.tabs.has(tb2) && x.tabs.has(user.id) && x.wins.has(b) && x.wins.has(78));
  assert.ok(!x.tabs.has(stray.id));
  assert.equal(x.calls.filter(([n, id]) => n === "windows.remove" && id === b).length, 0);
  assert.equal(x.events("tab.removed").length, 2); // the browser's own tabs.onRemoved did not double them
  assert.equal((await x.call("window.close", { windowId: a })).ok, true); // already gone: quiet
  assert.equal((await x.call("window.close", { windowId: 78 })).ok, true); // a window that is not ours
  assert.ok(x.wins.has(78) && x.tabs.has(user.id));
  assert.equal((await x.call("tab.new", { windowId: b })).ok, true); // B still works

  // B closed by hand: its tabs and the window are reported
  x.sent.length = 0;
  x.wins.delete(b);
  for (const t of [...x.tabs.values()].filter((t) => t.windowId === b)) {
    x.tabs.delete(t.id);
    x.e["tabs.onRemoved"].fire(t.id, { windowId: b, isWindowClosing: true });
  }
  x.e["windows.onRemoved"].fire(b);
  await tick();
  assert.equal(x.events("tab.removed").length, 3); // tb, tb2 and the tab made last
  assert.deepEqual(x.events("window.removed"), [{ event: "window.removed", windowId: b }]);
  assert.equal((await x.call("tab.new", { windowId: b })).error.code, "no_window");
  const registry = x.session.djdRegistry;
  assert.deepEqual([registry.windows, registry.owned], [[], []]);

  // app-offline ends every session: all work windows go, the user's windows stay
  x = await start();
  const c = (await x.call("window.open", {})).result.windowId;
  const d = (await x.call("window.open", {})).result.windowId;
  x.newTab(500);
  x.wins.add(500);
  x.port.onMessage.fire({ event: "app-offline" });
  await tick();
  assert.ok(!x.wins.has(c) && !x.wins.has(d) && x.wins.has(500));
  assert.equal(x.events("window.removed").length, 2);

  // leftovers of a previous background (a new instance ended their sessions) are closed on start
  const first = await start();
  const e1 = (await first.call("window.open", {})).result.windowId;
  const t1 = (await first.call("tab.new", { windowId: e1 })).result.tabId;
  const mine = first.newTab(first.wins.size + 100);
  first.wins.add(mine.windowId);
  assert.equal(first.session.djdRegistry.windows.length, 1);
  const second = await start(first); // same browser, background restarted
  assert.ok(!second.wins.has(e1) && !second.tabs.has(t1));
  assert.ok(second.wins.has(mine.windowId) && second.tabs.has(mine.id)); // the user's window stays
  assert.deepEqual([second.session.djdRegistry.windows, second.session.djdRegistry.owned], [[], []]);

  // site access taken back or granted in the browser's settings reaches the app at once, not only in hello
  x = await start();
  assert.equal(x.events("hello")[0].permissions, true);
  x.ui.sites = false;
  x.e["permissions.onRemoved"].fire({ origins: ["https://hypeddit.com/*"] });
  await tick();
  x.ui.sites = true;
  x.e["permissions.onAdded"].fire({ origins: ["https://hypeddit.com/*"] });
  await tick();
  assert.deepEqual(x.events("permissions"), [{ event: "permissions", granted: false }, { event: "permissions", granted: true }]);

  // an aborted navigation superseded by the site's next one that loads is a success (Beatport)
  x = await start();
  const nw = (await x.call("window.open", {})).result.windowId;
  const nt = (await x.call("tab.new", { windowId: nw })).result.tabId;
  const going = x.call("tab.goto", { tabId: nt, url: "https://www.beatport.com/track/a/1" });
  await tick();
  x.e["webNavigation.onErrorOccurred"].fire({ tabId: nt, frameId: 0, error: "net::ERR_ABORTED" });
  x.e["webNavigation.onCommitted"].fire({ tabId: nt, frameId: 0 });
  x.e["webNavigation.onDOMContentLoaded"].fire({ tabId: nt, frameId: 0 });
  assert.equal((await going).ok, true);
  // Firefox reports the same abort/redirect family as nsresult codes; the next load still wins (Hypeddit)
  const later = () => new Promise((r) => setTimeout(r, 150)); // past a poll, inside the grace
  for (const error of ["net::ERR_ABORTED", "Error code 2152398850", "Error code 2152398859", "Error code 2147500036"]) {
    const loading = x.call("tab.goto", { tabId: nt, url: "https://hypeddit.com/1rjvpc" });
    await tick();
    x.e["webNavigation.onErrorOccurred"].fire({ tabId: nt, frameId: 0, error });
    await later();
    x.e["webNavigation.onCommitted"].fire({ tabId: nt, frameId: 0 });
    x.e["webNavigation.onDOMContentLoaded"].fire({ tabId: nt, frameId: 0 });
    assert.equal((await loading).ok, true, error);
  }
  // any other error (connection refused) fails at once
  const refused = x.call("tab.goto", { tabId: nt, url: "https://hypeddit.com/1rjvpc" });
  await tick();
  x.e["webNavigation.onErrorOccurred"].fire({ tabId: nt, frameId: 0, error: "Error code 2152398861" });
  assert.equal((await refused).error.code, "navigation_failed");
  // Firefox, first navigation of a new window: aborted with no further event while the page still loads;
  // once the grace is over the tab is asked, and the page that completes later is a success
  const HYP = "https://hypeddit.com/1rjvpc";
  const grace = () => new Promise((r) => setTimeout(r, 3300));
  const fw = (await x.call("window.open", {})).result.windowId;
  const ft = (await x.call("tab.new", { windowId: fw })).result.tabId;
  x.tabs.get(ft).status = "loading";
  const slow = x.call("tab.goto", { tabId: ft, url: HYP, timeoutMs: 15000 }, 10000);
  await tick();
  x.e["webNavigation.onErrorOccurred"].fire({ tabId: ft, frameId: 0, error: "Error code 2152398850" });
  await grace();
  x.tabs.get(ft).status = "complete"; // loaded, but no commit or DOMContentLoaded reached us
  assert.equal((await slow).ok, true);
  // a real abort: the tab stays a finished about:blank; the navigation is started once more, then it fails
  const blankTab = (await x.call("tab.new", { windowId: fw })).result.tabId;
  x.tabs.get(blankTab).status = "complete";
  const realUpdate = x.api.tabs.update;
  const starts = [];
  x.api.tabs.update = async (id, props) => (props.url && starts.push(id), x.tabs.get(id)); // the load never begins
  const stuck = x.call("tab.goto", { tabId: blankTab, url: HYP, timeoutMs: 15000 }, 10000);
  await tick();
  x.e["webNavigation.onErrorOccurred"].fire({ tabId: blankTab, frameId: 0, error: "Error code 2152398850" });
  assert.equal((await stuck).error.code, "navigation_failed");
  assert.deepEqual(starts, [blankTab, blankTab]);
  x.api.tabs.update = realUpdate;

  // Firefox: a window.open popup in a window of its own has no openerTabId; webNavigation names its
  // source, so it is the session's and closes with it. Chromium reports both: adopted once.
  const pw = (await x.call("window.open", {})).result.windowId;
  const pt = (await x.call("tab.new", { windowId: pw })).result.tabId;
  x.wins.add(90);
  const fx = x.newTab(90, { url: "about:blank" });
  x.e["tabs.onCreated"].fire(fx);
  x.e["webNavigation.onCreatedNavigationTarget"].fire({ sourceTabId: pt, sourceFrameId: 0, tabId: fx.id, url: "https://soundcloud.com/x" });
  x.wins.add(91);
  const cr = x.newTab(91, { openerTabId: pt, pendingUrl: "https://www.instagram.com/x" });
  x.e["tabs.onCreated"].fire(cr);
  x.e["webNavigation.onCreatedNavigationTarget"].fire({ sourceTabId: pt, sourceFrameId: 0, tabId: cr.id, url: "https://www.instagram.com/x" });
  x.wins.add(92);
  const other = x.newTab(92); // a popup of a tab that is not ours
  x.e["webNavigation.onCreatedNavigationTarget"].fire({ sourceTabId: 999, sourceFrameId: 0, tabId: other.id, url: "https://soundcloud.com/y" });
  await tick();
  assert.deepEqual(x.events("tab.created").map((m) => [m.tabId, m.openerTabId, m.url]),
    [[cr.id, pt, "https://www.instagram.com/"], [fx.id, pt, "https://soundcloud.com/x"]]);
  assert.ok(x.tabs.get(fx.id).muted);
  assert.deepEqual(x.calls.filter((c) => c[1] === 90 || c[1] === 91).map((c) => c[0]), ["windows.update", "windows.update"]);
  assert.equal((await x.call("window.close", { windowId: pw })).ok, true);
  assert.ok(!x.wins.has(90) && !x.wins.has(91) && !x.tabs.has(fx.id) && !x.tabs.has(cr.id));
  assert.ok(x.wins.has(92) && x.tabs.has(other.id));
  // checkout stays closed under a locale prefix and at Bandcamp's real checkout entry
  for (const url of ["https://www.beatport.com/de/checkout/main/1", "https://www.beatport.com/en-us/checkout/a/1",
    "https://bandcamp.com/cart/checkout_start?x=1"]) {
    assert.equal((await x.call("tab.goto", { tabId: nt, url })).error.code, "forbidden", url);
  }

  // a download from a CDN webRequest never saw is the own tab's whose origin is its referrer
  x = await start();
  const w = (await x.call("window.open", {})).result.windowId;
  const gate = (await x.call("tab.new", { windowId: w })).result.tabId;
  x.tabs.get(gate).url = "https://hypeddit.com/1rjvpc";
  x.e["downloads.onCreated"].fire({ id: 7, url: "https://cdn.example/f.mp3", referrer: "https://hypeddit.com/", danger: "safe" });
  await tick();
  assert.deepEqual(x.events("download.created").map((m) => [m.downloadId, m.tabId]), [[7, gate]]);
  // a referrer off the allowlist, or no own tab on its origin, is not attributed
  x.e["downloads.onCreated"].fire({ id: 8, url: "https://cdn.example/g.mp3", referrer: "https://cdn.example/", danger: "safe" });
  x.e["downloads.onCreated"].fire({ id: 9, url: "https://cdn.example/h.mp3", referrer: "https://bandcamp.com/", danger: "safe" });
  await tick();
  assert.equal(x.events("download.created").length, 1);
  // ---- offscreen sessions (plan F4b): virtual window and tabs backed by frames of the offscreen document
  const GATE = "https://hypeddit.com/1rjvpc";
  const connectFrame = (x, tabId, url, extra = {}) => {
    const nonce = x.off.port.posted.find((m) => m.op === "load" && m.nonce === x.nonces[tabId]).nonce;
    const p = fakePort(nonce, { url, ...extra });
    x.e["runtime.onConnect"].fire(p);
    return p;
  };
  const opened = async (x, args = { visible: false, offscreen: true }) => (await x.call("window.open", args)).result;
  const newVirtual = async (x, windowId, url) => {
    const tabId = (await x.call("tab.new", { windowId, url })).result.tabId;
    x.nonces ??= {};
    x.nonces[tabId] = x.off.port.posted.filter((m) => m.op === "load").at(-1).nonce;
    return tabId;
  };
  const answer = async (p, res, seen = p.posted.length) => {
    for (let i = 0; i < 20 && p.posted.length === seen; i++) await tick();
    const m = p.posted.at(-1);
    p.onMessage.fire({ id: m.id, res });
    return m;
  };

  x = await start();
  const ow = await opened(x);
  assert.equal(ow.offscreen, true);
  assert.ok(ow.windowId < -1);
  assert.equal(x.off.docs, 1);
  assert.equal(x.off.opts.reasons[0], "IFRAME_SCRIPTING");
  assert.equal(x.wins.size, 0); // no browser window at all
  const [script, main] = x.off.scripts;
  assert.deepEqual(script.matches, ["https://hypeddit.com/*", "https://www.hypeddit.com/*", "https://hypd.it/*", "https://www.hypd.it/*",
    "https://toneden.io/*", "https://www.toneden.io/*"]); // the "frames" groups: the gates
  assert.deepEqual([script.js, script.allFrames, script.persistAcrossSessions], [["dom.js", "frame.js"], true, false]);
  // the page-world part (no popups, no sound) runs before the gate's own scripts, on the same hosts
  assert.deepEqual([main.js, main.world, main.runAt, main.matches], [["frame-main.js"], "MAIN", "document_start", script.matches]);
  assert.equal((await opened(x)).offscreen, true); // a second session shares the document and the scripts
  assert.deepEqual([x.off.docs, x.off.scripts.length], [1, 2]);
  for (const cmd of ["window.show", "window.hide"]) assert.equal((await x.call(cmd, { windowId: ow.windowId })).error.code, "offscreen");

  assert.equal((await x.call("tab.new", { windowId: ow.windowId, url: "https://example.com/" })).error.code, "refused");
  const vt = await newVirtual(x, ow.windowId, GATE);
  assert.ok(vt < -1);
  assert.deepEqual(x.off.port.posted.at(-1), { op: "load", nonce: x.nonces[vt], url: GATE });
  assert.equal(x.off.port.posted.at(-1).nonce.startsWith("nonce-"), true);
  assert.equal((await x.call("tab.info", { tabId: vt })).result.url, "about:blank"); // nothing reported yet
  for (const cmd of ["tab.focus", "tab.capture", "dom.call"]) {
    assert.equal((await x.call(cmd, { tabId: vt, name: "bandcampTralbum" })).error.code, "offscreen", cmd);
  }
  assert.equal((await x.call("dom.count", { tabId: vt, locator: [{ css: "a" }] })).error.code, "refused"); // blank frame

  // tab.goto resolves when the new document's content script has connected
  const visiting = x.call("tab.goto", { tabId: vt, url: GATE, timeoutMs: 2000 });
  await tick();
  const fp = connectFrame(x, vt, GATE);
  assert.deepEqual((await visiting).result, { url: GATE, status: null });
  assert.equal(fp.disconnected, false);
  assert.deepEqual(x.events("tab.updated").map((m) => [m.tabId, m.url]), [[vt, GATE]]);
  assert.deepEqual(x.off.port.posted.at(-1), { op: "load", nonce: x.nonces[vt], url: GATE });
  assert.equal((await x.call("tab.goto", { tabId: vt, url: "https://example.com/" })).error.code, "refused");
  assert.equal((await x.call("tab.goto", { tabId: vt, url: "https://hypeddit.com/", timeoutMs: 100 })).error.code, "timeout");
  const info = (await x.call("tab.info", { tabId: vt })).result;
  assert.deepEqual([info.url, info.host, info.status, info.windowId], [GATE, "hypeddit.com", "complete", ow.windowId]);

  // dom.* and tab.html are forwarded to the frame's content script (the djDom engine), results untrusted
  let pending = x.call("dom.count", { tabId: vt, locator: [{ css: "a" }] });
  let sent = await answer(fp, { ok: true, result: 3 });
  assert.deepEqual([sent.op, sent.spec.op, sent.spec.locator, sent.spec.deny.pattern.includes("check")], ["run", "count", [{ css: "a" }], true]);
  assert.deepEqual((await pending).result, 3);
  pending = x.call("tab.html", { tabId: vt, maxBytes: 99 });
  assert.equal((await answer(fp, { html: "<p>", truncated: false })).maxBytes, 99);
  assert.deepEqual((await pending).result, { html: "<p>", truncated: false });
  pending = x.call("dom.text", { tabId: vt, locator: [{ css: "p" }] });
  await answer(fp, "not an object");
  assert.equal((await pending).error.code, "dom_error");

  // cancel: the running click is told in the frame, and its answer ends the command
  pending = x.call("dom.click", { tabId: vt, locator: [{ css: "a" }], timeoutMs: 5000 });
  await tick();
  const run = fp.posted.at(-1);
  x.port.onMessage.fire({ id: 9000, cmd: "cancel", args: { id: Number(run.spec.token) } });
  await tick();
  assert.deepEqual(fp.posted.at(-1), { op: "cancel", token: run.spec.token });
  fp.onMessage.fire({ id: run.id, res: { ok: false, code: "cancelled", message: "command cancelled" } });
  assert.equal((await pending).error.code, "cancelled");

  // a document replaced while a command waits: it is sent again to the next one
  pending = x.call("dom.count", { tabId: vt, locator: [{ css: "a" }] });
  await tick();
  fp.onDisconnect.fire(); // the frame navigates
  await tick();
  const fp2 = connectFrame(x, vt, "https://hypeddit.com/hot-or-not/x");
  await answer(fp2, { ok: true, result: 1 });
  assert.equal((await pending).result, 1);
  assert.equal(fp2.disconnected, false);
  assert.equal((await x.call("tab.info", { tabId: vt })).result.url, "https://hypeddit.com/hot-or-not/x");
  const reloading = x.call("tab.reload", { tabId: vt, timeoutMs: 2000 });
  await tick();
  assert.deepEqual(x.off.port.posted.at(-1), { op: "load", nonce: x.nonces[vt], url: "https://hypeddit.com/hot-or-not/x" });
  const fp3 = connectFrame(x, vt, "https://hypeddit.com/hot-or-not/x");
  assert.equal((await reloading).ok, true);
  assert.equal(fp2.disconnected, true); // replaced by the new document's port

  // ports that are not a frame of a live virtual tab are cut at once
  const vt2 = await newVirtual(x, ow.windowId);
  assert.equal(x.off.port.posted.at(-1).url, null); // a blank frame
  const cut = (p) => {
    x.e["runtime.onConnect"].fire(p);
    return p;
  };
  const nonce2 = x.nonces[vt2];
  const url2 = "https://hypeddit.com/two";
  const bad = [
    fakePort("no-such-nonce", { url: url2 }),
    fakePort("", { url: url2 }),
    fakePort(nonce2, { frameId: 0, url: url2, tab: { id: 9 } }), // a tab of the user's own
    fakePort(nonce2, { url: "https://bandcamp.com/x" }), // allowlisted, but not a gate
    fakePort(nonce2, { url: "https://toneden.io.evil.example/" }),
    fakePort(nonce2, { url: "https://evil.example/" }),
    fakePort(nonce2, { url: "https://hypeddit.com.evil.example/" }),
    fakePort(nonce2, { url: "https://soundcloud.com/signin" }),
    fakePort(nonce2, { url: "welcome.html" }), // another page of the extension
    fakePort("offscreen", { url: "https://hypeddit.com/" }), // not the offscreen document
    fakePort("offscreen", { frameId: 0, url: "offscreen.html", tab: { id: 9 } }),
    fakePort("offscreen", { url: "welcome.html" }),
    fakePort(undefined, undefined),
  ].map(cut);
  await tick();
  assert.deepEqual(bad.map((p) => p.disconnected), bad.map(() => true));
  assert.equal(x.off.port.disconnected, false);
  assert.equal(fp3.disconnected, false);
  assert.equal(x.events("tab.updated").filter((m) => m.tabId === vt2).length, 0);
  const good = cut(fakePort(nonce2, { url: url2 }));
  await tick();
  assert.equal(good.disconnected, false);

  // a download from a CDN is the virtual tab's whose reported URL has the referrer's origin; two are ambiguous
  const dl = { id: 21, url: "https://cdn.example/f.mp3", referrer: "https://hypeddit.com/", danger: "safe" };
  x.e["downloads.onCreated"].fire(dl);
  await tick();
  assert.deepEqual(x.events("download.created"), []); // vt and vt2 are both on hypeddit.com
  // ... unless the app named the tab whose Download it is about to click
  assert.equal((await x.call("download.expect", { tabId: vt2 })).ok, true);
  x.e["downloads.onCreated"].fire({ ...dl, id: 20 });
  await tick();
  assert.deepEqual(x.events("download.created").map((m) => [m.downloadId, m.tabId]), [[20, vt2]]);
  x.e["downloads.onCreated"].fire({ ...dl, id: 19 }); // the expectation is used up
  await tick();
  assert.equal(x.events("download.created").length, 1);
  assert.equal((await x.call("download.expect", { tabId: 4242 })).error.code, "not_owned");
  x.sent.splice(0, x.sent.length, ...x.sent.filter((m) => m.event !== "download.created"));
  assert.equal((await x.call("tab.close", { tabId: vt2 })).ok, true);
  assert.deepEqual(x.off.port.posted.at(-1), { op: "remove", nonce: nonce2 });
  assert.equal(good.disconnected, true);
  assert.deepEqual(x.events("tab.removed"), [{ event: "tab.removed", tabId: vt2 }]);
  assert.equal((await x.call("tab.info", { tabId: vt2 })).error.code, "not_owned");
  x.e["downloads.onCreated"].fire(dl);
  await tick();
  assert.deepEqual(x.events("download.created").map((m) => [m.downloadId, m.tabId]), [[21, vt]]);
  x.e["downloads.onCreated"].fire({ ...dl, id: 22, referrer: "https://cdn.example/" });
  await tick();
  assert.equal(x.events("download.created").length, 1);
  // requests of the offscreen document itself are tab -1: never an own tab
  x.e["webRequest.onResponseStarted"].fire({ tabId: -1, url: "https://cdn.example/f.mp3" });

  // window.close ends the session: frames, events, and - with the last offscreen window - script and document
  const ow2 = (await x.call("window.close", { windowId: ow.windowId })).result;
  assert.equal(ow2, null);
  assert.deepEqual(x.events("tab.removed").map((m) => m.tabId), [vt2, vt]);
  assert.deepEqual(x.events("window.removed").map((m) => m.windowId), [ow.windowId]);
  assert.equal(fp3.disconnected, true);
  assert.deepEqual([x.off.closed, x.off.scripts.length], [0, 2]); // the second session still needs them
  const ownId = x.session.djdRegistry.windows.map(([id]) => id);
  assert.equal(ownId.length, 1);
  assert.equal((await x.call("window.close", { windowId: ownId[0] })).ok, true);
  assert.deepEqual([x.off.closed, x.off.scripts.length], [1, 0]);
  assert.deepEqual([x.session.djdRegistry.windows, x.session.djdRegistry.owned], [[], []]);
  assert.equal((await x.call("tab.new", { windowId: ow.windowId })).error.code, "no_window");

  // app-offline ends offscreen sessions like windows; a visible/real session next to it stays its own
  x = await start();
  const oa = await opened(x);
  const real = (await x.call("window.open", { visible: true })).result;
  assert.deepEqual([real.offscreen, x.wins.has(real.windowId)], [false, true]);
  await newVirtual(x, oa.windowId, GATE);
  x.port.onMessage.fire({ event: "app-offline" });
  await tick();
  assert.deepEqual(x.events("window.removed").map((m) => m.windowId).sort(), [oa.windowId, real.windowId].sort());
  assert.equal(x.events("tab.removed").length, 2); // the virtual tab and the real window's spare
  assert.deepEqual([x.off.closed, x.off.scripts.length, x.wins.size], [1, 0, 0]);

  // the offscreen document going away ends its sessions
  x = await start();
  const od = await opened(x);
  await newVirtual(x, od.windowId, GATE);
  x.off.port.onDisconnect.fire();
  await tick();
  assert.deepEqual(x.events("window.removed").map((m) => m.windowId), [od.windowId]);

  // a restarted background closes the offscreen document and the frame script its predecessor left
  const before = await start();
  const o1 = await opened(before);
  await newVirtual(before, o1.windowId, GATE);
  assert.equal(before.session.djdRegistry.windows.length, 1);
  const after = await start(before);
  assert.deepEqual([before.off.closed, before.off.scripts.length], [1, 0]);
  assert.deepEqual([after.session.djdRegistry.windows, after.session.djdRegistry.owned], [[], []]);

  // Firefox has no chrome.offscreen: the reply says so and the window is a normal one, minimized
  x = await start(fakeBrowser({ offscreen: false }));
  const ff = await opened(x);
  assert.deepEqual([ff.offscreen, x.wins.has(ff.windowId)], [false, true]);
  assert.deepEqual(x.calls, [["windows.update", ff.windowId, { state: "minimized" }]]);
  assert.equal((await x.call("tab.new", { windowId: ff.windowId })).ok, true);
  // so does a browser whose offscreen document cannot be created, and nothing is left registered
  x = await start(fakeBrowser({ failOffscreen: true }));
  const fb = await opened(x);
  assert.deepEqual([fb.offscreen, x.wins.has(fb.windowId), x.off.scripts.length], [false, true, 0]);
  // a visible window is never offscreen
  x = await start();
  assert.deepEqual([(await opened(x, { visible: true, offscreen: true })).offscreen, x.off.docs], [false, 0]);

  // a framed gate's Connect opens Spotify's sign-in as an inactive tab of the person's window, and
  // nothing else it asks to open (plan F10); the tab goes with the offscreen session
  x = await start();
  const userWin = (await x.api.windows.create()).id;
  x.user.window = userWin;
  const sp = await opened(x);
  const spTab = await newVirtual(x, sp.windowId, GATE);
  await tick();
  const fport = connectFrame(x, spTab, GATE);
  await tick();
  const SPOTIFY = "https://accounts.spotify.com/authorize?client_id=x&redirect_uri=https%3A%2F%2Fhypeddit.com%2Fspotify_callback&state=s";
  const TD_AUTH = "https://www.toneden.io/auth/soundcloud?fanOptOut=true&redirectPostID=5834468&userType=fan&spotlightTrackID=5834468&clientAuth=true";
  for (const url of ["https://soundcloud.com/artist", "https://accounts.spotify.com/authorize?redirect_uri=https%3A%2F%2Fevil.example%2F",
    "javascript:alert(1)", TD_AUTH, SPOTIFY]) fport.onMessage.fire({ op: "open", url }); // ToneDen's are a ToneDen frame's
  await tick();
  const [signIn] = x.events("tab.created");
  assert.equal(x.events("tab.created").length, 1);
  assert.deepEqual([signIn.openerTabId, x.tabs.get(signIn.tabId).windowId, x.tabs.get(signIn.tabId).active, x.tabs.get(signIn.tabId).muted],
    [spTab, userWin, false, true]);
  assert.equal(signIn.url, "https://accounts.spotify.com/"); // the OAuth state never leaves in an event
  await x.call("window.close", { windowId: sp.windowId });
  assert.equal(x.tabs.has(signIn.tabId), false);

  // a framed ToneDen gate may open ToneDen's own sign-in start and its unlocked file as inactive tabs of
  // the person's window, nothing else; credentials in their URLs never leave in an event, and the file's
  // download is that tab's (seen by webRequest on the ToneDen host before its redirect to a CDN)
  x = await start();
  x.user.window = (await x.api.windows.create()).id;
  const td = await opened(x);
  const TD_GATE = "https://www.toneden.io/badcomputer/post/bad-computer-4me";
  const tdTab = await newVirtual(x, td.windowId, TD_GATE);
  await tick();
  const tport = connectFrame(x, tdTab, TD_GATE);
  await tick();
  assert.equal(tport.disconnected, false);
  const TD_FILE = "https://www.toneden.io/api/v1/attachments/5834468/download?jwt=secret";
  for (const url of [SPOTIFY, "https://www.toneden.io/auth/facebook", "https://www.toneden.io/auth/soundcloud/callback",
    "https://www.toneden.io/badcomputer/post/x", "https://www.toneden.io/api/v1/attachments/x/download",
    "https://evil.example/auth/soundcloud", "https://toneden.io.evil.example/auth/soundcloud",
    "https://www.toneden.io:8443/auth/soundcloud", "http://www.toneden.io/auth/soundcloud",
    "https://user@www.toneden.io/auth/soundcloud", "https://soundcloud.com/connect", TD_AUTH, TD_FILE]) {
    tport.onMessage.fire({ op: "open", url });
  }
  await tick();
  const [tdAuth, tdFile] = x.events("tab.created");
  assert.equal(x.events("tab.created").length, 2);
  assert.deepEqual([tdAuth.url, tdFile.url], [TD_AUTH, "https://www.toneden.io/api/v1/attachments/5834468/download"]);
  for (const t of [tdAuth, tdFile]) {
    assert.deepEqual([t.openerTabId, x.tabs.get(t.tabId).windowId, x.tabs.get(t.tabId).active, x.tabs.get(t.tabId).muted],
      [tdTab, x.user.window, false, true]);
  }
  x.e["tabs.onUpdated"].fire(tdAuth.tabId, { url: "https://www.toneden.io/auth/soundcloud/callback?code=abc&state=s&x=1" });
  await tick();
  assert.equal(x.events("tab.updated").at(-1).url, "https://www.toneden.io/auth/soundcloud/callback?x=1");
  x.e["webRequest.onBeforeRedirect"].fire({ tabId: tdFile.tabId, url: TD_FILE, redirectUrl: "https://cdn.example/4me.mp3" });
  await tick();
  x.e["downloads.onCreated"].fire({ id: 31, url: TD_FILE, finalUrl: "https://cdn.example/4me.mp3", referrer: "", danger: "safe" });
  await tick();
  assert.deepEqual(x.events("download.created").map((m) => [m.downloadId, m.tabId]), [[31, tdFile.tabId]]);
  await x.call("window.close", { windowId: td.windowId });
  assert.deepEqual([x.tabs.has(tdAuth.tabId), x.tabs.has(tdFile.tabId)], [false, false]);

  // a background session (carts) works in inactive tabs of the person's window and never makes one
  x = await start();
  const mine2 = (await x.api.windows.create()).id;
  const theirs = x.newTab(mine2, { url: "https://example.com/", active: true });
  x.user.window = mine2;
  x.calls.length = 0;
  x.user.created.length = 0;
  const bg = (await x.call("window.open", { visible: false, background: true })).result;
  assert.ok(bg.background && bg.windowId < 0);
  const bt = (await x.call("tab.new", { windowId: bg.windowId, url: "https://bandcamp.com/" })).result.tabId;
  const bt2 = (await x.call("tab.new", { windowId: bg.windowId })).result.tabId;
  assert.deepEqual([x.tabs.get(bt).windowId, x.tabs.get(bt).active, x.tabs.get(bt2).windowId], [mine2, false, mine2]);
  assert.equal((await x.call("window.show", { windowId: bg.windowId })).ok, true);
  assert.equal((await x.call("tab.capture", { tabId: bt })).error.code, "background");
  x.e["tabs.onCreated"].fire(x.newTab(mine2, { openerTabId: bt, url: "https://bandcamp.com/x" })); // a popup tab
  await tick();
  assert.ok(!x.user.created.length && !x.calls.some(([name]) => name === "windows.update"), "nothing on screen moved");
  // the finished cart is handed over: no longer owned, so closing the session leaves it and the window
  assert.equal((await x.call("tab.release", { tabId: bt })).ok, true);
  assert.equal((await x.call("tab.goto", { tabId: bt, url: "https://bandcamp.com/cart" })).error.code, "not_owned");
  await x.call("window.close", { windowId: bg.windowId });
  assert.deepEqual([x.tabs.has(bt), x.tabs.has(bt2), x.tabs.has(theirs.id), x.wins.has(mine2)], [true, false, true, true]);
  // the toolbar panel: the app's player.state is kept checked, only popup.html may send prev/toggle/next
  x = await start();
  const art = "data:image/jpeg;base64,/9j/4AAQ==";
  x.e["runtime.onInstalled"].fire({ reason: "update" });
  x.e["runtime.onStartup"].fire(); // again: the entries already there are kept, nothing throws
  await tick();
  assert.deepEqual(Object.keys(x.ui.menus), ["djd-dig-link", "djd-dig-page"]);
  const scPatterns = ["https://soundcloud.com/*", "https://www.soundcloud.com/*", "https://m.soundcloud.com/*"];
  assert.deepEqual(x.ui.menus["djd-dig-link"], { id: "djd-dig-link", title: "Dig in dj-digger", contexts: ["link"],
    targetUrlPatterns: scPatterns, visible: false }); // no app yet: hidden
  assert.deepEqual(x.ui.menus["djd-dig-page"].documentUrlPatterns, scPatterns);
  const pair = ["#b8286a", "#3a5fd6", "#ff2fa8", "#1e9bff"];
  x.port.onMessage.fire({ cmd: "player.state", args: {
    title: "T".repeat(500), artist: "A", playing: true, position: 61.5, duration: 200, at: 1700000000.25, seed: 123,
    artwork: art, colors: pair, dark: true, extra: "dropped",
    jobs: { busy: true, label: "L".repeat(200), done: 3, total: 10, more: 1 }, needs: 2 } });
  await tick();
  assert.deepEqual(x.session.djdPlayer, { title: "T".repeat(300), artist: "A", playing: true, position: 61.5, duration: 200,
    at: 1700000000.25, seed: 123, artwork: art, colors: pair, dark: true,
    jobs: { busy: true, label: "L".repeat(120), done: 3, total: 10 }, needs: 2 });
  assert.deepEqual([x.ui.badge, x.ui.badgeColor], ["2", "#ff2fa8"]); // the count in the pair's neon foot
  assert.equal(x.ui.menus["djd-dig-link"].visible && x.ui.menus["djd-dig-page"].visible, true);
  assert.equal(x.sent.filter((m) => "id" in m || m.ok !== undefined).length, 0); // no reply to it
  x.port.onMessage.fire({ cmd: "player.state", args: { title: 5, playing: "yes", seed: -1, dark: 1,
    artwork: "data:image/svg+xml;base64,PHN2Zz4=", colors: ["red", "#000000", "#000000", "#000000"],
    position: NaN, duration: -1, at: "now", jobs: { busy: 1, label: 7, done: -1, total: 1.5 }, needs: 1000 } });
  await tick();
  const empty = { busy: false, label: "", done: 0, total: 0 };
  assert.deepEqual(x.session.djdPlayer, { title: "", artist: "", playing: false, position: 0, duration: 0, at: 0, seed: 7,
    artwork: "", colors: null, dark: false, jobs: empty, needs: 0 });
  assert.equal(x.ui.badge, ""); // no gates waiting: cleared
  x.port.onMessage.fire({ cmd: "player.state", args: { artwork: "data:image/png;base64," + "A".repeat(48 * 1024), jobs: "busy", needs: 1 } });
  await tick();
  assert.deepEqual([x.session.djdPlayer.artwork, x.session.djdPlayer.jobs, x.ui.badge, x.ui.badgeColor], ["", empty, "1", "#ff0094"]);
  const popup = { id: "djd", url: "popup.html" };
  const SET = "https://soundcloud.com/some-dj/sets/deep_cuts-2";
  for (const [m, sender] of [[{ op: "next" }, popup], [{ op: "toggle" }, popup], [{ op: "prev" }, popup],
    [{ op: "cancel", id: 3 }, popup], [{ op: "seek", fraction: 0.25, extra: 1 }, popup], [{ op: "seek", fraction: 1 }, popup],
    [{ op: "gates", choice: "skip" }, popup], [{ op: "import", url: "https://m.soundcloud.com/some-dj/sets/deep_cuts-2?in=x#t=1" }, popup],
    [{ op: "stop" }, popup], [{ op: "next" }, { id: "djd", url: "offscreen.html" }],
    [{ op: "seek", fraction: 1.5 }, popup], [{ op: "seek", fraction: NaN }, popup], [{ op: "seek", fraction: "0.5" }, popup],
    [{ op: "gates", choice: "all" }, popup], [{ op: "import", url: "https://evil.example/some-dj" }, popup],
    [{ op: "import", url: SET }, { id: "djd", url: "offscreen.html" }], [{ op: "page", url: SET }, { id: "djd", url: "welcome.html" }],
    [{ op: "gates", choice: "open" }, { id: "djd", url: "https://hypeddit.com/x", tab: { id: 3 } }],
    [{ op: "next" }, { id: "djd", url: "https://hypeddit.com/x", tab: { id: 3 } }],
    [{ op: "cancel" }, { id: "other", url: "popup.html" }], [null, popup], ["next", popup]]) {
    x.e["runtime.onMessage"].fire(m, sender, () => assert.fail("only the popup's page question is answered"));
  }
  assert.deepEqual(x.events("player.command"), [{ event: "player.command", op: "next" }, { event: "player.command", op: "toggle" },
    { event: "player.command", op: "prev" }, { event: "player.command", op: "cancel" },
    { event: "player.command", op: "seek", fraction: 0.25 }, { event: "player.command", op: "seek", fraction: 1 },
    { event: "player.command", op: "gates", choice: "skip" }, { event: "player.command", op: "import", url: SET }]);
  // what the popup may offer for its page, and what an import may carry: nothing but these shapes
  const kind = (url) => {
    let answer;
    x.e["runtime.onMessage"].fire({ op: "page", url }, popup, (r) => (answer = r));
    return answer.kind;
  };
  for (const [url, expected] of [
    [SET, "set"], ["https://www.soundcloud.com/some-dj/sets/deep_cuts-2/", "set"], ["https://SoundCloud.com/some-dj", "profile"],
    ["https://soundcloud.com/some-dj", "profile"], ["https://soundcloud.com/some-dj/sets", "profile"],
    ["https://soundcloud.com/some-dj/tracks?ref=x", "profile"], ["https://m.soundcloud.com/some-dj/popular-tracks", "profile"],
    ["https://soundcloud.com/some-dj/a-single-track", null], ["https://soundcloud.com/some-dj/sets/x/s-SECRET", null],
    ["https://soundcloud.com/discover", null], ["https://soundcloud.com/you/sets", null], ["https://soundcloud.com/", null],
    ["http://soundcloud.com/some-dj", null], ["https://evil.com/some-dj", null], ["https://soundcloud.com.evil.com/some-dj", null],
    ["https://api.soundcloud.com/some-dj", null], ["https://user:pw@soundcloud.com/some-dj", null],
    ["https://soundcloud.com:8443/some-dj", null], ["https://soundcloud.com/some%2Fdj", null],
    ["https://soundcloud.com/some-dj%2Fsets%2Fx", null], ["https://soundcloud.com/..%2F..%2Fsome-dj", null],
    ["https://soundcloud.com/some-dj/../evil", "profile"], // the URL parser resolves the dots: /evil
    ["javascript:alert(1)//soundcloud.com/x", null], ["data:text/html,https://soundcloud.com/x", null],
    ["https://soundcloud.com/" + "a".repeat(500), null], [42, null], [undefined, null]]) {
    assert.equal(kind(url), expected, String(url));
  }
  // the context menu: the link or the page clicked, the same check
  const sentBefore = x.events("player.command").length;
  for (const info of [{ menuItemId: "djd-dig-link", linkUrl: "https://soundcloud.com/some-dj/sets/x?si=1", pageUrl: "https://example.com/" },
    { menuItemId: "djd-dig-page", pageUrl: "https://www.soundcloud.com/other_dj/albums" },
    { menuItemId: "djd-dig-link", linkUrl: "https://soundcloud.com/some-dj/a-track" },
    { menuItemId: "djd-dig-page", linkUrl: SET, pageUrl: "https://soundcloud.com/some-dj/a-track" },
    { menuItemId: "someone-else", linkUrl: SET }]) {
    x.e["contextMenus.onClicked"].fire(info, { id: 5 });
  }
  assert.deepEqual(x.events("player.command").slice(sentBefore).map((m) => m.url),
    ["https://soundcloud.com/some-dj/sets/x", "https://soundcloud.com/other_dj"]);
  x.port.onMessage.fire({ event: "app-offline" });
  await tick();
  assert.equal(x.session.djdPlayer, null);
  assert.equal(x.ui.badge, ""); // the app is gone: no count, no menu
  assert.equal(x.ui.menus["djd-dig-link"].visible || x.ui.menus["djd-dig-page"].visible, false);

  // a browser with no window of its own gets one, and the session closes it again
  x = await start();
  const lone = (await x.call("window.open", { visible: false, background: true })).result.windowId;
  const [made] = x.user.created;
  await x.call("tab.new", { windowId: lone });
  await x.call("window.close", { windowId: lone });
  assert.equal(x.wins.has(made), false);
  // ... unless a cart was handed over in it: then that window holds the person's tab and stays
  x.user.created.length = 0;
  const lone2 = (await x.call("window.open", { visible: false, background: true })).result.windowId;
  const work = (await x.call("tab.new", { windowId: lone2, url: "https://bandcamp.com/" })).result.tabId;
  const cartTab = (await x.call("tab.new", { windowId: lone2, url: "https://bandcamp.com/cart" })).result.tabId;
  await x.call("tab.release", { tabId: cartTab });
  await x.call("window.close", { windowId: lone2 });
  assert.deepEqual([x.wins.has(x.user.created[0]), x.tabs.has(cartTab), x.tabs.has(work)], [true, true, false]);
  console.log("ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
