# Qt Quick desktop and Windows packaging

Implementation branch: `feat/qt-quick-windows`, based on `9d86d56`.
This record describes implementation and acceptance status; it is not a release
announcement or a claim that an installer has passed Windows acceptance.

## Run from the checkout

```bash
uv run --extra gui --extra play --extra analyze dj-digger-gui
```

Source execution needs FFmpeg/ffprobe on PATH. GUI is optional; CLI/TUI retain
existing commands and configuration/data locations. `gui.json` stores only GUI
presentation preferences. The desktop uses QML and a Python backend, without
Qt WebEngine. Playwright's separate Chromium is started on demand by account,
store and download-gate flows, so those operations still carry browser RAM cost.

## Implementation coverage

- Top waveform and transport; table selection, sorting, filtering and clipboard;
  playlist and paged local folder navigation; dark/light/system and PL/EN menus.
- Collection, refresh, profile and summary imports; local playlists, removal,
  restore, delete, status and undo; settings and SoundCloud authentication.
- Existing DownloadWorkflow and consent prompts; scan, stored metadata, bounded
  analysis helper, manual BPM/key changes and guarded local-file deletion.
- Existing reviewed audio export and interrupted-export recovery; store preflight,
  editable price validation, manual completion, retry and explicit Soundiiz export.
- Worker-owned service orchestration with queued Qt signals, stable identities,
  stale-result rejection, serialized playback mutations and bounded prefetch.

GUI controls do not replicate every TUI interaction exactly. File/folder fields offer system pickers; provider-generated diagnostic text and cart summaries are not fully
translated. Full interaction parity, accessibility and keyboard-only acceptance
remain manual checks, not established by the automated model/startup tests.

## Windows build and checks

On a disposable Windows x64 build environment:

```powershell
uv run --frozen --extra gui --extra play --extra analyze --group desktop-build --python 3.12 python packaging/windows/build.py
```

Build inputs use `uv.lock`, hashed FFmpeg/Inno Setup downloads and pinned CI
actions. The result is an unsigned test installer in `dist/installer/`, with a
SHA256 file and a dependency/build manifest. The onedir distribution includes
Python, the used Qt Quick modules, miniaudio, analysis libraries, FFmpeg/ffprobe,
and the Chromium revision required by Playwright. Building requires internet;
running/installing the produced bundle does not require downloading components.
Provider operations naturally still need network access.

CI runs GUI contracts on Linux/macOS/Windows and builds only on Windows. Its
frozen runtime check probes a synthetic WAV, decodes it, launches the analysis
helper, and opens a local headless Chromium page. The installer test runs only
on a disposable Windows GitHub Actions runner: install, reinstall, installed
runtime check, uninstall, and preservation of a synthetic user-data sentinel.
The workflow has read-only repository permissions and uploads a 14-day artifact;
it does not publish a release or use signing credentials.

The installer runs per user, offers a desktop shortcut and uses a stable AppId.
A named mutex prevents installer replacement while the desktop/helper holds it.
This does not provide a general single-instance lock: users should close other
CLI/TUI processes before upgrading or concurrently changing the shared settings.
No user database/configuration/browser profile is removed by the installer.

## Local evidence and outstanding acceptance

Linux source startup/close, stale folder suppression, pending-dialog shutdown,
selection persistence, numeric sorting, progress completion redraw and optional
Qt import boundaries have automated tests. The shared analysis/media regression
suite is also exercised. The complete offline suite passed **916 tests, 83 deselected** in 124.38 s.
The focused desktop suite passed **13 tests**, including the final opening
operation-admission and native-picker changes. Ruff, `git diff --check` and the
specification map pass.
A Linux onedir build also passed startup/close and the synthetic probe, decode,
analysis-helper and Chromium checks. For this Linux-only diagnostic, external
tools and the browser cache were linked under the Windows bundle names; it is
not evidence that Windows DLLs, installer behavior or offline distribution passed.

A seven-run synthetic 10,000-row model benchmark on the local Linux Python 3.14
runtime measured medians: replace 2.95 ms, sort 2.74 ms, filter 2.81 ms. Run:

```bash
uv run --extra gui python scripts/gui_benchmark.py
```

These are table-model timings, not rendering latency or whole-app memory
measurements. No claim about RAM savings versus TUI or browser apps follows.

Before treating this as a supported desktop release, verify on real Windows 11,
Linux and macOS: audible playback/device changes, physical mouse/keyboard and
screen-reader navigation, high-DPI/multiple monitors, large-library scrolling,
installer shortcuts, locked-file handling and upgrades from the prior installed
version. Provider actions are covered by existing offline contracts but were not
run against real accounts as part of implementation. Windows/macOS runtime checks
have not been run locally; a workflow definition is not a passing CI result.

