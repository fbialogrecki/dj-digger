# Reliable downloads, gate login, and error banner

## Goal

Make batch downloads report honest progress and actionable failures, keep the
error banner readable, resolve the reported Hypeddit router correctly, and add
one-time Spotify login so Spotify-backed Hypeddit gates can perform the action
the user explicitly allowed.

## Scope

The change covers:

- the Textual error banner and logging while the TUI owns the terminal;
- initial download progress for single and batch downloads;
- SoundCloud free downloads whose API payload has no `download_url`;
- Hypeddit gates that require a real email, SoundCloud actions, and one Spotify
  artist follow/save step;
- Hypeddit pages that are link routers rather than download gates;
- Spotify OAuth login, token refresh, status, and logout from the CLI.

It does not add browser automation, reuse Hypeddit's OAuth client or cookies,
support arbitrary Spotify gate action types, or introduce a new dependency.

## Architecture

### TUI errors and progress

`ErrorBanner` continues to own its list of unique messages. It renders a Rich
`Text` value rather than a markup string so brackets and other text from track
titles cannot become formatting instructions. Its close button uses a literal
`X`, and the existing bounded scroll region remains the sole overflow
mechanism.

While `run_tui()` owns the terminal, the `dj_digger` stream logger is muted and
restored afterward. User-facing failures already travel through Textual
notifications or `ErrorBanner`; allowing the stream handler to write behind the
live screen only corrupts the frame.

Both single and batch download workers publish `0.0` before resolving a gate or
requesting a SoundCloud download. Byte callbacks remain the only source of
positive progress.

### SoundCloud direct downloads

The existing order remains gate first, then SoundCloud. When a track is marked
downloadable but has no concrete `download_url`, `SoundCloudClient` uses the
authenticated `/tracks/{id}/download` endpoint.

Missing credentials, rejected credentials, and other HTTP failures produce
distinct `SoundCloudError` messages. A concrete artist-provided URL remains a
valid fallback. This makes the Medusa track work with a valid stored login and
explains precisely why it cannot work otherwise.

### Spotify authentication

A focused `dj_digger/spotify.py` module implements Spotify Authorization Code
with PKCE using the standard library plus the already-installed `requests`.
The CLI exposes:

```text
dj-digger auth spotify login --client-id <client-id>
dj-digger auth spotify status
dj-digger auth spotify logout
```

Login opens the configured browser and listens temporarily on an explicit
`127.0.0.1` loopback address. It validates OAuth `state`, exchanges the code
with the PKCE verifier, and stores the public client ID, refresh token, access
token, scopes, and expiry in an owner-only JSON file. Access tokens are refreshed
on demand. No client secret is accepted or stored.

The requested scope is only `user-follow-modify`. Spotify users must register
their own developer application and loopback redirect URI because development
applications are account-bound and quota-limited.

### Hypeddit resolution

`resolve_hypeddit_download_url()` parses the declared `nwSteps` before posting
completion calls.

- An `email` step with the reserved `.invalid` address fails immediately with a
  Settings instruction and sends nothing.
- An `sp` step requires stored Spotify credentials and enabled gate social
  actions. For the reported `xngfus` shape, `ART|<id>` becomes
  `spotify:artist:<id>` and is sent to Spotify's current `PUT /me/library`
  endpoint before Hypeddit is told that the step completed.
- Unknown Spotify action prefixes fail explicitly and remain manual instead of
  claiming success.
- Hypeddit POST failures and download rejections retain enough response detail
  to produce an actionable, non-secret error rather than returning an
  unexplained `None`.

`store_links_on_page()` remains the import-time router detector. The live
`l87679` page already yields only its Beatport destination on the current code;
the implementation adds a fixed regression fixture rather than changing the
working production path.

## Data flow

1. Import resolves SoundCloud tracks and expands purchase-link hubs.
2. A Hypeddit page with no download action is replaced by its shop links.
3. A real gate stays attached to the track.
4. Batch download marks the selected row as `0%` and asks
   `SoundCloudClient.download_track()` for a file.
5. Hypeddit prerequisites are validated before any third-party mutation.
6. Spotify credentials are refreshed when needed and the declared artist URI is
   saved/followed only when gate social actions are enabled.
7. Hypeddit returns the file URL, then byte callbacks advance progress.
8. Failures clear progress and enter the bounded error banner without terminal
   log output corrupting the screen.

## Security and privacy

- PKCE uses a cryptographically random verifier and `state` value.
- The callback binds only to `127.0.0.1`, validates `state`, serves one result,
  and has a finite timeout.
- Tokens are written atomically to a mode-`0600` file in a mode-`0700`
  configuration directory.
- Error messages never include OAuth tokens, signed download URLs, cookies, or
  response bodies that may contain them.
- Email and social actions are never submitted when their required user setting
  is absent or disabled.

## Testing

All changes follow red-green-refactor with offline tests only:

- `tests/test_tui.py` verifies a visible literal close label, bounded banner
  layout, clearing errors, literal bracketed messages, `0%` initial progress,
  and logger restoration after the TUI exits.
- `tests/test_soundcloud.py` verifies missing, rejected, and successful
  authenticated download endpoints without network or disk outside `tmp_path`.
- `tests/test_spotify.py` verifies PKCE parameters, callback state validation,
  secure persistence, refresh, status, logout, URI submission, and redacted
  failures with fake HTTP responses.
- `tests/test_gates.py` verifies no request is sent for a placeholder email,
  Spotify is required for `sp`, `ART|...` is submitted before Hypeddit
  completion, unknown action types fail safely, and a captured `l87679`-style
  router returns Beatport without retaining Hypeddit.
- `tests/test_cli.py` verifies the nested Spotify authentication commands while
  preserving the existing SoundCloud authentication syntax.

The full offline `uv run pytest` suite is the release gate. Live checks may
inspect public GET responses, but automated tests never contact SoundCloud,
Hypeddit, Spotify, or user runtime data.

## Success criteria

- The error banner remains readable under many batch failures and its `X` is
  visible and functional.
- A selected download displays `0%` until bytes arrive.
- The Medusa track downloads with valid SoundCloud credentials or reports the
  exact authentication failure.
- `zrw7vu` cannot submit the reserved placeholder email.
- `xngfus` can complete its declared Spotify artist action after one-time PKCE
  login, subject to Spotify account and development-app limits.
- An `l87679`-style router imports only its Beatport link.
