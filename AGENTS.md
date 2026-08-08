# AGENTS.md

Welcome to **dj-soundcloud-digger**! This document provides an architectural blueprint, design guidelines, and developer reference for AI agents (and human contributors) working on this codebase.

---

## 🎧 Repo Overview & Purpose

`dj-soundcloud-digger` is a fast, terminal-native crate digging tool and TUI application designed for DJs and music collectors. It extracts purchase, free-download, and download-gate links directly from SoundCloud playlists, user likes, artist profiles, or track links—without requiring browser scrolling, SoundCloud accounts, or API keys.

### Key Capabilities
- **Batch Link Extraction**: Uses SoundCloud's internal API v2 (`resolve` + batch hydration) to load hundreds of tracks in seconds.
- **Store & Gate Categorization**: Classifies links into distinct categories (`bandcamp`, `beatport`, `traxsource`, `junodownload`, `shop`, `gate`, `smartlink`, `streaming`, `no-link`, `soundcloud`).
- **Interactive TUI**: Rich Textual terminal interface with full keyboard navigation, filtering, multi-crate library support, and instant audio preview.
- **In-Memory Audio Preview**: Zero-latency streaming and seeking using `miniaudio`, rendering custom block waveforms and reactive audio level meters.
- **Track State Synchronization**: Tracks marked as `got` or `skipped` are persisted across playlists using unique SoundCloud track IDs.
- **Download Gate Resolution**: Resolves and automates download gates (Hypeddit, Toneden, etc.) using Playwright and browser authentication.

---

## 🏗️ Architecture & Module Map

The codebase is organized under `dj_digger/`:

| Module | Core Responsibility | Key Symbols / Classes |
| --- | --- | --- |
| [`dj_digger/cli.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/cli.py) | CLI argument parsing, export commands, and non-interactive execution | `main()`, `run()`, `export_crate()` |
| [`dj_digger/tui.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/tui.py) | Textual application UI, DataTable grid, crate sidebar, search/filter | `DiggerApp`, `CrateSidebar`, `ErrorBanner` |
| [`dj_digger/soundcloud.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/soundcloud.py) | SoundCloud v2 API integration, dynamic `client_id` discovery & hydration | `SoundCloudClient`, `hydrate_ids()` |
| [`dj_digger/links.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/links.py) | Domain classification rules for music stores, gates, and smart links | `categorise_link()`, `LinkRecord` |
| [`dj_digger/player.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/player.py) | Streamable audio player, in-memory chunk decoder, waveform renderer, glow meter | `Player`, `paint_waveform()`, `LevelMeter` |
| [`dj_digger/library.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/library.py) | Local crate library manager (`~/.local/share/dj-digger/crates/`) | `CrateRecord`, `list_crates()`, `load()` |
| [`dj_digger/state.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/state.py) | Global track state store (`got`, `skipped`, `opened`) with thread locks | `TrackState`, `default_state_path()` |
| [`dj_digger/models.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/models.py) | Core data dataclasses (`Track`, `Crate`, `StoreCategory`) | `Track`, `Crate` |
| [`dj_digger/gates.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/gates.py) | Free-download gate unlocker automation using Playwright/httpx | `GateUnlocker`, `GateType` |
| [`dj_digger/auth.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/auth.py) | Browser cookie extractor & token verification for gated downloads | `get_stored_token()`, `auto_detect_and_verify()` |
| [`dj_digger/html_fallback.py`](file:///home/peregrin/dj-soundcloud-digger/dj_digger/html_fallback.py) | HTML fallback parser for offline/saved page HTML (`__sc_hydration`) | `extract_from_hydration()`, `load_playlist()` |

---

## 🛠️ Development Setup & Commands

### Virtual Environment & Dependencies
This project uses [`uv`](https://github.com/astral-sh/uv) for fast dependency management.

```bash
# Create venv and install package in editable mode with all extras
uv venv
uv pip install -e '.[play,yaml,dev]'
```

### Running the Application
```bash
# Launch interactive TUI with a playlist or track URL
uv run dj-digger https://soundcloud.com/artist/sets/playlist

# Launch TUI without initial URL (opens empty library)
uv run dj-digger

# Non-interactive CLI export to CSV/JSON/YAML
uv run dj-digger https://soundcloud.com/artist/sets/playlist --no-tui -f csv -o export.csv
```

### Test Suite Execution
Always verify changes using `pytest`:

```bash
# Offline test suite (360+ fast unit tests, no network or real audio device required)
uv run pytest

# Run live API test suite (hits live SoundCloud endpoints to verify API contract stability)
uv run pytest -m live
```

---

## 📐 Agent Development Guidelines (Ponytail Principles)

When modifying this repository, follow these core tenets:

1. **Keep it Minimal (Ponytail Philosophy)**:
   - Always prefer the standard library over adding new third-party dependencies.
   - Deletion over addition: favor minimal, readable, high-impact diffs over complex abstractions.
   - If an intentional trade-off or shortcut is made, document it with a `# ponytail: <reason>` comment.

2. **Root Cause Fixes**:
   - Fix bugs at the source rather than patching symptoms at call sites.
   - When modifying a function signature or core data structure, update all callers across the repository.

3. **Offline Test Safety**:
   - Offline tests in `tests/` must NEVER hit live network endpoints or touch user runtime data (`~/.local/share/dj-digger`).
   - Mock network responses or use recorded fixture payloads (`tests/fixtures/`).

4. **Async & Non-Blocking TUI Responsiveness**:
   - Long-running network or disk operations in `tui.py` must run in background threads/workers (e.g. `work()` decorators or background tasks) to prevent UI freezes.
