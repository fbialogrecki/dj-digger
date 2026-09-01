# Recorded Bandcamp pages for the DOM contract tests

`tests/test_bandcamp_dom.py` (marker `bandcamp_dom`) drives these files in a
real headless Chromium with every network request answered from disk, so the
selectors the cart relies on are checked against the page as Bandcamp actually
renders it, without touching the store. The test skips when the files are
absent, and this folder ships empty: the pages are recorded by the owner.

## Recording

Use a fresh private Chromium profile, logged out of Bandcamp, on a track with
seller-approved name-your-price pricing (so no money is involved), and save
`document.documentElement.outerHTML` from the DevTools console at each state:

| file | state |
| --- | --- |
| `track_page.html` | the track page as it loads |
| `track_page_buy_open.html` | after clicking "Buy Digital Track" (price field visible) |
| `sidecart_after_add.html` | after "Add to cart", with the side cart open |
| `cart_page.html` | `https://bandcamp.com/cart` |

Then, for every file:

- remove each `<script src=...>` tag; keep inline scripts (`TralbumData` is
  what the adapter reads);
- make sure this prints nothing:

  ```bash
  grep -niE "fan_id|identity|set-cookie|@[a-z0-9-]+\.[a-z]{2,}" tests/fixtures/bandcamp/*.html
  ```

- note the product URL the pages came from in `product_url.txt` (one line),
  so the test can route requests for it.

Afterwards remove the track from your cart by hand; the recording never
touches the cart automatically.
