# dj-soundcloud-digger

Paste a SoundCloud link, get every purchase and free-download link behind it, then
walk the list one track at a time and mark off what you already own.

```bash
dj-digger https://soundcloud.com/someone/sets/that-playlist
```

That is the whole workflow. No saving pages, no scrolling to the bottom of a
playlist, no API key, no account.

## What changed in 0.2

Version 0.1 needed you to open a playlist in the browser, scroll to the very
bottom so every track lazy-loaded into the DOM, save the page with `Ctrl+S`, then
point the tool at the HTML file. It then fetched every track page one by one.

It turns out none of that was necessary. SoundCloud's own web app asks
`api-v2.soundcloud.com/resolve` for a playlist and gets **every track id back in
one response** - the lazy loading only ever affected what was drawn on screen, not
what the server sent. Tracks are then hydrated 50 at a time, and each one already
carries its purchase link as a field.

A 282-track playlist takes six requests and about three seconds, where the old
path took 282 requests and about five minutes.

## Install

```bash
uv tool install dj-soundcloud-digger
```

or

```bash
pipx install dj-soundcloud-digger
```

From a clone, for development:

```bash
uv venv && uv pip install -e '.[yaml,dev]'
```

YAML export is an optional extra (`dj-soundcloud-digger[yaml]`) because PyYAML is
only needed if you actually want YAML.

## Links you can dig

| Link | What you get |
| --- | --- |
| `soundcloud.com/user/sets/playlist` | every track in the playlist |
| `soundcloud.com/user/likes` | everything that user liked |
| `soundcloud.com/user` | that artist's own tracks |
| `soundcloud.com/user/track` | a single track |
| `playlist.html` | a saved page, see the fallback section |

## The interactive browser

By default `dj-digger` drops you into a table of everything it found. Opening 287
links at once is not a workflow, so instead you move through them:

| Key | Action |
| --- | --- |
| arrows | move around |
| `o` or `enter` | open the selected link in your browser |
| `g` | mark as got, and move to the next one |
| `s` | mark as skipped |
| `u` | unmark |
| `a` | open everything currently visible (asks first above 20 links) |
| `/` | filter by artist or title |
| `1`-`5` | show only one store, `0` for all |
| `h` | hide what you already handled |
| `e` | export the rows you can currently see |
| `q` | quit |

**Marks are keyed by track id, not by playlist.** A track you bought once reads as
`got it` the next time it turns up in somebody else's set. That state lives in a
small JSON file under your platform's data directory (`~/.local/share/dj-digger/`
on Linux).

## Non-interactive use

Add `--no-tui` to just write the export and exit. It is also skipped
automatically when output is not a terminal, so piping works as you would expect.

```bash
dj-digger https://soundcloud.com/someone/likes --no-tui -o crate.json
dj-digger <link> -f csv -o crate.csv     # json (default), yaml, csv or none
dj-digger <link> -n 20                   # first 20 tracks only
dj-digger open crate.json                # reopen an export in the browser
dj-digger open crate.json --category bandcamp   # straight to opening tabs
```

The v0.1 flag names (`dig`, `--export`, `--max-tracks`) still work.

## Stores

Links are grouped into `bandcamp`, `beatport`, `junodownload`, `hypeddit` and
`others`. A link only earns a store category by matching that store's domain,
because `purchase_url` is not always a shop - artists also hang interviews and
press articles off it. Tracks with no store link anywhere still get a row, so
nothing silently disappears.

## Saved-HTML fallback

Private and unlisted playlists are not reachable through the API, and the API is
undocumented so it could change. For those cases, save the page with `Ctrl+S` and
pass the file:

```bash
dj-digger playlist.html
```

You no longer need to scroll first: the saved page contains a `__sc_hydration`
blob listing every track id, which goes straight to the batch hydrator. If that
blob is missing, the tool falls back to scraping each track page, which is the
slow v0.1 behaviour and the only place `--delay` still matters.

## A caveat worth knowing

`api-v2.soundcloud.com` is public but undocumented, and the `client_id` is lifted
from SoundCloud's own JavaScript bundles. It is cached and re-discovered
automatically when it stops being accepted, but SoundCloud could change any of
this without notice. That is why the saved-HTML path is still here.

## Development

```bash
pytest              # offline suite, uses recorded API payloads
pytest -m live      # hits the real API, tells you if SoundCloud moved something
```

The live tests are the early-warning system for the paragraph above. Point them
somewhere else with `DJ_DIGGER_LIVE_URL` if the default playlist disappears.

## License

Apache License 2.0. See [LICENSE](LICENSE).
