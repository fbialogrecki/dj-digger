# Chrome Web Store Listing — dj-digger

> Draft for extension 1.4.5 (unreleased), updated 2026-11-20.
>
> Copy-paste source for the Chrome Developer Dashboard. Step-by-step upload
> instructions (Polish): [docs/store/PUBLISHING-pl.md](docs/store/PUBLISHING-pl.md).
> Upload file: `dist/extension/dj-digger-chrome-<version>.zip` from
> `python3 scripts/package_extension.py`.

## Store Listing

**Extension Name**

```
dj-digger
```

**Short Description** (88 / 132 characters; the dashboard takes it from the
package's `manifest.json` "description", so change it there)

```
Lets the dj-digger desktop app drive download gates and store carts in your own browser.
```

**Detailed Description** (plain text, paste as is)

```
dj-digger is the browser companion of the free dj-digger desktop app for DJs. It lets the app do the browser steps of crate digging in your own browser, on the sites you are already signed in to, and puts the app's player in your toolbar.

Install and start the dj-digger desktop app once on the same computer (Windows, macOS, Linux) so it can register with your browser. The extension can then open the app on request: https://github.com/fbialogrecki/dj-digger
When the app is not installed (or too old), the extension's status page and toolbar panel say so and offer a Download button that opens the app's latest release on GitHub: https://github.com/fbialogrecki/dj-digger/releases/latest

WHAT IT DOES
• Free-download gates: when the desktop app cannot finish a Hypeddit or ToneDen download gate by itself, it finishes it here, in your browser, and the downloaded file goes to the app.
• Store carts: the app adds the tracks you picked to your Bandcamp cart, or looks tracks up on Beatport, in your browser. It never checks out or pays: checkout and payment pages are closed, and buying stays your decision.
• SoundCloud sign-in: the app can sign in to SoundCloud with the session your browser already has, so you do not have to paste anything.
• Toolbar panel. Open or show dj-digger, control playback, see or cancel the running job, and answer the app when download gates need you. While desktop playback is paused or disconnected, the open panel can show the track playing in a SoundCloud tab.
• SoundCloud import. Use the panel on a playlist or profile, or the import button in a playlist header or tile. Choosing Import opens the desktop app if it is closed.
• SoundCloud playlist editing. When you confirm a create or add action in the desktop app, the extension completes SoundCloud's playlist form in a temporary tab.

HOW TO USE
1. Install and start the dj-digger desktop app. It connects itself to this browser.
2. Install this extension. Its status page shows "Desktop app link" and "Site access". Installed the extension first? The status page and the panel link to the app's download.
3. Use the app as usual. When it needs the browser it opens its own window or a background tab, and closes it when done. The toolbar can read the current track from a playing SoundCloud tab.

SOCIAL ACTIONS ON DOWNLOAD GATES
Download gates ask for a follow, a like, a repost, a comment or your e-mail address in exchange for a free download. The desktop app setting "Allow social actions" decides whether the app may do these steps for you, on the accounts you are signed in to in this browser, and with the name, e-mail address and comment texts you entered in the app's Settings. Turn it off in the app to finish such gates yourself.

PRIVACY
Nothing is sent to the developer or to any server of the developer. There are no analytics and no ads. The extension talks to the dj-digger app on your own computer. It performs website actions in its temporary tabs on Hypeddit, ToneDen, Bandcamp, Beatport and SoundCloud. On the SoundCloud pages you open, it adds import buttons to playlist headers and verified playlist tiles. Privacy policy: https://github.com/fbialogrecki/dj-digger/blob/main/PRIVACY.md

PERMISSIONS
• "Read and change your data on hypeddit.com, toneden.io, bandcamp.com, beatport.com, soundcloud.com": needed to fill in download gates and carts in the tabs the app opens; other sites are never accessed.
• "Communicate with cooperating native applications": the link to the dj-digger desktop app.
• "Read your browsing history" (shown for tab access): used to follow the extension’s own tabs. Opening the toolbar also checks for a SoundCloud import and, when the desktop is idle, a playing SoundCloud track.
• "Manage your downloads": to hand the files of download gates to the app.

SUPPORT
Questions and bug reports: https://github.com/fbialogrecki/dj-digger/issues

Version 1.4.0: first store release.
```

**Category**: Productivity → Tools
(alternative: Lifestyle → Entertainment; Tools fits "companion of a desktop app" best)

**Single Purpose**

```
Lets the dj-digger desktop app on the same computer complete download gates, store carts and SoundCloud sign-in in the user's own browser, and control that app from the toolbar.
```

**Primary Language**: English

## Graphics & Assets

| Asset | Dimensions | Status | Filename |
|-------|-----------|--------|----------|
| Store Icon | 128×128 PNG | ✅ Ready | `dj_digger/webext/extension/icons/icon-128.png` |
| Screenshot 1 | 1280×800 | ✅ Ready | `docs/store/screenshots/1-player.png` |
| Screenshot 2 | 1280×800 | ✅ Ready | `docs/store/screenshots/2-import.png` |
| Screenshot 3 | 1280×800 | ✅ Ready | `docs/store/screenshots/3-jobs-gates.png` |
| Screenshot 4 | 1280×800 | ✅ Ready | `docs/store/screenshots/4-welcome.png` |
| Small Promo Tile | 440×280 | ✅ Ready | `docs/store/screenshots/promo-small-440x280.png` |
| Marquee Promo Tile | 1400×560 | ⬜ Not needed (unlisted) | |

### Screenshot Notes

The real `popup.html` and `welcome.html` with sample state ("Midnight Groove",
"DJ Example", generated artwork), framed on a dark branded background:
1. player panel while a track plays, 2. import offer on a SoundCloud playlist,
3. "3 gates need you" plus a running import job with Cancel, 4. status page.
Regenerate after UI changes:
`uv run --extra gui python scripts/store_screenshots.py dj_digger/webext/extension docs/store/screenshots`.

## Permissions Justification

Dashboard → Privacy practices tab. One field per permission; host permissions
share one field (text at the end of the table).

| Permission | Type | Justification |
|------------|------|---------------|
| nativeMessaging | permissions | Connects to the native host `com.github.fbialogrecki.djdigger` on the same computer. The host starts the installed desktop app when the user selects Open or Import. The channel carries browser tasks and results, player commands, and the SoundCloud playlist link and date selected for import. |
| tabs | permissions | Opens, navigates, mutes, focuses and closes the tabs and windows it creates for the desktop app, follows popups those tabs open (provider sign-in windows) to close them with the session, and reads the address and load state of these own tabs only. A finished cart tab is handed over to the user. An open toolbar may also find an audible soundcloud.com tab for its read-only track display. Also takes a screenshot of an own store tab for the app's local cart diagnostics when an add-to-cart click cannot be verified. |
| scripting | permissions | Runs bundled page functions in temporary tabs to complete download gates, store carts, and the SoundCloud Add to playlist form for changes you confirm in the app. For the open toolbar, reads current track metadata only from the top frame of a playing SoundCloud tab. While a hidden gate session runs, registers the bundled script for Hypeddit and ToneDen frames inside the extension's offscreen document. |
| downloads | permissions | Recognises the file a download gate in an own tab starts, reports its location to the desktop app (which copies it into the user's crate folder), reports unsafe downloads or a "Save as" prompt as blocked instead of accepting them, and removes the browser's copy and history entry once the app has taken the file. Downloads not started by its own tabs are ignored. |
| cookies | permissions | Exactly two hard-coded operations: reads SoundCloud's `oauth_token` cookie when the user signs the desktop app in to SoundCloud (the app then uses SoundCloud's API with it), and deletes Hypeddit's `filedownloading` cookie between gate downloads, because while it is set Hypeddit refuses the next download. No other cookie is read, written or deleted. |
| webRequest | permissions | Observes only (no blocking, no modification) the responses and redirects of its own tabs on the allowed sites, to learn the HTTP status of a page it loaded and to match a starting download to the own tab that requested it. Requests of other tabs are ignored. |
| webNavigation | permissions | Knows when a page in an own tab finished loading or failed, so a step waits for the page; and closes an own tab the moment it heads for a checkout or payment page of Bandcamp or Beatport, so the extension can never take part in a purchase. |
| storage | permissions | Session storage only (cleared when the browser closes): the list of its own tabs and windows (to clean them up after a restart), the link status shown on the status page and the player state shown in the toolbar panel. Nothing is synced and nothing is stored permanently. |
| alarms | permissions | A 30-second alarm that reconnects to the desktop app after the app was closed or the background was stopped. |
| offscreen | permissions | Runs download gates (Hypeddit, ToneDen) in hidden frames of an offscreen document (reason IFRAME_SCRIPTING), so no browser window pops up during a hidden gate batch; some desktops cannot keep a browser window hidden. Gate media is muted there. |
| activeTab | permissions | When the user opens the toolbar panel, its address is read once to decide whether the page is a SoundCloud playlist or profile that can be imported into the app; nothing else of the page is read. |
| contextMenus | permissions | Adds "Dig in dj-digger" to the right-click menu on SoundCloud pages and links, which imports that playlist, track or profile into the desktop app. Shown only while the app is running. |

**Host permissions justification** (one field):

```
Each host is a site the dj-digger desktop app works with, and the extension uses these hosts for the following tasks:
- hypeddit.com, www.hypeddit.com, hypd.it, www.hypd.it, toneden.io, www.toneden.io: free-download gates. The extension opens the gate, completes its steps (e-mail form, follow/like/repost/comment steps when the user allows social actions in the app) and starts the download.
- bandcamp.com and *.bandcamp.com (artist pages are subdomains): adds the tracks the user picked in the app to the user's own Bandcamp cart. Checkout and payment pages are never opened; buttons named like checkout, pay or buy now are never clicked (except Bandcamp's "Buy Digital Track/Album", which only opens the price and add-to-cart panel).
- beatport.com and *.beatport.com: looks up tracks and adds them to the user's Beatport cart; checkout, payment and billing pages are never opened.
- soundcloud.com for sign-in and reading the oauth_token cookie when the user connects their SoundCloud account. A bundled content script adds "Import to DJ digger" to playlist headers and tiles with their own playlist link. When clicked, it hands off the clean playlist address to the desktop app. For playlist changes confirmed in the app, a dedicated script checks the signed-in account and operates SoundCloud's Add to playlist form in a temporary track tab.
```

**Are you using remote code?** No. All JavaScript is in the package; there is no
`eval`, no remote script, and page functions are the extension's own bundled ones.

## Privacy & Data Use

### Data Collection

The extension has no server and sends nothing to the developer. All data it
handles goes between the browser and the dj-digger desktop app on the same
computer (Native Messaging), or to the site the user is completing a step on.
The dashboard form asks what the item "collects or uses", so declare what it
handles:

| Data Type | Handled? | Off device? | Purpose | Third parties? |
|-----------|----------|-------------|---------|----------------|
| Personally identifiable info | Yes | Only to the gate site | The name and e-mail address the user entered in the desktop app are filled into a download gate's form when the gate asks for them | The gate site (Hypeddit / ToneDen), at the user's request, as part of that gate |
| Health info | No | | | |
| Financial and payment info | No | | Payment fields are never filled, checkout is never opened | |
| Authentication info | Yes | No | SoundCloud `oauth_token` cookie handed to the local desktop app for its SoundCloud sign-in | No |
| Personal communications | Yes | Only to the gate site | The comment text a gate asks for, picked from the comment list the user configured in the app, is filled into the gate (only with "Allow social actions") | The gate site, which posts it with the user's account |
| Location | No | | | |
| Web history | No | | The panel checks the current tab for an import and can read track metadata from a playing SoundCloud tab. It does not retain browsing or listening history. | |
| User activity | No | | No clicks, keystrokes or browsing are monitored | |
| Website content | Yes | No | Text and HTML of own tabs on the allowed sites, read to complete gates and carts; page copies and screenshots for cart diagnostics are saved by the desktop app locally (e-mail addresses removed) | No |

### Data Use Certification

- [x] Data is NOT sold to third parties
- [x] Data is NOT used or transferred for purposes unrelated to the item's single purpose
- [x] Data is NOT used or transferred to determine creditworthiness or for lending purposes

## Privacy Policy

**Privacy Policy URL**: https://github.com/fbialogrecki/dj-digger/blob/main/PRIVACY.md

The repository is public; the URL is live once `PRIVACY.md` is on `main`
(verify it opens without signing in before submitting).

## Distribution

**Visibility**: Unlisted (installable by anyone with the link, not in search)
**Regions**: All regions
**Pricing**: Free

## Developer Info

**Publisher Name**: Filip Białogrecki
**Contact Email**: _owner to fill in (shown publicly, must be verified in the dashboard)_
**Support URL**: https://github.com/fbialogrecki/dj-digger/issues
**Homepage URL**: https://github.com/fbialogrecki/dj-digger

**Test instructions for the reviewer** (dashboard field, optional but recommended):

```
Install the desktop app from https://github.com/fbialogrecki/dj-digger (Windows and macOS installers under Releases, or "pipx install 'dj-sc-digger[gui]'" and run dj-digger-gui). Start it once to register the native host. The extension's options page shows the link under "Desktop app link". Close the desktop and select "Open dj-digger" in the toolbar panel. Close it again, open a public SoundCloud playlist, and choose "Import this playlist into dj-digger" in the panel or "Import to DJ digger" in the playlist header. The desktop opens and imports the playlist. A SoundCloud account is only needed for account-specific imports and confirmed playlist changes.
```

## Version History

Package uploads are automated: a published GitHub release runs
`.github/workflows/chrome-web-store.yml`, which uploads the Chrome zip and submits
it for review when `manifest.json`'s version is newer than the store's (setup in
`docs/store/PUBLISHING-pl.md`, section 8). Listing text, screenshots and
justifications are still edited by hand in the dashboard.

| Version | Date | Changes | Status |
|---------|------|---------|--------|
| 1.4.0 | 2026-10-14 | First store submission: gates, Bandcamp/Beatport carts, SoundCloud sign-in, toolbar player panel and SoundCloud import | Draft |
| 1.4.1 | — | Update: a navigation Firefox reports as aborted or redirected is followed like Chrome's ERR_ABORTED; site access granted or removed in the browser's settings is reported to the app at once. No permission changes | Superseded by 1.4.2 |
| 1.4.2 | — | Update: an aborted navigation with no further event asks the tab (loaded, still loading, or retried once from a blank tab); popups of own tabs are also recognised by webNavigation's source tab (Firefox popups have no opener) and closed with the session. No permission changes | Superseded by 1.4.3 |
| 1.4.3 | — | Update: the status page and the toolbar panel tell a missing (or too old) desktop app from a stopped one and offer a Download button to the app's latest GitHub release; on soundcloud.com an "Import to DJ digger" button (track and playlist pages, ⋯ menus, track and playlist rows) sends that track or playlist to the running app, and single tracks can be imported. New content script on soundcloud.com, an already declared host; no permission changes | Submitted for review 2026-10-27 |
| 1.4.4 | — | Update: the "Import to DJ digger" icon also appears on each track row of a playlist or station track list (SoundCloud's `.trackItem` rows). No permission changes | To upload |
| 1.4.5 | — | SoundCloud playback artwork and read-only metadata in the open toolbar, and confirmed desktop activation. Existing permissions. | Unreleased |

## Review Notes

### Review risk notes

- **In-depth review is likely.** `cookies`, `webRequest`, `nativeMessaging`,
  `scripting`, `tabs` and `downloads` together, plus host access to five sites,
  are flagged for manual review; first review can take days to weeks. The
  justifications above are specific; keep them in sync with the code.
- **Social actions on gates (highest policy risk).** With "Allow social actions"
  on (the app's default is **on**, stated next to the setting), the app makes the
  extension fill a gate's comment field with a text picked at random from the
  user's comment list (defaults like "Fire!" or "Banger!"), press the gate's
  follow/like/repost links and Connect buttons, after which Hypeddit or ToneDen
  follows, reposts or comments with the user's own SoundCloud/Spotify account. A
  follow link is "ticked" by the gate's own click handler without the provider
  page being opened. The Spam and Abuse policy says (checked 2026-10-14,
  https://developer.chrome.com/docs/webstore/program-policies/spam-and-abuse):
  "We don't allow extensions that send messages on behalf of the user without
  giving the user the ability to confirm the content and intended recipients."
  A posted comment fits that wording: the user configured the texts and the
  switch, but does not confirm each comment or recipient. Reviewers may also read
  automated follows/likes as fake engagement. Describe it exactly as in the
  detailed description (user's own accounts, the user's own texts, a switch in
  the app). To lower the risk before submitting, consider: default the switch to
  off, or ask before posting a comment (show text and artist), or leave comments
  out of the browser path. This is the owner's decision; nothing was changed.
- **Single purpose.** Gates, carts, sign-in, player and import are several
  features; they are presented as one purpose (the companion that lets the
  desktop app act in the browser and be controlled from it). A reviewer may still
  ask for a narrower statement.
- **Minimum functionality.** Without the desktop app the extension can show SoundCloud browser playback in its
  panel. The status page and panel indicate a missing app with a Download
  button to the app's latest GitHub release (the welcome page opens once on
  install; nothing else opens on its own). The description says so in its first
  lines and the test instructions tell the reviewer how to get the app.
- **Other sites' names.** Hypeddit, ToneDen, Bandcamp, Beatport and SoundCloud are
  named descriptively (what the extension works with), not in the name or icon.
- **Store purchases.** The cart flow never checks out; worth stating if asked.
- **Store ID.** The store assigns its own extension ID (the packaged manifest has
  no `key`). The desktop app's native host accepts it only after
  `CHROME_STORE_ID` in `dj_digger/webext/register.py` is set and that app version
  is released and started once; until then the store install shows "not
  connected".


### Known Issues / Limitations

- Needs the desktop app; Flatpak/Snap browsers cannot start the native host.
- The desktop app's Connect browser dialog still explains "Load unpacked"; point
  it to the store link once the item is live.

### Rejection History

| Date | Reason | Fix Applied | Resubmitted |
|------|--------|-------------|-------------|
