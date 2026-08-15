# Changelog

## 0.7.0

### Deprecated

- **`--browser` is still here.** 0.6.0 said it would go in 0.7; it did not, so
  nothing that used it breaks on this upgrade. It still warns, and the removal
  moves to 0.8. The browser remains a setting: press `S` and pick from what the
  machine actually has.

### Added

- **Settings open on the first launch.** A fresh install had a profile nobody
  had ever seen: gates were handed the placeholder name and address, and the
  library scan walked whatever `~/Music` happened to contain. With no config
  file on disk the crate browser now opens Settings first, and the scan waits
  for the answer. Scan folders are editable there too, which was the one
  setting the screen could not reach.
- **A refresh says what it brought in.** Tracks that were not in the crate
  before are marked `NEW` and sorted to the top; the playlist's own order is
  kept inside each half. A refresh that turns up nothing leaves the previous
  batch marked, so pressing `r` twice does not lose it.
- **Link hubs are opened and replaced by the shops behind them.** Plenty of
  purchase links hand over no file at all - ampsuite release pages, and gates
  running in smart-link mode - they are just a list of streaming services and
  shops. Those pages are now read during a dig, the Bandcamp and Beatport links
  behind them (including the ones wrapped in the hub's own redirect) are added
  to the track, and the hub link itself is dropped so the track is not badged
  as a gate that gates nothing. A page that does offer a download is left
  exactly as it was.

## 0.6.0

### Breaking

- **Python 3.12 or newer is required.** 3.9 reached end of life in October 2025
  and 3.10 does so in October 2026. The test suite now runs against 3.12, 3.13
  and 3.14 in CI, so the claim is checked rather than asserted.
- **The YAML export format is gone**, along with the `[yaml]` extra and the
  `pyyaml` dependency. Use `-f json` or `-f csv`. Reading a `.yaml` summary
  written by an earlier version reports what happened instead of failing to
  parse.
- **`--browser` is deprecated and will be removed in 0.7.** It still works this
  release and prints a warning. The browser is a setting now: press `S` in the
  crate browser and pick from what the machine actually has.

### Security

- **Links are checked before they are opened.** A `purchase_url` is whatever the
  artist typed into SoundCloud, and a summary file is whatever was on disk;
  neither was validated, so `file://`, `javascript:`, `data:` and `\\host\share`
  reached the browser layer verbatim. Only `http` and `https` with a host are
  opened now, checked in `store_for_url`, in `categorise`, in `load_summary`,
  and again at the point of opening.
- **The OAuth token is never world-readable.** `auth.json` was created with the
  umask default and narrowed with a `chmod` afterwards, leaving a window in
  which any other account could read it. It is now created at 0600 and moved
  into place atomically, in a directory tightened to 0700.
- **A browser named in the config file is no longer executed unchecked.**
  `webbrowser.get()` accepts a command line as well as a browser name, so a
  stored preference is matched against the machine's own list first.
- **The default gate identity is unroutable.** The old default email was a real
  address at a real provider, submitted automatically to third-party download
  gates - so every unconfigured install was signing a stranger up for artist
  mailing lists. It is now a reserved `.invalid` address, the old one is dropped
  on load, and the resolvers warn before submitting a placeholder.
- **CI runs the tests before publishing** and pins its third-party actions to
  commits rather than movable tags.

### Fixed

- **Clearing a status no longer comes back.** The legacy JSON import ran on
  every `Database()` construction, and the crate library builds one per call, so
  a status you had just cleared was re-read from the state.json mirror and put
  back. It now runs once per database per process.
- **`dj-digger --version` reports the real version.** It said 0.4.20 while the
  package was 0.5.1; the number is now read from the installed distribution.
- **The crate browser lets go of everything on the way out.** `DiggerApp`
  defined `on_unmount` twice and Python kept the second, so the 30fps ticker
  went on running after the app closed.
- **Discovery failure is reported.** Three invented `client_id` fallbacks were
  tried and cached when discovery failed - one of them 28 characters long and
  silently skipped - which poisoned the cache for every later run.

### Added

- **The local library scanner runs.** Shipped in 0.5.0 but wired only to a
  package that never executed, it now walks `scan_directories` in the background
  on startup, marks matched tracks with `📁`, and copies the path with `y`.
  A track is only marked as *got* automatically when artist and title both
  match and it is still unmarked - a filename never overrules a decision you
  made by hand.
- **Browser detection**, including WSL: links can be handed to the Windows
  browser through `wslview`, `explorer.exe` or PowerShell.

### Internal

- `dj_digger/ui/`, 633 lines of a second Textual app that nothing imported and
  that could never have run, is gone.
- `tui.py` was 2034 lines in one module and one 114-method class. It is now a
  package of thirteen files whose largest is 318 lines, with a test asserting
  that no two parts define the same method.
- Annotations use `X | None`, `list[...]` and `typing.Self` throughout; the 29
  `from __future__ import annotations` imports are gone.
