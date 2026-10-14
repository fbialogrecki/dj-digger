# Privacy Policy for the dj-digger browser extension

Last updated: 2026-10-14

The dj-digger browser extension (Chrome, Chromium, Brave, Edge, Firefox) is the
companion of the dj-digger desktop app. It lets the app complete browser steps
in your own browser and lets you control the app from the toolbar. This policy
covers the extension. The developer runs no server for it: **the extension
sends nothing to the developer**, has no analytics, no tracking and no ads.

## Where data goes

The extension exchanges data only with:

1. **The dj-digger desktop app on your own computer**, through the browser's
   Native Messaging channel (a program the app registers with your browser). This
   data never leaves your computer through the extension.
2. **The website you are completing a step on**, in a tab the extension opened
   for the app, exactly as if you used that website yourself: Hypeddit
   (hypeddit.com, hypd.it), ToneDen (toneden.io), Bandcamp (bandcamp.com),
   Beatport (beatport.com) and SoundCloud (soundcloud.com, sign-in page only).

The extension itself makes no network request of its own. It does not work on
any other website.

## What data is handled, and why

- **Pages of its own tabs on the sites above** (text and page content): read to
  find and press the right buttons on download gates and in store carts. Results
  go to the desktop app. When an add-to-cart click cannot be verified, the app
  saves a screenshot and a copy of the page locally for troubleshooting (e-mail
  addresses and link parameters removed; the last ten are kept in the app's data
  folder).
- **Your SoundCloud sign-in cookie** (`oauth_token`): read only when you sign the
  desktop app in to SoundCloud, and handed to the app, which stores it locally and
  uses it with SoundCloud's own API. No other cookie is read. The extension also
  deletes one Hypeddit cookie (`filedownloading`) between gate downloads, because
  while it is set Hypeddit refuses the next download.
- **Your name, e-mail address and comment texts** that you entered in the
  desktop app's Settings: filled into a download gate's form when the gate asks
  for them, and so sent to that gate's site (Hypeddit or ToneDen), which may pass
  them to the artist as the gate's terms say. Follow, like, repost and comment
  steps, and connecting Spotify, Deezer or Apple Music to a gate, are done on the
  accounts signed in to your browser only while "Allow social actions" is on in
  the app; turn it off to do those steps yourself.
- **Downloads started by its own gate tabs**: the file location is given to the
  app, which copies the file to your crate folder; the browser's copy and its
  download-history entry are then removed. Your other downloads are ignored.
- **The address of your current tab**: read once when you open the toolbar
  panel, to see whether it is a SoundCloud playlist or profile. Only if you then
  choose Import (or "Dig in dj-digger" in the right-click menu) is a clean
  `https://soundcloud.com/...` link sent to the app.
- **Player state from the app** (track title, artist, position, a small cover
  picture, colours, the running job and how many gates need you): shown in the
  toolbar panel.

The extension never reads or changes your other tabs, never opens checkout or
payment pages, and never fills password or payment fields.

## How data is stored

The extension keeps only temporary session data in the browser (the list of its
own tabs, the link status and the player state), which the browser deletes when it
closes. Nothing is synced to your browser account. What the desktop app stores
on your computer is described in its documentation; the app also sends nothing to
the developer.

## Data sharing

Data is not sold, not shared with anyone, and not used for advertising,
profiling, creditworthiness or any purpose other than the features above. The
only third parties that receive anything are the sites listed above, and only
what you would send them yourself when using the same step in your browser.

## Data retention and deletion

Session data is gone when the browser closes, or when you remove the extension.
Downloaded gate files stay where the app saved them. To stop the extension from
working entirely, disable or remove it in your browser's extension settings, or
choose "Disconnect browsers" in the desktop app.

## Changes to this policy

Changes are published in this file, in the project's public repository, with a
new "Last updated" date; the extension's version history notes them.

## Contact

Questions about privacy: open an issue at
https://github.com/fbialogrecki/dj-digger/issues
