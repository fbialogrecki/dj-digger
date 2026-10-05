# Recorded Bandcamp pages (reference snapshots)

The recorded pages are reference snapshots of the page states the Bandcamp cart
relies on, kept for manual checking of the extension's selectors (for example
against `dom.js`) without touching the store. No automated test loads them since
the Playwright-based `bandcamp_dom` marker was removed. This folder includes the
three owner-recorded page states and their source URL; the procedure below
describes how to replace the recordings.

## Recording

Use a fresh private browser window, logged out of Bandcamp, on a track with
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
  so tests can match requests for it.

Afterwards remove the track from your cart by hand; the recording never
touches the cart automatically.
