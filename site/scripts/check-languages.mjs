// After a build: every page exists in every language with the same sections, pictures and
// code blocks as the English one, says its language, and links within that language.
import { readFileSync } from "node:fs";

const languages = ["en", "de", "es", "fr", "it", "pl", "ru"];
const bases = ["/", "/app/", "/extension/", "/download/", "/about/"];
// Served once for every language: build output, the script and the preview card.
const shared = /^\/(_astro\/|install\.sh$|og\.png$|sitemap\.xml$|robots\.txt$)/;
const problems = [];

const shape = (html) => ({
  sections: [...html.matchAll(/<section\b[^>]*?\sid="([^"]+)"/g)].map((m) => m[1]).join(" "),
  pictures: (html.match(/<picture/g) ?? []).length,
  code: [...html.matchAll(/data-copy="([^"]*)"/g)].map((m) => m[1]).join("\n"),
});

for (const base of bases) {
  const english = shape(readFileSync(`dist${base}index.html`, "utf8"));
  for (const lang of languages) {
    const path = lang === "en" ? base : `/${lang}${base}`;
    let html;
    try {
      html = readFileSync(`dist${path}index.html`, "utf8");
    } catch {
      problems.push(`${path}: missing`);
      continue;
    }
    const page = shape(html);
    for (const key of Object.keys(page))
      if (page[key] !== english[key]) problems.push(`${path}: ${key} differ from English\n  ${page[key]}\n  ${english[key]}`);
    if (!html.includes(`<html lang="${lang}"`)) problems.push(`${path}: not marked lang="${lang}"`);
    for (const [anchor] of html.matchAll(/<a\b[^>]*>/g)) {
      const href = /href="([^"]*)"/.exec(anchor)?.[1] ?? "";
      if (!href.startsWith("/") || shared.test(href) || anchor.includes("hreflang=")) continue;
      const inLanguage = lang === "en" ? !languages.some((code) => href.startsWith(`/${code}/`)) : href.startsWith(`/${lang}/`);
      if (!inLanguage) problems.push(`${path}: links to ${href}`);
    }
  }
}

// A page's own styles and scripts are copied into every language: they must stay the same.
const blocks = (source) => source.match(/<(style|script)\b[\s\S]*?<\/\1>/g) ?? [];
for (const page of ["index", "app", "extension", "download", "about"]) {
  const english = blocks(readFileSync(`src/pages/${page}.astro`, "utf8")).join("\n");
  for (const lang of languages.slice(1)) {
    let source;
    try {
      source = readFileSync(`src/pages/${lang}/${page}.astro`, "utf8");
    } catch {
      continue; // reported above as missing
    }
    if (blocks(source).join("\n") !== english) problems.push(`src/pages/${lang}/${page}.astro: <style> or <script> differs from English`);
  }
}

if (problems.length) {
  console.error(`Languages check failed:\n${problems.join("\n")}`);
  process.exit(1);
}
console.log(`Languages check: ${bases.length} pages in ${languages.length} languages match.`);
