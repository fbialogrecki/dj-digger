#!/bin/sh
# dj-digger installer for Linux. https://github.com/fbialogrecki/dj-digger
#
#   sh install.sh [--yes] [--dry-run]
#   wget -qO- <address of this file> | sh -s -- --dry-run
#
# Installs uv into your home directory, then dj-digger with its desktop and
# analysis extras, and adds a menu entry. Run it again to update dj-digger.
# System packages (FFmpeg and the libraries Qt needs) are the only part that
# needs root: the exact command is shown first and runs only if you agree.
# Nothing else is changed outside your home directory.
#
# The package lists were measured on clean Ubuntu 24.04, Debian 12, Fedora,
# Arch and openSUSE Tumbleweed images, not copied from a guide.
set -eu

PACKAGE='dj-sc-digger[gui,analyze]'
REPO='https://github.com/fbialogrecki/dj-digger'
UV_INSTALLER='https://astral.sh/uv/install.sh'
MARK='# Created by the dj-digger install script'

say() { printf '%s\n' "$*"; }
die() { printf 'dj-digger install: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
did() { done_list="${done_list}  - $*
"; }
skipped() { skipped_list="${skipped_list}  - $*
"; }

usage() {
    say 'Usage: sh install.sh [--yes] [--dry-run]'
    say '  --yes      run the system package command without asking'
    say '  --dry-run  show what would be done and change nothing'
}

# 0 = yes, 1 = no, 2 = nobody to ask. stdin is the pipe the script came through.
ask() {
    [ "$yes" = 1 ] && return 0
    (exec </dev/tty) 2>/dev/null || return 2
    printf '%s [y/N] ' "$1" >/dev/tty
    read -r reply </dev/tty || reply=''
    case $reply in [yY] | [yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}

fetch() {
    if have curl; then
        curl --proto '=https' --tlsv1.2 -fsSL "$1" -o "$2"
    elif have wget; then
        wget --https-only -qO "$2" "$1"
    else
        die 'curl or wget is needed to download uv.'
    fi
}

dpkg_has() {
    case $(dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null) in installed*) return 0 ;; *) return 1 ;; esac
}

pkg_installed() {
    case $pm in
        apt) dpkg_has "$1" || dpkg_has "${1}t64" ;;
        dnf | zypper) rpm -q --whatprovides "$1" >/dev/null 2>&1 ;;
        pacman) pacman -T "$1" >/dev/null 2>&1 ;;
    esac
}

# Ubuntu 24.04 renamed some libraries (libasound2 -> libasound2t64); the old name
# is then ambiguous to apt. Needs the package lists apt already has on a real system.
pkg_name() {
    if [ "$pm" = apt ] && apt-cache show "${1}t64" >/dev/null 2>&1; then
        say "${1}t64"
    else
        say "$1"
    fi
}

has_fonts() { have fc-list && [ -n "$(fc-list 2>/dev/null | head -n 1)" ]; }

system_packages() {
    case $pm in
        apt)
            libs='libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libglib2.0-0 libdbus-1-3 libfontconfig1
                libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-render0
                libxcb-shape0 libxcb-util1 libxcb-xkb1 libwayland-cursor0 libwayland-egl1 libwayland-server0
                libasound2 libpulse0'
            ffmpeg=ffmpeg font=fonts-dejavu-core
            ;;
        dnf)
            libs='libglvnd-egl libglvnd-glx libxkbcommon libxkbcommon-x11 fontconfig dbus-libs
                xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util
                libwayland-cursor libwayland-egl libwayland-server alsa-lib pulseaudio-libs'
            ffmpeg=ffmpeg-free font=dejavu-sans-fonts
            ;;
        pacman)
            libs='libglvnd fontconfig libxkbcommon libxkbcommon-x11 xcb-util-cursor xcb-util-wm
                xcb-util-image xcb-util-keysyms xcb-util-renderutil alsa-lib libpulse'
            ffmpeg=ffmpeg font=ttf-dejavu
            ;;
        zypper)
            libs='libglvnd Mesa-libGL1 Mesa-libEGL1 libdbus-1-3 libfontconfig1 libglib-2_0-0 libgthread-2_0-0
                libxkbcommon0 libxkbcommon-x11-0 krb5 libxcb-cursor0 libxcb-icccm4 libxcb-image0
                libxcb-keysyms1 libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-util1 libxcb-xkb1
                libwayland-cursor0 libwayland-egl1 libwayland-server0 libasound2 libpulse0'
            ffmpeg=ffmpeg font=dejavu-fonts
            ;;
        *)
            skipped "System packages: no apt, dnf, pacman or zypper here, so FFmpeg and the Qt libraries were not checked. See $REPO/blob/main/docs/installation.md#linux"
            return 0
            ;;
    esac

    missing=''
    for p in $libs; do
        pkg_installed "$p" || missing="$missing $(pkg_name "$p")"
    done
    if ! have ffmpeg || ! have ffprobe; then missing="$missing $ffmpeg"; fi
    has_fonts || missing="$missing $font"
    have awk || missing="$missing gawk" # uv's installer needs awk
    if [ -z "$missing" ]; then
        did 'System packages: FFmpeg and the Qt libraries were already installed.'
        return 0
    fi

    if [ "$(id -u)" = 0 ]; then sudo=''; else sudo='sudo '; fi
    case $pm in
        apt) cmd="${sudo}apt-get update && ${sudo}apt-get install -y --no-install-recommends$missing" ;;
        dnf) cmd="${sudo}dnf install -y$missing" ;;
        # No -y: syncing without a full upgrade breaks Arch. If this fails, run pacman -Syu first.
        pacman) cmd="${sudo}pacman -S --needed --noconfirm$missing" ;;
        zypper) cmd="${sudo}zypper --non-interactive install --no-recommends$missing" ;;
    esac

    say ''
    say 'dj-digger needs these system packages (FFmpeg and libraries for Qt):'
    say ''
    say "    $cmd"
    say ''
    if [ "$dry" = 1 ]; then
        say 'Dry run: would ask before running it.'
        return 0
    fi
    if [ -n "$sudo" ] && ! have sudo; then
        skipped "System packages: sudo is not installed. Run as root: $cmd"
        return 0
    fi
    answer=0
    ask 'Run this command now?' || answer=$?
    case $answer in
        0)
            if sh -c "$cmd"; then
                did "System packages installed:$missing"
            else
                skipped "System packages: the command failed. Run it yourself: $cmd"
            fi
            ;;
        1) skipped "System packages: you declined. dj-digger may not start without them: $cmd" ;;
        *) skipped "System packages: no terminal to ask on and no --yes, so nothing was run: $cmd" ;;
    esac
}

