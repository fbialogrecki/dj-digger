# Changelog

## Unreleased

### Fixed

- "Import to DJ digger" on SoundCloud: the track rows of a playlist page or a
  station (Discover → track stations) now get the icon before ⋯ too. Browser
  extension 1.4.4.
- Desktop player: the volume slider is back at the right end of the transport,
  the time played / length beside the buttons; the 15 s zoom strip above the
  waveform and the large clock are gone. A kick lights the whole played side
  of the waveform again, not only the last 2 s.
- Desktop toolbar: the search field spans the whole width; below it the
  actions on the selection come first, then the store pills and filters.

### Removed

- Desktop player: the estimated drop (the “Drop” marker, Jump to drop (J) and
  Start previews at the drop) and View → Spectral waveform. The bars are drawn
  in the chosen colour pair again, and waveforms are computed without the
  low/mid/high bands; ones stored by 1.4.0-1.5.0 keep working.

## 1.5.0 — 2026-10-27

### Added

- "Import to DJ digger" on SoundCloud. With the browser extension (1.4.3), a
  track or playlist page gets an "Import to DJ digger" button after "Add to
  playlist", every ⋯ menu gets an "Import to DJ digger" item, and track and
  playlist rows get an icon before ⋯. Each sends that link to the running app,
  which imports it. The link is checked again in the extension and the app: https,
  soundcloud.com only. The extension asks for no new permissions.
- Single SoundCloud tracks can be imported, as a one-track playlist. The
  extension's right-click "Dig in dj-digger" now offers tracks too.
- The extension tells a missing desktop app from a stopped one. When the app is
  not installed, or too old to know the store extension, its status page and
  toolbar panel offer a Download button to the latest GitHub release.
- A project landing page (`site/`, Astro + three.js), with a GitHub Pages
  workflow.

### Fixed

- Drum & bass no longer reads at 2/3 or half tempo (about 116 or 87 instead of
  174). BPM analysis now weighs the kick and bass band separately from snares and
  hats. It searches 60–200 BPM without leaning towards 120, and resolves to about
  0.05 BPM instead of one frame. It moves to half or double tempo only when the
  backbeat clearly calls for it. Results below 78 BPM are doubled. Tracks are
  re-analysed with the new algorithm the next time they are analysed.
  Half-time DnB can still read as about 87, and genuine 70–77 BPM tracks now
  read double.
- The README's recommended install now includes the `gui` extra, so
  `dj-digger-gui` starts after `uv tool install` or `pipx install`.
- The sdist carries only the package, its tests, scripts and packaging files.
  It used to pick up whatever lay in the build checkout: 1.4.1's was 18 MB, and
  1.5.0's would have been 92 MB with the site's `node_modules`.

## 1.4.1 — 2026-10-19

### Fixed

- SoundCloud previews play again. SoundCloud stopped offering MP3 streams in
  October 2026 and now serves AAC over HLS (fragmented MP4, from
  `playback.media-streaming.soundcloud.cloud`), so every preview failed with
  "SoundCloud did not offer a supported MP3 stream". The player now prefers MP3
  when a track still has it and otherwise plays the best AAC stream, decoded by
  FFmpeg in memory; seeking and the waveform work as before. FFmpeg is required
  for these previews (the Windows and macOS installers bundle it; a PyPI install
  says so if it is missing). DRM-protected (encrypted) streams are never played;
  such a track says it is DRM-protected instead.

### Added

- Publishing a GitHub release now uploads the browser extension to the Chrome Web
  Store and submits it for review (`.github/workflows/chrome-web-store.yml`, Chrome
  Web Store API v2 with a service account). It skips with a notice when the store
  already has the extension's version, a submission is pending review, or the
  secrets are not configured; a manual run can upload a draft only.

## 1.4.0 — 2026-10-16

### Breaking

- dj-digger no longer bundles, downloads or drives its own Chromium. Download
  gates, store carts and the SoundCloud browser login now work through a
  browser extension installed in your own Chrome, Chromium, Brave, Edge or
  Firefox, so the browser extension is required for those three features.
  Everything else (collecting, downloads over HTTP, playback, the local
  library) is unchanged. `playwright` is no longer a dependency and the
  desktop installers no longer carry a browser.
- The old managed browser profiles are not used any more. `store-browser/` and
  `soundcloud-browser/` stay in the data directory
  (`~/.local/share/dj-digger`, or `$XDG_DATA_HOME/dj-digger`) and are never
  deleted by the application; close dj-digger and delete them by hand to
  reclaim the space. A Bandcamp guest cart left in the old `store-browser`
  profile is not visible in your own browser; the cart now uses your browser's
  Bandcamp session and cookies.
- The desktop now takes the same data-directory lock as the CLI: a second
  desktop, or any `dj-digger` command while the desktop is open, is refused
  ("Another dj-digger instance is using this data directory").

### Added

- The browser extension is available in the Chrome Web Store (unlisted, published
  2026-10-15). Connect browser in the desktop has an Open Chrome Web Store button
  for Chrome, Edge and Brave (Edge asks to allow extensions from other stores), and
  `dj-digger browser register` prints the store link; Load unpacked stays as a folded
  developer install. Firefox still uses a temporary add-on.
- Chrome Web Store preparation for the browser extension:
  `scripts/package_extension.py` builds clean, reproducible Chrome and Firefox
  uploads (`dist/extension/`), the native host also accepts the store's extension
  ID once `CHROME_STORE_ID` is set, and `CHROMEWEBSTORE.md`, `PRIVACY.md` and
  `docs/store/` hold the listing, privacy policy, screenshots and publishing steps.
- Desktop accessibility and polish: one accent focus ring for keyboard focus,
  clearly dimmer disabled controls, text that follows the system font size,
  short toasts for confirmations, an error banner announced to screen readers,
  an empty-library screen with Add playlist / Add folder / Import profile
  playlists, and View → Theme → High contrast.
- Desktop first start opens Welcome: paste your SoundCloud profile link and every
  playlist the profile created is imported in the background, with a summary; then
  the download folder, name and email, and Connect browser. Every step can be
  skipped. Profile links work with or without https, m./www., /sets or a query.
- Desktop toolbar: store and gate-site pills replace the store dropdown (several at
  once, filtered by name); a status filter (All / Unhandled / New / Opened / Owned /
  Skipped, H toggles Unhandled) replaces Hide handled; a Filters popup adds BPM
  range, harmonic key and genre; a filtered count, a clear button in search and
  Clear filters when nothing matches. Opening or downloading more than ten tracks
  asks first.
- Track right-click menu: Open, Download, Mark owned, Skip, Analyze and the cart
  next to Play and Reset, grouped; Copy link, Reveal in file manager, Select all
  and Invert selection; Remove and Delete in red with counts; disabled entries say
  why. It opens with the Menu key, Shift+F10 or a right click below the rows.
- Track table: Shift selects exactly the range (Ctrl+Shift adds it) and dragging
  across rows selects them; the keyboard row stays on its track through sorts and
  filters; the playing track gets a tinted row, a left bar and a play/pause icon;
  "Played" is shown; double click always plays (rename with F2 or a slow second
  click); header clicks cycle ascending/descending/original order and the sort is
  remembered; View → Compact rows; Format and Comment columns for local files.
- Sidebar: right-click menus act on the clicked folder or playlist (Analyze,
  Rescan, Show in file manager, Copy path, Pin/Unpin, Hide; Refresh, Rename,
  Duplicate, Export audio… for every file of a local playlist without opening
  it); hidden folders come back from the Folders menu; track counts, a
  filter field, foldable sections and one scrolling panel; drag selected local
  tracks onto a local playlist, or drop folders from the file manager; keyboard
  navigation and a visible splitter.
- Player: spectral waveform (View → Spectral waveform), a 15 s zoom strip with
  beat ticks, an estimated drop marker with Jump to drop (J) and optional preview
  start at the drop or 30 %, session hot cues (Ctrl+1–4 / Alt+1–4), 1–9/0 position
  jumps, 30 s seeks ({ / }), wheel seeking, a large elapsed/remaining clock, a
  plain square artwork option, and a kick light limited to the last 2 s behind the
  cursor.
