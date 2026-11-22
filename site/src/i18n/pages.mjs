// Every page's base path and the languages it exists in. Most pages exist in all of them; the guides
// only where they were written. Plain data without JSON imports, so scripts/check-languages.mjs can
// import it under Node as the layout and the sitemap do.
export const codes = ["en", "de", "es", "fr", "it", "pl", "ru"];

// Pages in fewer languages, with their name in each (also the breadcrumb and footer link).
export const partial = {
  "/faq/": { en: "Questions and answers", de: "Fragen und Antworten", pl: "Pytania i odpowiedzi" },
  "/guides/soundcloud-to-rekordbox/": {
    en: "SoundCloud to rekordbox",
    de: "Von SoundCloud zu rekordbox",
    pl: "Z SoundCloud do rekordbox",
  },
  "/guides/where-to-buy-soundcloud-tracks/": {
    en: "Where to buy a SoundCloud track",
    de: "Wo man SoundCloud-Tracks kauft",
    pl: "Gdzie kupić utwór z SoundCloud",
  },
  "/guides/organize-downloaded-dj-tracks/": { en: "Organize downloaded DJ tracks" },
  "/guides/flac-vs-wav-for-djs/": { en: "FLAC or WAV for your decks?" },
  "/guides/hypeddit-download-gates/": { en: "How Hypeddit download gates work" },
};

export const bases = ["/", "/app/", "/extension/", "/download/", "/about/", "/privacy/", "/terms/", ...Object.keys(partial)];
export const languagesOf = (base) => (partial[base] ? Object.keys(partial[base]) : codes);
