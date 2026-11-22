# Working with dj-digger

## Import and organize playlists

Press `A` in the desktop app to import a SoundCloud link. The command is **Import playlist from link…** in 1.7.0 and **Add playlist…** in 1.6.0. Opening a profile this way collects that artist’s uploaded tracks. Choose **Import profile playlists…** to collect the sets they created. You can import public sets without signing in. To include your private sets, sign in as their owner.

During import, tracks appear in playlist order. The playlist is saved when collection succeeds. Refresh a saved SoundCloud playlist to bring new tracks to the top with a **NEW** marker. A refresh preserves your decisions and the tracks you removed locally.

dj-digger 1.7.0 supports these playlist workflows:

- **Keep a recommendation mix.** Import Daily Drops or Weekly Wave from its SoundCloud page or paste its playlist link. Reopening the same day’s import returns that copy. Importing on a different day creates another dated entry.
- **Save a shortlist.** Select tracks in the table, right-click and choose **Send to → Create playlist from selection…**. A local playlist keeps the selected order and accepts both SoundCloud tracks and files. Drag a selection onto an editable playlist, or choose it under **Send to**, to append tracks. Existing entries are skipped.
- **Add to an imported playlist.** Drop tracks onto a saved SoundCloud import or choose it under **Send to**. The first time you add SoundCloud tracks to a set, choose whether to sync automatically, ask each time or keep additions local. Change this under **Preferences → Accounts and browser**. Sync requires a signed-in account that owns the destination.
- **Create a SoundCloud playlist.** When every selected track has a confirmed SoundCloud identity, **Create playlist from selection…** also offers to create a private or public set on your SoundCloud account.
- **Look up a track.** **Find in** searches Beatport, Bandcamp, YouTube, YouTube Music or Spotify with the selected artist and title. **Open track in SoundCloud** opens a known permalink. Both leave the track’s status as it was.

## Preview and mark decisions

Double-click a row or press `Enter` to play the selected track. When a local file is available, the player uses it. Otherwise it streams an available SoundCloud preview in memory. A region or account restriction can prevent playback even when the track page opens. Click the waveform to seek, use `[` and `]` for ten-second jumps, and press `Space` to pause or resume.

| Action | Result |
| --- | --- |
| Play a new track | The status becomes **Played**. It stays in the **Unhandled** filter. |
| Open its saved link (`O`) | A new or played track becomes **Opened**. |
| Mark owned (`G`) or skip (`K`) | The decision follows the track across playlists. Press `H` to hide handled tracks. |
| Reset status (`U`) | The track returns to **New**. **Tracks → Undo status change** undoes the last manual mark. |

Use the search field, store filters and status filter to narrow the table. A right-click or **More actions** opens commands for the selection. Bulk commands under **Tracks** show how many visible tracks they cover. **Help → Keyboard shortcuts** lists the controls for the running version.

Columns fit the filtered tracks, including rows outside the viewport. Drag a divider to keep a custom width across moves and restarts. Choose **Reset column widths** in the header menu to restore automatic sizing.

## Downloads and gates

Choose **Download** for a track with an artist-provided download or a supported gate. Completed files go into a playlist-named folder under your configured download directory and acquire **Got** status. A preview stream alone does not supply a downloadable file.

**Library → Scan local library** checks your configured scan folders for music you already have. The player can use the matched file directly. When you want to browse or analyze a folder’s contents, open it from **Folders** in the sidebar.

Hypeddit, ToneDen, GateRush and Droploud each have a supported download flow. Some gates require a name or email. The desktop tells you who will receive it before retrying. Review **Allow social actions** in Settings. When enabled, an already-authorized provider such as ToneDen can follow, repost or save through the account signed into your browser. Turn it off to handle required social steps yourself.