- Menu bar: Settings shows who is signed in and which browser is connected; a
  Playback menu; Select none and Invert selection; Library sections with tooltips
  and Open recent; View → Full screen (F11), Columns and Reset layout; colour
  swatches; Help → Open data folder, Open log folder, Check for updates, About;
  F1 and Ctrl+, shortcuts.
- Settings is grouped into sections with plain-language hints; Connect browser has
  numbered steps with copyable extension pages and confirms which browser
  connected; Gates need you and Download results use clear buttons and plain
  reasons.
- German, Spanish, French, Italian and Russian desktop translations next to
  English and Polish (View → Language); a first start picks the system
  language when it is one of them. The reworked menus, filters, dialogs and
  Welcome are translated too, with proper plural forms for track counts.
- The Stores column names each track's gate site (Hypeddit, ToneDen, …)
  instead of a generic "gate"; the store filter still groups all gates.
- The dj-digger browser extension (Manifest V3, one code base for Chrome-family
  browsers and Firefox), a `dj-digger-host` Native Messaging relay (a console
  script, and a console executable beside the GUI in the installers) and the
  channel between them and the app: a private local socket authenticated with
  a key in `config_dir()/webext.json`. The extension only works on Hypeddit,
  Bandcamp, Beatport and the SoundCloud sign-in page, only in tabs it opened,
  never opens a checkout or payment page, and sends nothing to the network.
- `dj-digger browser register` registers the host with the browsers it finds
  (Chrome, Chromium, Brave, Edge, Firefox; Flatpak and Snap browsers are
  reported as unsupported), copies the extension to the data directory's
  `browser-extension/` folder and prints it for "Load unpacked" (Chrome, Edge,
  Brave) or "Load Temporary Add-on" (Firefox). `dj-digger browser unregister`
  removes the registration.
- Desktop: a "Connect browser" dialog opens when a cart or the SoundCloud
  login finds no extension (steps for both browser families, the extension
  folder with Copy path, Check connection); Settings shows a "Browser
  automation" row with the connected browser and extension version, or what is
  missing; the Settings menu has "Connect browser…" and "Disconnect
  browsers…" (the desktop registers again at its next start).

- Beatport tracks now go into your real Beatport cart when your browser is signed
  in to Beatport: the exact track comes from a direct track link or from the
  release page's own rows (never from a search by artist and title), its title,
  version and price are revalidated before the click, the add is verified by the
  header counter and the cart page, and an uncertain click is never repeated.
  Not signed in, a security challenge or no exact match keeps the Soundiiz
  playlist path. The results list shows "in Beatport cart" apart from "to
  playlist". The extension also refuses `/account/billing` and
  `/subscriptions/billing` on Beatport.
- Settings: "Bandcamp name-your-price items go into the cart at" — the minimum
  price (default) or the price the artist suggests.
- Desktop: a "Download results" screen ends every gate batch with failures,
  listing each with its reason, with Retry these, Open them in the browser and,
  when the extension is missing, Connect the browser and retry. Rows show their
  state while the batch runs (half bar: in the browser pass; full bar: file in).
- A gate step with a comment field is filled with one of your gate comments in
  the browser pass too.
- A gate's Spotify step now completes in the background as well: Spotify's
  sign-in opens as a muted, inactive tab of your browser window and closes once
  Hypeddit has it, so with Spotify signed in (and Hypeddit authorised once) no
  window appears. Other provider logins still ask first.
- ToneDen gates (`toneden.io/<artist>/post/<slug>`) now download in the
  background through the browser extension, like Hypeddit gates: the gate runs
  in an offscreen frame, its follow/repost (SoundCloud) or follow/save (Spotify)
  button is pressed, ToneDen's own sign-in opens as a muted, inactive tab that
  closes once it is back at ToneDen, and the file arrives in the row. A sign-in
  that waits for you (not signed in, or ToneDen not yet approved at the
  provider) is asked about in "Gates need you"; the provider's own approval
  button is never pressed for you. The extension now also works on
  `toneden.io` (a new host permission: reload the unpacked extension, and in
  Firefox grant it on the extension's page).
- The browser extension has a face: its toolbar button opens a small player
  panel with the desktop's record (the playing track's cover on the label, the
  same barcode and backdrop, turning while it plays) in the desktop's colour
  pair and theme, the title and artist, and previous, play/pause and next
  buttons that control the desktop's player. It says "Open dj-digger to control
  playback" while the desktop is closed and "Nothing playing" when nothing is
  loaded. No new permission; reload the unpacked extension to get the button.
- The extension's panel does more: a seek bar with elapsed and total time; the
  desktop's running job with its progress and a Cancel button; "N gates need
  you" with Open them / Skip them, which answer the desktop's question (the
  count is also the toolbar button's badge); and, opened on a SoundCloud
  playlist or profile page, "Import this playlist into dj-digger" or "Import all
  playlists of this profile", which start the desktop's Add playlist or profile
  import. Right-click a SoundCloud playlist or profile link (or such a page) for
  **Dig in dj-digger**. The extension asks for two new permissions, `activeTab`
  and `contextMenus`; reload the unpacked extension.

### Changed

- "Prepare cart / Beatport playlist…" is now "Add to store carts…" and "Open
  links" is "Open in browser"; store actions no longer appear in local folder
  views. Stop is now S (was Ctrl+W).
- In Chromium-family browsers the hidden Hypeddit pass runs in an offscreen
  document of the extension (no browser window at all, which desktops such as
  GNOME on Wayland could not hide); gates that need you (a Connect login, a
  CAPTCHA, a download the browser holds back) then open in a normal window. The
  extension asks for the new `offscreen` permission and keeps no new host or
  message channel: only the extension's own offscreen frames may talk to it.
  Firefox and everything outside Hypeddit keep the work window.
- Hypeddit gates run in one work window of your browser: hidden (an unfocused
  window) first, then the same window shown for the gates that need you; the
  tabs of finished gates are closed and the rest are reloaded in place. Files
  are taken from the browser's own download, one gate download at a time, and
  a download the browser holds back (a "where to save" prompt, an unsafe-file
  warning) is never accepted automatically.
- Bandcamp carts and Beatport lookups run in your own browser, with no second
  browser or cookie copy (see below for where). Saved cart diagnostics also lose
  e-mail addresses and are not written for pages above 2 MB.
- The SoundCloud login reads the `oauth_token` cookie from your browser (no
  window when it is still valid) and otherwise opens the sign-in page in the
  work window, waiting up to five minutes as before; closing that sign-in
  tab ends the wait at once and saves nothing.
- Every automation run (cart, gate batch, SoundCloud login) has its own work
  window, so one no longer closes another's pages; a window you close by hand
  is reopened for the next cart batch. A second dj-digger (for example with
  another data directory) no longer takes over the extension socket of a
  running one and is told "another dj-digger is already connected to the
  browser extension".
- Gate social actions now act on the accounts you are logged in to in your
  browser. A Connect step (Spotify, Deezer, Apple Music, Threads) you
  authorised for Hypeddit once completes without a prompt, and Hypeddit may
  then follow or save on your own account. The Settings text for "Allow
  social actions", the README and the specification say so; turn the setting
  off to click nothing.
- Nothing of the browser appears on its own any more. Gates that need you no
  longer open a window by themselves: the desktop asks "Gates need you" first
  (open them in the browser, or skip them). Carts work in inactive tabs of your
  own browser window instead of a window of their own, with no review form: the
  batch ends with one active cart tab per store, which is then yours (the
  extension no longer guards it, so checkout from it works). "Finish in browser"
  for uncertain clicks is now only on the results screen. Cart results count
  what was added and list every ticked track that was not, including tracks
  without any store link (previously dropped silently), with "Open the links
  that were not added in the browser"; Soundiiz stays as an option.
