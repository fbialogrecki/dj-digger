# Kick pulse: diagnosis and plan — 2026-09-29

Target: the desktop waveform and the record backdrop breathe with the kick
drum. Steady on 4/4, following the syncopated kick in dubstep / drum & bass,
still during intros, breakdowns and outros. The pulse saturates and slightly
brightens the existing colours; nothing flashes white. The record itself is
darker and never pulses.

Status: all three layers shipped (2026-09-29). Layer 3 differences from the
plan below: lightness + 0.03 (not + 0.05) in the dark theme and no saturation
boost on saturated reds, because the red-value bound is far tighter than the
luminance bound on the bordeaux tones; the bars crossfade fully (0.85) to the
milder peak colour instead of 0.51 to a neon one.

Follow-up after listening (2026-09-30): the pulse was too faint, the record's
grooves and sheen were unwanted, and DHS — The House of God (Isaac mix) pulsed
on roughly one kick in three. Its kicks carry a loud click that fades as the
body swells (flat full-band power) and, every few beats, a clap that outweighs
the bass at the attack. The detector now accepts a clear bass-level rise and a
bass-led body, and lets a stronger peak retime a waiting hit; strong pulses on
the first 130 s rose from 0.5-1.3 /s to 1.8-2.5 /s (kick rate 2.27 /s) with the
other three probe tracks unchanged. Peak colours are stronger (bounded by the
actual WCAG thresholds instead of half of them) with a halo on the bars, only
the played side pulses, the record is flat black, and the playhead follows the
interpolated clock at subpixel precision without its glow.
`PROJECT-SPECIFICATION.md` §3.4 and §3.8 describe what runs today. Dropped from
layer 1 after measurement: the causal median (sustained-bass) reference turned
the synthetic 4 Hz amplitude wobble into a roll, and the attack-sharpness test
is meaningless with the 46 ms analysis window, which smears every attack over
four hops. Neither was needed for the measured false pulses; the level read over
40 ms after the attack plus the soft gain removed them.

## What is wrong today (measured)

`scripts`-style read-only probes ran the current `beats.KickDetector` over
three mastered tracks from the user's downloads, compared with a librosa
beat grid. No app state or music files were modified.

| Track | Tempo | Detector pulses | Beats | Pulses on a 16th grid |
| --- | ---: | ---: | ---: | ---: |
| Kalipo — Captain (house, 4/4) | 117 | 1.5 /s, 0 in the intro | 1.7 /s | ~100 % in drops |
| Teminite × Panda Eyes — Highscore (dubstep, half-time) | 112 | 2.5 /s overall, **3–5 /s in drops** | 1.8 /s | 60–90 % |
| Hatework Machine × Bovska — Kaktus (172 BPM) | 172 | 1.7 /s, **6 /s at 45–65 s** | 2.9 /s | ~95 % |

Inside the Highscore drop the detector fires every 16th. The real kicks
(one every half bar) have a 30–120 Hz level of 130–147 (arbitrary units);
the 16th-note chatter between them measures 85–110. So the false pulses are
not noise: they are bass stabs / wobble notes, on the grid, ~30 % quieter
than the kick.

Four root causes:

1. **No amplitude information reaches the UI.** `Kick.strength` is
   `sqrt(novelty / max recent novelty)`, which is 0.9–1.0 for almost every
   hit on all three tracks. Every event flashes at full intensity, so bass
   stabs and kicks look identical.
2. **Nothing suppresses sustained or slowly moving bass.** The detector is a
   frame-to-frame flux with a 5-bin frequency max filter. Wobble bass and
   swells with a fresh attack pass, and the IDMT drum-loop evaluation
   (`docs/kick-pulse-validation.md`) contains no bass at all, so its 96 %
   precision says nothing about this.
