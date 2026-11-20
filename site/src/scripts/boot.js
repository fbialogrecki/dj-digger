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
    if (wanted && wanted !== "en" && offered.has(wanted)) {
      root.dataset.redirecting = "true";
      location.replace(new URL(offered.get(wanted)).pathname + location.hash);
    }
  }
} catch {}

// Animate navigation with the current document's position, without persisting scroll history.
addEventListener("pagereveal", (event) => {
  if (!event.viewTransition) return;
  root.classList.add("played");
  const css = getComputedStyle(root);
  const [start, end, rest] = ["--arm-start", "--arm-end", "--arm-rest"].map((name) => parseFloat(css.getPropertyValue(name)));
  const position = Math.min(scrollY / Math.max(root.scrollHeight - innerHeight, 1), 1);
  root.style.setProperty("--drop", `${rest - start - position * (end - start)}deg`);
});
