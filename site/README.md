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
| `/` | `src/pages/index.astro` | What it is, the player, the pinned three-step scene (Collect, Decide, Buy or download), the app and the extension, trust, download |
| `/app/` | `src/pages/app.astro` | The desktop app, section by section, each with a crop of the real window |
| `/extension/` | `src/pages/extension.astro` | The browser extension (Chrome, Edge and Brave), permissions, privacy |
| `/download/` | `src/pages/download.astro` | One box with a tab per system (the visitor's first), checksums, then the extension |
| `/about/` | `src/pages/about.astro` | How it is built, and the credits (`#credits`). `/liner-notes/` redirects here (301, `firebase.json`) |
| `/privacy/`, `/terms/` | `src/pages/[...policy].astro` | Website privacy, consent choices, terms of use and third-party licences in all seven languages |
| 404 | `src/pages/404.astro` | One sentence and the way home |

Every page but the 404 also exists in the app's other languages, under the language's
code: `src/pages/de/app.astro` is `/de/app/`, and so on for `es`, `fr`, `it`, `pl`, `ru`.

`blueprint/blueprint.md` is the agreed structure: pages, sections, their order and
purpose. Only describe features that ship; `PROJECT_SPECIFICATION.md`, `README.md` and
`CHANGELOG.md` at the released commit are the source of truth.

## How it is put together

- **`site.config.mjs`** holds the public address (`SITE_URL`), the repository links,
  and the public release version supplied by CI (local preview defaults to `pyproject.toml`). Download links are composed from that
  version (`asset("windows-x64-test.exe")`), never typed by hand.
- **`src/layouts/Base.astro`** is every page's shell: head and SEO, the one-line
  header (App, Extension, About, Download, GitHub, language, theme; a Menu disclosure
  on narrow screens), the deck (the record) beside the text, the full-width
  footer under both, and the one lightbox `<dialog>` every `<Shot>` opens in. A page
  passes `title`, `description` and `label` (which colours its record label takes:
  `home`, `app`, `extension`, `download`, `about`); `tracks` is accepted and ignored.
- **Sections.** A page is a list of sections, each a `<Track id title heading>`, with
  no numbers and no overline labels. The first is its hero: `<Track hero …>` renders
  an `h1`, and `<Track hero sub …>` is the quieter opening of every page but the
  landing page. The deck is sticky inside the page grid, so the record stops where the
  text ends and the footer runs under it. On wide screens the record is large and a little
  under half of it is tucked past the window's right edge (`--disc`, `--hidden`).
- **`src/components/Record.astro`** draws the record and, as a separate element, the
  tonearm, as inline SVG; the tonearm is drawn but hidden (its bearing would sit past the
  window's edge). The record's radial code comes from the same seeded generator
  as `scripts/app_icon.py`. `src/scripts/geometry.js` holds the arm's angles.
- **`src/scripts/deck.js`** lets the record be grabbed and turned (the page scrolls by
  the same share of its length, all the way to the footer), runs the Copy buttons and
  the theme button, and is the fallback where CSS scroll-driven animations are missing.
  `lightbox.js` opens shots larger, `player.js` runs the player, `seeded.js` is the
  records' seeded generator (build time), and `src/scripts/boot.js` runs in the head.
- **Content security policy.** Every page carries one (`security.csp` in
  `astro.config.mjs`): bundled scripts and styles are allowed by hash; a measurement-enabled build also permits the Google tag script and analytics endpoints, and `firebase.json` adds the header-only directives. So no `style="…"`
  attributes and no `is:inline` scripts in pages; the one script that must run before
  first paint is `src/scripts/boot.js`, allowed by its own hash.
- **Motion** is CSS first: `animation-timeline: scroll()` turns the disc; the spin-up on a first visit is keyframes. Going
  to another page turns the record over with a cross-document view transition: the disc
  flips over its spindle. Everything is off under
  `prefers-reduced-motion`, and the page is complete without JavaScript.

## Languages

The site speaks the app's seven languages, English at the root. `src/i18n/index.mjs`
lists them and maps paths between them; `src/i18n/<code>.json` holds the layout's and
components' strings (header, footer, Copy, the extension's data-flow diagram). A
language's page is a copy of the English one with its text translated: imports one
folder deeper, internal links under `/<code>/`, its own screenshots from
`src/assets/screenshots/<code>/` (the extension panel and the player are shared;
the extension has no translations). Name the app's buttons and dialogs as the app's
catalog does (`dj_digger/gui/translations/<code>.ts`).

`npm run build` ends with `scripts/check-languages.mjs`: every page must exist in every
language with the English page's section ids (in order), pictures and commands, the same
`<style>` and `<script>`, the right `lang` and links that stay in its language. So a change to an
English page is a change to all seven.

The header's language switcher works without a script. Each page names its other
languages in `hreflang` links, and the sitemap lists them too. On an English page,
`boot.js` sends a visitor to the first of the browser's languages that the site
speaks, or to the language picked with the switcher once there is one
(`localStorage.djdLang`). Pages in other languages never redirect.

## Page kit

Shared pieces live in `src/styles/global.css` and `src/components/`. Use them before
writing page-specific CSS; put what is truly page-specific in the page's own `<style>`.

| Piece | Use |
| --- | --- |
| `<Track id title heading? hero? sub?>` | A section with its heading; no numbers, no overline |
| `<Shot src alt caption? full? eager?>` | A real screenshot or crop, always whole at its own shape. A click opens it larger in the lightbox: `full` (the whole-window shot it was cut from) or the same image. Without a script it is a link to that image (WebP, up to 2560 px) |
| `<Code code label?>` | A command with a Copy key |
| `<Loop>` | The player, recreated in HTML: cover with the track's artwork, title, waveform and transport; no audio (one per page; slot and props ignored) |
| `<Icon name weight?>` | A [Phosphor](https://phosphoricons.com) icon inlined at build time, `aria-hidden`, 1.15em; `weight` is `regular` (default), `thin`, `light`, `bold`, `fill` or `duotone` |
| `.lede`, `.copy` | Opening paragraph, body paragraph |
| `.stack` | Vertical rhythm between children |
| `.movement` | A sub-point inside a track: `h3`, text, usually a shot |
| `.ticks` | A short list of facts |
| `.cta`, `.button`, `.button.primary`, `.button.compact`, `.button.icon-only` | Actions, drawn as the player's keys: mono uppercase label after an icon (`<a class="button primary"><Icon name="download-simple" />Download</a>`). One magenta primary per screen; the rest are dark keys whose blue edge lights on hover and focus. `compact` is the small key (Copy, header, player); `icon-only` needs an `aria-label`. No text-link style: a link is a button, or plain in running text |
| `.notice` | A calm aside to read before acting |
| `.cards`, `.card` | A few parallel items |
| `.spec` | Label and value pairs (`dl`) |
| `.table` | A scrollable wrapper around a `table` |
| `.facts` | A row of short facts with a dot each |
| `kbd`, `.sr` | A key; text for screen readers only |

Colours are the app's dark and light themes and its magenta-blue waveform pair. The
site follows the system's theme; the header button switches it and remembers the
choice (`localStorage`, restored by `boot.js` before first paint). Switching back to the
system's own theme forgets the choice, so the site follows the system again. Every colour token
is one `light-dark()` value in `global.css`, so a new colour needs both sides there.
Text uses `--fg`, `--muted` and `--accent` only, which keep WCAG AA contrast on `--bg`
and `--panel` in both themes; `--neon-foot` and `--neon-top` are decoration, never text.

## Images, the player and its music

The screenshots come from one script, run on the release the site describes, so the site
never shows an interface that has not shipped. In an extracted `git archive` of the release
tag (with this branch's `scripts/site_screenshots.py` copied in, if the tag predates it), from
its root:

```bash
uv run --extra gui --extra analyze python scripts/site_screenshots.py --track PATH [lang]
```

`PATH` (or `DJD_SITE_TRACK`) is the MP3 of "Drift Away" by JMHBM, the demo track that plays in
the screenshots; the script never downloads it. It runs the real app against an isolated demo
profile under `/tmp/dj-digger-demo` and writes, under `site/src/assets/` of that tree (copy them
back here):

- `screenshots/*.png`: eight whole windows at 2x and the extension panel (playing, and
  during a cart job); `screenshots/crops/*.png`: tight crops of those windows.

With a language code (`de`, `es`, `fr`, `it`, `pl`, `ru`) the app runs in that language and
only its window shots and crops are written, to `screenshots/<code>/`; the panel is shared.
Nothing there is drawn by hand. "Drift Away" is by JMHBM (Johnathon M. Horner, Beat Mekanik),
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), via
[Free Music Archive](https://freemusicarchive.org/music/beat-mekanik/single/drift-away/); the
About page credits it.

The player (`<Loop>`) is not a recording: it recreates the app's player in HTML and SVG,
showing "Mathilde Fernandez - Temple Sourire (Strenx & Lezguy Edit)" by Strenx & Lezguy, used
with the artists' permission, as the app shows a SoundCloud track: its artwork on the record's
label and the title as SoundCloud writes it (no artist line under it). `src/assets/player/temple-sourire.json`
(title, duration, the permalink and SoundCloud's waveform samples scaled to 0-1) and
`temple-sourire.jpg` (the `t500x500` artwork) were taken once from the public track page
(`artwork_url`, `waveform_url`); the site serves both itself and requests nothing from SoundCloud.
There is no audio: the playhead only runs over the track's duration. SoundCloud gives no BPM or
key for the track, so there are no chips and no kick light. The About page credits it.

`uv run --extra gui --with fonttools --with brotli python scripts/site_og.py` redraws
`public/og.png`, the preview card, from the app icon's own drawing routine.

Fonts are Archivo and JetBrains Mono, with Cyrillic from Roboto Flex (Archivo has none; it has the
same width and weight axes), all SIL Open Font License (texts beside them in `src/assets/fonts/`).
They are subset to Latin, Latin Extended-A and Cyrillic, split by `unicode-range` so a page
downloads only the files its letters need, and served from this site. Icons are
[Phosphor Icons](https://github.com/phosphor-icons/core) (`@phosphor-icons/core` 2.1.1,
MIT License, © 2023 Phosphor Icons), inlined into the pages at build time.

## Deploy

Firebase Hosting serves `site/dist` (`firebase.json` in the repository root).
`.github/workflows/site.yml` builds and deploys when a release is published or by
manual dispatch; setup and manual deployment are in
[docs/site-deployment.md](../docs/site-deployment.md). The site also serves
`public/install.sh`, the Linux install script.
