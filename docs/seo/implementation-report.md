# Website SEO implementation · 22 November 2026

The release candidate has **61 canonical pages**, up from 59, across seven site languages. The public site still serves the earlier revision. The five key-event definitions were saved in the live GA4 property. The website code is on the local branch `codex/site-seo`, based on `aa68b05`.

## Pages and discovery

| Pages | Change |
| --- | --- |
| Home in EN, DE, ES, FR, IT, PL and RU | Describe importing, auditioning, remembering owned tracks and opening artist-provided links. EN, DE and PL link directly to their rekordbox guide. The English title retains the product name and crate-digging intent. |
| /guides/organize-downloaded-dj-tracks/ | New English guide to an inbox, mix identification, stable file paths, reusable gig playlists and missing files. Includes an existing screenshot of the real local library with demo data. |
| /guides/flac-vs-wav-for-djs/ | New English guide comparing two named player models and showing how to choose a playback copy, preserve a master and diagnose an unreadable file. Cites manufacturer manuals. |
| SoundCloud to rekordbox and where-to-buy guides, EN/DE/PL | Explain importing local files, checking the beatgrid and USB export, troubleshooting a missing track and checking which mix the shop sells. Link to the appropriate next workflow. |
| Hypeddit guide and FAQ links | Preserve the indexed URL. Replace the date-led article with a sequence for reading an artist’s gate, completing chosen steps and troubleshooting a missing file. Link directly to Hypeddit’s documentation. |
| Privacy and terms in all seven languages | Provide page-specific search descriptions. Translate the expanded scope of consent in the banner and privacy notice. |
| Shared layout, footer, sitemap and llms.txt | Register actual article languages. Add the new guide links, a visible Home → article breadcrumb and article-specific Open Graph metadata. |

A fresh browser keeps the requested language on a direct visit. The bare homepage still honors a language the visitor explicitly selected. Existing static canonicals, localized HTML, x-default, XML alternates, social image and noindex 404 worked at baseline. The release check now tests their built output. Hosting adds noindex headers for the graph and text utilities, preserving access to the installer and documentation. Localized legacy redirects now use literal language paths so Firebase Hosting and its emulator can both parse them.

## Structured data and measurement