- The extension also refuses Bandcamp's `/cart/checkout_start` and checkout
  paths under a locale prefix (`/de/checkout/…`).

### Fixed

- Firefox: gates sent to the browser no longer fail within seconds with "Could
  not open Hypeddit in the browser: Error code 2152398850". A navigation Firefox
  reports as aborted or redirected (`NS_BINDING_ABORTED`, `NS_BINDING_REDIRECTED`,
  `NS_ERROR_ABORT`) is followed to the page that loads, as Chrome's
  `ERR_ABORTED` already was. A gate page the browser really could not load now
  reads "the browser could not open the gate page" with Retry first, not "the
  gate is gone or has changed". The first gate after a new work window no
  longer fails this way when Firefox aborts that window's first navigation:
  the tab is asked once the wait is over, and a navigation that never started
  is started once more.
- Firefox: the sign-in and follow popups a gate opens (SoundCloud, Instagram)
  close with the gate session instead of staying open as separate windows.
  The browser extension is 1.4.2.
- Granting or removing the extension's site access in the browser's settings is
  reported to dj-digger at once, so Settings and Connect browser show "missing
  site access" without reloading the extension; gates, carts and the SoundCloud
  login then stop up front with "Allow dj-digger site access in the browser's
  extension settings" instead of an obscure error.
- Download results no longer nest reasons ("… (browser extension restarted
  (after: …))"): each row shows the plain reason and at most one detail.
- Emoji and non-Latin characters in track titles show in the Download results
  and Cart results lists.
- Closing Settings or another dialog with Cancel or Escape no longer shows
  "Cancelled"; it is shown only when a running job was cancelled.
- The package version is 1.4.0 (`pyproject.toml` still said 1.3.0).
- Closing the browser, or the extension restarting, during a gate download no
  longer asks "Gates need you" or reports the rows as needing a step only you
  can do: they fail with "the browser was closed or the extension restarted",
  Download results offers Retry first (and Connect browser and retry when no
  browser is connected), and the status bar no longer shows "Downloading gates
  in the background" while the results wait for an answer.
- Download batches, gate batches included, now write their start, each row's
  outcome (site, track, status, failure group) and their end counts to
  `dj-digger.log`; failures are warnings. No emails, names, tokens or query
  strings are logged.
- Settings → Connect browser… stays in the menu while a browser is connected,
  so a second browser can be connected and Check connection reached.
- The desktop no longer uses a fifth of a CPU core at idle: the waveform noise
  shown with no track loaded no longer drifts on every frame (it stands still);
  nothing animates or ticks while no track plays and nothing runs.
- Signing in to SoundCloud while your browser is already signed in no longer
  flashes an empty browser window: the login cookie is read without opening any
  window, and the work window opens only when the sign-in page is needed.
- Store carts no longer leave their work tabs (product lookups, the page of a
  skipped or already bought item) open in your browser next to the finished cart
  tab: each cart run closes them when it ends, however it ends.
- Desktop toolbar: the filtered count (`3 / 144`) has its own room after the store
  pills instead of being drawn over the last one; another playlist starts its pills
  scrolled back to the first; the status filter is as wide as its longest option in
  every language (no more "All statuse…").
- Store pills and Stores badges read proper names (SoundCloud, Bandcamp, Juno
  Download, Apple Music, Smart link, No link, …) like the gate sites already did; the
  generic ones are translated, filtering still keys on the category.
- Cart results shows its next steps as buttons (Retry failed items, Finish in
  browser, Send playlist to Soundiiz, Open links not added, Show carts) with Close,
  instead of a "Next step" list plus Cancel / Continue; the dialog widens so they fit.
- A track put in a store cart now reads Opened instead of New, as an opened link does.

- Store carts wait up to 20 s for a "Just a moment…" security check that clears
  itself in your browser (seen on Beatport) instead of failing at once and bringing
  the tab to the front; only a check that does not clear asks for you.
- Skipping the first-run Settings no longer stops a download; a saved browser that
  is not available is kept instead of silently reset. Saved column widths, order,
  hidden columns and recent items now really restore at startup.
- Settings offered "System default" twice in the browser list. The field is now
  "Open links in", and Connect browser says that SoundCloud sign-in, gates and carts
  open in the browser where the extension is installed.
- Cancel in "Gates need you" only closed that question: the download went on
  and ended in "Download results". It now stops the whole download, and a
  cancelled download (also from the status bar) asks nothing more.
- A failed row in a running download showed "-100%" in its Status cell; it now
  says "✗ Failed".
- A Hypeddit gate whose step timed out in the background (a slow page) asked
  "Gates need you" at once; it now gets one more background try first. A
  timeout names the control it waited for.
- A ToneDen or other non-Hypeddit gate said "press 'o' to open"; it now names
  the site and says to open the link in the browser.
- A gate whose page no longer exists (a removed ToneDen post, an Artist Union
  track that answers 404) now fails at once with "the gate page no longer
  exists" instead of "open it in the browser", and is never handed to the
  browser or asked about.
- Gate frames no longer tell the gate site the extension's address as their
  referrer, and an OAuth `code`/`state` or a ToneDen `jwt` in a gate-site URL
  no longer reaches the app's events or logs.
- Beatport: a click made before the page had loaded your cart did nothing, and
  the counter then jumping to your real cart size was taken as a verified add
  (seen live: tracks reported "in Beatport cart" that were not). The counter is
  now trusted only once it has settled, and a track you bought before on Beatport
  is reported as already bought instead of an unverified click (Beatport's
  "repurchase it again?" question is answered with Cancel).

### Removed

- Playwright, the bundled and downloaded Chromium (the "Download Chromium"
  prompt), the application-managed SoundCloud and store browser profiles, the
  separate cart viewer browser and the "Store accounts…" menu entry with its
  Bandcamp login (guest carts are unchanged).
- The `bandcamp_dom` and `shop_mutate` test markers and their tests; `shop_live`
  now runs through the connected extension.

## 1.3.0 — 2026-10-05

### Added

- View → Colours: the desktop waveform and artwork backdrop take one colour
  pair (magenta and blue, green and yellow, purple and orange, cyan and
  magenta, crimson and ice blue, gold and chrome, violet and gold, teal and
  coral, emerald and pink, jade and raspberry, rose and mint, lime and violet,
  acid and ultraviolet, amber and teal, copper and cyan, blue and orange, sky
  and lemon, indigo and lime), each with dark and light theme values; the light
  theme values are as light and saturated as 3:1 against the window allows. The backdrop is always the
  pair's two colours, laid out per track from the track key.
- Desktop folder views: double-click the artist or the track name (or press
  F2) to edit it in place. A new name renames the file on disk, keeping its
  extension, analysis, waveform and links; it never replaces another file and
  is refused while the file is loaded in the player.
- A `played` status: starting a track takes its `New` away. Tracks you have not
  heard yet read `New` in the Status column.
- In playlists, a folder icon before the name marks tracks found in your local
  files.

### Changed

- Local tracks are named after their file on disk; the artist comes from a
  hand-set value, the artist tag, or the text before the first ` - ` of the
  file name.
- The sidebar folder tree lists only folders with music in them or below them.
- The sidebar headers (Playlists, Folders — formerly Local files) are centred,
  and each list ends in a quiet full-width "+" row right under its last entry.
- The player shows from startup: before a track loads, a plain record with a
  white label on the colour pair and slowly drifting waveform noise. The noise
  moves smoothly with the display's frame rate and is drawn by the GPU; it
  rests while the window is in the background.
- Right-click a folder in the sidebar to hide it (Settings → Preferences →
  Hidden folders brings it back). Clicking a folder whose music is only in its
  subfolders expands it instead of opening an empty list.
- Folder views show genre, year and label again, read from the files' tags.
  `.mp4` files no longer count as audio.
- Hide handled is an eye toggle at the right end of the toolbar; the toolbar
  buttons are separate tiles with a faint outline.
- The volume slider fills in the chosen colour pair and has a smaller knob.
- The Windows installer runs on Windows 10 version 1809 or newer as well as
  Windows 11; it used to refuse every Windows 10.
