import array
import math

import numpy as np
import pytest
from helpers import drums

from dj_digger.beats import KickDetector, PulseHistory

RATE = 44100


def track(audio, start=0.0, tracker=None, chunk=4410):
    tracker = tracker or PulseHistory()
    detector = KickDetector(tracker)
    for offset in range(0, len(audio), chunk):
        detector.feed(audio[offset:offset + chunk], start + offset / 2 / RATE)
    return tracker


def assert_hits(tracker, truth, start=0, end=100):
    pulses, period = tracker.pulses(start, end)
    assert [t for t, _ in pulses] == pytest.approx(truth, abs=.015)
    assert all(.5 <= amplitude <= 1 for _, amplitude in pulses)
    assert .25 <= period <= 1


def test_first_kick_is_available_without_tempo_warmup_or_prediction():
    tracker = track(drums([0], .08))
    assert_hits(tracker, [0])
    assert tracker.pulses(.05, 2)[0] == []


@pytest.mark.parametrize('bpm', [128, 174, 210])
def test_rolls_flash_each_real_hit_and_stop_at_the_breakdown(bpm):
    beat = 60 / bpm
    four = [i * beat for i in range(4)]
    eighths = [4 * beat + i * beat / 2 for i in range(4)]
    sixteenths = [6 * beat + i * beat / 4 for i in range(8)]
    truth = four + eighths + sixteenths
    tracker = track(drums(truth, truth[-1] + .08))
    assert_hits(tracker, [t for t in truth if t >= truth[-1] - 1.9], truth[-1] - 1.9)
    assert tracker.pulses(truth[-1] + .03, truth[-1] + 2)[0] == []


def test_bass_between_kicks_and_snares_do_not_add_flashes():
    beat = 60 / 128
    truth = [i * beat for i in range(24)]
    tracker = track(drums(truth, 24 * beat, bass_notes=[t + beat / 2 for t in truth],
                          snares=[t + beat / 4 for t in truth]))
    assert_hits(tracker, truth[-4:], truth[-4] - .02)


def test_quiet_intro_and_breakdown_do_not_invent_a_grid():
    # Slow bass pad with slight amplitude modulation, plus noisy high percussion.
    pad = array.array('h', (int(7000 * min(1, i / RATE) * (.8 + .15 * math.sin(i / RATE * 8))
                               * math.sin(2 * math.pi * 55 * i / RATE))
                           for i in range(3 * RATE) for _ in range(2)))
    assert track(pad).pulses(0, 10)[0] == []
    assert track(drums([], 3, snares=[i * .125 for i in range(24)])).pulses(0, 10)[0] == []
    tracker = track(drums([0], .1))
    assert_hits(tracker, [0])
    # A kickless gap has no predicted events, and the first drop kick returns at once.
    assert tracker.pulses(.1, 3)[0] == []
    track(drums([0], .08), start=3, tracker=tracker)
    assert_hits(tracker, [3])


def test_seek_discards_old_hits_and_detects_at_the_new_position():
    tracker = track(drums([0], .1))
    tracker.seeked()
    assert tracker.pulses(0, 100)[0] == []
    track(drums([0], .08), start=60, tracker=tracker)
    assert_hits(tracker, [60])


def test_channel_phase_and_callback_boundaries_do_not_change_hits():
    audio = drums([0, .45, .9], 1)
    inverted = array.array('h', (v if i % 2 == 0 else -v for i, v in enumerate(audio)))
    for pcm in (audio, inverted):
        for chunk in (882, 2054, 4410):
            assert_hits(track(pcm, chunk=chunk), [0, .45, .9])


def modulated_bass(kind, seconds=4):
    """Kickless amplitude wobble, pitch sweeps and a harmonic growl at 4 then 8 Hz."""
    samples = array.array('h')
    phase = 0
    for i in range(int(seconds * RATE)):
        time = i / RATE
        rate = 4 if time < 2 else 8
        modulation = (1 - math.cos(2 * math.pi * rate * time)) / 2
        amplitude = .65 * (.08 + .92 * modulation) if kind == 'wobble' else .5
        frequency = 55 if kind == 'wobble' else 45 + 155 * modulation
        phase += 2 * math.pi * frequency / RATE
        sound = math.sin(phase)
        if kind == 'growl':
            sound = (sound + .3 * math.sin(3 * phase)) / .9
        samples.extend([int(amplitude * sound * 30000)] * 2)
    return samples


def all_hits(audio):
    detector = KickDetector()
    return [hit for offset in range(0, len(audio), 4410)
            for hit in detector.feed(audio[offset:offset + 4410], offset / 2 / RATE)]


@pytest.mark.parametrize('kind', ['wobble', 'sweep', 'growl'])
def test_changing_loud_bass_alone_is_not_a_kick_roll(kind):
    # An abruptly started tone can have one onset; changing its pitch or
    # amplitude must not turn the sustained note into a roll.
    hits = all_hits(modulated_bass(kind))
    assert len(hits) <= 1
    assert all(hit.time <= .015 for hit in hits)


