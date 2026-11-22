import { languages, pathIn } from "../i18n/index.mjs";

// Five pages in seven languages do not need a sitemap integration. Each entry names its
// versions in the other languages, as the pages' own hreflang links do.
const bases = ["/", "/app/", "/extension/", "/download/", "/liner-notes/"];

export const GET = ({ site }) => {
  const url = (lang, base) => new URL(pathIn(lang, base), site);
  const entry = (lang, base) =>
    `  <url>\n    <loc>${url(lang, base)}</loc>\n` +
    languages.map(({ code }) => `    <xhtml:link rel="alternate" hreflang="${code}" href="${url(code, base)}"/>\n`).join("") +
    `    <xhtml:link rel="alternate" hreflang="x-default" href="${url("en", base)}"/>\n  </url>\n`;
  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
      `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n` +
      languages.flatMap(({ code }) => bases.map((base) => entry(code, base))).join("") +
      `</urlset>\n`,
    { headers: { "Content-Type": "application/xml; charset=utf-8" } },
  );
};