- Space only pauses or resumes the loaded track, whatever is selected; Enter
  starts the selected track (it used to open links, which stay on O). With
  nothing loaded, Space still starts the selected track.
- The row menu (right click or More actions) no longer repeats the toolbar.
- The search field has the same faint outline as the toolbar buttons.
- The menu bar keeps only actions with no button elsewhere (Library, Tracks,
  View, Settings, Help); the Playback and Tools menus are gone, and their
  shortcuts still work and are listed in Help → Keyboard shortcuts.
  Preferences moved from S to Shift+S.
- The Title column is now Track name. Status, year and time are never cut
  short, and times from an hour read h:mm:ss.
- BPM and key chips sit next to the artist in the player; the match ring is the
  text colour, and the twelve key colours are spaced evenly in OKLCH.
- One badge style for every store (download gates in the warning colour);
  `no-link` leaves the cell empty. The keyboard row has a single outline.
- The search field has rounded corners and a `/` key hint; dialog checkbox and
  field outlines meet 3:1 contrast. The player, search, toolbar
  and table share one right edge.

- On a kick the whole played side of the waveform lights up, not only a short
  stretch behind the cursor.
- The kick light dies away like a lamp (exponentially, 75 ms time constant)
  instead of falling in a straight line; it is still dark within 150 ms.

### Removed

- The Textual terminal UI and the `--no-tui` flag. `dj-digger` is now a
  headless CLI: `dig` exports, saves to the library and prints a summary table,
  and `open SUMMARY` always batch-opens links (asking for a category unless
  `--category` is given). The interactive UI is the Qt Quick desktop,
  `dj-digger-gui`.
- The `textual` dependency, `TEXTUAL_ANIMATIONS`, and the unused settings
  `theme`, `columns`, `sidebar_split` and `sidebar_mode`.
- Dead code left behind by the TUI: `links.group_by_track`,
  `tracks_from_records` and `LINK_KEYWORDS`, `player.fraction` and
  `change_volume`, `logging_setup.current_log_error` and `open_log_folder`,
  `Track.duration_label`, `playlist.sort_rows` and `operation_targets`,
  `Row.record_for`, `CrateRecord.restore`, `TrackState.clear_local_file`,
  `OperationHandle.describe`, `AccountService.wait_authentication` and
  `save_profile`, `OpeningService.open_many`, `GateProfileAnswer`.

### Added

- The record label in the player shows the track's artwork, turning with the
  record and with its colours untouched: SoundCloud artwork (kept in memory for
  the session only), or for a local file its embedded picture or a `cover`,
  `folder`, `front` or `albumart` image beside it. Settings → Default cover
  picks a picture for every track without one.

### Changed

- The waveform of a SoundCloud track is computed by dj-digger from the audio it
  has downloaded, like a local file's, instead of SoundCloud's waveform JSON; it
  appears once the whole track is buffered.
- Running `dj-digger` without a target now exits with "Nothing to dig. Pass a
  SoundCloud link, or run dj-digger-gui to browse."
- The desktop has a darker club palette with blue and bordeaux accents, a Now
  playing header with generated record artwork carrying a barcode of the track,
  BPM and key chips with Camelot notation (View → Key notation) and harmonic and
  tempo match rings, and animations that View → Animations turns off.
- The desktop waveform and record backdrop glow with the kick drum: a
  continuous kick level, every 10 ms, instead of yes/no flashes, so the light
  never stops on a missed hit and keeps up at any tempo. The level is the
  40-130 Hz energy rising above what holds there (a sustained bass, its
  sidechain recovery, a pad), weighted by how much it sounds like a kick, first
  a generic one, then the kick this track keeps repeating (learned from onsets
  that drop in pitch, which a bass note does not), and compared with the
  track's recent loudness, so breakdowns stay dark. The player decodes 0.4 s
  ahead of the audio device and works out what the speaker plays from how much
  it has queued, so the light is on time without a setting: View → Pulse timing
  is gone; the light samples 40 ms ahead for the display's own delay. Kicks
  are the brightest and fills, filtered kicks and bass hits glow dimmer, in
  proportion. The light jumps up with a kick and falls to dark within 150 ms,
  showing nothing below 15 %, so it goes out between kicks even at 174 BPM. The
  played waveform rests in deep saturated tones and lights up in neon (over
  twice as bright on the dark theme) just behind the playhead, with
  a GPU glow where the scene graph has one; the playhead tints neon with it. The
  record backdrop glows as before, and the record is a flat near-black that
  never pulses itself. The playhead slides with the heard audio instead of
  stepping. `scripts/evaluate_kicks.py --audio` reports pulses and lit time per
  15 s for your own files.
  Mute is a speaker icon that is struck through while muted.
- New application icon: a record with a barcode like the track artwork, a white
  label and a blue-to-bordeaux tile, at 256 px for the window and Linux docks.
- Selected rows, playlists and folders use a soft pastel blue instead of the
  bright accent.
- Add playlist asks for a SoundCloud link, lists the import in the sidebar at
  once, fills the table as tracks arrive and shows stage and done / total counts
  with a progress bar in the status bar.
- Converting audio asks which decks the files must play on instead of a format,
  picks FLAC when they all read it and WAV otherwise, at the highest bit depth and
  sampling rate they share, remembers the choice, and shows compatibility
  for those decks only. A file is kept as it is only when every chosen deck plays
  it, so a 32 kHz MP3 is no longer copied for a CDJ-3000. Decks that play the
  same files share one box, ten in two columns.
- The deck rules now follow the playable-file table of each manual and cover the
  CDJ-900, 900NXS and TOUR1, the XDJ-700, 1000, 1000MK2 and AERO, and the XDJ-RX,
  RX2, RR, XZ, RX3, OPUS-QUAD, XDJ-AZ, OMNIS-DUO and XDJ-AN. MP3 and AAC at 16-24 kHz
  count as playable on the players whose manuals list them.
- The export review warns when the destination drive uses a file system a chosen
  deck cannot read (NTFS, or exFAT for most decks) and when files would sit more
  than eight folders deep, which decks do not show.
- On Linux the desktop reports the `dj-digger` app ID, so an open window shows the
  name and icon of `dj-digger.desktop` instead of `python3`.

### Fixed

- Starting a track no longer stutters. The player panel switches height at once
  and fades its artwork and waveform in, instead of repainting every waveform
  canvas on each frame of a resize; table cells no longer re-evaluate on every
  25 ms audio tick; the `beats` event is sent only when the pulses change; the
  volume rescale and the local-file waveform run in numpy instead of a Python
  loop over every sample.
- The desktop no longer freezes for about 1.5 s when a track starts or the window
  is resized: the kick glow around the waveform was a blurred Canvas shadow,
  and is now two translucent rims that paint in milliseconds.
- A SoundCloud track starts playing without waiting for its waveform. Until the
  waveform is in, the record is blank on a white sleeve and the bars flicker as
  random noise (a track change scatters the previous waveform into it);
  then they settle into the waveform while the colours and the record code fade
  in. Every played track's waveform is kept in the library (schema 3 adds a
  `waveforms` table), so a track played again shows it at once; the local
  waveform JSON cache in the user cache directory is no longer used.
- Upgrading with the Windows installer now refreshes the desktop and Start-menu
  shortcut icons; Explorer kept showing the previous app icon until its icon
  cache was rebuilt.
- Marking or undoing statuses no longer reloads the playlist and re-queries every
  local file; the statuses are written in one transaction.
- Opening a folder probes only files it has never inspected and updates their rows
  in place instead of rebuilding the table, and an already indexed file no longer
  costs a database write.
- A playlist import redraws the table at most once a second instead of every 50
  tracks; table counts and store filters are computed once per change.
- Matching tracks to local files no longer lists a folder for every file that is
  still in place.
- The hidden store-cart browser no longer identifies itself as `HeadlessChrome`;
  it presents the headed user agent the hidden gate browser already used.
