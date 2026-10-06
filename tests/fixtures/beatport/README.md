# Beatport page states (synthetic)

Hand-written pages that reproduce only the structure the Beatport cart relies on,
as recorded live on 2026-10-07 (release rows, a track page with its own add
control apart from the recommendations, the logged-in cart with one real row and
recommendation rows). Titles, artists, ids and the cart id are invented: real
pages carry session tokens and account data and are never copied here. The
logged-out `/cart` redirect to `/?next=/cart` is modelled by the fake page in
`tests/test_beatport_cart.py`.
