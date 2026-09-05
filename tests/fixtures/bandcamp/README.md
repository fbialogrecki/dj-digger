# Recorded Bandcamp pages for the DOM contract tests

`tests/test_bandcamp_dom.py` (marker `bandcamp_dom`) drives these files in a
real headless Chromium with every network request answered from disk, so the
selectors the cart relies on are checked against the page as Bandcamp actually
renders it, without touching the store. The test skips when the files are
absent or Playwright Chromium is unavailable. This folder includes the three
owner-recorded page states and their source URL; the procedure below describes
how to replace the recordings.

## Recording

Use a fresh private Chromium profile, logged out of Bandcamp, on a track with
seller-approved name-your-price pricing (so no money is involved), and save
`document.documentElement.outerHTML` from the DevTools console at each state:

| file | state |
| --- | --- |
| `track_page.html` | the track page as it loads |
| `track_page_buy_open.html` | after clicking "Buy Digital Track" (price field visible) |
| `sidecart_after_add.html` | after "Add to cart", with the side cart open |

Then, for every file:

- drop the `src="..."` attribute from every `<script>` tag but keep the tag:
  Bandcamp carries `TralbumData` in that tag's `data-tralbum` attribute, and
  the adapter reads it from there;
- empty the `data-blob` of `#collectors-data` and the `page-context` of
  `<page-footer>` (other fans' public data and page telemetry);
- make sure this prints nothing:

  ```bash
  grep -niE "fan_id|identity|set-cookie|[a-z0-9._-]+@[a-z0-9-]+\.[a-z]{2,}" tests/fixtures/bandcamp/*.html
  ```

- note the product URL the pages came from in `product_url.txt` (one line),
  so the test can route requests for it.

Afterwards remove the track from your cart by hand; the recording never
touches the cart automatically.