- The JSON link export is written atomically, so a failed write no longer leaves
  a truncated summary in place of the previous one.
- After a column was dragged to a new place, opening a playlist with more rows
  no longer closes the desktop app.
- Seeking and changing tracks no longer replay audio queued from the old position,
  and seeking late in an MP3 no longer pauses while the decoder works its way there.

### Removed

- Saved SoundCloud HTML pages are no longer accepted as a target, and `dig
  --delay`, which only paced that path, is gone. Sign in to SoundCloud to read
  your own private playlists.

## 1.2.1 — 2026-09-27

### Fixed

- Desktop Add playlist collects the entered URL or saved HTML file instead of
  failing with `'dict' object has no attribute 'strip'`.
- Desktop Store accounts signs in to Bandcamp only instead of failing with `'beatport'`.
- Desktop Settings no longer brings back the reserved placeholder email after an
  empty email was saved, so settings save without clearing the field again.
- Desktop summary import asks for a JSON file, and loading a CSV export says it
  is output-only instead of reporting invalid JSON.
- Desktop Replace originals no longer asks for a destination folder.
- A partial desktop export names the first failed file and its reason in the
  error banner, and finished operations no longer leave their label in the status bar.
- A symbolic link beside its file no longer shares that file's row, and deleting
  local files checks every file before removing any, so a refused link can no
  longer leave its original deleted.
- Title-only scan matches play locally in the desktop, and Download in either
  interface no longer marks such a guessed file as owned.
- Cancelling Store accounts or a cart is no longer shown as an error, and the
  desktop offers to download a missing Playwright Chromium like the TUI.
- A Soundiiz handoff above 200 tracks says so instead of a generic import failure.
- TUI Settings can clear the email and refuses an invalid one; both interfaces
  save no email the same way.
- The TUI plays any track that has a file on disk locally, including downloads and
  title-only matches, instead of streaming it or refusing it without a track id.
- A failed SoundCloud login browser names causes the app diagnosed itself, such as
  a failed Chromium download or a profile already in use.

## 1.2.0 — 2026-09-16

### Added

- Optional Qt Quick desktop with English/Polish menus, light/dark themes, playlist
  and local-folder navigation, waveform playback, downloads, analysis and audio export.
- Self-contained Windows 11 x64 installer and macOS 15+ disk images for Apple
  Silicon and Intel. Desktop packages remain unsigned previews without automatic updates.
- English installation guides covering downloads, first-launch warnings, updates
  and removal for Windows and Mac.

### Improved

- Desktop toolbar, selection actions, full-width status bar, native file pickers,
  and resizable, reorderable and hideable columns with saved preferences.
- Playback responsiveness through retained decoded audio, coalesced seeks and
  waveform updates independent of position ticks.
- Manual download-gate sessions can advance other tabs while provider login waits.

### Fixed

- Default installations include the audio decoder required for playback.
- Legacy libraries migrate without rejecting their older local-file layout;
  large Windows filesystem identities retain their exact values.
- Mac application bundles start the windowed GUI and include native media tools
  and an analysis helper for each supported processor.
- Package metadata and version reporting use the published `dj-sc-digger` name;
  release checks find the current wheel without hard-coded version filenames.

### Limitations

- Desktop installers are previews. Automated startup, media, browser and installer
  checks do not establish physical audio-device, screen-reader, high-DPI or full
  keyboard/mouse acceptance on every supported platform.
- Windows may warn about an unrecognized publisher. Mac packages have ad-hoc
  signatures only, without Developer ID or Apple notarization. See the installation guide.
- Existing provider, analysis-accuracy and hardware-compatibility limitations from
  1.1.0 still apply.

## 1.1.0 — 2026-09-09

### Added

- Local file explorer, paginated folder views, pinned directories and local playlists.
- Local FFmpeg playback, independent waveform caching and BPM/key analysis with manual overrides.
- Manufacturer-based deck profiles, verified complete-folder audio export and journaled replacement/recovery.
- Public/owner-private SoundCloud profile playlist import with stable provider identity and partial-response protection.
- Schema 2 migration with consistent SQLite backups and a CLI instance lock.
- Distribution renamed to `dj-sc-digger`; GitHub repository renamed to `dj-digger`; module, CLI and data directories remain unchanged.
- Private rotating diagnostic logs, an in-app log viewer and per-file analysis reports.
- F4 view summaries and an isolated analysis benchmark with explicit reference coverage.

### Improved

- Responsive track columns and footer, tabbed settings, readable cart results, and export dialogs with always-visible confirmation/cancel actions.
- Local BPM/key fields show their saved value and source without triggering reanalysis.
- Explorer horizontal scrolling uses a thin thumb; waveform colors follow playback position without pulsing with amplitude.
- Analysis subprocesses and owned FFmpeg processes are cleaned up on cancellation and failures.

### Fixed

- Late table-layout events no longer access removed widgets during application shutdown.

- SoundCloud repost collection uses the current stream endpoint and rejects repeated pagination pages.
- SoundCloud preview falls back to MP3 HLS when progressive MP3 is absent; account/region blocks, absent streams and unsupported formats have distinct diagnostics.
- Browser launch failures are reported accurately, and automatic local scans no longer show access errors for folders the user did not explicitly open.

### Limitations

- BPM/key values are estimates; accuracy has not been established against a human-labelled DJ corpus. Deck compatibility is manufacturer-documented rather than hardware-tested.
- Private SoundCloud playlist import has offline fixture coverage; live owner-private completeness has not been established.
- MP3 HLS preview is limited to 50 MiB in memory. AAC/Opus HLS and encrypted streams are unsupported; SoundCloud availability restrictions still apply.

## 1.0.0

### Added

- The footer labels `b` and `shift+b` as searches in Bandcamp and Beatport,
  shows `c` beside `shift+c`, and displays shifted letter keys in lowercase.
- Download now uses `d`, `Shift+D` is labelled Download all, and adding a
  playlist moves to `a`.
- The interface and documentation now call saved collections playlists rather
  than crates. Internal data names stay unchanged for compatibility.
- Ctrl+C quits the playlist browser like `q`, from the search box too. Textual
  only showed a hint to press Ctrl+Q.
- `ctrl+x` stops whatever long job is running: a dig, a download batch, a bulk
  open, a scan or a store cart. What was already collected or downloaded is
  kept, and nothing partial is saved.
- The status bar shows the running job with a spinner, progress counts and
  `^X stop`. A refresh no longer blanks the table while it digs, and a scan
  that hits folders it cannot read says so in the error banner.
- Multi-select: `v` toggles a row, `Shift+V` extends to the cursor, `Ctrl+A`
  selects everything shown; batch download, open, cart, export, marks and
  removal then act on the selection.
- The `f`/`Shift+F` store-cycling keys are gone; the number keys, `0`, and a
  click on the legend's `…` cover it, and the footer has the room back.
- Sorting with `t` (title, time, genre, status, store, and any enabled BPM,
  key or year column) and `Shift+T` to reverse; the header shows the arrow.
- The search box matches every word in any order against artist, title,
  genre, tags and label. Escape in the box keeps the filter; on the list it
  clears the selection, then the search, then the store filters. `f`/`Shift+F`
  step from the store toggled last instead of jumping to the first.
- Tracks carry BPM, key, release year and label when SoundCloud has them;
  Settings can show them as columns, and JSON/CSV exports include them (the
  four CSV columns are appended after the original five).
- A theme choice in Settings, remembered between runs. Every colour the
  interface paints now comes from the theme (badges, marks, waveform, volume
  bar, banner, help) instead of the terminal's cyan, green and yellow.
- `Shift+U` (reset statuses) asks before it wipes the marks.
- `Shift+P` opens every exact Beatport track page shown in your regular,
  logged-in browser so each can be added to the cart with one click; the `c`
  and `Shift+C` labels now say that Beatport goes to a playlist, not a cart.