3. **The flash is a strobe, not a pulse.** A 45–180 ms quadratic release is
   below the range where the eye reads a swell (Bloch's law, ~100 ms); it
   reads as flicker, and the Broca–Sulzer effect makes it look harsher.
   Fixed-duration animations restarted per hit also cannot merge rolls.
4. **`neon()` is garish and unsafe at the bordeaux end.** Full HSL
   saturation at lightness 0.5–0.6 drifts colours, and the bordeaux → brighter
   bordeaux step exceeds the WCAG 2.3.1 red-flash bound (`(R−G−B)×320`
   changing by more than 20).

What is already right and stays: audio is decoded ahead of the speaker, the
QML clock schedules pulses on the playback clock and the View → Pulse timing
offset calibrates the device. That chain is sound; it just receives bad
events and renders them badly.

## Design

Three small layers, each with one job. Keep `KickDetector` (it is good on
4/4); add amplitude and sustained-bass rejection to it, add one gain stage in
`PulseHistory`, and replace the QML flash with an envelope.

### 1. Detector: reject sustained bass, report level (`beats.py`)

- **Causal harmonic reference (Stark 2013 / FitzGerald).** Keep the last
  ~16 low-band spectra (160 ms). Before the flux, subtract the per-bin
  median of those frames from the current frame and half-wave rectify. A held
  sub, a slow swell or an LFO wobble becomes its own reference and produces
  almost no flux; a kick landing on it still jumps.
- **Log compression** of magnitudes (`log10(1 + γ|X|)`, SuperFlux) so
  limited masters do not flatten the flux. Max filter down to 3 bins.
- **Attack sharpness.** A kick peaks in < 5 ms; a 1/8-note wobble takes
  ~50 ms to rise. Require `(E(n) − E(n−1)) / (E(n) − E(n−4)) ≥ 0.6` on the
  30–150 Hz energy.
- **`Kick.level`**: the 30–120 Hz level at the confirmed peak (the detector
  already computes `level`). This replaces `strength` as the amplitude
  source. Minimum gap stays 60 ms.
- The one-frame lookahead already used for peak confirmation may grow to
  30 ms (`post_max`); the decode buffer covers it.

### 2. Pulse gain: relative level with a soft gate (`PulseHistory`)

- **Running reference** `ref = max(level, ref · e^(−Δt/τ))` with
  τ = 2 bars from the known BPM (local tracks carry one) or 4 s otherwise,
  floored at an absolute noise level so silence is never normalised up.
- **Amplitude** `amp = clamp((level / ref − 0.5) / 0.5, 0, 1)`. A kick at the
  reference pulses fully; a stab at 70 % of it pulses at 40 %; below half it
  is not a pulse. Quiet passages after a loud section still pulse, only
  dimmer — the probe showed a hard gate at 0.75 with a 6 s memory silences
  the real kicks in Kalipo's outro (1.6 /s → 0.1 /s), so this must be a soft
  gain, not a threshold.
- Measured with a hard prototype of this gate (threshold 0.75, τ = 6 s):
  Highscore drops fall from 3.4 /s to 2.0–2.5 /s and stay 94–96 % on the
  grid; Kalipo and Kaktus drops are unchanged.
- **Rhythm depth (phase 2, only if breakdown false pulses remain).** Keep the
  last 8 accepted kicks; confidence = share of inter-onset intervals that are
  1, 2, 3 or 4 × a common period within ±4 %. Depth ramps 0 → 1 over one bar
  when confident and 1 → 0 over two bars when not; the remembered period
  reopens the gate at once when two kicks land on it (the drop). Feed it
  kicks only, never full-band flux, or hi-hats keep it open in breaks.
- Event shape: `pulses` become `[time, amp]`, and the event carries
  `period` (seconds per beat: track BPM if known, else the median accepted
  gap) so the UI can size its release.

### 3. Rendering: envelope, colour, record (`Main.qml`)

- **Envelope instead of animations.** One scalar `flash` updated in the
  existing `FrameAnimation`: attack 25 ms (two frames, ease-out), hold
  ~40 ms, then `flash *= e^(−dt/τ)` with τ = 0.3 × beat, clamped to 90–180 ms
  (128 BPM → ~140 ms; 174 BPM → ~100 ms). Visible pulse ≈ 0.7 of a beat.
- **Peak-hold, never sum.** A hit sets `flash = max(flash, amp)`. Hits under
  100 ms apart merge; a hit within half a beat of the previous one is scaled
  by 0.6 so main kicks stay accented and rolls read as one swell.
- **Colour.** Replace `neon()` with a precomputed peak colour per palette
  tone: same hue, chroma × 1.25, lightness + 0.05 in the dark theme and
  + 0.02 in the light theme (OKLCH; approximated in QML via `Qt.hsla` with
  saturation + 0.15 and lightness + 0.05/0.02, checked per tone). Keep the
  two-layer opacity crossfade: opacity is free in the scene graph, a Canvas
  repaint is not. Bound per pulsed colour pair: peak-to-trough relative
  luminance < 0.05 and red value `(R−G−B)×320` change < 15 (bordeaux). One
  offline test asserts both on the palette.
- **Animations off** shows the base colours as today (the spec already
  states it); no static mid-level colour is needed.
- **Blacker record.** Today the disc is 60 % black over the coloured
  backdrop, which is why it reads as tinted. Draw it opaque `#0c0c0e`, add
  groove rings at white 6 % alpha every 2 px and one sheen wedge to white
  12 %, lower the label contrast (tone mixed 20 % toward the surface). The
  disc keeps turning and never pulses; only the backdrop glow does.

### Plumbing

- `Player.beats()` returns `(pulses, period)`; `backend.tick` sends
  `pulses` and `period`; `bridge.beats` unchanged in shape apart from the new
  key. `energy` is dropped (always 0 today).
- Spec §3.4 (detector, history, event) and §3.8 (flash/envelope/colour/
  record) are updated in the same change; `CHANGELOG.md` Unreleased entry.

## Validation

- **Real tracks, read-only.** Promote the probe into
  `scripts/evaluate_pulse.py <audio>`: prints pulses per 10 s, mean amplitude
  and alignment to a librosa grid (librosa is the optional `analyze` extra).
  Acceptance on the three tracks above: Highscore drops ≤ 1.3 × the librosa
  beat rate with ≥ 90 % on the 16th grid; Kalipo intro 0 pulses and outro
  ≥ 80 % of its raw count; Kaktus 45–65 s ≤ 1.5 × the beat rate.
- **Offline tests (`tests/test_beats.py`).** Wobble + kicks mix yields ≤ 1.1 ×
  the kick count (today the sweep/growl tests pass only because the synthetic
  bass has no attack); a loud section followed by kicks 8 dB quieter still
  reports every kick with amp ≥ 0.4; `level` monotone in kick gain; the
  palette safety bound.
- **Listening.** Timing on the user's device with View → Pulse timing; this
  plan changes no latency.

## Skipped and why

- No predicted beats, no beat tracker driving visuals (BTrack, BeatNet):
  both carry momentum through breakdowns, the exact behaviour to avoid.
- No second analysis decoder several seconds ahead: the causal median plus
  the soft gain already handle intros and swells; revisit only if phase 2
  rhythm depth is still not enough.
- No shader effects, no extra dependencies: opacity crossfade of two
  pre-painted layers is the cheapest correct rendering.

## Sources

- Böck & Widmer 2013, SuperFlux: https://www.dafx.de/paper-archive/2013/papers/09.dafx2013_submission_12.pdf
- Stark 2013, causal percussive beat tracking: https://adamstark.co.uk/pdf/papers/percussive-beat-tracking-2013.pdf
- Dixon 2006 peak picking: https://www.dafx.de/paper-archive/2006/papers/p_133.pdf
- Meier, Krause, Müller 2024, real-time PLP: https://transactions.ismir.net/articles/10.5334/tismir.189
- projectM loudness (bass / long average): https://github.com/projectM-visualizer/projectm/blob/master/src/libprojectM/Audio/Loudness.cpp
- WLED audioreactive envelopes and squelch: https://github.com/wled/WLED/blob/main/usermods/audioreactive/audio_reactive.cpp
- MilkDrop preset authoring (bass, bass_att, decay): https://www.geisswerks.com/milkdrop/milkdrop_preset_authoring.html
- WCAG 2.3.1 flash thresholds: https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html
- ITU-R BT.1359 audio/video sync tolerance: https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1359-1-199811-I!!PDF-E.pdf
- OKLCH gamut mapping: https://evilmartians.com/chronicles/oklch-in-css-why-quit-rgb-hsl
