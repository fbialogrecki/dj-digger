// frame-main.js - the page-world part of a Hypeddit gate framed by offscreen.html (plan F4b), run at
// document_start, before the page's own scripts. Nothing in such a frame may open a window or make a
// sound, and an offscreen document cannot be muted like a tab:
// - Hypeddit's follow, like and repost links call window.open (PopupCenterDual) to show the artist on
//   SoundCloud, which autoplays, and a popup of an offscreen frame is not an own tab (neither muted
//   nor closed). The page's own click handler ticks the step either way; Connect (OAuth) is never
//   clicked in these frames.
// - Once Download is pressed the gate starts playing other tracks: its media elements are muted the
//   moment they appear, players embedded from other sites (out of this script's reach) are taken out
//   before they load, and Web Audio stays suspended.
// Inert anywhere but in a frame directly under an extension page (the user's own tabs are untouched).
"use strict";
(() => {
  if (window.top === window || !location.ancestorOrigins?.[0]?.startsWith("chrome-extension://")) return;
  const shut = { closed: true, focus() {}, blur() {}, close() {}, postMessage() {} };
  window.open = () => shut;

  const hush = (el) => {
    el.muted = true;
    el.autoplay = false;
    try {
      el.pause();
    } catch {
      // not playing
    }
  };
  HTMLMediaElement.prototype.play = function () {
    hush(this);
    return Promise.resolve();
  };
  for (const type of ["play", "playing", "volumechange"]) {
    window.addEventListener(type, (e) => e.target instanceof HTMLMediaElement && hush(e.target), true);
  }

  const PLAYER = /^https?:\/\/(w\.soundcloud\.com|(www\.)?youtube(-nocookie)?\.com\/embed|open\.spotify\.com\/embed|bandcamp\.com\/EmbeddedPlayer|player\.vimeo\.com)\//i;
  const check = (el) => {
    if (el instanceof HTMLMediaElement) hush(el);
    else if (el instanceof HTMLIFrameElement && PLAYER.test(el.src)) el.remove();
  };
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === "attributes") check(r.target);
      for (const node of r.addedNodes) {
        if (node instanceof Element) [node, ...node.querySelectorAll("audio, video, iframe")].forEach(check);
      }
    }
  }).observe(document, { childList: true, subtree: true, attributes: true, attributeFilter: ["src", "autoplay", "muted"] });

  for (const name of ["AudioContext", "webkitAudioContext"]) {
    const Real = window[name];
    if (!Real) continue;
    window[name] = class extends Real {
      constructor(...args) {
        super(...args);
        super.suspend();
      }
      resume() {
        return Promise.resolve();
      }
    };
  }
})();