- When a Bandcamp click cannot be verified, the app saves a screenshot and a
  redacted page copy under `cart-diagnostics` in its data folder (last ten
  kept). After two unverified clicks in a batch it stops clicking, opens the
  remaining products with Buy expanded, and asks you to finish them; the
  result screen offers **Finish in browser** for anything left uncertain.
- A gate handed to the private browser is now finished out of sight: a
  hidden Chromium walks the gate's own step slides the way a fan does (the
  follow and like links, whose provider pages are closed unread; the
  Connect of a Spotify, Deezer, Apple Music or Threads step, whose login
  popup comes back by itself when the profile is signed in there; the email
  slide, filled with the address from Settings) and presses its Download.
  Only a step no program can do alone - a provider asking you to sign in, a
  CAPTCHA, an email the profile lacks - opens a window, with every such gate
  of the batch in it, and there the same driver keeps walking the steps
  before and after the one that needs you. A page without those controls is
  only watched.
- Two opt-in test suites for the Bandcamp cart: `bandcamp_dom` replays
  recorded store pages in a real headless Chromium with no network, and
  `shop_mutate` performs one add/verify/remove on a name-your-price track.

### Fixed

- Legacy `pro.beatport.com` links are canonicalized to `www.beatport.com` and
  persisted when preparing a playlist, instead of targeting a retired host.
- Soundiiz imports now derive artist/title from promo-style SoundCloud titles
  instead of sending uploader names and `PREMIERE`/label noise, including compact
  separators, preview markers, and a third dash-separated label. Featured
  performers and remixers are sent as additional catalog artists. Exact
  Beatport track results replace stored release links for reuse by later imports.
- Beatport playlist handoff now creates a real temporary Soundiiz import with
  Beatport preselected instead of opening Soundiiz's marketing/tutorial page.
- Bandcamp batches verify `already_in_cart` against the global cart and finish
  on that cart page; a seller-specific side cart can no longer suppress an
  addition or leave Chromium showing the last product with an incomplete cart.
- The footer now measures the application width on its first composition, so
  clicking or focusing the table no longer makes additional shortcuts appear.
- Footer actions are grouped as `o`/`Shift+O` and `b`/`Shift+B`; Beatport and
  unmark are visible. Settings moves to `s`, and skip moves to `k`.
- The status bar scrolls sideways like the footer instead of clipping the
  store legend (a plain mouse wheel over it scrolls it, no Shift needed), and the track counts (`tracks · got · skipped · opened`) are
  gone from it; only the running job and the view's state remain on the right.
- Bandcamp cart verification now recognises the side cart as Bandcamp renders
  it (a row with the product link and an `x` delete control) and counts its
  rows, instead of looking for a "remove" link and a menubar badge that artist
  pages do not have. The first diagnostics dump from a real batch showed both
  additions sitting in the cart unrecognised. The manual-finish step also no
  longer crashes the batch on its progress label.
- Recorded Bandcamp pages (`tests/fixtures/bandcamp/`) now back the
  `bandcamp_dom` suite, and the test run keeps its data under a temporary
  directory instead of writing diagnostics into the real data folder.
- A Hypeddit gate that refuses the first unlock is retried once the way its own
  skip buttons do (`is_skippable=1`); pages offering alternative steps get the
  cheapest one (direct download, then click-through, then email). Gate flows
  are limited to two per host instead of one at a time overall.
- A Hypeddit gate that refuses the unlock, or one whose social steps are
  disabled in Settings, now opens in the private browser instead of failing;
  the failure message keeps the HTTP reason. A batch hands at most eight such
  gates to the browser per run.
- A Bandcamp batch whose clicks all verified no longer shows "Cart automation
  failed" when the final cart window cannot be shown; the additions are kept
  and the window problem is one warning row. The cause was the hidden work
  context being relaunched as a visible one on the same profile, which raced
  Chromium's profile lock. The work stays hidden; the finished cart and the
  manual-finish pages open in a separate visible browser carrying the same
  cookies, and a still-locked profile is retried before it is reported.
- Bandcamp click verification runs its count, side-cart and reload checks on
  separate clocks inside a 45-second budget and says which stage gave up,
  instead of one 30-second timeout shorter than the checks inside it.
- Quitting no longer waits on a background thread that cannot be stopped: the
  browser closes within three seconds and the process ends with the reason in
  the log, and a second Ctrl+C during shutdown exits at once.
- Opening a link or a store search no longer freezes the interface while the
  browser is being reached; on WSL that handoff could take twenty seconds.
- The README key tables now match the playlist browser: no phantom keys,
  every bound key listed, shifted keys shown as `Shift+…` in the footer and
  help, and the bulk-open confirmation names the right key.
- The browser gate driver looked for step buttons the desktop gate does not
  show - its steps are slides revealed by the sidebar's Download - so a gate
  handed to the browser had to be clicked through by hand, including the
  steps around a Spotify login. The driver now follows the slides.
- A download saved by a provider popup was never credited to the tab that
  opened it, and a popup was never recognised as one, because Playwright's
  `opener` is a method and the code read it as an attribute.
- A browser batch of gates no longer gives up on a still-open tab when one of
  the other rows was refused up front, and a batch that finds the private
  profile busy reports those refusals alongside the lock error instead of
  dropping them.
- A SoundCloud track URL with query parameters other than `in=` is no longer
  rewritten with a doubled `??`.
- A gate whose email slide also asks for a name no longer stalls the hidden
  browser on "Please enter your name.": the name from Settings is filled in
  with the address, and a profile still carrying the placeholder name sends
  the row to the window with that reason.
- The hidden browser no longer hands a gate to a window merely because
  Hypeddit sent the first visit after a download to its hot-or-not poll: the
  tab is pointed at the gate again and walked out of sight, so the window
  with its flashing provider popups only appears for a login or a CAPTCHA.
- In a hidden batch every gate after the first waited five minutes for a
  file that never came: Hypeddit refuses a download while the
  `filedownloading` cookie of the previous one is still set, and the next
  tab pressed its Download seconds later. The cookie is cleared before each
  Download now.

### Changed

- Textual is now pinned to the 8.x line; the playlist browser depends on its
  binding semantics and a few private hooks, so a major upgrade needs review.
- Track statuses are mirrored in memory after the first read, the playlist
  sidebar lists headers and reads a playlist's tracks only when it is opened,
  and single-row changes (a mark, an opened link, the playing marker, download
  progress) repaint that row instead of rebuilding the table. A large library
  starts, refreshes and searches without stutter.
- The local scan writes in batches, follows symlinked folders as before, and
  reports folders it could not enter instead of skipping them silently.
- The managed Chromium lifecycle (profile, display check, launch errors,
  installer) lives in one module, `browser_session.py`, shared by the cart,
  the gate fallback and the SoundCloud login instead of two copies.
- `cart.py` is split: its errors and dataclasses, store URL rules, product
  matching, page parsing and Beatport playlist helpers each live in their own
  module, re-exported from `cart` so nothing importing it changes.
- A repo-wide cleanup removed about a thousand lines without changing what
  the app does: dead helpers and never-read fields (`db.get_track_status`,
  `library.all_crates`, sync locator helpers and an unread session state in
  `cart.py`, `Palette.panel`, the always-`None` `direct_url`), duplicated
  code folded into one place (the redirect walker, the Chrome request
  headers, the browser profile directory, the Bandcamp Buy-dialog sequence,
  the cart "cancelled" and preflight-failure results, the store URL parser,
  the crate builders in the tests) and the longest functions split
  (`_preflight`, `_execute_store`, `run_batch`, the Hypeddit inspection and
  browser batch, `download_track`, the CLI login).
- A single Hypeddit browser download is now a batch of one: its five-minute
  limit is one deadline, counted once the automatic steps are done, instead
  of one per popup plus one for the download, and only tabs its own page
  opened are watched. A hidden pass always has that limit; a window without
  one stays as long as a tab stays open.
- The browser batch toast reads "Finishing N Hypeddit gates in the hidden
  browser; a window opens only for a step that needs you" instead of asking
  you to complete the tabs and close Chromium.
