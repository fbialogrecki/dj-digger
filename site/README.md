# dj-digger website

The project landing page: [Astro](https://astro.build) (static output) plus
[three.js](https://threejs.org) for the hero record. Plain CSS, no framework.

```bash
cd site
npm install
npm run dev       # http://localhost:4321/dj-digger/
npm run build     # static files in site/dist/
npm run preview   # serve the build (astro preview stop to end it)
```

Node 22.23.4 or newer (Astro's minimum).

- Content and styles: `src/pages/index.astro`. Only describe features that ship;
  `PROJECT-SPECIFICATION.md` and `README.md` are the source of truth.
- Hero animation: `src/scripts/hero-scene.js`. It loads after the page is idle and
  only with WebGL2; without it the static app icon stays. `prefers-reduced-motion`
  freezes it on one frame.
- Images are imported from the repository (`docs/store/screenshots/`, the app and
  extension icons), so updating those files updates the site.

## Deploy

`.github/workflows/pages.yml` builds `site/` and deploys it to GitHub Pages
(`https://fbialogrecki.github.io/dj-digger/`) on pushes to `main` that touch the
site or its images, or by manual dispatch. Enable it once under Settings → Pages →
Source: GitHub Actions. For a custom domain, set it there and change `site`/`base`
in `astro.config.mjs`.
