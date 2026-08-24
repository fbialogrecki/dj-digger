# 🎧 dj-soundcloud-digger

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Built with Textual](https://img.shields.io/badge/TUI-Textual-ff69b4.svg)](https://textual.textualize.io/)
[![Version](https://img.shields.io/badge/version-0.9.1-orange.svg)](pyproject.toml)

> **The ultimate crate-digging companion for DJs and electronic music collectors.**
> Instantly extract purchase links, free downloads, and download gates from SoundCloud playlists, user likes, or artist profiles—then preview tracks and manage your library in a high-performance terminal interface.

```bash
dj-digger https://soundcloud.com/someone/sets/that-playlist
```

---

## ⚡ Highlights & Features

- **🚀 Ultra-Fast API v2 Digging**: Reads a 300-track playlist off the API in ~3 seconds using batch hydration calls—no browser scrolling or DOM scraping required. That figure is the SoundCloud half; a crate whose purchase links need following (see *Link-Hub Expansion* below) then spends as long as those third-party servers take, which on a 484-track playlist is around a minute.
- **🏷️ Smart Store & Gate Classification**: Group links automatically into **Bandcamp**, **Beatport**, **Traxsource**, **JunoDownload**, **Record Shops**, **Download Gates** (Hypeddit, Toneden, etc.), **Smart links**, and **Direct SoundCloud Downloads**.
- **🎶 In-Memory Audio Preview**: Zero-latency streaming and seeking powered by `miniaudio`. Pre-fetches upcoming tracks and renders dynamic 16-level block waveforms with reactive audio level meters.
- **📦 Multi-Crate Local Library**: Save dig sessions as local crates in `~/.local/share/dj-digger/digger.db`. Switch, refresh, or search across crates seamlessly.
- **🧠 Cross-Crate Track Memory**: Track decisions (`got it` / `skipped`) are stored globally by SoundCloud track ID. Buying a track once marks it across all future playlists.
- **🔓 Download Gate Automation**: Resolves follow-to-download gates (Hypeddit, ToneDen, GateRush, Droploud) by replaying their step-completion calls. Gate resolution itself needs no browser automation or Playwright. **What this sends on your behalf:** your name and email from Settings, and—unless you turn it off—a repost, a follow and a comment recorded against your SoundCloud account, because that is what the gate is asking for in exchange for the file. The switch is on the Settings screen (`S`), which also opens on the first run. Turning it off keeps your account out of it; some gates then hand over nothing. Note that automating a gate is your call to make against SoundCloud's and the gate operator's terms, and the requests go out with a browser's User-Agent.
- **🔐 Spotify Gate Login**: Hypeddit gates that explicitly require following a Spotify artist can use an optional one-time PKCE login. No Spotify client secret is stored, and disabling gate social actions keeps these gates manual.
- **🔗 Link-Hub Expansion**: A purchase link that turns out to be a list of shops rather than a download—an ampsuite release page, a gate running in smart-link mode—is opened, and the Bandcamp and Beatport links behind it are added to the track directly instead of a `gate` badge.
- **🛒 Verified Store Carts**: An optional, user-triggered Chromium flow finds the exact linked track on Bandcamp or Beatport, shows a price preflight, and verifies the stable product ID in the cart. Login and checkout stay manual.
- **🆕 New Since Last Refresh**: Refreshing a crate marks whatever the playlist gained with `NEW` and sorts it to the top.
- **📄 Saved-HTML Fallback**: Fully supports saved HTML pages (`Ctrl+S`) for private or unlisted SoundCloud playlists.
- **⚙️ CLI & Non-Interactive Mode**: Export crates directly to JSON or CSV for automated pipelines and scripts.

---

## 📦 Installation

### Recommended (via `uv` or `pipx`)

```bash
# Install with audio preview and store-cart support
uv tool install --with-executables-from playwright 'dj-soundcloud-digger[play,shop]'
playwright install chromium
```

or with `pipx`:

```bash
pipx install --include-deps 'dj-soundcloud-digger[play,shop]'
playwright install chromium
```

### From Source (Development)

```bash
git clone https://github.com/fbialogrecki/dj-soundcloud-digger.git
cd dj-soundcloud-digger
uv venv
uv pip install -e '.[play,shop,dev]'
uv run playwright install chromium
```

> **Requires Python 3.12 or newer.**
>
> **Note on optional extras**:
> - `play`: Enables in-memory audio preview via `miniaudio`.
> - `shop`: Enables the Bandcamp/Beatport cart flow via a visible Chromium window.
> If an extra is absent, the rest of the tool still works and explains how to enable the requested feature.

---

## 🔗 Supported Input Links

| Input Type | Supported URL Pattern | What You Get |
| --- | --- | --- |
| **Playlist / Set** | `soundcloud.com/user/sets/playlist-name` | Every track in the playlist |
| **User Likes** | `soundcloud.com/user/likes` | Every track liked by the user |
| **Artist Profile** | `soundcloud.com/user` | All tracks uploaded by the artist |
| **Single Track** | `soundcloud.com/user/track-name` | Single track metadata & purchase links |
| **Saved HTML** | `playlist.html` | Private / unlisted playlist saved locally |
| **Interactive Prompt**| *Run without arguments* | Prompts for a link or opens saved crates |

---

## 🖥️ Interactive TUI Workflow

Launching `dj-digger` opens an interactive Textual browser divided into three key areas:
1. **Crate Sidebar (`Ctrl+B`)**: Switch between saved crates, add new links (`d`), or refresh existing crates (`r`).
2. **Track Table**: Displays tracks with status badges (`·` untouched, `○` opened, `✓` got, `✗` skipped), position, artist/title, available store badges, genre, and duration.
3. **Player & Waveform Bar**: Shows playback state, position, volume, real-time VU level meter, and a 16-level waveform display.

```
▸ 0 all  1 soundcloud·18  2 bandcamp·12  3 gate·53      83/83 tracks · got 0 · skipped 4
```

### Keybinding Reference

Press `?` inside the TUI at any time to view the full grouped keybinding modal.

#### Track Navigation & Status Marks
| Key | Action |
| --- | --- |
| `Up` / `Down` / `j` / `k` | Navigate track rows |
| `o` or `Enter` | Open the best link (or active store filter) in your default web browser |
| `w` | Download artist-provided SoundCloud MP3/WAV to your download folder (set it with `S`) |
| `g` | Mark track as **Got** (`✓`) and move to next track |
| `s` | Mark track as **Skipped** (`✗`) and move to next track |
| `u` | Clear track status mark (`·`) |
| `x` | Remove track from current crate (`Ctrl+Z` to undo) |
| `y` | Copy the path of the local file that matches this track (`📁` in the row) |

#### Audio Preview Controls
| Key | Action |
| --- | --- |
| `Space` | Play / Pause highlighted track |
| `[` / `]` | Seek backward / forward 10 seconds |
| `n` / `p` | Advance to Next / Previous track |
| `-` / `=` | Decrease / Increase playback volume (`m` to mute/unmute) |
| *Mouse Click* | Click anywhere on the waveform display to seek immediately |

#### Filtering, Stores & Library
| Key | Action |
| --- | --- |
| `/` | Live search/filter by artist, title, or genre |
| `f` / `F` | Step forward / backward through store filters |
| `1` – `9` | Jump directly to store category filter |
| `0` | Reset store filter (show all tracks) |
| `h` | Toggle hiding handled tracks (`got` / `skipped`) |
| `a` | Open all visible store links in browser (asks confirmation for >20 links) |
| `c` | Verify and add the highlighted exact track to Bandcamp or Beatport |
| `C` | Preflight visible tracks, then add the confirmed batch sequentially |
| `e` | Export visible rows to file |
| `d` | Add a new crate from a SoundCloud URL |
| `r` | Refresh current crate from SoundCloud (preserves local deletions) |
| `Ctrl+B` | Toggle Crate Sidebar |

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

Pressing `c` or `C` starts a visible Chromium window only when you ask for it. The
app uses a dedicated persistent browser profile, separate from your everyday
browser, so you may need to log in manually the first time. It never reads or
fills your password, chooses a payment method, or completes checkout.

- `c` resolves and adds the highlighted track.
- `C` resolves all currently visible, unhandled tracks, then asks for one batch
  confirmation. The active Bandcamp or Beatport filter limits the target store.
- With no store filter, Bandcamp is tried first. Beatport is used only when the
  exact track is genuinely unavailable for individual purchase on Bandcamp—not
  when Bandcamp fails technically.

Batch mode resolves every candidate first and shows exact products, prices,
currencies, existing cart items, and skips before any cart is changed. Store
pages are rechecked immediately before each click. Ambiguous titles, version
mismatches, changed prices or product IDs, CAPTCHA, and changed store UI stop the
affected operation instead of guessing. The browser remains open at the used
carts for manual format selection and checkout.

Only canonical Bandcamp and Beatport HTTPS domains are automated. Custom artist
domains and global store search are intentionally outside this first version.
The feature depends on the stores' current visible interfaces; use it in line
with their terms. Linux needs a graphical session; WSL users need WSLg or another
working display.

---

## 🔐 Authentication for Artist Downloads

Some artist-provided SoundCloud downloads require a logged-in account even when
the track is public. Run `dj-digger auth login` to detect a Firefox session, or
provide the SoundCloud OAuth cookie explicitly:

```bash
dj-digger auth login --token YOUR_SOUNDCLOUD_OAUTH_TOKEN
dj-digger auth status
```

### Spotify-backed download gates

Some Hypeddit gates require following an artist on Spotify. Create an app in the
[Spotify Developer Dashboard](https://developer.spotify.com/dashboard), add
`http://127.0.0.1/callback` as its redirect URI, and log in once:

```bash
dj-digger auth spotify login --client-id YOUR_CLIENT_ID
dj-digger auth spotify status
```

The login uses Authorization Code with PKCE and requests only
`user-follow-modify`. It listens temporarily on a dynamically selected
`127.0.0.1` port; Spotify permits dynamic ports for literal loopback addresses,
but not for `localhost`. Credentials stay in an owner-only local file. See
Spotify's [redirect URI rules](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri)
and [PKCE guide](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow).

Disable **gate social actions** in Settings to prevent the program from changing
Spotify or SoundCloud. Gates requiring those actions will then remain manual.
Spotify development apps currently require the owner to have Premium and allow
up to five authenticated, allowlisted users; each dj-digger user should normally
supply their own Client ID. See Spotify's current
[quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes).

Remove the saved login with:

```bash
dj-digger auth spotify logout
```

---

## 🎧 In-Memory Streaming & Waveform Engine

- **Zero-Latency Playback**: Decodes audio chunks directly off the network socket in memory via `miniaudio`.
- **Pre-Fetching & Gapless Transitions**: While listening to a track, the next track's stream URL, waveform data, and initial MBs are buffered 20 seconds prior to track completion.
- **SoundCloud Waveform Rendering**: Renders SoundCloud's 1800-sample waveform data into a 2-row block ASCII visualizer with logarithmic loudness scaling.
- **Reactive Audio VU Metering**: Real-time RMS audio signal measurement rendered at 30 FPS.
- **Remote SSH / Headless Systems**: Automatically degrades to 4 FPS under `TEXTUAL_ANIMATIONS=none` for smooth operation over SSH connections.

---

## 🤖 Non-Interactive CLI & Automation

Add `--no-tui` to run `dj-digger` in batch mode for terminal pipelines, cron jobs, or export scripts:

```bash
# Export playlist links to CSV
dj-digger https://soundcloud.com/user/sets/playlist --no-tui -f csv -o playlist.csv

# Limit extraction to first 20 tracks and export to JSON
dj-digger https://soundcloud.com/user/likes -n 20 -f json -o likes.json

# Re-open a previously exported JSON crate in the TUI browser
dj-digger open likes.json

# Open all Bandcamp links directly in browser from a saved crate
dj-digger open likes.json --category bandcamp
```

---

## 🏗️ Project Architecture

```
                       ┌─────────────────────────┐
                       │  SoundCloud URL / HTML  │
                       └────────────┬────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ SoundCloudClient v2 │
                         │ (Resolve & Hydrate) │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ Link Categorizer    │
                         │ (Store & Gate Engine│
                         └──────────┬──────────┘
                                    │
           ┌────────────────────────┼────────────────────────┐
           │                        │                        │
┌──────────▼──────────┐  ┌──────────▼──────────┐  ┌──────────▼──────────┐
│   Textual TUI App   │  │ Local Crate Library│  │ In-Memory Player   │
│ (Interactive Grid)  │  │ (~/.../crates/)    │  │ (Miniaudio & VU)   │
└─────────────────────┘  └────────────────────┘  └────────────────────┘
```

---

## 🧪 Testing & Quality Assurance

The codebase includes an extensive offline test suite covering unit tests, API serialization, player buffering, link parsing, cart safety, and TUI reactive widgets.

```bash
# Run offline test suite (550+ tests, no network required)
uv run pytest

# Run live integration tests (verifies SoundCloud API v2 contract stability)
uv run pytest -m live

# Open public store pages read-only; never logs in or changes a cart
uv run --extra shop pytest -m shop_live
```

---

## 📄 License

Distributed under the **Apache License 2.0**. See [`LICENSE`](LICENSE) for details.