@pytest.mark.parametrize('kind', ['sweep', 'growl'])
def test_kicks_survive_on_top_of_changing_bass(kind):
    truth = [.25 + i * .5 for i in range(8)]
    bass = modulated_bass(kind)
    kicks = drums(truth, 4)
    mixed = array.array('h', (max(-32767, min(32767, a // 2 + b)) for a, b in zip(bass, kicks)))
    assert [k.time for k in all_hits(mixed) if k.time > .015] == pytest.approx(truth, abs=.015)


def test_first_attack_arrives_within_100ms_and_never_emits_twice():
    history = PulseHistory()
    detector = KickDetector(history)
    audio = drums([0], .1)
    published = []
    for offset in range(0, len(audio), 882):  # Actual 10 ms input chunks.
        hits = detector.feed(audio[offset:offset + 882], offset / 2 / RATE)
        published.extend((hit, (offset + 882) / 2 / RATE) for hit in hits)
    assert len(published) == 1
    hit, delivered = published[0]
    assert hit.time == pytest.approx(0, abs=.015)
    assert delivered <= .1  # The attack, then 40 ms of its body for the level.
    assert history.pulses(0, .1)[0] == [(hit.time, 1.0)]


@pytest.mark.parametrize('gain', [1, 4, 8, 12])
def test_heavy_limiting_does_not_hide_kicks(gain):
    # A soft limiter compresses both the kick and the background. The old
    # full-band growth/decay gates missed all eight hits at gains 8 and 12.
    truth = [.25 + i * .5 for i in range(8)]
    pcm = np.asarray(drums(truth, 4), dtype=float) / 32768
    limited = array.array('h', (np.tanh(pcm * gain) * 28000).astype('int16').tobytes())
    assert [hit.time for hit in all_hits(limited)] == pytest.approx(truth, abs=.015)


def quieter(audio, factor):
    return array.array('h', (sample // factor for sample in audio))


def test_half_level_hits_between_kicks_do_not_pulse_and_a_quieter_section_recovers():
    beat = 60 / 128
    kicks = [i * beat for i in range(8)]
    stabs = [t + beat / 2 for t in kicks]
    loud = drums(kicks, 8 * beat)
    half = quieter(drums(stabs, 8 * beat), 2)
    mixed = array.array('h', (max(-32767, min(32767, a + b)) for a, b in zip(loud, half)))
    # Kicks at 0 .. 7 beats, then only half-level kicks for two more bars.
    tracker = track(mixed + quieter(drums([i * beat for i in range(8)], 8 * beat), 2), tracker=PulseHistory(beat))
    pulses, period = tracker.pulses(0, 100)
    assert period == beat
    times = [t for t, _ in pulses]
    assert all(min(abs(t - k) for k in kicks + [8 * beat + i * beat for i in range(8)]) < .015 for t in times)
    assert all(a >= .8 for t, a in pulses if t < 8 * beat - .1)  # The kicks, never the stabs.
    later = [(t, a) for t, a in pulses if t >= 8 * beat - .1]
    assert later and later[-1][1] >= .5 and later[-1][0] >= 8 * beat + 4 * beat - .1  # Recovered within two bars.


def test_period_follows_the_track_bpm_or_the_gaps_between_hits():
    assert PulseHistory(60 / 174).period() == 60 / 174
    beat = 60 / 128
    tracker = track(drums([i * beat for i in range(6)], 6 * beat))
    assert tracker.period() == pytest.approx(beat, abs=.02)
    assert PulseHistory().period() == .5



def test_a_clap_on_the_kick_does_not_hide_it():
    # A short 200-1500 Hz clap 10 ms ahead of each kick outweighs its bass at the
    # attack, as in DHS - The House of God; the kick's body is still bass-led.
    beat = 60 / 128
    truth = [.25 + i * beat for i in range(8)]
    kicks = np.asarray(drums(truth, 4.2), dtype=float).reshape(-1, 2)[:, 0] / 32768
    spectrum = np.fft.rfft(np.random.default_rng(1).standard_normal(len(kicks)))
    frequencies = np.fft.rfftfreq(len(kicks), 1 / RATE)
    spectrum[(frequencies < 200) | (frequencies > 1500)] = 0
    clap = np.fft.irfft(spectrum, len(kicks))
    clap /= np.abs(clap).max()
    envelope = np.zeros(len(kicks))
    for t in truth:
        start = int((t - .01) * RATE)
        envelope[start:start + int(.2 * RATE)] = np.exp(-np.arange(int(.2 * RATE)) / RATE * 70)
    mixed = np.clip(.5 * kicks + 1.5 * clap * envelope, -1, 1)
    pcm = array.array('h', np.repeat((mixed * 32767).astype('int16'), 2).tobytes())
    assert [hit.time for hit in all_hits(pcm)] == pytest.approx(truth, abs=.015)
