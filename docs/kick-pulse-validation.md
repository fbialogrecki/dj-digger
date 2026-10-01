# Kick pulse validation — 2026-09-29

The previous RMS attack/decay detector missed heavily limited kicks while
still accepting some bass movement. The replacement uses short stereo spectra,
frequency-neighbour suppression and recent onset contrast. It does not predict
beats from BPM. Flash intensity is reduced by 40 %, with a quadratic release.

## Annotated recordings

Offline evaluation used the 95 `#MIX.wav` loops in the
[IDMT-SMT-Drums V2 archive](https://www.idmt.fraunhofer.de/en/publications/datasets/drums.html),
containing 2,174 annotated kick onsets and 1,438.28 seconds of audio.
Predictions match annotations one-to-one within 50 ms; duplicate predictions
count as false positives. These are drum loops, not complete mastered songs.
This is an engineering comparison, not an independent generalisation study:
12 loops were used during development before checking the full collection.

| Detector | Matched kicks | Missed kicks | Additional pulses | Precision | Recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous local RMS version | 1,930 | 244 | 463 | 80.65 % | 88.78 % |
| Spectral version | 2,124 | 50 | 90 | 95.93 % | 97.70 % |

Mean timing error among matched events was 3.79 ms for the spectral version.
Processing the collection took 8.92 s on the development machine, excluding
archive loading and conversion. This does not measure device output latency.

Reproduce against a separately obtained local archive:

```sh
uv run python scripts/evaluate_kicks.py /path/to/IDMT-SMT-DRUMS-V2.zip
```

The script is offline and does not access application state. Dataset audio and
annotations are not included in the repository or application; their separate
CC BY-NC-ND 4.0 licence applies. The earlier detector was evaluated from a
temporary snapshot before replacement, against the same archive and matcher.

## Regressions and remaining limits

The offline suite checks onset timing, stereo phase, callback boundaries,
seek/reset, pause/resume, snare rejection, quiet pads, changing bass and fast
kick rolls at 128/174/210 BPM. Soft-limited synthetic mixes at gains 8 and 12
previously lost all eight kick onsets; the new detector finds all eight within
15 ms. The initial kick is published within 100 ms in the streaming test (the
attack plus 40 ms of its body, read for the pulse amplitude since 2026-09-29),
without a tempo warm-up or a decay-confirmation wait.

Kickless amplitude wobble produces no repeated pulses, and sustained pitch
sweeps/growls do not turn into rolls. An abruptly started bass tone can produce
one initial onset: removing the mandatory decay gate deliberately permits that
ambiguity. Tests explicitly retain this limitation instead of claiming perfect
instrument classification. Percussive synth bass can still resemble a kick,
and a kick masked in a dense mix can still be missed.

The public streams of [Kotori — Deshi](https://soundcloud.com/imkotori/deshi)
and [Jacidorex — Sophia (Resonance Rework)](https://soundcloud.com/resonance_bt/jacidorex-sophia-resonance-1)
were analysed during diagnosis. Their temporary spectral features were replayed
through the new detector. Those songs have no hand-labelled kick reference in
this task; pulse counts are therefore not presented as accuracy or listening
validation. Final subjective alignment on the user's output device still
requires listening. The saved device timing adjustment is preserved.

## Local delivery checks

- Specification section map, Ruff and lockfile checks passed.
- Full offline suite: **1,006 passed, 83 deselected**.
- Installed from the working checkout into the existing local `dj-sc-digger`
  tool environment; all 114 package files match the source by SHA-256.
- Installed GUI smoke test passed with temporary HOME/XDG directories and
  offscreen Qt; the user's application profile was not used for the test.
- Installed NumPy 2.5.3 also passed the eight-kick heavily limited regression;
  the installed environment's dependency compatibility check passed.

# Kick pulse v2 validation — 2026-10-01

The v2 detector (`docs/kick-pulse-plan.md`) replaced the spectral onset
detector above. The IDMT archive was not available for this run; its rerun
(`scripts/evaluate_kicks.py ARCHIVE`) remains open.

## Synthetic mixes (`tests/test_beats.py`)

Each mix has 48 kicks at 128 BPM under offbeat hats with one kind of bass,
soft-limited. The test kick shapes differ from the detector's generic kicks.
In the table, "≥ 0.95" means at least 0.95 for every kick shape, except
where noted.

| Bass under the kicks | False flashes | Recall after the first two bars |
| --- | ---: | --- |
| None, offbeat notes, one plucked note | 0 | 1.0 |
| Walking plucks on every eighth | 0 | ≥ 0.9; punch 0.72, deep 0.85 |
| 16th rolling bass | 0 | ≥ 0.95; punch 0.9 |
| Sidechained sub | 0 | ≥ 0.95; punch 0.9, deep 0.92 |
| Reese | 0 | ≥ 0.95; 808 0.95, punch 0.85, deep 0.67 |
| Kickless stabs, plucks, walking, pad, held sub | 0 | n/a |

Other scenarios:
- **Bass 1.5× the kick:** no false flashes. Walking bass keeps 0.5 recall;
  every other bass keeps at least 0.9.
- **Bass-only intro, then kicks:** no flash before the first kick, then every
  kick.
- **Kick 9 dB quieter after a loud section:** every kick.
- **Kick changed mid-song:** the new kick is followed within about eight kicks.
- **Seek:** the learned kick survives it.
- **A bass sweeping 45-200 Hz under every kick:** no false flashes, but only
  1-3 of 8 kicks flash. This is a documented limit.

## Local tracks (read-only, `scripts/evaluate_kicks.py --audio`)

Kicks per second in 15 s windows. The v1 figures are the old detector's
surviving pulses, measured on the first 180 s during the review.

| Track | Expected | v2 | v1 |
| --- | --- | --- | --- |
| Kalipo — Captain | every beat (≈ 2/s), quiet intro | 1.8-2.0 in drops, 0 in the intro | ≈ 2.0 |
| Sorry Boys — Wracam (URBANSKI remix) | every beat | 1.9-2.1 | ≈ 1.9 |
| Teminite × Panda Eyes — Highscore | every half bar (≈ 0.9/s) | 0.7-0.9 in drops | 2.5-3.5 |
| Hatework Machine × Bovska — Kaktus | DnB pattern | 1.3-1.5 in drops, no bursts | 2.1-5.0 |

The other four tracks (Radiohead remix, ProleteR, Sonny Alven, Night Marks)
have no reference. Every one of the eight learned a kick that drops in pitch.
Processing takes about 0.42 % of real time. Through the player, with a local
source and 50 ms device periods, every kick in Kalipo's first 120 s was known
at least 143 ms before the device took it.

# Kick light v3 validation — 2026-10-01

v3 publishes a continuous kick level (0-1, every 10 ms) instead of decisions;
see `docs/kick-pulse-plan.md`.

## Synthetic mixes (`tests/test_beats.py`)

48 kicks at 128 BPM under offbeat hats, one kind of bass, soft-limited. The
"at kicks" column is the 10th percentile of the level at the kicks; "between"
is the highest level away from the kicks.

| Bass | At kicks | Between |
| --- | --- | --- |
| None, offbeat, one plucked note, rolling 16ths | 1.0 | ≤ 0.18 |
| Sidechained sub | ≥ 0.94 | 0.01; a long 808: 0.51 |
| Reese | 1.0; deep 0.65 | ≤ 0.21 |
| Walking plucks | 0.66-0.98 by kick | ≤ 0.27 |
| Bass 1.5× the kick | ≥ 0.69 | ≤ 0.27 |

Other scenarios:
- **Kickless breakdown after a drop:** the repeated note and the pad stay at 0.
  Stabs peak at 0.19 and walking plucks at 0.59, but 95 % of their levels are
  at or below 0.2.
- **Pads, held subs and silence:** 0.
- **4/4 at 128, 174 and 210 BPM:** every kick reaches 1.0 and the level falls to
  0 between kicks.
- **Timing:** the level peaks 14 ms after the attack before the −9 ms timestamp
  correction, and within ±12 ms after it.

## Local tracks (read-only, `scripts/evaluate_kicks.py --audio`)

Pulses (level peaks ≥ 0.5) per second in 15 s windows, fed in 50 ms periods
like the player.

| Track | v3 in the drops | v2 |
| --- | --- | --- |
| PYREZ — Everything In Its Right Place (≈ 158 BPM) | 2.5-2.9, dark only in 105-135 s | 0 over 30-60 s and 110-170 s |
| Kalipo — Captain | 1.8-2.2, intro 0 | 1.8-2.0 |
| Sorry Boys — Wracam (URBANSKI remix) | 1.9-2.1 | 1.9-2.1 |
| Teminite × Panda Eyes — Highscore | 0.9-1.1 (every half bar) | 0.7-0.9 |
| Hatework Machine × Bovska — Kaktus | 1.3-1.7 | 1.3-1.5 |

All eight tracks learn a kick that drops in pitch, and the analysis takes about
0.57 % of real time. Through the player, with PYREZ from 60 s, levels reached
184-189 ms beyond the device position. On this machine (PipeWire, 50 ms
periods) the measured device queue was 46-71 ms.

## Light shape (2026-10-01, after listening)

With the level sampled at 60 fps, a 120 ms exponential release left a faint tail
that kept the neon tint on. Measured after the first 30 s of each track:
- Kalipo was dark (light under 5 %) only 30 % of the time.
- PYREZ was dark only 38 % of the time.
- 4/4 at 174 BPM was never dark.

The window now falls in a straight line, from full to dark in 150 ms, and shows
nothing below a 15 % gate. With that:
- Kalipo is dark about 70-75 % of the time.
- PYREZ is dark about 70-75 % of the time.
- The other six tracks are dark 75-93 % of the time.
- 174 BPM is dark about 60-70 % of the time.

## Colours (2026-10-01, after listening)

On the dark theme the pastel rest colours were lighter than the neon. Relative
luminance was 0.34 and 0.40 at rest against 0.30 and 0.32 in neon: the rest
looked lit, and a kick looked like the light going out. The colours now are:

| | Bordeaux side | Blue side |
| --- | --- | --- |
| Rest | `#96526a` (Y 0.14) | `#5470b0` (Y 0.17) |
| Neon | `#ff62b4` (Y 0.33) | `#6fa8ff` (Y 0.39) |

On a kick the neon is about 2.4× brighter. The rest colours keep about 3.3:1
and 3.8:1 against the panel, and no colour is a saturated red.

## Rhythm, skipped kicks and colours (2026-10-01, Brais — Zubat, streamed)

Brais — Zubat (techno, about 162 BPM) was streamed read-only through the app's
own SoundCloud path. The main kicks lit fully. A three-hit fill after every six
kicks, at about 40 % of the kick's low energy, sat at levels of 0-0.3: a 1.5
power and the 15 % gate removed it, which read as skipped kicks and broken
rhythm. Two changes followed:
- The kick weight now has a floor of 0.4 and the curve is linear. The fill
  glows at about 0.15-0.25 while main kicks stay at about 0.93. Dark time stays
  at 60-87 % across the tracks.
- The window samples 40 ms ahead (`displayLead`) to cover the frames on their
  way to the eye.

A real-time simulation of the whole path covered the real player, a device
taking 50 ms periods, the 25 ms backend tick and a 60 fps loop running the
QML's logic. It lit 60 of 60 drop kicks with a 15 ms spread.

The dark-theme colours are now saturated, not pastel:

| | Rest | Neon |
| --- | --- | --- |
| Raspberry side | `#b8286a` | `#ff2fa8` |
| Blue side | `#3a5fd6` | `#1e9bff` |

The neon is 2.1-2.2× brighter than the rest. The rest keeps 3.1-3.3:1 against
the panel, and no colour is a saturated red.

## The real desktop, frame by frame (2026-10-01)

Reported: on Kalipo every few kicks showed no light, on Big Jet Plane the kick
rarely showed, and the light looked as if it stalled and came back. The offline
measurements above said both tracks were fine, so the real stack was run:
`Main.qml`, `Bridge`, `Backend`, `Player` and the audio device, muted, offscreen,
on isolated application state, with the window's `flash`, clock and levels
logged on every frame. Two bugs, both outside the analysis:

- **Only levels of exactly 0 and 1 reached the window.** The loudness decay
  constants were numpy floats, so every level between 0 and 1 was a
  `numpy.float64`. Qt passes that to QML as an opaque object, not a number, and
  the window's arithmetic on it gave no light. A kick at full level showed; a
  kick at 0.5-0.99 and every rising and falling edge did not. Levels are now
  plain floats.
- **The heard time ran at the handed position.** After a start the device took
  its first 50 ms at once and the next 0.2 s later, so "handed less elapsed"
  read -0.08 to -0.19 s and the clock was clamped to the position: about 70 ms
  early, moving in 50 ms steps. The queue is now at least what the last
  callback handed over.

| Kalipo - Captain, 45-105 s, live | Before | After |
| --- | --- | --- |
| Kicks under 0.3 brightness | 20 of 114 | 0 |
| Dimmest kick | 0.00 | 0.95 |
| Frames with the clock stuck at the snapshot position | 54 % | 0 % |
| Jumps over 20 ms in the heard time | 1199 | 0 |

Big Jet Plane (Sonny Alven remix), 90-150 s, after: 62 kicks, median brightness
0.94, the dimmest 0.48, none under 0.3. The window ran at 63 frames a second
with no frame gap over 28 ms.

The feedback gathered on v3 before this (always lit, dark on the kick, skipped
kicks, no rhythm) was given on a window that saw only 0 and 1.
