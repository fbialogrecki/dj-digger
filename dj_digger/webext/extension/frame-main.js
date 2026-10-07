// frame-main.js - the page-world part of a Hypeddit gate framed by offscreen.html (plan F4b), run at
// document_start. Nothing in such a frame may open a window or play sound: Hypeddit's follow, like and
// repost links call window.open (PopupCenterDual) to show the artist on SoundCloud, which autoplays, and
// a popup of an offscreen frame is not an own tab, so it is neither muted nor closed. The page's own
// click handler ticks the step either way; Connect (OAuth) is never clicked in these frames.
// Inert anywhere but in a frame directly under an extension page (the user's own tabs are untouched).
"use strict";
(() => {
  if (window.top === window || !location.ancestorOrigins?.[0]?.startsWith("chrome-extension://")) return;
  const shut = { closed: true, focus() {}, blur() {}, close() {}, postMessage() {} };
  window.open = () => shut;
  HTMLMediaElement.prototype.play = function () {
    this.muted = true;
    return Promise.resolve();
  };
})();
