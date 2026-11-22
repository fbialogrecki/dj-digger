# Install dj-digger on Windows, Mac or Linux

Windows and Mac installers include the components needed to run the app.
On Linux, one terminal command installs the desktop and its dependencies.

**Choose your computer:** [Windows](#windows) · [Mac](#mac) · [Linux](#linux)

> **Before you start**
>
> dj-digger is a free, open-source hobby project. Its desktop downloads are
> currently **preview versions**: you can try them, but you may encounter bugs.
> They do not currently have a verified publisher signature, and the Mac app has
> not been submitted to Apple's notarization service for automated checks.
> Your computer may therefore warn that the app is unrecognized, cannot be
> verified, or might be unsafe, and may block it from opening.
>
> A verified publisher signature is a digital proof of who published an app.
> Obtaining and maintaining signing credentials involves costs and administration
> that this hobby project has not taken on for these previews. Being open source
> means the code is available to inspect; it does **not** guarantee that a download
> is safe. Only continue if you trust the project and the source of your download.

## Find the right download

1. Open the [official dj-digger downloads](https://djdigger.app/download/).
   For older builds, see [GitHub Releases](https://github.com/fbialogrecki/dj-digger/releases).
2. Choose the installer for your computer. On GitHub, find a release with
   **desktop preview** downloads and expand **Assets**.

| Your computer | What to download |
| --- | --- |
| Windows 10 (version 1809 or newer) or Windows 11, 64-bit Intel or AMD | File ending in `windows-x64-test.exe` |
| Mac with an Apple M-series chip, macOS 15 or newer | File ending in `macos-arm64-test.dmg` |
| Mac with an Intel processor, macOS 15 or newer | File ending in `macos-x86_64-test.dmg` |

The version number at the beginning of the filename can change. The word `test`
is expected for these previews. You do not need **Source code**, `.whl`,
`.sha256`, or `.json` files to install the desktop app.

Use downloads from **github.com/fbialogrecki/dj-digger**. Avoid copies on
third-party download sites or files forwarded by someone else.

## Windows

### 1. Check your computer

You need **Windows 10 (version 1809 or newer) or Windows 11 on a 64-bit Intel or
AMD PC**. Older Windows 10 versions and Windows on ARM are not supported by this
installer.

If you are unsure, open **Settings → System → About** and look at **System type**
and **Windows specifications** (on Windows 10 the **Version** there must be 1809 or
a later one, such as 1903, 20H2 or 22H2). The system type should mention an x64-based
processor.

### 2. Run the installer

1. Download the file ending in `windows-x64-test.exe` using the steps above.
2. Open your **Downloads** folder and double-click the downloaded file.
3. If Windows shows a warning, read the next section before continuing.
4. Choose your language if asked, then follow the setup screens. The suggested
   installation folder is suitable for most people.
5. Finish setup. Open the **Start menu**, search for **dj-digger**, and open it.
   You can also use the desktop shortcut if you selected that option during setup.

### If Windows blocks the installer

You may see **“Windows protected your PC”**, a message about an unrecognized app,
or **“Unknown publisher”**. These previews are not signed with a verified
publisher certificate, so Windows cannot confirm their publisher and may not
have enough download history to recognize them.

For the **SmartScreen unrecognized-app warning**, if you downloaded the file
from the official project and choose to proceed:

1. Select **More info**.
2. Check that the app name matches the dj-digger installer you downloaded.
3. Select **Run anyway**, if that option is available.

This lets that installer run without turning off SmartScreen for your computer.

**No “Run anyway” button?** Windows Smart App Control or rules set by your
workplace or school may prevent this unsigned preview from running. Do not turn
off those protections to install it. On a managed computer, ask your IT team;
otherwise, report the message using the help link below. This preview may not
work with your current security settings.

**If your browser blocks the download, or antivirus reports a named threat,**
pause and report the exact message. Do not assume every warning is caused by
the missing signature, disable antivirus, or add an exclusion.

For more detail, see Microsoft's explanations of
[SmartScreen warnings](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
and [Smart App Control](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions).

## Mac

### 1. Check your Mac

You need **macOS 15 (Sequoia) or newer**.

Open the **Apple menu → About This Mac** to check your macOS version and processor:

- If you see **Chip: Apple M1, M2, M3**, or another Apple M-series chip, choose
  the download with **`arm64`** in its name.
- If you see **Processor: Intel**, choose the download with **`x86_64`** in its name.

### 2. Install the app

1. Download the matching `.dmg` file using the steps above.
2. Double-click it in your **Downloads** folder. A window will open with the
   dj-digger app and an **Applications** shortcut.
3. Drag **dj-digger** onto **Applications** and wait for the copy to finish.
4. Eject the dj-digger disk image using the eject button beside it in Finder.
5. Open **Finder → Applications** and double-click **dj-digger**.

### If your Mac blocks the first launch

macOS may say that the developer cannot be verified or that Apple cannot check
the app for malicious software. The preview does not have an Apple Developer ID
signature or Apple notarization. Its local build signature does not verify the
publisher's identity with Apple.

If you downloaded the app from the official project and choose to proceed:

1. Dismiss the warning after trying to open **dj-digger** from **Applications**.
2. Open **System Settings → Privacy & Security**.
3. Scroll to the security section and find the message about **dj-digger**.
4. Click **Open Anyway** and confirm when prompted. Your Mac may ask you to
   authenticate with your password or Touch ID.

This creates an exception for this app. You do not need to disable Gatekeeper
or change security settings for every app on your Mac.

**No approval option?** Try opening dj-digger from Applications once more, then
return to Privacy & Security. If it is still unavailable, report the message
below. A workplace or school may restrict which apps can run; ask your IT team
on a managed Mac.

**If macOS says the app will damage your computer, contains malware, or is
damaged, stop.** That is different from an unidentified-developer warning.
Report the exact message instead of using commands to remove security checks.

Apple's [guide to opening apps safely](https://support.apple.com/en-gb/102445)
includes screenshots and explains the different warnings.

## Linux

Linux has no installer file. One terminal command installs dj-digger with
everything it needs, and running it again later updates the app.

### 1. Run the install script

```bash
wget -qO- https://djdigger.app/install.sh | sh
```

No `wget`? Use `curl -fsSL https://djdigger.app/install.sh | sh` instead.
You can read the [hosted script](https://djdigger.app/install.sh) before running
it. The [source is on GitHub](https://github.com/fbialogrecki/dj-digger/blob/main/site/public/install.sh).

The script performs these steps.

- Installs [uv](https://docs.astral.sh/uv/) into your home folder. uv brings its
  own Python, so the Python of your system is not used or changed.
- Checks for FFmpeg and the system libraries the app's window needs. If any are
  missing, it shows the exact `apt-get`, `dnf`, `pacman` or `zypper` command and
  asks before running it with `sudo`. This is the only step that needs your password.
- Installs dj-digger from [PyPI](https://pypi.org/project/dj-sc-digger/) and adds
  it to your applications menu.
- Ends with a list of what it did and what it did not do.

Apart from that one package command, nothing outside your home folder is changed.

To see what the script would do without changing anything, or to skip its question:

```bash
wget -qO- https://djdigger.app/install.sh | sh -s -- --dry-run
wget -qO- https://djdigger.app/install.sh | sh -s -- --yes
```

The script is tested on clean installations of Ubuntu 24.04, Debian 12, Fedora 45,
Arch Linux and openSUSE Tumbleweed (64-bit Intel or AMD). On another distribution
it still installs dj-digger, but tells you that it could not check the system
packages. On Arch Linux, update your system first (`sudo pacman -Syu`).

### 2. Start dj-digger

Open **dj-digger** from your applications menu. From a terminal, open a new
terminal window first and run `dj-digger-gui`.

### Install it yourself instead

If you would rather not run a script, install FFmpeg (with `ffprobe`) from your
distribution, then use uv or pipx (pipx needs Python 3.12 or newer):

```bash
uv tool install 'dj-sc-digger[gui,analyze]'
pipx install 'dj-sc-digger[gui,analyze]'
```

## Connect your browser

Download gates, store carts and SoundCloud sign-in can use the dj-digger extension in your own browser.

1. Start dj-digger once to register its native messaging host. In the **1.6.0 installer**, open **Settings → Connect browser…**. In the **source checkout**, open **Settings → Preferences… → Accounts and browser → Connect browser…**. The dialog includes the extension folder and a **Check connection** button. With the desktop closed you can also register the host and print the folder path with `dj-digger browser register`.
2. In Chrome, Edge, Brave or Chromium, install [dj-digger from the Chrome Web Store](https://chromewebstore.google.com/detail/dj-digger/ndimmdooibgcajgnoncdnckolhlmnnkb). Edge may ask you to allow extensions from other stores. For an unpacked install, open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked** and select the extension folder. For Firefox, use the signed extension if provided in the release, or choose **Load Temporary Add-on** in `about:debugging#/runtime/this-firefox` and select the folder’s `manifest.json`. Firefox removes the temporary add-on at restart.
3. Grant the site permissions requested on the extension’s welcome page, then choose **Check connection** in the app. On Linux the native host requires a browser that can start it. Flatpak and Snap browser installations are unsupported.

With the desktop running, use **Import to DJ digger** on supported SoundCloud pages, or choose **Import this playlist into dj-digger** in the extension panel. The source checkout also lets the panel start or restore the desktop and import Daily Drops or Weekly Wave as dated snapshots. See [playlist workflows](workflows.md#import-and-organize-playlists).

For your own private playlists and account-only artist downloads, choose **Sign in to SoundCloud…** under **Settings** in 1.6.0, or **Accounts and browser** in the source checkout. If SoundCloud rejects a saved login, the app offers **Refresh login**.

To disconnect, use the connected browser’s **Disconnect…** action in the same settings, or close the desktop and run `dj-digger browser unregister`. The desktop registers the host again at its next start. To remove the extension itself, use the browser’s extensions page.

### Old browser profiles

Earlier versions kept private browser profiles in the data folder. They are no
longer used and the app never deletes them. To free the space, close dj-digger and
delete the `store-browser` and `soundcloud-browser` folders from the data folder
by hand.

### For the project owner: lint and sign the Firefox extension

Run these from `dj_digger/webext/extension`. Node is needed only here; CI does not
use it. Signing needs the owner's AMO account and API keys, so it is a manual step.

```bash
npx web-ext lint
npx web-ext sign --channel=unlisted
```

Provide the keys through the `WEB_EXT_API_KEY` and `WEB_EXT_API_SECRET` environment
variables, never in the repository or on the command line.

## Updating or removing the app

**Updates are manual for these previews.** Close dj-digger before updating.
Read the notes for the new version on the [official downloads page](https://djdigger.app/download/).

- **Windows:** download and run the new installer, using the same installation
  folder as before.
- **Mac:** download the new DMG for your processor and drag the app into
  Applications again. Choose **Replace** when asked.
- **Linux:** run the install script again.

To remove the application:

- **Windows:** open **Settings → Apps → Installed apps**, find **dj-digger**,
  and choose **Uninstall**.
- **Mac:** close dj-digger, then move it from **Applications** to the **Trash**.
- **Linux:** run `uv tool uninstall dj-sc-digger`, then delete
  `~/.local/share/applications/dj-digger.desktop` and
  `~/.local/share/icons/hicolor/256x256/apps/dj-digger.png`.

Removing the app does not automatically remove its separately stored library,
settings, or account data, or your downloaded music.

## Need a hand?

[Report an installation problem](https://github.com/fbialogrecki/dj-digger/issues).
Include your Windows or macOS version or your Linux distribution, the downloaded filename, and the exact
message you saw. A screenshot can help; hide personal information before sharing
it. Never include passwords or sign-in codes.
