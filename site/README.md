# dj-digger website

The project site: static [Astro](https://astro.build) pages, plain CSS, no UI framework
and no runtime dependencies. One idea carries it: the site is a record. The vinyl from
the app icon, seen from straight above, turns as the page scrolls, and can be grabbed
and turned to scroll it.

```bash
cd site
npm install
npm run dev       # http://localhost:4321/
npm run build     # static files in site/dist/
npm run preview   # serve the build
```

Node 22.23.4 or newer (Astro's minimum).

## Pages

| Path | File | Role |
| --- | --- | --- |
| `/` | `src/pages/index.astro` | The sleeve: what it is, the digging loop, both sides, trust, download |
| `/app/` | `src/pages/app.astro` | The desktop app, track by track |
| `/extension/` | `src/pages/extension.astro` | The browser extension, permissions, privacy |
| `/download/` | `src/pages/download.astro` | Installers, the Linux one-liner, checksums |
| `/liner-notes/` | `src/pages/liner-notes.astro` | How it is built |
| 404 | `src/pages/404.astro` | Empty groove |

`blueprint/blueprint.md` is the agreed structure: pages, sections, their order and
purpose. Only describe features that ship; `PROJECT_SPECIFICATION.md`, `README.md` and
`CHANGELOG.md` at the released commit are the source of truth.

## How it is put together

- **`site.config.mjs`** holds the public address (`SITE_URL`), the repository links,
  and the version read from `pyproject.toml`. Download links are composed from that
  version (`asset("windows-x64-test.exe")`), never typed by hand.
- **`src/layouts/Base.astro`** is every page's shell: head and SEO, the one-line
  header, the deck (record, tonearm, tracklist), footer, and the "now playing" bar on
  narrow screens. A page passes `title`, `description`, `label` (which colours its
  record label takes: `home`, `app`, `extension`, `download`, `notes`) and `tracks`.
- **Tracks.** A page is a list of tracks: `{ id, no, title }`, numbered 01, 02, ….
  Each one is a `<Track id no title label heading>` section, and the tracklist beside
  the record links to them and marks the one being read. The first track of a page is
  its hero: `<Track hero …>` renders an `h1`, and `<Track hero sub …>` is the quieter
  opening of every page but the landing page.
- **`src/components/Record.astro`** draws the record and, as a separate element, the
  tonearm, as inline SVG. The record's radial code comes from the same seeded generator
  as `scripts/app_icon.py`. `src/scripts/geometry.js` holds the arm's angles.
- **`src/scripts/deck.js`** marks the current track, lets the record be grabbed and
  turned (the page scrolls by the same share of its length), and is the fallback where
  CSS scroll-driven animations are missing. `src/scripts/boot.js` runs in the head.
- **Content security policy.** Every page carries one (`security.csp` in
  `astro.config.mjs`): scripts and styles are allowed by hash, everything else only
  from this site, and `firebase.json` adds the header-only directives. So no `style="…"`
  attributes and no `is:inline` scripts in pages; the one script that must run before
  first paint is `src/scripts/boot.js`, allowed by its own hash.
- **Motion** is CSS first: `animation-timeline: scroll()` turns the disc and moves the
  arm from the edge to the label; the needle drop on a first visit is keyframes. Going
  to another page turns the record over with a cross-document view transition: the arm
  lifts off, the disc flips, the arm comes back down. Everything is off under
  `prefers-reduced-motion`, and the page is complete without JavaScript.

## Page kit

Shared pieces live in `src/styles/global.css` and `src/components/`. Use them before
writing page-specific CSS; put what is truly page-specific in the page's own `<style>`.

| Piece | Use |
| --- | --- |
| `<Track>` | A section with its number, small label and heading |
| `<Shot src alt caption full?>` | A real screenshot or crop in a frame; `full` links to the whole-window shot |
| `<Code code label?>` | A command with a Copy button |
| `<Loop>` | The real player loop over its poster, with a Pause button; the slot is the caption |
| `.eyebrow`, `.lede`, `.copy` | Small label, opening paragraph, body paragraph |
| `.stack` | Vertical rhythm between children |
| `.movement` | A sub-point inside a track: `h3`, text, usually a shot |
| `.ticks` | A short list of facts |
| `.cta`, `.button`, `.button.primary`, `.quiet` | Actions; one primary button per screen |
| `.notice` | A calm aside to read before acting |
| `.cards`, `.card` | A few parallel items |
| `.spec` | Label and value pairs (`dl`) |
| `.table` | A scrollable wrapper around a `table` |
| `.facts` | A row of short facts with a dot each |
| `kbd`, `.sr` | A key; text for screen readers only |

Colours are the app's dark theme and its magenta-blue waveform pair (`--neon-foot`,
`--neon-top`); text uses `--fg` and `--muted` only, which keep WCAG AA contrast on
`--bg` and `--panel`.

## Images and video

`uv run --extra gui --extra analyze python scripts/site_screenshots.py` (from the
repository root) regenerates everything under `src/assets/screenshots/` and
`src/assets/video/` from an isolated demo profile: eight full-window shots, nine crops
and the player loop. Nothing there is drawn by hand. Run it on the released commit, so
the site never shows an interface that has not shipped.

`uv run --extra gui --with fonttools --with brotli python scripts/site_og.py` redraws
`public/og.png`, the preview card, from the app icon's own drawing routine.

Fonts are Archivo and JetBrains Mono (SIL Open Font License, texts beside them in
`src/assets/fonts/`), subset to Latin and served from this site.

## Deploy

Firebase Hosting serves `site/dist` (`firebase.json` in the repository root).
`.github/workflows/site.yml` builds and deploys when a release is published or by
manual dispatch; setup and manual deployment are in
[docs/site-deployment.md](../docs/site-deployment.md). The site also serves
`public/install.sh`, the Linux install script.
