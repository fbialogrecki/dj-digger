// Runs in the head, before first paint. Kept in its own file so astro.config.mjs can
// allow exactly this text in the content security policy.
const root = document.documentElement;
// The theme chosen with the header button; without one the site follows the system.
try {
  if (localStorage.djdTheme) root.dataset.theme = localStorage.djdTheme;
} catch {}

// English pages go to the language picked in the header or, until one is picked, the
// first of the browser's languages that the site speaks (the hreflang links in the head).
try {
  if (root.lang === "en") {
    const offered = new Map([...document.querySelectorAll("link[hreflang]")].map((link) => [link.hreflang, link.href]));
    const wanted = localStorage.djdLang || navigator.languages.map((tag) => tag.slice(0, 2).toLowerCase()).find((code) => offered.has(code));
    if (wanted && wanted !== "en" && offered.has(wanted)) location.replace(new URL(offered.get(wanted)).pathname + location.hash);
  }
} catch {}

// The needle drop plays once per session.
try {
  sessionStorage.djd ? root.classList.add("played") : (sessionStorage.djd = 1);
} catch {}

// Turning the record over between pages (global.css): the arm lifts off from where it
// was and comes down where it will be. The page being left notes how far its arm had
// travelled; the page arriving turns both positions into angles for the transition.
const progress = () => Math.min(scrollY / Math.max(root.scrollHeight - innerHeight, 1), 1);
addEventListener("pageswap", () => {
  try {
    sessionStorage.djdArm = progress();
  } catch {}
});
addEventListener("pagereveal", (event) => {
  if (!event.viewTransition) return;
  const css = getComputedStyle(root);
  const [start, end, rest] = ["--arm-start", "--arm-end", "--arm-rest"].map((name) => parseFloat(css.getPropertyValue(name)));
  let left = 0;
  try {
    left = Number(sessionStorage.djdArm) || 0;
  } catch {}
  root.style.setProperty("--lift", `${rest - left * (end - start)}deg`);
  root.style.setProperty("--drop", `${rest - progress() * (end - start)}deg`);
});