The graph retains the real individual publisher, WebSite, SoftwareApplication, BreadcrumbList and Article types. The article URL and dates agree with its canonical and social tags. The extension declares browsers as software requirements. The desktop markup no longer calls the illustrated social preview a software screenshot. Software prices reflect the free app. No review or rating was supplied. The local checks validate graph structure and page agreement. Google determines feature eligibility. [Schema.org Article](https://schema.org/Article) · [Google article guidelines](https://developers.google.com/search/docs/appearance/structured-data/article) · [Google software app guidelines](https://developers.google.com/search/docs/appearance/structured-data/software-app) · [Localized versions](https://developers.google.com/search/docs/specialty/international/localized-versions)

The consented listener preserves the three download event names and successful Linux copy event. It adds Chrome Web Store, project repository and guide-to-product clicks with bounded parameters. No generic outbound event was added. GA4 Admin confirmed Enhanced Measurement was off on the matching web stream. The new consent revision requires renewal of older opt-ins and honors unexpired refusals. Isolated tests intercepted every external request. [Event definitions and property configuration](monitoring.md)

## Verification

| Check | Result |
| --- | --- |
| Production baseline | HTTP-fetch and parse all 59 sitemap URLs. All returned 200, one H1 and matching canonical/alternates. Titles matched the clean main-branch site. Missing URL returned 404. |
| Production-mode Astro build | 62 generated editorial/error HTML pages, including the noindex 404. The route and language checks passed for all 61 indexable pages. |
| SEO regression | Parsed XML sitemap and raw generated HTML, compared reciprocal language sets and x-default, checked unique titles/descriptions, H1s, image alt attributes, OG and Twitter card, article and software JSON-LD, internal route/fragment targets and crawlability from the homepage. Passed. |
| Firebase Hosting emulator | Public page and new guide 200, missing page 404, graph/JSON and text utilities noindex, clean/legacy URLs resolve. English, German and Polish redirect cases and an unsupported language were exercised. |
| Browser and analytics | New and previous consent revisions, denial, withdrawal, expiry, blocked storage, two tabs, one page view per load, exact custom click names, CTA/installer exclusivity, sanitized URLs and no-JS downloads passed. No production hits were sent by the automated test. |
| Responsive and accessibility | Seven routes at 360 × 800, 390 × 844 and 1440 × 900 passed heading and horizontal-width checks. axe-core reported zero WCAG A/AA rule violations on home, app, download and both new guides in light mobile and dark desktop views. Existing tests exercise mobile menu focus, Escape, tapping outside and keyboard access. Headed Chrome at 1536 px with classic scrollbars confirmed the fix from 1528 px of scrollable width to the available 1521 px. |
| Type and lint | Astro checker 0.9.12 with TypeScript 5 and Node 24 type declarations, using isolated tooling and ES2022/DOM/Bundler settings, reported zero errors across 73 files. Ruff, the spec section map and git whitespace checks passed. |
| Application verification | 1191 offline tests passed, 78 deselected. Python wheel and sdist built. The wheel passed the repository’s isolated installation/CLI smoke check. |

To repeat the release checks locally, run from the repository root:

```sh
(cd site && npm ci && GA4_MEASUREMENT_ID=G-SEOQA npm run build && python3 scripts/check-seo.py)
uv run --script site/scripts/check-consent.py
python3 scripts/spec_section_map.py --check
uv run --extra dev ruff check .
uv run --extra dev pytest
```

## Mobile performance

These are local cold-load **lab medians**. Chromium 157 ran three fresh contexts per page and revision. Measurements alternated baseline and candidate, using the same static HTTP server, EN locale, 390 × 844 viewport, DPR 2, 4× CPU slowdown, 65 ms latency and 200,000 bytes/s download throughput. There was no interaction, scrolling or analytics consent. LCP and CLS came from PerformanceObserver. Bytes sum CDP encodedDataLength through network idle plus 700 ms and include the initially requested resources. This server does not reproduce Firebase edge compression. Script time is CDP ScriptDuration, not INP.

| Page | LCP ms, before → after | CLS, before → after | Initial bytes, before → after | Script ms, before → after |
| --- | ---: | ---: | ---: | ---: |
| Home | 1076 → 952 | 0 → 0 | 323869 → 314485 | 34 → 36 |
| App | 1152 → 1348 | 0 → 0 | 317251 → 318053 | 57 → 34 |
| Download | 944 → 968 | 0 → 0 | 271228 → 272035 | 45 → 36 |
| SoundCloud to rekordbox | 852 → 856 | 0 → 0 | 255407 → 257230 | 39 → 32 |
| Organize downloaded tracks | new → 844 | new → 0 | new → 256979 | new → 33 |
| FLAC vs WAV | new → 876 | new → 0 | new → 255992 | new → 24 |

The shared decorative SVG fell from 3828 to 2841 bytes and from 39 path/circle elements to 22. The candidate reserves the first-visit banner’s space before its consent handler starts. An earlier baseline run observed CLS around 0.11 on three pages; the paired baseline run measured zero, so the magnitude of the CLS change is uncertain. The homepage transferred 9384 fewer bytes in the paired run. The app page’s median LCP increased by 196 ms. These short runs do not establish a site-wide speed gain. Search Console had no field LCP, CLS or INP data for this domain.

## Release status

The live domain still has the 59-page sitemap. Search Console already indexed the existing English rekordbox guide using its declared canonical. The new pages’ index status, post-release GA4 events, search traffic and rankings await deployment and observation. [Initial audit](audit.md) · [Keyword map](keyword-strategy.md) · [30/60/90-day monitoring plan](monitoring.md)
