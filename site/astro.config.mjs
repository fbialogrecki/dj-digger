import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { defineConfig } from "astro/config";
import { siteUrl } from "./site.config.mjs";

// The one script written into the page as it is (Base.astro); Astro hashes the bundled ones itself.
const boot = createHash("sha256").update(readFileSync("./src/scripts/boot.js")).digest("base64");

export default defineConfig({
  site: siteUrl,
  trailingSlash: "always",
  // No Markdown here; its highlighter would only warn about the policy below.
  markdown: { syntaxHighlight: false },
  // A content security policy in every page: scripts and styles are allowed by hash,
  // everything else only from this site. firebase.json adds the header-only directives.
  security: {
    csp: {
      directives: ["default-src 'self'", "img-src 'self' data:", "object-src 'none'", "base-uri 'self'", "form-action 'none'"],
      scriptDirective: { hashes: [`sha256-${boot}`] },
    },
  },
  // Screenshots and the app icon are imported from the repository itself, one level up.
  vite: {
    server: { fs: { allow: [".."] } },
    // A font inlined as a data: URL would need a looser policy; keep fonts as files.
    build: { assetsInlineLimit: (file) => (file.endsWith(".woff2") ? false : undefined) },
  },
});
