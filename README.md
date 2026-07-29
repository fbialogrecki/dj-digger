# dj-soundcloud-digger

Paste a SoundCloud link, get every purchase and free-download link behind it, then
walk the list one track at a time and mark off what you already own.

```bash
dj-digger https://soundcloud.com/someone/sets/that-playlist
```

That is the whole workflow. No saving pages, no scrolling to the bottom of a
playlist, no API key, no account.

Or just run it and let it ask:

```bash
dj-digger
```

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

Three other things came with it. Results open in an interactive browser instead of
firing every link at your browser at once, and that browser keeps a library of
crates you can switch between and preview audio from. And the four store
categories grew into eleven, so smart links, download gates and record shops stop
being lumped together as `others`.

## Install

```bash
uv tool install 'dj-soundcloud-digger[play]'
```

or

```bash
pipx install 'dj-soundcloud-digger[play]'
```

From a clone, for development:

```bash
uv venv && uv pip install -e '.[play,yaml,dev]'
```

Both extras are optional. `play` pulls in miniaudio for audio preview, and `yaml`
pulls in PyYAML for YAML export; without them everything else still works and the
features that need them say so.

## Links you can dig

| Link | What you get |
| --- | --- |
| `soundcloud.com/user/sets/playlist` | every track in the playlist |
| `soundcloud.com/user/likes` | everything that user liked |
| `soundcloud.com/user` | that artist's own tracks |
| `soundcloud.com/user/track` | a single track |
| `playlist.html` | a saved page, see the fallback section |
| nothing at all | you get asked for one |

## The interactive browser

By default `dj-digger` drops you into a table of everything it found. Opening 287
links at once is not a workflow, so instead you move through them:

Press `?` for the full list, grouped by what each key acts on. That grouping is
the point: it was previously impossible to tell whether a key hit one row or the
whole list.

On the highlighted row:

| Key | Action |
| --- | --- |
| arrows | move around |
| `o` or `enter` | open its store link in your browser |
| `g` | mark as got, and move to the next one |
| `s` | mark as skipped, `u` to unmark |
| `x` | remove it from this crate, `ctrl+z` to undo |

Playback:

| Key | Action |
| --- | --- |
| `space` | play or pause |
| `[` `]` | back or forward 10 seconds |
| `n` `p` | next or previous track |
| `-` `=` | quieter or louder, `m` mutes |
| click | seek to that point on the waveform |

On the whole visible list:

| Key | Action |
| --- | --- |
| `a` | open every link shown (asks first above 20) |
| `/` | filter by artist or title |
| `f` / `F` | step forward or back through the stores in this crate |
| `1`-`9` | jump straight to a store, `0` for all |
| `h` | hide what you already handled |
| `e` | export the rows you can currently see |

Crates:

| Key | Action |
| --- | --- |
| `d` | add a crate from a link |
| `r` | refresh the highlighted crate from SoundCloud |
| `shift+X` | delete it, after confirming |
| `ctrl+b` | show or hide the crate sidebar |

The store line under the header is the legend for the number keys, and it only
lists stores this crate actually contains:

```
0 all  1 bandcamp·189  2 beatport·4  3 junodownload·1  4 shop·8  5 smartlink·2  6 others·86
```

So `1` is always the first store you have rather than a fixed category, and `f`
never makes you cycle through eight empty ones.

**Marks are keyed by track id, not by playlist.** A track you bought once reads as
`got it` the next time it turns up in somebody else's set. That state lives in a
small JSON file under your platform's data directory (`~/.local/share/dj-digger/`
on Linux).

## Your crate library

Every crate you dig is saved, so switching between playlists is a keypress rather
than a restart. The sidebar lists them; `enter` on one loads it.

Crates store whole tracks rather than the categorised links, which means
improving the store detection improves crates you imported months ago. Refreshing
(`r`) re-digs the saved link and **keeps tracks you deleted locally deleted** -
that is why deletions are tracked separately from the track list.

Removing a track with `x` only touches your copy. We read SoundCloud with an
anonymous client id, which grants no write access whatsoever, so editing the real
playlist would need a full OAuth login. A crate marked with `*` came from an
export file and is missing fields the API would have given us; `r` fills it in.

## Previewing tracks

`space` previews the highlighted track. The MP3 downloads to a temporary
directory first - about 1.5 seconds for a 7 minute track - which is what makes
seeking instant afterwards. Click anywhere on the waveform to jump there.

The waveform is not computed locally: SoundCloud publishes 1800 samples per track
and we just draw them.

Nothing is cached between runs. A persistent cache of whole tracks reaches
gigabytes after an evening of digging and would then need an eviction policy, so
the temporary directory goes away when you quit.

If there is no audio output, or miniaudio is not installed, the player says so in
its bar and everything else carries on working.

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

Links are grouped by what you can actually do with them, best outcome first:

| Category | Means |
| --- | --- |
| `bandcamp` `beatport` `traxsource` `junodownload` `apple` | buy it there |
| `shop` | another record shop: Boomkat, Hard Wax, Clone, Decks, Deejay, Red Eye, Juno, Phonica, Rush Hour, Bleep, Gumroad |
| `hypeddit` | free, behind a Hypeddit gate |
| `download` | free, behind another gate: Wump, The Artist Union, Toneden, Pump Your Sound |
| `smartlink` | a click-through page: lnk.to, ffm.to, fanlink, smarturl, orcd.co, Linktree, or a label's own `.link` domain |
| `streaming` | Spotify, YouTube, Deezer, Tidal - nothing to buy |
| `others` | an unrecognised link, or no link at all |

That grouping is not guesswork: it comes from surveying `purchase_url` across 53
playlists and 3497 tracks. Smart links were by far the biggest thing the old
four-store version threw into `others`, followed by follow-to-download gates.

A link only earns a category by matching a domain on a boundary, so
`evil-bandcamp.com` does not pass as Bandcamp. `purchase_url` is not always a
shop either - artists hang interviews and press articles off it - and those stay
in `others`. Tracks with no link anywhere still get a row, so nothing silently
disappears.

Descriptions are treated as a weaker source than `purchase_url`: a buyable link
in there is worth having, but the label's Linktree and Spotify profile pasted
into every single description are not, so smart links and streaming links only
count when they are the track's actual purchase field.

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

Audio preview leans on the same API, plus the `progressive` transcoding and
`waveform_url` specifically. `pytest -m live` checks both still exist.

## Development

```bash
pytest              # offline suite, uses recorded API payloads
pytest -m live      # hits the real API, tells you if SoundCloud moved something
```

The live tests are the early-warning system for the paragraph above. Point them
somewhere else with `DJ_DIGGER_LIVE_URL` if the default playlist disappears.

The offline suite never opens an audio device and never touches your real crate
library or status file - both are redirected to a temporary directory.

## License

Apache License 2.0. See [LICENSE](LICENSE).