- The local file cache keeps only the path, modification time and normalised
  stem; the unread size, artist and title columns are dropped, so an
  existing cache is rebuilt by the next scan.
- The redirect and browser-download refusals read "Redirected to an unsafe
  address" and "That link returned a web page rather than an audio file".
- A SoundCloud login that completes while a download still holds the old
  client no longer queues a hidden client swap: it says "Signed in to
  SoundCloud, but a download is still running on the old login: let it finish
  or stop it with ctrl+x, then press w again" and leaves the retry to you.
- The dig reports only through its job: when another job starts during a
  dig, the status line and spinner follow the newer job.
- The "more than 20 tabs, press again" confirmation is one key: pressing
  `Shift+O`, then `Shift+P`, then `Shift+O` again asks again instead of
  opening.
- Escape on the confirm, manual-cart and gate-profile dialogs answers "no"
  through one shared action; the footer keys are unchanged.

### Removed

- The Spotify integration (`dj-digger auth spotify`, the PKCE login, and the
  library writes for Hypeddit `sp` steps). Hypeddit clears its Spotify step
  through its own OAuth app and server session, so a user's own Spotify login
  could never satisfy the gate; the step is now reported like the other
  click-throughs. A saved `spotify.json` is no longer read and may be deleted.
- The unused synchronous cart stack in `cart.py` (about a thousand lines with
  no caller since the asynchronous session took over) and its tests.
- The unreachable Beatport cart code (cart page, add-to-cart control, cart
  membership check, login). Beatport items have been playlist entries since
  0.15.0; the code that would have clicked a Beatport cart never ran.

## 0.15.0

### Added

- Beatport results can now be saved as a non-overwriting plain-text playlist,
  copied for a Soundiiz plain-text import, and handed to Beatport's official
  Soundiiz transfer page. Exact track URLs are preferred; release-page and
  blocked lookups fall back to artist/title matching.
- Explicitly selecting both Bandcamp and Beatport now sends each eligible track
  to both workflows instead of treating Beatport only as Bandcamp's fallback.
- Store-cart batches now have live progress, an editable review screen, grouped
  results, safe retry for failures known to precede a click, and Settings actions
  for checking, opening, or explicitly resetting the dedicated store session.
- Cart diagnostics now record lifecycle phases, redacted navigation/status,
  per-track outcomes, browser HTTP/console signals, and aggregate counts in
  `--log-file` without recording URL queries, credentials, or raw console text.
- Bandcamp moved-link recovery now uses its visible autocomplete, accepts only an
  exact canonical track, and inspects at most three returned album pages without
  entering the CAPTCHA-protected full search page.

### Fixed

- The purchase-review table now shrinks in short terminals instead of pushing
  its Continue button off-screen. A visible instruction and `Enter` shortcut
  make the intentional review pause distinct from a stalled batch.
- Natural decoder EOF no longer escapes through miniaudio's CFFI callback as
  `RuntimeError: generator raised StopIteration`. Playback events are tagged by
  generation, so a late callback after seek or stop cannot advance the wrong row.
- Bandcamp product discovery now tolerates its current split between public DOM,
  `TralbumData`, and structured metadata. It also dismisses the necessary-cookie
  footer, accepts an exact unique trailing title across artist aliases, and does
  not require an account login for a cookie-backed cart.
- Bandcamp storefront homepages and undeclared name-your-price values no longer
  masquerade as global structure failures and stop the remaining queue. Current
  DOM identity/price now wins over stale `?action=download` metadata, safe plain
  HTTP store links are upgraded after domain validation, and common `//`, quoted
  premiere, and `feat.` title forms can be matched exactly.
- Beatport's production verification no longer loops in managed Chromium or
  aborts an unrelated Bandcamp batch when its server rejects Playwright.
- Bandcamp cart verification now checks the post-click item count, opens the real
  side-cart control instead of the SVG sprite, inspects its current rows, and
  performs one final reload check. A still-unverified page stays open instead of
  disappearing, and no second add click is issued.
- Bandcamp cart membership now requires a visible removable row with the exact
  canonical track, preventing hidden page data from marking the rest of a batch
  as already added. Flexible prices default to the verified minimum, remain
  editable with `E`, and fail before mutation if Bandcamp withdraws its price
  input.
- Bandcamp storefront recovery now reaches the global autocomplete instead of
  reopening the same label homepage. Beatport release rows can use their track
  slug to preserve an exact mix or remix before the target page is revalidated.

### Changed

- Whole-list opening now follows the existing Shift convention: `o` opens the
  highlighted track and `Shift+O` opens all visible tracks. The local-file marker
  is a monochrome `▣` at the start of the first marker column, and the collapsed
  error banner occupies the full first row above the TUI.
- Store work uses one persistent Chromium profile: preflight and Bandcamp
  mutation stay headless with two bounded pages, then the same profile is
  relaunched visibly only for the final Bandcamp cart.
- Beatport public metadata is preflighted only to improve playlist accuracy.
  Managed login and cart mutation are no longer attempted; Settings now exposes
  only the Bandcamp store session. Exact numeric Beatport track URLs go straight
  to the transfer playlist without starting Chromium.
- Product-checking progress now says that nothing is added until review, and an
  album-only Bandcamp track is reported explicitly rather than substituting a
  full album purchase.

## 0.14.0

### Added

- The player bar now carries a one-row control strip under the waveform:
  previous, play/pause and next buttons, the track title, the clock, a volume
  slider you can click or drag, and a close button. `ctrl+w` closes it from the
  keyboard. Play/pause there acts on the track that is playing rather than on
  the row the cursor sits on.
- `--log-file PATH` writes the log to a file, with timestamps, instead of to the
  terminal - and the crate browser no longer silences the log when it is given.
  Textual draws the interface on standard error, so a log line under the browser
  landed in the middle of the track list and redirecting the shell's stderr to
  catch it took the interface with it. There was no way to keep a log of a TUI
  session before this.

### Fixed

- Any exception out of a player operation - not just the ones the player has
  names for - is now shown as a player message instead of escaping through
  Textual's message pump and taking the TUI down.
- A TUI crash is written to `--log-file` with its traceback before the screen
  is torn down, and the log captures native (segfault-level) crashes too via
  `faulthandler`. Previously a crash could erase its own report along with the
  alternate screen, leaving the log ending mid-sentence.
- A backend that refuses to start or stop the audio device no longer crashes the
  app. Pressing play twice in quick succession was enough: miniaudio raises its
  own numbered error out of `device.start`, and nothing on the interface side
  caught it, so it came out through Textual's message pump. It is now reported
  the same way a missing audio device already was, and the device is rebuilt on
  the next attempt.
- Starting a track no longer opens its stream from the interface thread. The
  connect waits up to thirty seconds for a slow CDN, and the whole TUI was
  frozen behind it; it now happens on the worker that already fetches the rest
  of what the track needs.

### Changed

- The error banner opens collapsed to one summary line with the error count.
  Click it to read the messages, click it again to fold them away. It used to
  arrive with every message on screen, taking half the terminal over the list
  you were reading. Its close control is now a three-column `✕` rather than a
  yellow block, the bar stops at 88 columns instead of spanning the terminal,
  and the message list has a thin scrollbar in the banner's own colours.
- The transport buttons are bold text glyphs (`◀◀`, `▶`/`❚❚`, `▶▶`, `✕`) on
  three-row translucent chips - no emoji, which every terminal renders in its
  own colour and size, and no theme-coloured background. A terminal cell cannot
  be scaled, so chip size and weight are what make them read as controls; the
  volume slider's speaker went the same way (`♪` / `Ø`).
- The playhead pulse is two columns in shades of cyan instead of a twelve-column
  band reaching bold white, which read as the tail of the waveform flickering
  thirty times a second.
- The player bar is a row shorter overall and its waveform is twice as tall:
  the title, the clock and the play/pause glyph moved down into the control
  strip, and the four rows that frees go to the picture.
- The crate sidebar heading is centred, with a blank row between it and the
  first crate name.

## 0.13.3

### Changed

