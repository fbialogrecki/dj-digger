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

function fakeBrowser() {
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
    "webNavigation.onErrorOccurred", "webRequest.onResponseStarted", "webRequest.onBeforeRedirect",
    "webRequest.onCompleted", "downloads.onCreated", "downloads.onChanged", "runtime.onInstalled"]) {
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
  const api = {
    runtime: {
      getManifest: () => JSON.parse(read("manifest.json")),
      getURL: (n) => n,
      connectNative: () => port,
      onInstalled: e["runtime.onInstalled"],
    },
    permissions: { contains: async () => true },
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
        return { id, tabs: [newTab(id)] };
      },
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
      create: async ({ windowId, url }) => {
        if (!wins.has(windowId)) throw new Error("no window");
        return newTab(windowId, { url });
      },
      update: async (id, { url, muted }) => Object.assign(tabs.get(id), url ? { url } : {}, muted ? { muted } : {}),
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
    webNavigation: Object.fromEntries(["onBeforeNavigate", "onCommitted", "onDOMContentLoaded", "onErrorOccurred"]
      .map((n) => [n, e["webNavigation." + n]])),
    webRequest: Object.fromEntries(["onResponseStarted", "onBeforeRedirect", "onCompleted"]
      .map((n) => [n, e["webRequest." + n]])),
    downloads: { onCreated: e["downloads.onCreated"], onChanged: e["downloads.onChanged"], search: async () => [] },
    cookies: {},
  };
  return { api, e, port, sent, calls, tabs, wins, newTab, session };
}

async function start(fake = fakeBrowser()) {
  const sandbox = {
    browser: fake.api, console, URL, Promise, Date, setTimeout, clearTimeout, AbortController, JSON,
    crypto: { randomUUID: () => "instance" }, navigator: { userAgent: "Chrome/121.0" },
    fetch: async () => ({ json: async () => JSON.parse(read("allowlist.json")) }),
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(read("dom.js"), sandbox);
  vm.runInContext(read("background.js"), sandbox);
  await tick();
  let id = 0;
  const call = async (cmd, args = {}) => {
    const mine = ++id;
    fake.port.onMessage.fire({ id: mine, cmd, args });
    for (let i = 0; i < 100; i++) {
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
  console.log("ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
