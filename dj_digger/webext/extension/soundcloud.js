// soundcloud.js - the manifest's one static content script, on https://soundcloud.com/* top frames only.
// It adds "Import to DJ digger" (the app icon) to SoundCloud's own controls: the action row of a track or
// set page (after "Add to playlist", else before "More"), before "More" on each track or playlist row of a
// stream, profile or search list, and as an item of each "More" menu of a track or set (library tiles,
// list rows). A click sends {op: "import", url} to background.js, which checks it again and hands it to the
// desktop app like the context menu's import. The page is untrusted: only the shape of a link is read
// (djdTarget), never its text; class names, not the UI language, find the controls. Nothing else is read.
"use strict";
const DJD_LABEL = "Import to DJ digger";
// icons/icon-32.png (a test keeps them equal): with no web_accessible_resources the page cannot load the file
const DJD_ICON = "data:image/png;base64,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";
const DJD_PAGES = new Set(["discover", "feed", "stream", "you", "search", "upload", "charts", "settings", "messages",
  "notifications", "pages", "signin", "logout", "people", "tags", "terms-of-use", "pro", "mobile", "jobs", "imprint"]);
const DJD_NOT_TRACKS = new Set(["sets", "tracks", "albums", "popular-tracks", "reposts", "likes", "followers", "following",
  "comments", "spotlight"]);
const DJD_NAME = /^[A-Za-z0-9_-]+$/;

// The canonical https://soundcloud.com link of a track (/<user>/<track>) or set (/<user>/sets/<name>), else
// null; the query, the fragment and private-share links are left out. A relative link is taken against
// https://soundcloud.com/ (so "#" or "" is the bare origin, no link). background.js decides again.
function djdTarget(href) {
  let u;
  try {
    u = new URL(href, "https://soundcloud.com/");
  } catch {
    return null;
  }
  if (u.protocol !== "https:" || u.hostname !== "soundcloud.com" || u.port || u.username || u.password) return null;
  const names = u.pathname.replace(/^\/|\/$/g, "").split("/");
  if (!names.every((n) => DJD_NAME.test(n)) || DJD_PAGES.has(names[0].toLowerCase())) return null;
  const set = names.length === 3 && names[1] === "sets";
  const track = names.length === 2 && !DJD_NOT_TRACKS.has(names[1]);
  return set || track ? `https://soundcloud.com/${names.join("/")}` : null;
}

// The link a control stands for: the page's own on a track or set page, else the first track or set link
// of the list item it sits in.
const DJD_ITEMS = ".trackItem, .soundBadge, .playableTile, .sound, .soundList__item, .badgeList__item";
function djdItemUrl(control, here) {
  if (!control) return null;
  if (control.closest(".listenEngagement")) return djdTarget(here);
  for (const a of control.closest(DJD_ITEMS)?.querySelectorAll("a[href]") ?? []) {
    const url = djdTarget(a.getAttribute("href")); // the raw link: a.href makes "#" this very page
    if (url) return url;
  }
  return null;
}

if (globalThis.document && window.top === window) {
  const api = globalThis.browser ?? globalThis.chrome;
  // Look like the sibling: its size and kind classes, and its label's (visually hidden on icon-only rows).
  const STYLE = /^(sc-button(-secondary|-tertiary|-small|-medium|-large|-icon|-responsive)?|moreActions__button)$/;
  const show = (b, label, text) => {
    label.textContent = b.title = text;
    b.setAttribute("aria-label", text);
  };
  async function send(b, label, url) {
    b.disabled = true;
    show(b, label, "Sending to DJ digger…");
    let answer = null;
    try {
      answer = await api.runtime.sendMessage({ op: "import", url });
    } catch {
      // the extension was reloaded or updated under this page
    }
    show(b, label, answer?.ok ? "Sent to DJ digger" : "dj-digger app not running");
    setTimeout(() => {
      show(b, label, DJD_LABEL);
      b.disabled = false;
    }, 3000);
  }
  // A button like `ref` for the link urlOf() gives at click time; none where there is no link now.
  function add(ref, place, urlOf) {
    if (!urlOf()) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = ["djd-import", ...[...(ref?.classList ?? [])].filter((c) => STYLE.test(c))].join(" ");
    b.title = DJD_LABEL;
    b.setAttribute("aria-label", DJD_LABEL);
    const icon = Object.assign(document.createElement("img"), { src: DJD_ICON, alt: "" });
    icon.style.cssText = "width:16px;height:16px;vertical-align:middle;border-radius:3px";
    const box = document.createElement("div");
    box.append(icon);
    const label = Object.assign(document.createElement("span"), { textContent: DJD_LABEL });
    label.className = ref?.querySelector(".sc-button-label")?.className || "sc-button-label";
    b.append(box, label);
    b.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation(); // not SoundCloud's: no play, no navigation, and a menu stays open for the answer
      const url = urlOf();
      if (!b.disabled && url) send(b, label, url);
    });
    place(b);
  }
  const mine = (el) => el.querySelector(":scope > .djd-import");
  const first = (g) => g.querySelector(":scope > button");
  function scan() {
    queued = false;
    // a track or set page's action row
    for (const g of document.querySelectorAll(".listenEngagement__actions .sc-button-group")) {
      if (mine(g)) continue;
      const after = g.querySelector(":scope > .sc-button-addtoset");
      const more = g.querySelector(":scope > .sc-button-more");
      add(after ?? more ?? first(g), (b) => (after ? after.after(b) : g.insertBefore(b, more)), () => djdTarget(location.href));
    }
    // a track or playlist row of a list: icon only, like its neighbours
    for (const g of document.querySelectorAll(".sound__soundActions .sc-button-group")) {
      if (mine(g)) continue;
      const more = g.querySelector(":scope > .sc-button-more");
      add(more ?? first(g), (b) => g.insertBefore(b, more), () => djdItemUrl(g, location.href));
    }
    // a "More" menu: the item is the one of the button that owns the menu
    for (const menu of document.querySelectorAll(".dropdownMenu .moreActions")) {
      const group = [...menu.querySelectorAll(":scope > .moreActions__group")].pop() ?? menu;
      if (mine(group)) continue;
      const id = menu.closest(".dropdownMenu").id;
      const owner = id && document.querySelector(`[aria-owns="${CSS.escape(id)}"]`);
      add(menu.querySelector(".moreActions__button"), (b) => group.append(b), () => djdItemUrl(owner, location.href));
    }
  }
  // SoundCloud is a single-page app: one observer, at most one scan a frame, each one idempotent.
  let queued = false;
  new MutationObserver(() => {
    if (!queued) queued = !!requestAnimationFrame(scan);
  }).observe(document.body, { childList: true, subtree: true });
  scan();
}
