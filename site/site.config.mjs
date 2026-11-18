import { readFileSync } from "node:fs";

// The one place the public address lives: canonical URLs, Open Graph, the sitemap
// and the install one-liner all read it. Set SITE_URL when building for deployment.
export const siteUrl = process.env.SITE_URL ?? "https://dj-digger.example";

export const repo = "https://github.com/fbialogrecki/dj-digger";
export const chromeStore = "https://chromewebstore.google.com/detail/dj-digger/ndimmdooibgcajgnoncdnckolhlmnnkb";
export const author = { name: "Filip Białogrecki", url: "https://github.com/fbialogrecki" };

// Download links follow the released version instead of naming one by hand.
// Read from the working directory (npm runs in site/): the bundled copy of this module lives elsewhere.
export const version = /^version = "(.+)"$/m.exec(readFileSync("../pyproject.toml", "utf8"))[1];
export const asset = (name) => `${repo}/releases/download/v${version}/dj-digger-${version}-${name}`;
