# 🎧 dj-digger

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](https://github.com/fbialogrecki/dj-digger/blob/main/LICENSE)
[![PyPI](https://img.shields.io/pypi/v/dj-sc-digger.svg)](https://pypi.org/project/dj-sc-digger/)

> **A local-first playlist workflow for DJs.**
> Turn SoundCloud playlists, likes, and artist profiles into an auditionable,
> searchable library, then find the safest available path to download or buy
> each track.

`dj-digger` keeps the whole digging loop in one desktop application (with a headless CLI for scripts):
collect a playlist, preview and filter it, mark decisions, match music already on
disk, and follow verified store or download links. Your library and preferences
stay on your machine, and checkout always stays in your hands.

The current implemented behavior, architecture, interfaces, data model, and
security/privacy boundaries are maintained in
[PROJECT-SPECIFICATION.md](https://github.com/fbialogrecki/dj-digger/blob/main/PROJECT-SPECIFICATION.md).

```bash
dj-digger https://soundcloud.com/someone/sets/that-playlist   # headless: export + save to the library
dj-digger-gui                                                    # interactive desktop
```

---

## 🖥️ Install the desktop app

**No Python or terminal commands needed.** Use our step-by-step guides:

| Your computer | Installation guide |
| --- | --- |
| Windows 10 (1809 or newer) or 11 — Intel or AMD, 64-bit | [Install on Windows](docs/installation.md#windows) |
| macOS 15 or newer — Apple Silicon or Intel | [Install on Mac](docs/installation.md#mac) |

> **Preview downloads:** dj-digger is a free, open-source hobby project. These
> desktop previews do not have a verified publisher signature or Apple
> notarization, so your computer may warn that the app is unrecognized or might
> be unsafe. The guides explain why this happens and how to approve the specific
> app when that option is available. Keep your system security protections on.

[Find the right download](docs/installation.md#find-the-right-download) ·
[Official downloads](https://github.com/fbialogrecki/dj-digger/releases)

## Qt Quick desktop (preview)

The optional desktop runs locally using Qt Quick, without a WebView. From this
checkout:

```bash
uv run --extra gui --extra analyze dj-digger-gui
```

Install FFmpeg/ffprobe separately when running from source. Existing CLI
commands, library and account files remain available. Download gates, store
carts and the SoundCloud login drive your own browser through the dj-digger
extension; see [Connect your browser](#connect-your-browser).

Desktop keyboard shortcuts: `a` adds a playlist, `o`/`Enter` opens links,
`d` downloads, `g`/`k`/`u` mark got/skipped/untouched, `/` searches, `h` hides
handled tracks, `[`/`]` seek, `n`/`p` step tracks, `m` mutes, `Ctrl+B` toggles
the sidebar and `?` shows the full generated list. Commands that need a selection
stay disabled without one; "all visible" variants are separate menu entries.

The `Desktop tests and test installers` workflow builds an offline Windows 10/11
x64 test installer and macOS 15+ DMGs for Apple Silicon (`arm64`) and Intel
(`x86_64`). For installation and first-launch warnings, see the
[desktop installation guide](docs/installation.md). See the
[desktop implementation and acceptance record](docs/implementation/qt-quick-desktop.md)
for commands, coverage and remaining platform checks.

## ⚡ The workflow

- **Collect complete playlists**: SoundCloud API v2 reads playlists, likes, profiles,
  and individual tracks without relying on the page's finite rendered list; sign
  in to SoundCloud to read your own private playlists.
- **Audition and narrow down**: Preview, seek, search, filter, and sort tracks in
  the desktop app without leaving the playlist.
- **Remember every decision**: Local playlists keep `got it` / `skipped` status across
  playlists, refreshes identify new additions, and a library scan finds tracks
  already on disk.
- **Find the track, not just a link**: Store, smart-link, and download-gate URLs
  are classified and expanded into useful Bandcamp, Beatport, shop, or download
  destinations.
- **Acquire with guardrails**: Download artist-provided files, resolve supported
  gates, verify Bandcamp products, or prepare a Beatport playlist. Ambiguous
  matches and checkout remain manual.
- **Keep control locally**: Playlists, decisions, credentials, and browser profiles
  remain on your machine; there is no project-hosted account or backend.

## Additional capabilities

- **API-first collection**: A 300-track playlist can be read from SoundCloud in
  about three seconds using batch hydration. Following third-party purchase links
  takes as long as those providers take; a 484-track playlist is around a minute.
- **Store and gate classification**: Group links into **Bandcamp**, **Beatport**,
  **Traxsource**, **JunoDownload**, **record shops**, **download gates**, **smart
  links**, and **direct SoundCloud downloads**.
- **In-memory audio preview**: Stream, seek, prefetch upcoming tracks, and show
  waveforms in the desktop, powered by `miniaudio`.
  SoundCloud preview supports progressive MP3 and MP3 HLS (up to 50 MiB in memory).
- **Multi-playlist local library**: Save, switch, refresh, and search playlists stored in
  `~/.local/share/dj-digger/digger.db`.
- **Cross-playlist track memory**: A decision made for a SoundCloud track applies
  when that track appears in another playlist.
- **🔓 Download Gate Automation**: Resolves follow-to-download gates (Hypeddit, ToneDen, GateRush, Droploud) through their supported download flows. SoundCloud, Instagram, YouTube and similar link steps are click-through markers: the app does not call their follow, like, repost, comment APIs or open external social links to simulate a click. A gate may receive the name or email configured in Settings when its manifest requires them. Spotify steps are reported like the other click-throughs: Hypeddit clears them through its own Spotify app, so no Spotify login is needed or used. CAPTCHA and unknown provider steps remain manual in your browser. Gate automation remains subject to the provider's terms.
- **🔗 Link-Hub Expansion**: A purchase link that turns out to be a list of shops rather than a download—an ampsuite release page, a gate running in smart-link mode—is opened, and the Bandcamp and Beatport links behind it are added to the track directly instead of a `gate` badge.
- **🛒 Store Purchase Assistance**: An optional, user-triggered flow verifies Bandcamp additions and prepares Beatport tracks as an importable playlist. Login, playlist transfer, and checkout stay manual.
- **🆕 New Since Last Refresh**: Refreshing a playlist marks whatever it gained with `NEW` and sorts it to the top.
- **⚙️ Headless CLI**: Export playlists directly to JSON or CSV for automated pipelines and scripts.

---

## 📦 Installation

**Version 1.4.1 is available on [PyPI](https://pypi.org/project/dj-sc-digger/1.4.1/)
and [GitHub Releases](https://github.com/fbialogrecki/dj-digger/releases/tag/v1.4.1).**
Install the package `dj-sc-digger`; launch it with `dj-digger`.

Upgrading from `dj-soundcloud-digger`? Follow the
[package-name migration instructions](#upgrade-from-the-old-package-name) first.

### Recommended (via `uv` or `pipx`)

```bash
# Install with audio preview and local BPM/key analysis
uv tool install 'dj-sc-digger[analyze]'
dj-digger
```

or with `pipx`:

```bash
pipx install 'dj-sc-digger[analyze]'
dj-digger
```

### From Source (Development)

```bash
git clone https://github.com/fbialogrecki/dj-digger.git
cd dj-digger
uv venv
uv pip install -e '.[analyze,dev]'
```

Run the working tree with playback and analysis:

```bash
uv run --extra analyze dj-digger
```

Logs are saved automatically at INFO level. The application
keeps up to five log files of 2 MiB each, redacts credentials, and never uploads
diagnostics automatically. Locations:

- Linux: `$XDG_STATE_HOME/dj-digger` or `~/.local/state/dj-digger`.
- Windows: `%LOCALAPPDATA%\dj-digger\Logs`.
- macOS: `~/Library/Logs/dj-digger`.

The browser is drawn on standard error, so `2>file` would redirect the interface
itself. For more detail or a different log destination, use:

```bash
uv run dj-digger --log-level DEBUG --log-file /tmp/dj-digger.log
```

To try it against a throwaway library instead of your real playlists, point the XDG
directories somewhere temporary for that one run:

```bash
XDG_DATA_HOME=/tmp/dj-dev XDG_CONFIG_HOME=/tmp/dj-dev XDG_CACHE_HOME=/tmp/dj-dev uv run dj-digger
```

> **Requires Python 3.12 or newer.**
>
> **Note on optional extras**:
> - Audio preview via `miniaudio` is included by default.
> - `analyze`: Enables local BPM/key estimation via `librosa`. Omit it if you only
>   need playback and link collection.
>
> Local audio inspection, playback, analysis and export also require **FFmpeg
> and ffprobe** installed separately and available on `PATH`.
>
> Download gates, store carts and the SoundCloud login need the browser extension
> (Chrome, Edge, Brave or Firefox); nothing is downloaded for them. See
> [Connect your browser](#connect-your-browser).

---

## 🌐 Connect your browser

Download gates, store carts and the SoundCloud login are driven by a small
extension in your own browser (Chrome, Edge, Brave, Chromium or Firefox). There is
no managed browser to download, and the extension talks only to dj-digger on this
computer. Connect it once. The desktop does the registration itself at every start;
its **Connect browser…** dialog (Settings menu, and opened by the first cart or
SoundCloud login that needs it) shows the folder to load with a **Copy path** button and
**Check connection**. From a terminal, with the desktop closed (a `dj-digger` command and
the desktop cannot run together):

```bash
dj-digger browser register
```

The command registers the native-messaging host with the browsers it finds and
copies the extension to the `browser-extension/` folder of the dj-digger data
directory (`~/.local/share/dj-digger`, or `$XDG_DATA_HOME/dj-digger`), printing the
exact path. Browsers installed as Flatpak or Snap cannot start the host and are
reported as unsupported. Then, in the browser:

- **Chrome, Edge, Brave, Chromium:** install
  [dj-digger from the Chrome Web Store](https://chromewebstore.google.com/detail/dj-digger/ndimmdooibgcajgnoncdnckolhlmnnkb) (Edge asks to allow extensions from
  other stores: allow it). Developer install: `chrome://extensions` → **Developer
  mode** → **Load unpacked** → that `browser-extension/` folder.
- **Firefox:** install the signed extension file when the release provides one, or for
  a trial open `about:debugging#/runtime/this-firefox`, choose **Load Temporary
  Add-on** and select `browser-extension/manifest.json` (it is removed when Firefox
  restarts).

Open the extension's welcome page to grant the site permissions it asks for. While
the desktop runs, the extension's toolbar button opens a small player panel: the
playing track on the desktop's record, with previous, play/pause, next and a seek bar,
the running job with Cancel, and "N gates need you" (also the button's badge) with
Open them / Skip them. Opened on a SoundCloud playlist or profile page it offers to
import it; right-clicking a SoundCloud playlist or profile link offers **Dig in
dj-digger**. For these two the extension asks for the `activeTab` and `contextMenus`
permissions; it reads the current tab's address only when you open the panel. To
disconnect, run `dj-digger browser unregister` or use **Disconnect browsers…** in the
desktop's Settings menu (the desktop registers again at its next start); the extension
itself is removed from the browser's extensions page.

Releases before 1.4 kept managed browser profiles in the data directory. They are no
longer used and are never deleted automatically. To reclaim the space, close
dj-digger and delete the `store-browser/` and `soundcloud-browser/` folders there
yourself.

---

## 🔗 Supported Input Links

| Input Type | Supported URL Pattern | What You Get |
| --- | --- | --- |
| **Playlist / Set** | `soundcloud.com/user/sets/playlist-name` | Every track in the playlist |
| **User Likes** | `soundcloud.com/user/likes` | Every track liked by the user |
| **Artist Profile** | `soundcloud.com/user` | All tracks uploaded by the artist |
| **Single Track** | `soundcloud.com/user/track-name` | Single track metadata & purchase links |
| **Interactive UI** | `dj-digger-gui` | Browse saved playlists and add links in the desktop app |

---

## 🖥️ Interactive Desktop Workflow

`dj-digger-gui` is the only interactive interface: a playlist/folder sidebar, a
track table with status, artist/title, store badges, genre and duration columns,
and a player with waveform and transport controls. Press `?` (Help → Keyboard
shortcuts) for the full generated shortcut list. Running `dj-digger` without a
link prints a pointer to it and exits.

---

## 🏷️ Store & Gate Categories

Links are parsed and categorized using strict domain-boundary matching:

| Category | Description / Included Domains |
| --- | --- |
| `soundcloud` | Direct artist-provided download link enabled on SoundCloud |
| `bandcamp` | Official Bandcamp release page |
| `beatport` | Beatport purchase link |
| `traxsource` | Traxsource store link |
| `junodownload` | JunoDownload purchase page |
| `apple` | Apple Music / iTunes Store link |
| `shop` | Specialized record stores (*Boomkat, Hard Wax, Clone, Decks, Deejay, Red Eye, Juno, Phonica, Rush Hour, Bleep, Gumroad*) |
| `gate` | Follow-to-download gates (*Hypeddit, Gaterush, Droploud, Wump, Artist Union, Toneden, Pump Your Sound*) |
| `smartlink` | Landing pages (*lnk.to, ffm.to, fanlink, smarturl, orcd.co, DistroKid, Linktree*) |
| `streaming` | Pure streaming platforms (*Spotify, YouTube, Deezer, Tidal*) |
| `no-link` | No purchase or download link found |
| `others` | Unrecognized external web link |

### Bandcamp and Beatport carts

The cart action does the product checks and the cart clicks in your own browser,
driven by the dj-digger extension, with the browser's Bandcamp and Beatport
sessions as they are. No window opens: the work happens in a couple of inactive
tabs of your browser window, and your current tab stays where it is. When the
batch is done, one cart tab per store becomes active and is yours from then on —
review it and check out there. If the extension is not connected, the app says so
and shows the steps in [Connect your browser](#connect-your-browser). A Bandcamp
account is not required for its cookie-backed cart. The app never reads or fills
your password, chooses a payment method, or completes checkout.

- There is no review form: a cart is not a purchase. Bandcamp "name your price"
  items go in at their minimum, or at the price the artist suggests if you choose
  that in Settings.
- Beatport tracks go into your real Beatport cart when your browser is signed in
  and the exact track is known (a direct track link, or the release page's own
  rows; never a search by artist and title).
- The result screen counts what was added and lists every ticked track that was
  not (no store link, not found, ambiguous, failed), and offers to open those
  links in your browser. Safe failures can be retried; uncertain clicks can be
  finished by hand with **Finish in browser**.

Every product page is rechecked immediately before its click. Ambiguous titles,
version mismatches, changed prices or product IDs, CAPTCHA, and changed store UI
stop the affected item instead of guessing. A track sold only as part of a full
album is reported as album-only instead of silently adding the whole album. After
a click, verification uses the store's cart counter and cart view, each on its own
clock; an uncertain click is never repeated, and the app saves a screenshot and a
redacted copy of the page under `cart-diagnostics` in its data folder (last ten
kept) so a broken flow can be reported with the page that broke it.

Beatport tracks that could not go into the cart can still be sent to Soundiiz from
the result screen: it creates a new `Beatport playlist.txt` in the playlist's
download folder, copies its contents, creates a temporary Soundiiz import with
Beatport already selected, and opens the returned review page. Soundiiz accepts up to 200 tracks per import. The saved file and clipboard remain
available as a fallback. Promo prefixes, uploader names, preview markers and
trailing label fields are removed before matching, including titles whose artist
separator has no surrounding spaces. Exact Beatport track URLs are preferred in that
file and replace stored release links so later imports can reuse them. Release links and blocked public pages
are looked up for the exact title/remix and fall back to cleaned `artist - title`, which
Soundiiz presents for review before writing the Beatport playlist. Already-exact
numeric track URLs never open a page. Old `pro.beatport.com` links are automatically
rewritten and saved under `www.beatport.com`. In Beatport DJ, that playlist can then be
added to the default cart in one action.

Only canonical Bandcamp and Beatport HTTPS domains are inspected. Custom artist
domains remain outside this version. The feature depends on the stores' and
Soundiiz's public import interface; use it in line with their terms. Showing
the completed Bandcamp cart brings your browser's work window forward; the browser
must run where dj-digger runs (under WSL, a Linux browser through WSLg).

For a timestamped diagnostic log, run:

```bash
uv run dj-digger --log-level DEBUG --log-file /tmp/dj-digger-cart.log
```

---

## 🔐 Authentication for Artist Downloads

Some artist-provided SoundCloud downloads require a logged-in account even when
the track is public. Run `dj-digger auth login`: an existing valid login or a
readable Firefox session is used first, otherwise the dj-digger browser extension
reads the `oauth_token` cookie of your own browser, opening the SoundCloud sign-in
page in its work window and waiting up to five minutes if you are not logged in.
Only the verified `oauth_token` cookie is copied to dj-digger; passwords and other
cookies are not written to its credential file. If no browser is connected, the
command offers a hidden token-paste fallback:

```bash
dj-digger auth login
dj-digger auth login --token YOUR_SOUNDCLOUD_OAUTH_TOKEN
dj-digger auth status
dj-digger auth logout
```

The desktop opens the same choice automatically when a download needs SoundCloud.
Cancelling leaves the track untouched. A successful login updates credentials for subsequent API requests and retries
the waiting track once. Existing transfers keep their resources until they finish.

The verified SoundCloud API credential lives in the owner-only
`~/.config/dj-digger/auth.json` (the standard XDG environment variables override
the base directory).
If `SOUNDCLOUD_OAUTH_TOKEN` is set, it deliberately overrides that file; an
invalid value must be unset or updated before the CLI wizard can replace a login.

When Hypeddit or GateRush requires an email address, the desktop asks for a real
name and email before it submits the gate. It explains who receives those data,
rejects placeholders and malformed addresses, and retries only the downloads
that were waiting for the profile. Cancelling sends no retry request.

### Spotify steps on download gates

Hypeddit gates that show a Spotify step are handled like the other click-through
steps: the gate clears that step through Hypeddit's own Spotify app and server
session, so nothing dj-digger could do with your Spotify account would reach it.
Releases before 1.0 asked for a Spotify developer app and stored a login in
`~/.config/dj-digger/spotify.json`; that file is no longer read, and you can
delete it yourself.

When a gate ends up in the browser (a refusal, a CAPTCHA, a
provider login), the extension walks the gate's own step slides for you out of
sight (in Chrome-family browsers no window or tab appears at all; you only see the
download status in the app): it ticks the follow and like links (closing the
provider pages they open, unread), fills a comment field with one of your gate
comments, fills the email slide with the address from Settings (and the name, when
the gate asks for one), and presses Download. A step no program can do alone - a
provider asking you to sign in, a CAPTCHA, a missing email or name - makes the app
ask you first ("Gates need you"); only if you choose to open them does a browser
window appear, where the same driver keeps walking the steps before and after the
one that needs you. A Spotify step completes in the background when your browser is
signed in to Spotify (its sign-in opens briefly as an inactive tab and closes by
itself); authorise Hypeddit there once if Spotify asks. A batch with failures ends with a results screen where you can retry them
or open their gates in your browser. Nothing outside the Hypeddit page is clicked.
The extension works in the accounts your browser is signed in to, so a Connect step
that was authorized once before acts on that account. Turn off **Allow social actions**
in Settings to keep the program from reporting or clicking any social step at all. Gates requiring those actions
will then remain manual.

ToneDen gates (`toneden.io/<artist>/post/<slug>`) go the same way: out of sight the
extension presses the gate's follow/repost (SoundCloud) or follow/save (Spotify) button;
ToneDen's sign-in opens as a muted, inactive tab that closes by itself once it is back at
ToneDen, and the file arrives in the app. If your browser is not signed in to that
provider, or the provider wants you to approve ToneDen first, the app asks ("Gates need
you"); the approval itself is always yours to click. A gate whose page no longer exists is
reported as such and never opened.

---

## 🎧 In-Memory Streaming & Waveform Engine

- **In-memory SoundCloud preview**: Decodes progressive MP3 or MP3 HLS via `miniaudio`. HLS buffering is limited to 50 MiB; audio is not saved to disk.
- **Prefetch and automatic advance**: Prepares the next visible track during the final 20 seconds and advances at the end of playback. Network and device conditions can still cause buffering.
- **Waveform**: Played and unplayed regions are drawn in the desktop player; the desktop glows with the kick drum's level, not the bass line, computed from audio decoded ahead of the speaker (animation switch in its settings). Local-file waveforms are generated separately and cached.
- **Availability errors**: SoundCloud account/region restrictions, missing streams and unsupported formats are reported separately. A public track page does not guarantee an available stream.

---

## 🤖 Headless CLI & Automation

`dj-digger` is headless: `dig` exports and saves to the library, then prints a
summary table; `open` batch-opens links from a saved summary. Use it in
terminal pipelines, cron jobs, or export scripts:

```bash
# Export playlist links to CSV
dj-digger https://soundcloud.com/user/sets/playlist -f csv -o playlist.csv

# Limit extraction to first 20 tracks and export to JSON
dj-digger https://soundcloud.com/user/likes -n 20 -f json -o likes.json

# Open all Bandcamp links from a saved summary (asks for a category if omitted)
dj-digger open likes.json --category bandcamp
```

---

## 🏗️ Project Architecture

```text
CLI / Qt desktop controllers
    └── ApplicationServices (lazy composition)
        ├── Collection, download, purchase and account services
        │   └── SoundCloud, gate and store adapters
        ├── Library service → repositories → SQLite owner thread
        └── Playback service → in-memory audio engine
```

Controllers own presentation; services own completed effects and resource
lifecycles. The operation coordinator admits work and tracks cancellation until
workers finish. See [the architecture guide](https://github.com/fbialogrecki/dj-digger/blob/main/docs/architecture.md) for ownership
and [the specification](https://github.com/fbialogrecki/dj-digger/blob/main/PROJECT-SPECIFICATION.md) for behavioral contracts.

---

## 🧪 Testing & Quality Assurance

The codebase includes an extensive offline test suite covering unit tests, API serialization, player buffering, link parsing, and cart safety.

```bash
# Run the offline test suite (no network required)
uv run --frozen --extra dev pytest

# Run live integration tests (verifies SoundCloud API v2 contract stability)
uv run pytest -m live

# Open public store pages read-only; never logs in or changes a cart
uv run pytest -m shop_live
```

---

## 📄 License

Distributed under the **Apache License 2.0**. See [`LICENSE`](https://github.com/fbialogrecki/dj-digger/blob/main/LICENSE) for details.

## Local music and club folders (1.1)

In the desktop app, open a folder by path or use the explorer below your
playlists. Files are loaded in pages of 250. Opening a folder lists
names first, then reads audio tags in the background. It does not analyze audio.
Space or Enter previews a local file. FFmpeg/ffprobe must be installed and on
PATH; playback also needs the `play` extra. WAV, AIFF/AIF, FLAC/FLA, MP3, AAC,
and M4A/MP4 audio are supported, subject to successful decoder inspection.

To convert or prepare a club folder:

1. Open a directory and optionally select tracks. With no selection in a folder, export includes all matching files across every page.
2. Choose the audio export action, the target format and maximum bit depth/sample rate, and enter the destination directory. A new folder with copies is the default.
3. Inspect files and review the plan, check each action and the actual deck compatibility, then execute the plan.

To find BPM and key, use the analyze action. With no selection this analyzes
the highlighted track. **Analyze folder** analyzes all audio files directly in
the open folder, across every page and regardless of selection or filters.
Subfolders are not included.
The BPM and Key columns are always visible in local folder and local-playlist
views, including previously saved results without rerunning analysis. Use
**Edit BPM / key** to correct them manually.
After analysis, a short notification counts detected keys, unclear keys and
processing errors. Missing-result reasons and technical
errors are recorded in the logs. Per-file details are also
stored privately as `last-analysis.jsonl` in the default log directory, replaced
by the next run. Unclear keys are estimates without a decisive match, not decoder
failures. The analysis cache version recalculates older results once when analysis
is requested to record missing-result reasons.
For a checkout, start with `uv run --extra gui --extra analyze dj-digger-gui`.
The full shortcut list is under Help → Keyboard shortcuts.

Install analysis with `pip install 'dj-sc-digger[analyze]'` (or the equivalent
pipx/uv tool command). Analysis is optional and its libraries load in a separate
process only when requested. Results are estimates; ambiguous rhythm/key and
silence can return no value. Manual values take precedence, followed by current
analysis, then tags. Analysis never writes audio tags or rekordbox data.

Audio export defaults to **WAV, maximum 24-bit / 48 kHz, a NEW folder**. The
format applies to files requiring conversion. Compatible MP3/AAC and compatible
lossless files are kept rather than unnecessarily transcoded. All selected files
are copied, including unchanged files; original directories are preserved. With
no explicit selection, an open folder exports all matching files, including pages
not currently displayed. Recursive inclusion is an explicit checkbox.

The dialog shows manufacturer-documented profile compatibility and then the
compatibility of the actual planned set, including retained MP3/AAC parameters.
For example, 32 kHz MP3 can remain untouched but excludes CDJ-3000/3000X from the
actual compatible list. Unverified parameters are never shown as compatible.
This is **audio-file compatibility**, not hardware testing, a rekordbox export,
or approval of any USB filesystem. You can choose a mounted USB directory as
the destination; the app never formats a drive.

Replacement is optional and must be selected afresh for every operation. It
keeps a temporary original until the new file is verified and SQLite is updated,
then removes that original. Recovery preserves ambiguous files. Successful
replacement leaves no permanent backup. Symbolic/hard links and playing or
prefetched files cannot be replaced. Conversion never silently upsamples,
downmixes, normalizes, or converts a lossy file to improve its supposed quality.
Exceptions and omitted metadata are listed in the operation report.

Profile playlist import is separate from the existing profile-track dig. Public
playlists do not require login. Private playlists require a valid session for the
profile owner. This relies on SoundCloud's undocumented API: an incomplete or
missing-track response preserves the old playlist and is reported. No external
download gates are resolved by a mass import.

### Upgrade from the old package name

Close every running instance first. **Uninstall `dj-soundcloud-digger` before
installing `dj-sc-digger`**: both distributions own the same module and CLI script.
Do not install them together. Choose the commands for the manager you used:

```sh
# pip, inside the same virtual environment
python -m pip uninstall dj-soundcloud-digger
python -m pip install 'dj-sc-digger[analyze]'

# pipx
pipx uninstall dj-soundcloud-digger
pipx install 'dj-sc-digger[analyze]'

# uv tools
uv tool uninstall dj-soundcloud-digger
uv tool install 'dj-sc-digger[analyze]'
```

The `dj_digger` module, `dj-digger` command, configuration and data directories
keep their names. Schema 0/1 databases with the recognized 1.0 shape get a
verified SQLite backup including committed WAL data before migration to schema
2. Unknown databases are refused without changes. Downgrading requires consciously
restoring that backup while the app is closed; there is no automatic downgrade.
See [release procedure](https://github.com/fbialogrecki/dj-digger/blob/main/docs/implementation/release-1.1.md) and
[deck rule sources](https://github.com/fbialogrecki/dj-digger/blob/main/docs/implementation/deck-sources.md).


### Analysis validation

For a repeatable raw-analysis benchmark, use a fresh output directory:

```bash
uv run --extra analyze python scripts/benchmark_analysis.py --output /tmp/digger-benchmark
```

Add `--corpus /path/to/music` to read immediate audio files without editing audio
or tags. Optional `--references references.json` maps filenames to entries such as
`{"bpm": 120, "key": "Am", "verified": true}`. Only verified references count
toward accuracy; embedded tags are not assumed to be ground truth. The report
separates incorrect estimates, missing answers and half/double tempo. Controlled
cadences test intended tonal patterns; they do not validate real-music accuracy.
