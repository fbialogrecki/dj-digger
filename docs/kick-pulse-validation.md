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
