// The deck's behaviour: turning the record by hand, and the fallback where CSS
// scroll-driven animations are missing. The motion itself is CSS.
const root = document.documentElement;
const record = document.querySelector(".record");
const needsProgress = !CSS.supports("animation-timeline: scroll()");
const TURNS = 3; // the disc turns three times over the page (global.css: 1080deg)

const reach = () => Math.max(root.scrollHeight - innerHeight, 1);

function mark() {
  if (needsProgress) root.style.setProperty("--p", Math.min(scrollY / reach(), 1).toFixed(4));
}

let queued = false;
addEventListener(
  "scroll",
  () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      mark();
    });
  },
  { passive: true },
);
addEventListener("resize", mark);
// The needle drop always plays out: cutting it short, or a reload restoring the scroll
// position, would snap the disc back by however much of the spin-up was left.
mark();

// Same-page links glide. Set per click, not in CSS: a reload restoring its position must not
// glide, and Safari never focuses a clicked link, so html:focus-within cannot carry it.
const still = matchMedia("(prefers-reduced-motion: reduce)");
addEventListener("click", (event) => {
  const link = event.target instanceof Element ? event.target.closest("a[href*='#']") : null;
  if (!link || event.defaultPrevented || still.matches || link.target) return;
  if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; // opens elsewhere
  if (link.origin !== location.origin || link.pathname !== location.pathname) return;
  root.style.scrollBehavior = "smooth";
  const settle = () => root.style.removeProperty("scroll-behavior");
  addEventListener("scrollend", settle, { once: true });
  setTimeout(settle, 1500); // where scrollend is missing, or the link is already in view
});

// Grab the record and turn it: the page scrolls by the same share of its length.
// The spindle is fixed where it was grabbed: near the bottom the deck scrolls away with
// the page, and measuring from where it moved to would turn the record by itself.
if (record) {
  let last = null;
  let held = [];
  let spindle = [0, 0];
  const angle = (event) => Math.atan2(event.clientY - spindle[1], event.clientX - spindle[0]);
  record.addEventListener("pointerdown", (event) => {
    if (event.button) return;
    const box = record.getBoundingClientRect();
    // The spindle sits 104 units into the 232-unit-wide drawing.
    spindle = [box.left + (box.width * 104) / 232, box.top + box.height / 2];
    record.setPointerCapture(event.pointerId);
    record.classList.add("grabbing");
    // A grab mid needle drop holds it where it is (disc, arm and kicks); letting go finishes it.
    // A second finger must not replace the list, or nothing would resume what the first one paused.
    if (!held.length)
      held = [...document.querySelectorAll(".record .spin-up, .record .neon, .tonearm .drop")]
        .flatMap((element) => element.getAnimations())
        .filter((animation) => animation.playState === "running");
    for (const animation of held) animation.pause();
    last = angle(event);
    event.preventDefault();
  });
  record.addEventListener("pointermove", (event) => {
    if (last === null) return;
    const next = angle(event);
    const turn = Math.atan2(Math.sin(next - last), Math.cos(next - last)); // shortest way round
    last = next;
    scrollBy({ top: (turn / (2 * Math.PI * TURNS)) * reach(), behavior: "instant" });
  });
  for (const type of ["pointerup", "pointercancel"])
    record.addEventListener(type, () => {
      last = null;
      record.classList.remove("grabbing");
      for (const animation of held) animation.play();
      held = [];
    });
}

// Copy buttons of <Code>: hidden in the markup, shown here because they need a script.
for (const button of document.querySelectorAll("[data-copy]")) {
  button.hidden = false;
  const text = button.querySelector("span");
  const label = text.textContent;
  button.addEventListener("click", async () => {
    await navigator.clipboard.writeText(button.dataset.copy);
    if (button.closest("#linux") && button.dataset.copy.includes("/install.sh")) {
      document.dispatchEvent(new Event("djd:linux-command-copied"));
    }
    text.textContent = button.dataset.copied ?? "";
    button.classList.add("copied");
    setTimeout(() => {
      text.textContent = label;
      button.classList.remove("copied");
    }, 1600);
  });
}

// On desktop these same controls are always part of the header.
const menu = document.querySelector("#site-menu");
const wideHeader = matchMedia("(min-width: 53rem)");
function layoutMenu() {
  menu.toggleAttribute("popover", !wideHeader.matches);
}
layoutMenu();
wideHeader.addEventListener("change", layoutMenu);
menu.addEventListener("beforetoggle", (event) => {
  const open = event.newState === "open";
  for (const element of document.querySelectorAll(".skip, .top > :not(#site-menu), #consent, .page, .foot"))
    element.inert = open;
  if (!open) menu.querySelector(".lang").open = false;
});
menu.addEventListener("toggle", () => {
  if (menu.matches(":popover-open")) menu.querySelector(".links a").focus({ preventScroll: true });
});

let swipeStart;
function rightwardDistance(point) {
  if (!swipeStart) return 0;
  const dx = point.clientX - swipeStart.clientX;
  return dx > 1.5 * Math.abs(point.clientY - swipeStart.clientY) ? dx : 0;
}
menu.addEventListener("touchstart", (event) => {
  swipeStart = menu.matches(":popover-open") && event.touches.length === 1 ? event.touches[0] : null;
}, { passive: true });
menu.addEventListener("touchcancel", () => { swipeStart = null; });
menu.addEventListener("touchmove", (event) => {
  // Let vertical scrolling continue, but keep a horizontal swipe out of the browser's scroll gesture.
  if (event.cancelable && rightwardDistance(event.touches[0]) >= 6) event.preventDefault();
}, { passive: false });
menu.addEventListener("touchend", (event) => {
  const distance = rightwardDistance(event.changedTouches[0]);
  swipeStart = null;
  if (!event.touches.length && distance >= 48 && menu.matches(":popover-open")) {
    event.preventDefault(); // A swipe starting on a link must not follow it.
    menu.hidePopover();
  }
}, { passive: false });

// Day or night follows the system. The button flips whatever is showing; flipping back
// to the system's own theme drops the override, so the site follows the system again.
const theme = document.querySelector(".theme");
if (theme && CSS.supports("color", "light-dark(#000, #fff)")) {
  const system = matchMedia("(prefers-color-scheme: dark)");
  const dark = () => (root.dataset.theme ?? (system.matches ? "dark" : "light")) === "dark";
  const label = () => {
    const action = dark() ? theme.dataset.toLight : theme.dataset.toDark;
    theme.setAttribute("aria-label", action);
    theme.querySelector(".theme-label").textContent = action;
  };
  theme.hidden = false;
  label();
  system.addEventListener("change", label);
  theme.addEventListener("click", () => {
    const next = dark() ? "light" : "dark";
    const follow = next === (system.matches ? "dark" : "light");
    if (follow) delete root.dataset.theme;
    else root.dataset.theme = next;
    try {
      if (follow) localStorage.removeItem("djdTheme");
      else localStorage.djdTheme = next;
    } catch {}
    label();
  });
}

// A language picked in the header is remembered: boot.js honors it on the bare home URL.
for (const link of document.querySelectorAll("[data-lang]"))
  link.addEventListener("click", () => {
    try {
      localStorage.djdLang = link.dataset.lang;
    } catch {}
  });
