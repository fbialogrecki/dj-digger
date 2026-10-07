// frame.js - content script of a Hypeddit gate framed by offscreen.html (plan F4b). It is registered
// by background.js only while an offscreen session exists, on the Hypeddit hosts only, together with
// dom.js, and does nothing unless this is a frame directly under this extension's own offscreen
// document: in the user's own Hypeddit tabs it is inert. Its one channel is a port to background.js
// named by the frame's nonce (the iframe's name); it runs the same djDom engine on request.
"use strict";
(() => {
  const api = globalThis.browser ?? globalThis.chrome;
  if (window.top === window || location.ancestorOrigins?.[0] !== new URL(api.runtime.getURL("")).origin) return;
  const port = api.runtime.connect({ name: window.name });
  port.onMessage.addListener(async (m) => {
    if (m.op === "cancel") (globalThis.__djdCancel ||= new Set()).add(m.token);
    else if (m.op === "run") port.postMessage({ id: m.id, res: await djDom.run(m.spec) });
    else if (m.op === "html") port.postMessage({ id: m.id, res: djDom.html(m.maxBytes) });
  });
})();
