// frame.js - content script of a gate (Hypeddit, ToneDen) framed by offscreen.html (plan F4b). It is
// registered by background.js only while an offscreen session exists, on the gate hosts only (the
// "frames" groups of allowlist.json), together with dom.js, and does nothing unless this is a frame
// directly under this extension's own offscreen document: in the user's own gate tabs it is inert. Its one channel is a port to background.js
// named by the frame's nonce (the iframe's name); it runs the same djDom engine on request.
"use strict";
(() => {
  const api = globalThis.browser ?? globalThis.chrome;
  if (window.top === window || location.ancestorOrigins?.[0] !== new URL(api.runtime.getURL("")).origin) return;
  // Belt and braces next to the frame's autoplay policy: whatever the gate's own player starts is
  // silenced and stopped (a tab would be muted; this frame cannot be).
  document.addEventListener("play", (e) => {
    if (e.target instanceof HTMLMediaElement) Object.assign(e.target, { muted: true }).pause();
  }, true);
  const port = api.runtime.connect({ name: window.name });
  // window.open of the page (frame-main.js): background.js decides whether it may be opened
  document.addEventListener("djd-open", (e) => port.postMessage({ op: "open", url: String(e.detail) }));
  port.onMessage.addListener(async (m) => {
    if (m.op === "cancel") (globalThis.__djdCancel ||= new Set()).add(m.token);
    else if (m.op === "run") port.postMessage({ id: m.id, res: await djDom.run(m.spec) });
    else if (m.op === "html") port.postMessage({ id: m.id, res: djDom.html(m.maxBytes) });
  });
})();
