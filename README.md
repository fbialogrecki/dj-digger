# 🎧 dj-soundcloud-digger

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Built with Textual](https://img.shields.io/badge/TUI-Textual-ff69b4.svg)](https://textual.textualize.io/)
[![Version](https://img.shields.io/badge/version-0.5.1-orange.svg)](pyproject.toml)

> **The ultimate crate-digging companion for DJs and electronic music collectors.**
> Instantly extract purchase links, free downloads, and download gates from SoundCloud playlists, user likes, or artist profiles—then preview tracks and manage your library in a high-performance terminal interface.

```bash
dj-digger https://soundcloud.com/someone/sets/that-playlist
```

---

## ⚡ Highlights & Features

- **🚀 Ultra-Fast API v2 Digging**: Digs a 300-track playlist in ~3 seconds using batch hydration API calls—no browser scrolling or DOM scraping required.
- **🏷️ Smart Store & Gate Classification**: Group links automatically into **Bandcamp**, **Beatport**, **Traxsource**, **JunoDownload**, **Record Shops**, **Download Gates** (Hypeddit, Toneden, etc.), **Smart links**, and **Direct SoundCloud Downloads**.
- **🎶 In-Memory Audio Preview**: Zero-latency streaming and seeking powered by `miniaudio`. Pre-fetches upcoming tracks and renders dynamic 16-level block waveforms with reactive audio level meters.
- **📦 Multi-Crate Local Library**: Save dig sessions as local crates in `~/.local/share/dj-digger/crates/`. Switch, refresh, or search across crates seamlessly.
- **🧠 Cross-Crate Track Memory**: Track decisions (`got it` / `skipped`) are stored globally by SoundCloud track ID. Buying a track once marks it across all future playlists.
- **🔓 Download Gate Automation**: Integrates Playwright and cookie authentication to unlock follow-to-download gates automatically.
- **📄 Saved-HTML Fallback**: Fully supports saved HTML pages (`Ctrl+S`) for private or unlisted SoundCloud playlists.
- **⚙️ CLI & Non-Interactive Mode**: Export crates directly to JSON, CSV, or YAML for automated pipelines and scripts.

---

## 📦 Installation

### Recommended (via `uv` or `pipx`)

```bash
# Install with audio preview support (requires miniaudio)
uv tool install 'dj-soundcloud-digger[play]'
```

or with `pipx`:

```bash
pipx install 'dj-soundcloud-digger[play]'
```

### From Source (Development)

```bash
git clone https://github.com/fbialogrecki/dj-soundcloud-digger.git
cd dj-soundcloud-digger
uv venv
uv pip install -e '.[play,dev]'
```

> **Requires Python 3.12 or newer.**
>
> **Note on optional extras**:
> - `play`: Enables in-memory audio preview via `miniaudio`.
> If installed without `[play]`, the tool runs normally and displays an advisory if audio playback is requested.

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
| `w` | Download artist-provided SoundCloud MP3/WAV directly to `~/Downloads` |
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

The codebase includes an extensive offline test suite covering unit tests, API serialization, player buffering, link parsing, and TUI reactive widgets.

```bash
# Run offline test suite (360+ tests, no network required)
uv run pytest

# Run live integration tests (verifies SoundCloud API v2 contract stability)
uv run pytest -m live
```

---

## 📄 License

Distributed under the **Apache License 2.0**. See [`LICENSE`](LICENSE) for details.
