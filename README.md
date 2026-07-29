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

**One row is one track.** A track selling on Bandcamp and also sitting behind a
Hypeddit gate is one decision, not two, so it gets one row with a badge per
store. The badge in bold is the one `o` would follow.

| Column | Shows |
| --- | --- |
| `▶` | what is playing |
| mark | `·` untouched, `○` opened, `✓` got it, `✗` skipped |
| `#` | position in the crate |
| Track | artist and title, taking whatever width is left over |
| Stores | a badge per store; `shop` and `others` show the domain instead |
| Genre | whatever the artist typed, or their first tag |
| Time | track length |
| BPM | tempo, only when the crate has any - see below |

On the highlighted row:

| Key | Action |
| --- | --- |
| arrows | move around |
| `o` or `enter` | open its best link, or the filtered store, in your browser |
| `g` | mark as got, and move on; press again to undo |
| `s` | mark as skipped, and move on; press again to undo |
| `u` | clear the mark either way |
| `x` | remove it from this crate, `ctrl+z` to undo |

Playback:

| Key | Action |
| --- | --- |
| `space` | play or pause |
| `[` `]` | back or forward 10 seconds |
| `n` `p` | next or previous track |
| `-` `=` | quieter or louder, `m` mutes |
| click | seek to that point on the waveform |

A track that reaches its end rolls on to the next one, so auditioning a crate is
not a keypress per track. The cursor comes along for the ride, unless you have
moved it somewhere else - then playback carries on and `▶` shows where it got to.
Marking the track you are listening to moves the listening on too, which is what
makes `s` a triage key rather than a bookkeeping one.

Marking also lights the row for a quarter second, so a keypress is visibly a
change rather than a glyph you have to go looking for.

**Tempo.** SoundCloud has no BPM field - it is not in the track payload at all -
so the only tempo available is one the artist wrote down, in a tag, the title or
the description, and only when it says it is a tempo: a bare `150` is as likely
to be a catalogue number. On a hard techno playlist that is about one track in
twenty, so the column only appears when the crate has tempos in it and takes its
three columns back from the title when it does not. Some scenes tag it
religiously, and there the column is worth having.

On the whole visible list:

| Key | Action |
| --- | --- |
| `a` | open every link shown (asks first above 20) |
| `/` | filter by artist or title |
| `f` / `F` | step forward or back through the stores in this crate |
| `1`-`9` | jump straight to a store |
| `0` | drop the store filter and show everything again |
| `h` | hide what you already handled |
| `e` | export the rows you can currently see |

Crates:

| Key | Action |
| --- | --- |
| `d` | add a crate from a link |
| `r` | refresh the highlighted crate from SoundCloud |
| `shift+X` | delete it, after confirming |
| `ctrl+b` | show or hide the crate sidebar |

One bar sits between the table and the footer. On the left is the legend for the
number keys, listing only the stores this crate actually contains; on the right
is how far through it you are:

```
▸ 0 all  1 soundcloud·18  2 bandcamp·12  3 gate·53      83/83 tracks · got 0 · skipped 4
```

So `1` is always the first store you have rather than a fixed category, and `f`
never makes you cycle through eight empty ones. The `▸` marks what you are looking
at; `0` takes you back to everything. Narrow the terminal past the point where
both halves fit and the counts give way to the legend, which is the half with a
keyboard behind it - the bar is one line, never two.

Filtering is also how you overrule which link `o` opens. Left alone it follows
the best one, which puts buying it above earning it through a gate; pick the
store you want and `o` goes there instead.

**Marks are keyed by track id, not by playlist.** A track you bought once reads as
`got it` the next time it turns up in somebody else's set. That state lives in a
small JSON file under your platform's data directory (`~/.local/share/dj-digger/`
on Linux).

## Your crate library

Every crate you dig is saved, so switching between playlists is a keypress rather
than a restart. The sidebar lists them; `enter` on one loads it. The `↻` and `✕`
icons appear only on the row under the cursor or the mouse - the only row they
could act on anyway - so the other rows get those six columns for their names.

Crates store whole tracks rather than the categorised links, which means
improving the store detection improves crates you imported months ago. Refreshing
(`r`) re-digs the saved link and **keeps tracks you deleted locally deleted** -
that is why deletions are tracked separately from the track list.

Removing a track with `x` only touches your copy. We read SoundCloud with an
anonymous client id, which grants no write access whatsoever, so editing the real
playlist would need a full OAuth login. A crate marked with `*` came from an
export file and is missing fields the API would have given us; `r` fills it in.

## Previewing tracks

