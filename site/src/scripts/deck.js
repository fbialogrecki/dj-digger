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
    root.classList.add("played");
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
// Any input ends the needle drop.
for (const type of ["pointerdown", "keydown", "wheel", "touchstart"])
  addEventListener(type, () => root.classList.add("played"), { once: true, passive: true });
mark();

// Grab the record and turn it: the page scrolls by the same share of its length.
// The spindle is fixed where it was grabbed: near the bottom the deck scrolls away with
// the page, and measuring from where it moved to would turn the record by itself.
if (record) {
  let last = null;
  let spindle = [0, 0];
  const angle = (event) => Math.atan2(event.clientY - spindle[1], event.clientX - spindle[0]);
  record.addEventListener("pointerdown", (event) => {
    if (event.button) return;
    const box = record.getBoundingClientRect();
    // The spindle sits 104 units into the 232-unit-wide drawing.
    spindle = [box.left + (box.width * 104) / 232, box.top + box.height / 2];
    record.setPointerCapture(event.pointerId);
    record.classList.add("grabbing");
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
    });
}

// Copy buttons of <Code>: hidden in the markup, shown here because they need a script.
for (const button of document.querySelectorAll("[data-copy]")) {
  button.hidden = false;
  const text = button.querySelector("span");
  const label = text.textContent;
  button.addEventListener("click", async () => {
    await navigator.clipboard.writeText(button.dataset.copy);
    text.textContent = button.dataset.copied ?? "";
    button.classList.add("copied");
    setTimeout(() => {
      text.textContent = label;
      button.classList.remove("copied");
    }, 1600);
  });
}

// Day or night follows the system. The button flips whatever is showing; flipping back
// to the system's own theme drops the override, so the site follows the system again.
const theme = document.querySelector(".theme");
if (theme && CSS.supports("color", "light-dark(#000, #fff)")) {
  const system = matchMedia("(prefers-color-scheme: dark)");
  const dark = () => (root.dataset.theme ?? (system.matches ? "dark" : "light")) === "dark";
  const label = () => theme.setAttribute("aria-label", (dark() ? theme.dataset.toLight : theme.dataset.toDark) ?? "");
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

// A language picked in the header is remembered: boot.js sends English pages to it.
for (const link of document.querySelectorAll("[data-lang]"))
  link.addEventListener("click", () => {
    try {
      localStorage.djdLang = link.dataset.lang;
    } catch {}
  });
