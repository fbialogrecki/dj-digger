# Changelog

## 0.9.1

### Fixed

- Download progress, completion and failure now repaint only the affected track
  rows, so the list can be freely scrolled during single and batch downloads.
- Necessary table rebuilds preserve the selected track and the top visible
  track, including back-to-back completions when handled tracks are hidden.
- Throttled batch progress repaints every track waiting for an update instead of
  letting a busy download starve the other progress indicators.

## 0.9.0

### Security

- **Store links are verified before cart automation.** The optional Bandcamp and
  Beatport flow accepts only canonical HTTPS hosts, rejects credentials and
  custom ports, rechecks redirects, and stops on ambiguous products, changed
  prices or changed product IDs. Login and checkout remain manual.
- **A link could run a command under WSL.** Handing a URL to the Windows browser
  went through `powershell.exe -Command Start-Process <url>`, and everything
  after `-Command` is parsed by PowerShell as code rather than taken as an
  argument — `shell=False` does not help when the interpreter *is* PowerShell.
  A `purchase_url` is set by whoever uploaded the track, and one containing `;`
  or `$(...)` is a perfectly valid URL. The address now travels in an
  environment variable, which PowerShell reads and never re-parses.
- **A dig no longer reaches into your own network.** Link hubs and gates are
  fetched from addresses that come out of a track's purchase link, with no check
  on where they point, so one aimed at `127.0.0.1`, at a box on your LAN or at a
  cloud metadata service made your machine issue those requests. Loopback,
  link-local, private and reserved addresses are refused before anything is
  sent. Opening such a link by hand still works — that is your decision to make.
- **A download can no longer fill the disk.** The write loop ran until the server
  stopped sending; `Content-Length` was read but only ever fed the progress bar.
  There is a 2 GB ceiling now, applied to the declared length as well.
- **A lookalike host no longer receives our client_id.** The check was
  `"soundcloud.com" in host`, which is also true of
  `evil-soundcloud.com.attacker.example`.
- Scheme checks that used `startswith("http")` — which accepts `httpfoo://` —
  now parse the URL.

### Breaking

- **Crates and track statuses live in SQLite only.** They used to be written to
  `crates/<slug>.json` *and* the database, with the file treated as the real copy
  and the table as a fallback that had room for five of a record's fields — so a
  crate that fell back to it silently lost its import date, its `partial` flag
  and its `NEW` marks. There is one copy now. Existing `state.json` and
  `crates/*.json` are imported once, on first start after the upgrade, and then
  left on disk untouched; nothing reads or writes them again. Downgrading to 0.8
  after that point loses anything changed in between.

### Added

- **Exact-track Bandcamp and Beatport carts.** `c` preflights one track and `C`
  shows a batch confirmation with prices and per-item outcomes. A dedicated
  persistent Chromium profile keeps the cart session without exposing or
  reusing the user's normal browser profile. Install the optional `shop` extra.
- **A switch for what gates do with your account.** Every version up to 0.8 sent
  `is_repost` and `is_subscribe` to Hypeddit and a comment to GateRush, hard-coded
  and visible in no interface. It is a checkbox on the Settings screen now — the
  same screen a first run opens on — and it is on by default, so nothing changes
  unless you turn it off.
- **A download folder setting.** It was `~/Downloads`, written into the download
  code in two places.
- **Tests run on every push and pull request**, across Ubuntu, macOS and Windows.
  They only ran on a release or a tag before, which is the point at which it is
  too late for them to tell you anything.
- **A weekly job hits the real api-v2**, so a change on SoundCloud's side shows
  up as a red build rather than as a bug report.
- Ruff is a declared dev dependency, configured, and enforced in CI.
- `py.typed`, so the annotations reach anyone importing `dj_digger`.

### Fixed

- **Deleting a crate did nothing** unless it happened to still have a JSON file:
  the whole operation sat inside `if the file exists`, so a crate whose row
  outlived its file could not be removed at all. The confirmation appeared, the
  crate stayed, and it came back on every reload.
- **Link hubs that mention "download" anywhere are no longer mistaken for gates.**
  The check matched the word across the entire page, so a shop with it in a
  footer, a FAQ or an analytics script was left as a `gate` and never expanded.
  It now reads the text of the thing you would press, in eight languages — a
  German or Spanish gate used to be invisible to it. On a 484-track playlist this
  turned up 46 shop links that were previously never followed.
- **A batch download no longer shares one session between its four threads.** Gate
  flows are multi-step and held together by their own cookies, so four of them in
  one cookie jar overwrote each other's state. `dig` already got this right; the
  download path never had the fix.
- **A dead host costs seconds, not minutes.** Third-party pages were fetched with
  the retry budget meant for api-v2 — five connect retries against a 20 second
  timeout — and a playlist names the same dead smart-link domain over and over. A
  host that stops answering is now skipped for the rest of the dig. The dig this
  was measured on went from minutes to about a minute.
- **The log is ours again.** `logging.basicConfig` configured the root logger, so
  urllib3's retry warnings came out with our own output: dozens of
  `Retrying (Retry(total=1...))` lines before a single result. `--log-level DEBUG`
  still shows everything.
- SQLite connections are no longer leaked. A fresh `Database` was built on every
  call — three times inside `list_crates` alone — each opening its own connection,
  re-running every `CREATE TABLE`, and closing nothing.
- A gate that answers with its own web page is no longer saved as an `.mp3` that
  no player can open.
- A track called `Aux` or `Con` no longer fails to save on Windows.
- Scanning browser cookies no longer pretends to support Chromium. The query read
  a column that is always empty there, because the value is encrypted behind the
  system keyring — `dj-digger auth` says so now instead of reporting nothing found.

### Interface

- The help screen no longer wraps its own descriptions back to column 0, leaving
  words hanging underneath as if they were key names.
- The sidebar folds itself away below 110 columns, where it was costing the track
  title, the genre and the time column.
- The footer drops its least important bindings rather than cutting the last one
  mid-word. `q Quit` is visible for the first time.
- Settings scrolls, and its Save button is reachable on an 80×24 terminal. It was
  off the bottom of the screen — on the one screen a first run opens on.
- Store badges are elided rather than clipped, so `gate(hypeddit)` no longer
  arrives as `gate(hypedd`.
- The store counts in the status bar follow the search instead of staying at the
  crate's totals.
- The search box is one line rather than three, and says how to leave it.
- The terminal title says `dj-digger`, not `DiggerApp`.

## 0.8.0

### Breaking

- **`--browser` is gone.** Deprecated in 0.6.0, carried through 0.7.0 with a
  warning, removed here. Passing it is now an argument error rather than a
  warning, so a script still using it fails loudly instead of quietly opening
  the wrong browser. The browser is a setting: press `S` in the crate browser
  and pick from what this machine actually has. `--no-tui --category` reads the
  same setting, so batch opening and the interactive path no longer disagree
  about which browser you meant.

## 0.7.0

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