- Large internal cleanup after a full audit: dead code removed, duplicated
  logic consolidated, hand-rolled helpers replaced with standard-library
  equivalents, the seven largest functions split, and undocumented constants
  annotated. No user-facing behaviour changed, with the one exception below.
- `hypd.it` gate links now share the same Chromium fallback route as
  `hypeddit.com` ones when automatic resolution needs a browser step.

### Fixed

- Bandcamp and Beatport batches now keep one Chromium session from manual login
  through preflight, confirmation and cart addition. All candidate tabs are
  created up front and reused, so the browser no longer closes between phases or
  replaces the whole batch in one short-lived tab.

### Removed

- The one-time import of pre-0.9 data files (`state.json`, `crates/*.json`)
  no longer runs, and a crates table written by 0.8 or earlier is dropped and
  recreated in the new shape. Upgrading from 0.8 or earlier directly to this
  version therefore starts with an empty library; go through any 0.9–0.13
  release first if you need those crates and statuses carried over.

## 0.13.2

### Added

- The track context menu can copy a matched file from another library folder,
  such as `Music`, into the current playlist's download folder. The copy runs
  off the UI thread, preserves the source, avoids overwrites and is finalized
  atomically.

### Fixed

- The local-file cache now removes deleted paths and validates every cached
  match before exposing it, so missing files no longer retain a folder badge or
  a copyable dead path.
- Automatic `got` statuses now remember the exact backing file. Deleting that
  file clears only the status that depended on it, while a manually assigned
  `got` remains an independent ownership decision.
- Legacy stale cache matches from earlier releases are reconciled during the
  next scan, including playlists removed and imported again.

## 0.13.1

### Fixed

- Batch downloads no longer wait for every Hypeddit preflight before starting.
  Each worker checks its own link and immediately continues with that track,
  while all queued rows show progress as soon as the batch begins.
- A single left click now only selects a track. Opening remains available via
  `o`, `Enter` or a double click.
- Confident local-file matches automatically mark tracks as `got`, including
  stale `opened` or `skipped` statuses, and `w`/`W` avoid downloading verified
  files that are already on disk.
- Downloads are stored below the configured root in a safely named playlist
  folder instead of placing every file directly in the root Downloads folder.

## 0.13.0

### Changed

- Hypeddit browser fallbacks now share one private Chromium context and open
  every required gate in its own tab. The batch remains open for manual work,
  can be stopped with `Ctrl+X`, and only marks tracks as got after a validated
  atomic file save.
- Spotify ART and PLAY gate actions now use the post-February-2026
  `PUT /me/library` API with validated Spotify URIs. Three-part PLAY values are
  supported, missing scopes require reauthorization, and each URI gets one
  non-retried mutating request.

### Fixed

- Hypeddit gate pages no longer mistake audio URLs from Hot-or-Not
  recommendations, advertisements, widgets or SoundCloud previews for the
  current track. Active gates obtain their file only from `/gate/download/ul`.
- Dormant CAPTCHA assets no longer turn smartlinks into challenges. Visible
  challenges and explicit flow responses remain typed manual CAPTCHA outcomes
  and are never bypassed.
- Hypeddit hubs are expanded from purchase fields, extra links and descriptions,
  including persisted crates immediately before download. Pure wrappers are
  replaced with purchase stores, nested gates or `no-link` as appropriate.
- Gate failures retain authentication, CAPTCHA, manual, protocol, rejection and
  download types through the batch summary. All errors are shown, and bracketed
  Textual notifications render literally instead of raising `MarkupError`.

### Security

- SoundCloud, Instagram, YouTube and other click-through gate steps are recorded
  only in `skip_gate_steps[]`; no external social link is opened and no
  click-through provider follow, like, repost, comment or CAPTCHA bypass is
  attempted. Spotify ART/PLAY remains the explicit OAuth-backed exception above.
- SSRF and redirect checks, download size limits, HTML sniffing, owner-only
  temporary files, atomic finalization, telemetry best-effort limits and the ban
  on retrying mutating Hypeddit or Spotify requests remain in force.

## 0.12.1

### Fixed

- Restored the legacy `shop` extra as an empty compatibility alias. Playwright
  remains installed as a core dependency, while existing `pipx` and `uv tool`
  update commands using `dj-soundcloud-digger[shop]` no longer emit a warning.

## 0.12.0

### Added

- **Current Hypeddit desktop-gate support.** One parser now distinguishes gates,
  smartlinks, hybrid pages, nested gates, direct files, expired pages and
  challenges. SoundCloud and other link-only steps are completed without
  mutating provider APIs; Spotify artist and public-playlist steps use their
  documented OAuth endpoints.
- **Safe Chromium fallback.** CAPTCHA, provider OAuth and unknown future steps
  can continue in the existing private Playwright profile. Interactive and
  batch downloads share one profile lock and never copy API tokens into pages.
- **Guided SoundCloud login.** CLI and TUI flows can open the private Chromium
  profile, verify its OAuth cookie and retry only downloads waiting for login.
- **Read-only Hypeddit contract suite.** An opt-in live test classifies all 60
  reported pages using GET requests only, with no profile, OAuth or download
  submission.
- **Track context menu.** Right-clicking a row opens its Open, Copy path, Got,
  Skip, Clear and Remove actions instead of opening the link immediately.

### Security

- Gate and download URLs reject credentials, lookalike hosts, unsafe schemes
  and private or link-local literal addresses. Every page and file redirect is
  checked before the next request and capped at five hops.
- Downloads use owner-only temporary files, enforce the byte limit while
  streaming, sniff HTML masquerading as audio, serialize final-name selection
  and atomically replace only fully validated files.
- CSRF values, OAuth state, email, comments, query strings and signed download
  URLs are excluded from diagnostics. Hypeddit telemetry is best-effort and
  mutating POSTs are never retried.

### Fixed

- Hypeddit no longer calls obsolete mobile endpoints or performs SoundCloud
  follow, like, repost or comment actions that the current desktop gate does not
  verify. Declared link steps are represented exactly in `skip_gate_steps[]`.
- Hybrid smartlinks retain their downloadable gate while exposing shop links;
  wrappers such as `link/ky9i8z` become the nested gate plus Beatport and
  Bandcamp destinations.
- Batch downloads serialize gate resolution per provider, stream up to four
  validated files concurrently, keep failed tracks new and summarize failures
  as authentication, CAPTCHA, manual, protocol or rejection errors.
- Email and comment data are submitted only when the manifest requires them;
  lifetime-fan options default to opt-out.

## 0.10.0

### Added

- **Spotify-backed Hypeddit gates.** `dj-digger auth spotify login` uses
  Authorization Code with PKCE, stores refresh credentials privately and can
  complete the artist action declared by a gate through Spotify's current
  library endpoint. Status and logout commands are included.
- **Chromium installation on demand.** Store-cart support is installed by
  default. When Playwright's matching browser build is absent, the TUI asks
  before downloading it in the background and resumes the original preflight.

### Security

- Spotify login binds its temporary callback to `127.0.0.1`, validates OAuth
  state, requests only `user-follow-modify`, refreshes tokens without a client
  secret and writes credentials atomically with owner-only permissions.
- Hypeddit no longer submits the reserved placeholder email or claims unknown
  Spotify actions. Disabling gate social actions prevents Spotify mutations.

### Fixed

- The TUI error banner keeps bracketed messages literal, has a visible close
  button and is no longer corrupted by application logs writing behind it.
- Single and batch downloads stay at `0%` while a link or gate is being
  resolved instead of reporting fictional progress.
- SoundCloud artist downloads without a concrete URL use the authenticated
  download endpoint and distinguish missing, rejected and failed credentials.
- Hypeddit step and download failures now produce actionable errors. Smart-link
  pages such as `l87679` keep their Beatport destination without retaining a
  false gate link.
- Store carts distinguish a missing Chromium build from missing Linux system
  libraries, stop the installer process tree on cancellation and recognise an
  existing Bandcamp session after its login redirect.

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
