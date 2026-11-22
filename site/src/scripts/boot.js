// Runs in the head, before first paint. Kept in its own file so astro.config.mjs can
// allow exactly this text in the content security policy.
const root = document.documentElement;
// The theme chosen with the header button; without one the site follows the system.
try {
  if (localStorage.djdTheme) root.dataset.theme = localStorage.djdTheme;
} catch {}

// Honor a saved language on the bare home URL. Direct links retain their page and language.
try {
  if (location.pathname === "/" && !location.search && !location.hash && localStorage.djdLang) {
    const alternate = [...document.querySelectorAll('link[rel="alternate"]')]
      .find(link => link.hreflang === localStorage.djdLang && link.hreflang !== "en");
    if (alternate) {
      root.dataset.redirecting = "true";
      location.replace(new URL(alternate.href).pathname);
    }
  }
  // Reserve the in-flow banner on a first visit before the consent module runs.
  if (!localStorage.getItem("djdAnalyticsConsent")) root.dataset.firstVisit = "";
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
