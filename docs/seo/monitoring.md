# Measuring the website after release

## Measurement definitions

Use the verified Search Console **Domain property djdigger.app** and the GA4 **dj-digger-a9664** property, web stream **djdigger.app**. On 22 November 2026 the web stream had Enhanced Measurement switched off. Keep it off for this implementation. A single consented tag configuration records the page view. The custom click listener sends only explicitly recognized actions. Generic outbound click and automatic file-download events would add noise and collect destinations outside this design.

| Event | What happened | Key event |
| --- | --- | --- |
| download_windows_x64 | Visitor activated the Windows installer link | Yes |
| download_macos_arm64 | Visitor activated the Apple Silicon installer link | Yes |
| download_macos_x86_64 | Visitor activated the Intel Mac installer link | Yes |
| copy_linux_command | Clipboard copy of the Linux command succeeded | Yes |
| extension_install_click | Visitor followed the project’s Chrome Web Store listing | Yes |
| guide_cta_click | Visitor activated an in-article product/download CTA | No |
| github_click | Visitor followed the project repository root link | No |

All five named key events were configured and verified in **Admin → Data display → Events**. They use code-emitted event names, no default monetary value and once-per-session counting for each name. A visitor may perform different key actions within one session. Use the session key-event rate or distinct sessions for conversion analysis. The events measure intent. Installer completion, Linux command execution and extension installation are outside the site’s observation.

Existing installer names preserve reporting continuity. Events include language, release_version and content_category. Installer events include platform_name and architecture. Guide CTA events include a fixed destination (app, extension or download). Page location is built from the known page route. The tag receives the referrer origin. No custom link URL, search parameters, fragments, track names, contacts or clipboard contents are sent. Collection begins after consent. A previous opt-in requires renewal for the expanded purposes. An unexpired previous refusal remains valid.

## At deployment

1. Publish through the existing Website workflow after the reviewed commit reaches main. Record the website deployment SHA and timestamp beside the GA4 key-event configuration date. A release of the desktop app is a separate operation.
2. Fetch robots.txt and the listed sitemap. Expect 61 canonical pages for this revision, including both new English guides. Check English, German and Polish rekordbox pages for reciprocal alternates. A missing URL should return 404. The graph, install script, licences and llms.txt should remain accessible with X-Robots-Tag: noindex.
3. In Search Console → Sitemaps, inspect the submitted sitemap’s last read, status and discovered page count. Its last confirmed successful read before deployment was 20 November, with 59 pages. In URL Inspection, inspect the two new guide URLs and the existing rekordbox article. Compare live HTTP success, indexability, declared canonical and Google-selected canonical when available. Request indexing for a new guide after the updated public URL is live. A live test or request does not establish indexing.
4. Exercise consent in a clean browser. Before opt-in and after decline, check there is no Google tag request. After opt-in, verify a single page_view, one event for one real click, a working navigation, and no query or fragment in the measurement payload. Use Tag Assistant/DebugView on a QA stream or a filtered developer session. Withdraw through Cookie settings and confirm recording stops. Avoid sending synthetic conversions to production.
5. In GA4 Realtime and then Engagement → Events, verify names and sanitized parameters after real consented traffic arrives. Under Admin → Custom definitions, register event-scoped content_category and destination if needed in Explorations; optional dimensions include release_version, platform_name and architecture. Keep cardinality bounded. Event registration does not backfill historical values.

## 30, 60 and 90 days from publication

| When | Search Console | Analytics and decision |
| --- | --- | --- |
| 30 days | Check last crawl, Pages exclusions, duplicates, Google-selected canonicals, sitemap processing and field Web Vitals availability. Export Web results by date, query, page, country and device. | Verify daily page views, distinct sessions with each key event, and guide-to-product clicks. Compare Organic Search landing pages with Direct and Organic Social separately. Check data quality before changing titles. |
| 60 days | Compare two complete 28-day windows. Split branded and non-brand queries. Inspect repeated impressions with low CTR relative to pages at comparable positions, languages and devices. Inspect position 8–15 queries against the page that actually ranks. | Follow the sequence guide landing → guide CTA → installer/store intent. Identify the largest observable drop-off. Read the page on the relevant device before changing copy or placement. |
| 90 days | Compare page/query pairs again, including translations. Check whether pages share the same query and intent. Inspect mobile and desktop field LCP, INP and CLS if Google now reports enough traffic. | Keep useful guides, revise mismatched pages and commission a translation only where repeated demand warrants it. Record releases, referral campaigns and changes in consent uptake alongside the figures. |

For each comparison, retain the date range, search type, property, filters, denominator and raw counts. Four impressions and zero clicks at baseline cannot establish a CTR problem. GA4 observes consenting visits and GSC measures Google Search exposure. Neither source alone establishes causation.