Redistribution review must cover the exact bundled Qt/FFmpeg/Chromium and Python
packages, including applicable license notices and corresponding-source duties.
The build copies available dependency license files; this is not a declaration
that a public redistribution review is complete. Signing and automatic updates
remain outside this change.

## Desktop interaction correction — 2026-09-09

The transport now uses SVG icons with accessible labels. Both playback sources
use the TUI envelope calculation, drawn from a central baseline upward. Local
waveform computation has its own cancellable worker and explicitly publishes
the completed waveform, even while paused; loaded-object identity prevents late
results from replacing the display after stop or track replacement.

The folder sidebar uses a read-only home-rooted Qt filesystem tree. Expansion
requests directory data on demand; folders do not require a recursive scan of
the whole home directory. File rows remain in the existing paged music table.
This uses the existing Qt model's background enumeration, documented in
[QFileSystemModel](https://doc.qt.io/qt-6/qfilesystemmodel.html).

Focused verification includes a synthetic local WAV decoded by FFmpeg, pause
before waveform completion, cancellation/late results, actual folder selection
and expansion through QML delegates, and a rendered-image assertion that the
waveform has no mirrored lower half. Audio-device output is faked in these
offline tests.

Validation of this correction: **919 passed, 83 deselected** in the full offline
suite; **16 passed** in the focused desktop suite after the final frame-settlement
check. Ruff, specification-map validation and `git diff --check` passed.

## Theme readability correction — 2026-09-09

The Basic controls now receive explicit theme colors for alternating rows,
placeholders, indicators, hover/pressed surfaces, disabled text and tooltips.
Previously these roles inherited the host palette, producing light explorer
rows with light text and dark search hints in the dark application theme.
Selection text is paired with the accent background in both themes; the store
popup delegate uses that same pair instead of Basic's mismatched highlight roles.

The QML regression switches dark → light → dark and checks actual control colors
for search text/placeholders, selected and alternating tree rows, indicators and
disabled input text. Synthetic Linux rendering also covers the store popup and
edit dialog in both themes. This is targeted contrast verification, not a complete
accessibility certification or a Windows runtime check.

Validation: **919 passed, 83 deselected** in the full offline suite (170.20 s);
**14 passed** in `tests/test_gui.py`. Ruff, specification-map validation and
`git diff --check` passed.

## Desktop usability correction — 2026-09-09

A usability review of the first desktop build against the TUI produced these
changes, all in `gui/` plus the shared catalogs:

- Table: the title column now absorbs the remaining width, so Status and Stores
  are visible at the default window size instead of behind a horizontal scroll.
  Status cells use the TUI glyphs and colors, store cells are badges, local
  files and the playing track are marked, downloads show a row progress bar,
  numeric columns are right-aligned, the sort column shows an arrow, and a
  summary line reports visible/total/owned/skipped/selected counts. The store
  filter lists the categories present in the loaded view with counts.
- Keyboard: single-key shortcuts mirror the TUI keymap and appear in a Help
  dialog generated from the menu definitions. The table takes initial focus,
  Enter opens links, Delete removes, Home/End/PageUp/PageDown move the cursor,
  Esc clears selection then search then filters, `/` and Ctrl+F focus search.
  Shortcuts are suppressed inside text fields and while a modal dialog is open.
- Selection safety: commands that need a selection are disabled without one.
  Whole-view variants (open/download/cart for all visible) are separate,
  explicit menu commands; bulk opening asks above twenty links as in the TUI.
- Forms: enumerated values (export mode/format/bit depth/sample rate, summary
  format, browser, musical key with Camelot labels) are choice lists; BPM has a
  numeric validator; multi-line editors are framed; checkboxes carry their
  label; folder/file pickers sit beside the field. Validation runs through a
  backend `form()` loop that re-opens the same dialog with the entered values
  and the error text instead of discarding input. Confirmations name their
  action (Delete, Remove, Replace, Sign out …), delete-playlist shows the title,
  and logs/plans/URL lists use a read-only monospace view. Dialogs size to
  content. Cart results offer one next-step choice per round.
- Feedback: errors render in the footer in the danger color and every message
  is kept in a Messages dialog; the footer shows a loading state before the
  services are ready; empty views explain what to do next.
- Player: the panel collapses to a single row when nothing is loaded, clocks
  are m:ss, the waveform has a position cursor, hover time and drag seeking,
  transport buttons are disabled without a track, and mute/seek/volume steps
  are wired to the existing player API. Volume persists with the other
  presentation settings.
- Sidebar: the loaded playlist is highlighted with a source icon, playlists
  have a right-click menu, pinned folders are listed, folder paging shows the
  page range only above one page, the sidebar toggles with Ctrl+B and the
  playlists/files split is adjustable. Language and theme are checked radio
  entries; a Playback menu and a Help menu were added.

Not changed: the Basic control style stays because the existing contrast tests
and the packaged QML module set depend on it; store badges are not clickable
filters; there is no position column. The Polish catalog was extended for all
new strings and recompiled.

Validation: **905 passed, 83 deselected** in the full offline suite without the
desktop module (120.74 s) plus **16 passed** in `tests/test_gui.py`, including
new contracts for the form retry loop, the twenty-link bulk-open threshold and
the model counts/store totals/status labels. Ruff, specification-map validation,
`git diff --check` and the isolated-profile startup smoke passed. Offscreen
screenshots in both themes and in Polish were inspected by eye; no real
Windows/macOS run is claimed.

Follow-up on the same day: the transport order is previous / play-pause / next /
stop with the title beside the clock and the waveform filling the panel; the
sidebar spans the full height with the player in the main column in both GUI
and TUI (`compose()` moved the player widgets into `#main`); status is the first
column and BPM/key columns are hidden outside local views; the playback level
is a shared `volume` config field applied at player creation and saved at
shutdown. GUI (16), player, config and TUI (240) suites pass.


## Compact-window and feedback correction — 2026-09-10

Playlist and pinned-folder highlights now set their background explicitly in
both themes. The main pane may shrink without pushing transport or footer
controls outside the window: the sidebar has a window-relative maximum, the
volume slider and labels flex, and action buttons wrap below a separate status
row. The table keeps horizontal scrolling for wide sets of columns.

The Play button and Space share the selected-track rule; the Play/Pause icon
also follows that target. A separate error banner survives later informational
messages, offers Details, and can be dismissed without deleting message history.
The Polish catalog includes the new controls.

Regression coverage renders the real QML, checks selection colors in both
themes, clicks Play with different/same/no selection, and verifies the visible
controls remain inside a 760×520 Polish window with a long error. It also checks
that closing the error banner preserves history. Screenshots use synthetic data
and an inactive backend; they do not establish Windows installer acceptance.

Validation: **922 passed, 83 deselected** in the full offline suite, including
**17 desktop tests**. The final focused desktop run, Ruff, specification-map
check, diff whitespace check and isolated startup/shutdown smoke also passed.


## macOS test DMG — 2026-09-10

Build on a native macOS 15+ Apple Silicon or Intel machine:

```bash
brew install ffmpeg
uv run --frozen --extra gui --extra play --extra analyze --group desktop-build --python 3.12 python packaging/macos/build.py
```

The shared `packaging/desktop.spec` and entry points serve Windows and macOS.
No new runtime dependency is added. `desktop-build` replaces the Windows-specific
build-group name. The lockfile selects compatible analysis-library versions for
Intel macOS and NumPy 2.3.5 for the shared analysis dependency set.

Outputs are `dist/installer/dj-digger-1.1.0-macos-arm64-test.dmg` or
`dist/installer/dj-digger-1.1.0-macos-x86_64-test.dmg`, a SHA256 file and a build
manifest. The DMG contains the app, an Applications shortcut and PL/EN
instructions. The app bundles the matching Python, Qt, FFmpeg/ffprobe and their
libraries, audio/analysis dependencies, an analysis helper and Chromium. Homebrew
is needed on the build machine only; exact tool versions and source hashes before bundle relocation are recorded.
Available dependency notices are copied into the app, without asserting that a
public redistribution review is complete.

No Developer ID identity, Apple credentials, hardened-runtime signing or
notarization is configured. Native code and the outer bundle receive local
ad-hoc signatures so Apple Silicon can execute them; this does not identify a
trusted developer or bypass Gatekeeper. The package targets macOS 15+ because
its native build dependencies come from macOS 15 runners. It does not claim
compatibility with older macOS versions. Users may need to approve this specific
test app under Privacy & Security; no system-wide security changes are requested.

The desktop workflow uses `macos-15` for arm64 and `macos-15-intel` for x86_64.
After building, it mounts each DMG read-only, copies the app into an isolated
Applications directory containing spaces, ejects the image and tests the copied
app. The check verifies architecture, ad-hoc signature integrity, absence of
absolute non-system library dependencies, startup/shutdown, synthetic media,
analysis and the bundled browser with Homebrew removed from PATH. Artifacts are
uploaded only after this check succeeds, with 14-day retention. No release or
PyPI publication is triggered by this workflow.

Gatekeeper first-launch approval, Finder interaction, audible playback and
physical device/high-DPI checks remain manual acceptance work. CI runtime
results are recorded separately once builds finish.

Intel DMGs use Numba 0.62.x / llvmlite 0.45.x, the last series with Intel macOS wheels. The build dependency markers keep the newer Numba series on other platforms. The dependency audit uses `otool -m` to handle Chromium helper names containing parentheses.