desktop_entry() {
    data="${XDG_DATA_HOME:-$HOME/.local/share}"
    entry="$data/applications/dj-digger.desktop"
    if [ "$dry" = 1 ]; then
        say "Dry run: would write the menu entry $entry"
        return 0
    fi
    if [ -e "$entry" ] && ! grep -qF "$MARK" "$entry"; then
        skipped "Menu entry: $entry exists and was not made by this script, so it was left alone."
        return 0
    fi
    mkdir -p "$data/applications"
    for icon in "$(uv tool dir)"/dj-sc-digger/lib/python*/site-packages/dj_digger/gui/qml/icons/app.png; do
        if [ -f "$icon" ]; then
            mkdir -p "$data/icons/hicolor/256x256/apps"
            cp "$icon" "$data/icons/hicolor/256x256/apps/dj-digger.png"
        fi
    done
    cat >"$entry" <<EOF
$MARK
[Desktop Entry]
Type=Application
Name=dj-digger
Comment=Crate digging for DJs
Exec="$bindir/dj-digger-gui"
Icon=dj-digger
Terminal=false
Categories=AudioVideo;Audio;Music;
StartupWMClass=dj-digger
EOF
    did "Menu entry written: $entry"
}

main() {
    yes=0 dry=0 done_list='' skipped_list=''
    for arg in "$@"; do
        case $arg in
            -y | --yes) yes=1 ;;
            --dry-run) dry=1 ;;
            -h | --help)
                usage
                exit 0
                ;;
            *) die "unknown option $arg (try --help)" ;;
        esac
    done

    case $(uname -s) in
        Linux) ;;
        Darwin) die "this script is for Linux. On macOS use the disk image: $REPO/releases/latest" ;;
        MINGW* | MSYS* | CYGWIN*) die "this script is for Linux. On Windows use the installer: $REPO/releases/latest" ;;
        *) die "unsupported system $(uname -s). Manual steps: $REPO/blob/main/docs/installation.md" ;;
    esac
    case $(uname -m) in
        x86_64 | amd64 | aarch64 | arm64) ;;
        *) die "Qt has no build for $(uname -m). Manual steps: $REPO/blob/main/docs/installation.md" ;;
    esac
    [ -n "${HOME:-}" ] || die 'HOME is not set.'

    pm=''
    for candidate in apt-get dnf pacman zypper; do
        if have "$candidate"; then
            pm=${candidate%-get}
            break
        fi
    done

    system_packages

    old_path=$PATH
    PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$PATH"
    if have uv; then
        did "uv was already installed: $(command -v uv)"
    elif [ "$dry" = 1 ]; then
        say "Dry run: would install uv into your home directory from $UV_INSTALLER"
    else
        have awk || die "uv's installer needs awk. Install the system packages listed above, then run this script again."
        tmp=$(mktemp -d)
        trap 'rm -rf "$tmp"' EXIT
        fetch "$UV_INSTALLER" "$tmp/uv-install.sh"
        sh "$tmp/uv-install.sh"
        have uv || die 'uv was installed but is not on PATH; open a new terminal and run this script again.'
        did "uv installed: $(command -v uv)"
    fi

    if [ "$dry" = 1 ]; then
        say "Dry run: would run uv tool install --upgrade '$PACKAGE'"
        desktop_entry
        say 'Dry run: nothing was changed.'
        return 0
    fi

    uv tool install --upgrade "$PACKAGE"
    bindir=$(cd "$(uv tool dir --bin)" && pwd) # uv answers ~/.local/share/../bin when XDG_DATA_HOME is set
    did "dj-digger installed: $("$bindir/dj-digger" --version)"
    case ":$old_path:" in
        *":$bindir:"*) ;;
        *)
            uv tool update-shell >/dev/null 2>&1 || true
            did "$bindir added to PATH in your shell profile (open a new terminal to pick it up)."
            ;;
    esac
    desktop_entry

    say ''
    say 'Done:'
    printf '%s' "$done_list"
    if [ -n "$skipped_list" ]; then
        say 'Not done:'
        printf '%s' "$skipped_list"
    fi
    say ''
    say 'Start it from your applications menu, or run:'
    say ''
    say "    $bindir/dj-digger-gui"
    say ''
    say "Connect your browser next: $REPO/blob/main/docs/installation.md#connect-your-browser"
}

main "$@"