`space` previews the highlighted track. Nothing is written to disk: the MP3 is
decoded straight off the socket, so audio starts after about half a second rather
than waiting out a 6.6 MB download.

A copy is kept in memory as it arrives, though, and that is what makes seeking
free. Click anywhere on the waveform, or press `[` and `]`, and it jumps there
without touching the network - previously every seek opened a fresh connection
and cost half a second of silence, even to get back to audio that had just
played. The same copy is why there is no gap between tracks: with twenty seconds
left, the next one's stream URL, waveform and first megabytes are already being
fetched, so the auto-advance has nothing to wait for. Two tracks are held at a
time, about sixteen megabytes; anything over fifty megabytes, or a response that
will not declare its size, streams the old way instead.

The waveform is not computed locally: SoundCloud publishes 1800 samples per track
and we just draw them. Two rows of block characters give sixteen levels, columns
average rather than peak, and the loud end of the range is expanded - a mastered
techno track otherwise renders as a solid rectangle. It is deliberately not
stretched between its own minimum and maximum, because that made a track with no
dynamics at all look the most dynamic of the lot.

The dozen columns behind the playhead brighten with what is actually coming out
of the speakers, up to white on a hard hit - the level is read off the samples on
their way to the sound card, before the volume control, so it is the music that
shows and not the fader. It is measured against the loudest thing heard in the
last few seconds rather than against full scale, because a mastered techno record
sits at full scale from beginning to end and would never move otherwise. The
played region behind the glow stays a steady cyan: it is history, and flicker
there only tires the eye.

That runs at thirty frames a second while something is playing and not at all
when nothing is, and drops back to four a second under
`TEXTUAL_ANIMATIONS=none`, which is what you want over a slow ssh link.

If there is no audio output, or miniaudio is not installed, the player says so in
its bar and everything else carries on working.

That message means the session has no audio sink. `pactl list short sinks`
printing nothing confirms it. On a remote desktop there are usually two separate
things in the way:

- **No access to the sound card.** systemd-logind grants the ACL on `/dev/snd/*`
  to the active session on a seat. A remote session has no seat, so the card stays
  unreachable and PipeWire creates no sink even though the hardware is there.
  `getfacl /dev/snd/controlC0` shows who does have it. Adding yourself to the
  `audio` group works around it, but then sound comes out of the remote machine's
  speakers.
- **No audio redirection to your client.** That is the server's job:
  gnome-remote-desktop does it itself, while xrdp needs
  `pipewire-module-xrdp` and only loads it when `XRDP_SESSION` is set - so that
  package does nothing in a GNOME remote session.

To check the player itself without any of that, give PipeWire somewhere to write:

```bash
pactl load-module module-null-sink sink_name=test
```

Playback then runs silently but for real - the position advances and seeking
works. `pactl unload-module <id>` afterwards.

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
| `soundcloud` | SoundCloud will hand you the file itself, or there is nowhere else to go |
| `bandcamp` `beatport` `traxsource` `junodownload` `apple` | buy it there |
| `shop` | another record shop: Boomkat, Hard Wax, Clone, Decks, Deejay, Red Eye, Juno, Phonica, Rush Hour, Bleep, Gumroad |
| `gate` | free, once you follow or like: Hypeddit, Gaterush, Droploud, Wump, The Artist Union, Toneden, Pump Your Sound |
| `smartlink` | a click-through page: lnk.to, ffm.to, fanlink, smarturl, orcd.co, DistroKid, Linktree, or a label's own `.link` domain |
| `streaming` | Spotify, YouTube, Deezer, Tidal - nothing to buy |
| `others` | an unrecognised link |

That grouping is not guesswork: it comes from surveying `purchase_url` across 53
playlists and 3497 tracks. Smart links were by far the biggest thing the old
four-store version threw into `others`, followed by follow-to-download gates.
Every gate is the same chore from where you sit, so they share one category
rather than splitting the count between near-synonyms.

A link only earns a category by matching a domain on a boundary, so
`evil-bandcamp.com` does not pass as Bandcamp. `purchase_url` is not always a
shop either - artists hang interviews and press articles off it - and those stay
in `others`. Tracks with no link anywhere still get a row under `soundcloud`, so
nothing silently disappears and the track page is still one keypress away.

A `↓soundcloud` badge means the artist ticked the download box and has not yet
run out: `downloadable` on its own keeps saying yes long after the free
allowance is gone, so both that and `has_downloads_left` have to agree before
the badge appears. The file itself lives behind the download button on the track
page - the API endpoint for it needs a logged-in token, which this tool does not
ask you for.

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
