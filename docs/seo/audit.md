# Website SEO audit · 22 November 2026

Baseline is public Firebase Hosting and main at aa68b05. The open development checkout on fix/restore-player-layout predates the deployed guides. The site builds static HTML with Astro. A shared layout and page/language registry supply metadata, navigation and the sitemap. Python desktop, CLI, extension and store workflows are separate from website measurement.

| Priority | Initial finding and evidence | Resolution in this change |
| --- | --- | --- |
| Critical | None found. All 59 sitemap addresses returned HTTP 200 at their submitted address with one H1, a self-canonical, HTML alternates matching the sitemap, matching main-branch titles and no index exclusion. The missing-page probe returned 404. | Keep the static routes, absolute canonicals, reciprocal language clusters and crawlable robots policy. |
| High | The head script redirected English pages on a fresh visit using browser language. A copied guide URL could land on a different translation and lose its query. | Keep explicit URLs stable and offer translations through the existing language menu. |
| High | GA4 showed zero key events in the observed week. The site measured installers individually and Linux command copies but had no extension or guide CTA measurement. | Measure the distinct actions after opt-in and test their exclusivity. See monitoring.md for property configuration. |
| Medium | Three guide topics, no contextual next step for a reader sorting downloads or choosing playback formats. One gate guide framed a historical provider change as current behavior. | Add two practical guides and revise the acquisition, gate and rekordbox workflows. |
| Medium | The public architecture graph and install script returned 200 without indexing controls. They serve developers and installers. | Add scoped Hosting response headers while retaining public access. |
| Medium | The language build check compared section counts and link prefixes. A nonexistent in-language URL, incorrect alternate or orphan route could pass. Article pages also shared the website Open Graph type. | Add built-output validation and article-specific social metadata. |
| Medium | Desktop Chrome with a classic scrollbar exposed 7 px of horizontal overflow at a 1536 px viewport. The decorative deck used viewport units including the scrollbar. | Reserve a stable desktop scrollbar gutter and recheck the real scroll width. |
| Low | Every page embedded the same decorative record geometry twice. | Reuse the SVG geometry and measure the result on a cold mobile viewport. |

## Coverage

The 59 entries comprise home, desktop app, extension, download, about, privacy and terms in EN/DE/ES/FR/IT/PL/RU (49), FAQ and two guide topics in EN/DE/PL (9), and the English Hypeddit guide (1). English lives at the root. Non-English URLs start with the language code. Existing clean-URL and legacy redirects resolve /app and /app/index.html to /app/, and /liner-notes/ to /about/. Hashed assets have a one-year immutable caching rule. The shared preview image is 1200 × 630. The 404 and static utilities are outside the editorial inventory.

## Search and measurement baseline

The verified Search Console domain property reported 4 impressions, 0 clicks, 0% CTR and average position 3.8. The three-month selector contained observations only on 19–20 November. The sole disclosed query, “dj digger”, had 3 impressions at position 4.7. The homepage appeared 3 times, German home twice, Polish home, app, extension and the English rekordbox guide once each. Page totals can exceed property totals. The US, Germany, Poland and Russia had one impression each. Mobile and desktop had two each. The sitemap was last read successfully on 20 November with 59 discovered URLs. Page indexing was still processing. No mobile or desktop field Web Vitals were available. The breadcrumbs report showed four valid items and zero invalid. URL Inspection confirmed the English rekordbox guide was indexed, fetched by smartphone Googlebot on 20 November, with the declared URL selected as canonical. Its sitemap association showed a temporary processing error despite the successful property-level sitemap report.

The GA4 property for dj-digger showed 11 active users, 19 views, 58 events, 14 Direct sessions, two Organic Social sessions and zero recorded key events for 15–21 November. The period and measurement differ from Search Console. There is too little search data for a CTR rewrite or a reliable non-brand ranking opportunity.

## Outstanding verification

Production crawl, Google-selected canonicals, field experience and GA4 collection for the new build require a deployment and fresh observations. The build and browser results are in implementation-report.md.
