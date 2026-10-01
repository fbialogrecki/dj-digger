# Kick light v3: a continuous kick level — 2026-10-01

Status: shipped (2026-10-01). `PROJECT-SPECIFICATION.md` §3.4 and §3.8 describe
what runs, and `docs/kick-pulse-validation.md` holds the measurements. This
replaces v2 (discrete kick decisions, shipped and replaced the same day) and v1
(2026-09-29).

## Why v2 was not enough (reported, then measured)

The user reported that the pulse sometimes stops, sometimes comes late, and gets
lost at higher tempo, for example on PYREZ — Everything In Its Right Place.

- **It stopped.** v2 flashed only on yes/no decisions: an onset over a fixed
  threshold that matched the learned kick. PYREZ is about 158 BPM with a dense
  low end. There it learned its kick only after 20 s, relearned it at 69, 77 and
  146 s, and produced no flash at all over 30-60 s and 110-170 s, although the
  low band had a regular hit every 380 ms.
- **Pulsing started late.** A track's kick took about five repeats to learn,
  and a new one waited 4 s more.
- **There was a visible lag on clear kicks.** On this machine (PipeWire, USB
  interface), miniaudio queues 46-71 ms and the rest of the path adds about
  25 ms. The fixed 100 ms latency was therefore about 30 ms late. Add the 25 ms
  rise and the light was about 50 ms behind the kick.
- **High tempo.** The ±120 ms median covered most of a kick-to-kick interval.
  The 80 ms fingerprint overlapped the next hit. The cap of three flashes a
  second and the 120-180 ms release blurred the pulses.

## The idea, from the visualizer the user liked

Avi Rzayev's Medium visualizer (librosa + pygame) does no beat detection at all:

- it precomputes a spectrogram of the whole file;
- it reads the column at `pygame.mixer.music.get_pos()`;
- it moves each bar toward that level with a 0.1 s lag.

It looks right because motion is continuous and proportional, read from analysis
that is ready before the sound, and smooth motion hides timing error. It reacts
to every frequency, the bass line included.

## What shipped

1. **A kick level, not decisions.** For every 10 ms hop, `KickEnergy` computes
   the 40-130 Hz residual above each bin's ±12-hop median, so held bass is
   gone. It weighs that residual by kick-likeness:
   - similarity to the generic kick at first, then to the track's learned kick
     (learned from repeats that drop in pitch, as in v2);
   - learning only sharpens the weighting and never gates.

   It then normalises against recent loudness, with a share of the last half
   minute so breakdowns stay dark, and applies a 1.5 power. The value is ready
   about 0.2 s after the sound, inside the 0.4 s decode lookahead.
2. **The window samples the level at the heard time.** It jumps up at once and
   falls in a straight line to dark within 150 ms, with nothing shown below a
   15 % gate. A 120 ms exponential release first looked always lit. There is no
   cap, no lateness gate and no envelope state machine.
3. **Heard time is measured.** `Player.heard()` is the handed position minus
   what the device still holds (the median over callbacks of handed minus
   played since the device started), minus 0.02 s for the server quantum and
   the card. The snapshot carries `heard` and `at`, so delivery delay is
   removed too.
4. **Look unchanged from v2**, except that the neon window now follows the
   cursor while it is lit.

## Skipped and why

- **librosa at runtime:** its useful parts are already numpy here. Its
  beat/onset tools are offline (whole-signal dynamic programming,
  centred windows). It also brings numba, scipy and scikit-learn into the
  desktop bundle. It is used offline as a reference in the evaluation.
- **A predicted beat grid (PLP and similar):** it would glow through breakdowns.
- **A latency query per OS:** the miniaudio binding exposes none. The queue
  measurement covers the large, variable part.
