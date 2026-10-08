// offscreen.js - the offscreen document of the hidden gate pass (plan F4b): one iframe per virtual
// tab, named by a nonce that the frame's content script reports back. It has one channel, a port to
// background.js, which alone decides what is loaded (only allowlisted, navigable URLs).
"use strict";
const frames = new Map(); // nonce -> iframe
const port = chrome.runtime.connect({ name: "offscreen" });
port.onMessage.addListener((m) => {
  if (m.op === "load") {
    let frame = frames.get(m.nonce);
    if (!frame) {
      frame = document.createElement("iframe");
      frame.name = m.nonce;
      // An offscreen document cannot be muted like a tab: no frame in it (nor the SoundCloud
      // player a gate embeds) may start playing by itself.
      frame.allow = "autoplay 'none'";
      // A gate opened from the address bar has no referrer either, and this extension's address is
      // nobody's business (ToneDen would send its sign-in back to it: redirectURL=document.referrer).
      frame.referrerPolicy = "no-referrer";
      document.body.append(frame);
      frames.set(m.nonce, frame);
    }
    if (m.url) frame.src = m.url;
  } else if (m.op === "remove") {
    frames.get(m.nonce)?.remove();
    frames.delete(m.nonce);
  }
});
port.onDisconnect.addListener(() => {
  for (const frame of frames.values()) frame.remove();
  frames.clear();
});
