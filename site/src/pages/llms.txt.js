import { chromeStore, repo, version } from "../../site.config.mjs";

// A plain-text map of the site for language models and agents (llmstxt.org). Facts only from the pages it links.
export const GET = ({ site }) => {
  const url = (path) => new URL(path, site).href;
  return new Response(
    `# dj-digger

> dj-digger is a free, open-source (Apache 2.0), local-first crate-digging app for DJs on Windows, macOS and Linux. It imports whole SoundCloud playlists, likes, reposts and artist profiles into a library you can audition, remembers which tracks you got or skipped, and finds where to buy each track (Bandcamp, Beatport, Traxsource, Juno Download, Apple, record shops) or download it free through gates such as Hypeddit, ToneDen, GateRush and Droploud. It runs on your computer: no account, no dj-digger server.

The Python package is \`dj-sc-digger\`; the unrelated PyPI package named "dj-digger" is not this project.

- Current version: ${version}. Windows 10 (1809 or newer) and 11, macOS 15 or newer (Apple Silicon and Intel), Linux.
- The browser extension for Chrome, Edge and Brave finishes download gates and adds tracks to Bandcamp and Beatport carts in the user's own browser. It never checks out or pays and talks only to the app on the same computer.
- BPM and key are estimated on the computer. Local files: WAV, AIFF, FLAC, MP3, AAC and M4A; it exports copies in a format the chosen DJ decks read (FLAC or WAV). Never writes audio tags or rekordbox data.

## Pages

- [Home](${url("/")}): what dj-digger does: collect, decide, buy or download
- [Desktop app](${url("/app/")}): import, preview, crates, store links, download gates, carts, local library, export for the decks
- [Browser extension](${url("/extension/")}): download gates, carts, SoundCloud sign-in, supported browsers, data
- [Download](${url("/download/")}): installers, the Linux install command, checksums
- [About](${url("/about/")}): how it works, where data goes, credits
- [Privacy](${url("/privacy/")}): what the website measures, with consent
- [Questions and answers](${url("/faq/")}): cost, systems, SoundCloud, download gates, carts, rekordbox, data

## Guides

- [SoundCloud to rekordbox](${url("/guides/soundcloud-to-rekordbox/")}): why streamed SoundCloud tracks cannot go to USB, and how to get files instead
- [Where to buy a SoundCloud track](${url("/guides/where-to-buy-soundcloud-tracks/")}): where the buy link hides, and doing it for a whole playlist
- [Organize downloaded DJ tracks](${url("/guides/organize-downloaded-dj-tracks/")}): file locations, mix versions and set playlists
- [FLAC or WAV for DJs](${url("/guides/flac-vs-wav-for-djs/")}): match playback copies to the booth
- [Hypeddit download gates](${url("/guides/hypeddit-download-gates/")}): how to read gate requirements and troubleshoot missing downloads

## Source and distribution

- [Source code](${repo})
- [Changelog](${repo}/blob/v${version}/CHANGELOG.md)
- [PyPI: dj-sc-digger](https://pypi.org/project/dj-sc-digger/)
- [Chrome Web Store](${chromeStore})
`,
    { headers: { "Content-Type": "text/plain; charset=utf-8" } },
  );
};
