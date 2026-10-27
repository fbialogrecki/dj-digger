import { defineConfig } from "astro/config";

// GitHub Pages project site: https://fbialogrecki.github.io/dj-digger/
export default defineConfig({
  site: "https://fbialogrecki.github.io",
  base: "/dj-digger",
  // Screenshots and icons are imported from the repository itself, one level up.
  vite: {
    server: { fs: { allow: [".."] } },
    // three.js (~130 kB gzipped) is its own chunk, fetched only after the page is idle.
    build: { chunkSizeWarningLimit: 600 },
  },
});