If the desktop reports **Gates need you**, open the waiting gates to handle a sign-in, provider approval or CAPTCHA, or skip them. The batch result lists failed items and lets you retry them. See the [browser setup guide](installation.md#connect-your-browser).

### SoundCloud sign-in

Some artist downloads require a SoundCloud login even for public tracks. Use the desktop account controls, or close the desktop and run:

```bash
dj-digger auth login
dj-digger auth status
dj-digger auth logout
```

Login checks for a valid existing credential, then a readable Firefox session, then the connected browser. With no connected browser the CLI can prompt for a token using hidden input. Verified credentials live in the system keyring when available, with an owner-only configuration file as fallback. An explicitly set `SOUNDCLOUD_OAUTH_TOKEN` takes precedence. Unset or update it if SoundCloud rejects it.

## Store links and carts

The **Stores** column distinguishes Bandcamp, Beatport, Traxsource, Juno Download, Apple, record shops, download gates and smart links. A store filter can surface several tracks sold at the same shop. Some link hubs expand into individual store destinations. Use **Open saved link** to visit a destination directly.

1. Select the tracks and choose **Add to store carts…**. The browser extension checks the product and version on Bandcamp or Beatport, then verifies each addition. A Bandcamp guest cart works. Beatport requires a signed-in browser and an exact track match.
2. Read the result for each track. Album-only tracks, ambiguous matches and changed prices are reported. Use **Finish in browser** for an uncertain addition. Checkout and payment take place in the browser under your control.
3. For remaining Beatport tracks, **Send playlist to Soundiiz** saves `Beatport playlist.txt` and prepares a transfer for your review. A Soundiiz import takes up to 200 tracks. The local text file is available even when a transfer cannot be prepared.

The JSON and CSV exports use the category names `soundcloud`, `no-link`, `bandcamp`, `beatport`, `traxsource`, `junodownload`, `apple`, `shop`, `gate`, `smartlink`, `streaming` and `others`. For example, `dj-digger open playlist.json --category bandcamp --limit 5` opens up to five saved Bandcamp links. Use `--no-open` to inspect a JSON summary.

## Analyze files and export for decks

Open a folder to browse its files. Names appear first and audio tags fill in afterward. Opening a folder does not start BPM or key analysis.

- **Analyze BPM / key** analyzes the selected local files. **Analyze folder** includes all audio files directly in that folder across every page. Subfolders are excluded.
- **Edit BPM / key…** lets you correct an estimate. Manual values take precedence over analysis and file tags. These edits stay in the library. A file with no clear key can legitimately have an empty result.
- For source or Python package installs, local playback and export need FFmpeg and ffprobe on PATH. BPM and key estimation also needs the `analyze` extra.

To prepare files for a gig:

1. Select local files and choose **Export audio…**. For a whole local playlist, use its sidebar menu. From a folder with no selection, the export can include files beyond the current page. Choose **Include subfolders** if needed.
2. Choose your decks and a destination, leaving **Copy** selected to preserve the originals. The **Auto** format picks FLAC when all selected decks support it and WAV otherwise. You can also choose WAV, AIFF, FLAC or 320 kb/s MP3 for lossless source files. Creating a playback copy from incompatible MP3 or AAC, or encoding lossless audio as MP3, requires **Copy** mode.
3. Review the planned copies, conversions, exceptions and drive warnings before executing. The compatibility rules come from player manuals. A copied folder contains audio files. To create a rekordbox library on a USB device, import those files into rekordbox and use its export workflow. [SoundCloud to rekordbox guide](https://djdigger.app/guides/soundcloud-to-rekordbox/)
4. If a copy is interrupted, make sure the destination folder is available and choose **Library → Resume interrupted export…**. The app verifies previously written files and reports conflicts with any files that have changed. The report includes copies completed on earlier attempts.

**Replace originals** is a separate, in-place operation. After verification and a successful update it removes the temporary original. Select it only when you intend to replace the source file.

## Python installs and upgrades

For the desktop with playback and analysis on Python 3.12+, install the package in an isolated environment:

```bash
uv tool install 'dj-sc-digger[gui,analyze]'
dj-digger-gui
```

`pipx install 'dj-sc-digger[gui,analyze]'` installs the same desktop command. Use `dj-sc-digger` without extras for the headless CLI. Playback is part of the base package.

### Upgrade from the old package name

Close dj-digger and uninstall `dj-soundcloud-digger` with the package manager that installed it. Both distributions own the same Python module and commands. Then install `dj-sc-digger` with the same manager.

| Installed with | Remove old package | Install current desktop |
| --- | --- | --- |
| uv | `uv tool uninstall dj-soundcloud-digger` | `uv tool install 'dj-sc-digger[gui,analyze]'` |
| pipx | `pipx uninstall dj-soundcloud-digger` | `pipx install 'dj-sc-digger[gui,analyze]'` |
| pip in a virtual environment | `python -m pip uninstall dj-soundcloud-digger` | `python -m pip install 'dj-sc-digger[gui,analyze]'` |

The command names and data directories stay the same. Recognized 1.0 databases get a verified backup before the schema upgrade. See the [1.1 migration record](implementation/release-1.1.md) for rollback details.

## Diagnostics and verification

Use **Help** in the desktop to open the log folder. The default location is `$XDG_STATE_HOME/dj-digger` (usually `~/.local/state/dj-digger`) on Linux, `~/Library/Logs/dj-digger` on macOS and `%LOCALAPPDATA%\dj-digger\Logs` on Windows. Logs rotate at 2 MiB and stay on your computer. The most recent per-file analysis report is `last-analysis.jsonl` in that directory. For a specific CLI run, pass `--log-level DEBUG --log-file PATH`.

From a checkout, run the default offline checks with:

```bash
python3 scripts/spec_section_map.py --check
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev pytest
```

For a repeatable analysis benchmark, run `uv run --extra analyze python scripts/benchmark_analysis.py --output /tmp/digger-benchmark` with a fresh output directory. Optional `--corpus` and `--references` arguments evaluate local files against verified BPM and key annotations.
