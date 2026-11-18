// The deck's behaviour: which track is being read, turning the record by hand, and
// the fallback where CSS scroll-driven animations are missing. The motion itself is CSS.
const root = document.documentElement;
const tracks = [...document.querySelectorAll("[data-track]")];
const now = document.querySelector(".mini-now");
const record = document.querySelector(".record");
const needsProgress = !CSS.supports("animation-timeline: scroll()");
const TURNS = 3; // the disc turns three times over the page (global.css: 1080deg)

const reach = () => Math.max(root.scrollHeight - innerHeight, 1);

function mark() {
  if (needsProgress) root.style.setProperty("--p", Math.min(scrollY / reach(), 1).toFixed(4));
  // The track being read is the last one whose top has passed 40% of the viewport.
  const line = innerHeight * 0.4;
  const current = tracks.findLast((track) => track.getBoundingClientRect().top <= line) ?? tracks[0];
  if (!current || current.id === root.dataset.track) return;
  root.dataset.track = current.id;
  for (const link of document.querySelectorAll(".tracks a"))
    link.toggleAttribute("aria-current", link.getAttribute("href") === `#${current.id}`);
  if (now) now.textContent = `${current.dataset.track} ${current.dataset.title}`;
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
if (record) {
  let last = null;
  const angle = (event) => {
    const box = record.getBoundingClientRect();
    // The spindle sits 104 units into the 232-unit-wide drawing.
    return Math.atan2(event.clientY - (box.top + box.height / 2), event.clientX - (box.left + (box.width * 104) / 232));
  };
  record.addEventListener("pointerdown", (event) => {
    if (event.button) return;
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
  button.addEventListener("click", async () => {
    await navigator.clipboard.writeText(button.dataset.copy);
    button.textContent = "Copied";
    setTimeout(() => (button.textContent = "Copy"), 1600);
  });
}

// Day or night: the button flips whatever is showing and remembers it.
const theme = document.querySelector(".theme");
if (theme && CSS.supports("color", "light-dark(#000, #fff)")) {
  const dark = () => (root.dataset.theme ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
  const label = () => theme.setAttribute("aria-label", `Switch to ${dark() ? "light" : "dark"} theme`);
  theme.hidden = false;
  label();
  theme.addEventListener("click", () => {
    root.dataset.theme = dark() ? "light" : "dark";
    try {
      localStorage.djdTheme = root.dataset.theme;
    } catch {}
    label();
  });
}
