import array

import numpy as np
import pytest
from helpers import BEAT, KICKS, RATE, drums, song

from dj_digger import beats
from dj_digger.beats import GENERIC, KickEnergy


def levels(audio, energy=None, start=0.0, chunk=4410, keep=1000.):
    """(times, levels) of every 10 ms the analysis published, fed like the player feeds it."""
    energy = energy or KickEnergy()
    for offset in range(0, len(audio), chunk):
        energy.feed(audio[offset:offset + chunk], start + offset / 2 / RATE)
    first, step, values = energy.levels(-1., keep)
    return first + np.arange(len(values)) * step, np.array(values)


def at_kicks(times, values, kicks):
    """The level at each kick: its highest within 20 ms before to 30 ms after."""
    return np.array([values[(times >= k - .02) & (times <= k + .03)].max() for k in kicks])


def between_kicks(times, values, kicks):
    """Levels away from every kick and its first 150 ms, from the first kick on."""
    away = times >= kicks[0]
    for k in kicks:
        away &= (times < k - .03) | (times > k + .15)
    return values[away]


@pytest.fixture(autouse=True)
def keep_everything(monkeypatch):
    monkeypatch.setattr(beats, 'KEEP', 1000.)


# The level at the kicks, where it falls short of 0.9: a bass note on every kick
# blurs its fingerprint, and a fast, clicky or a deep kick against a moving bass
# is the hardest to match.
KICK_LEVEL = {('walking', 'punch'): .7, ('walking', 'driven'): .75, ('walking', 'deep'): .85,
              ('reese', 'deep'): .7}
# Low hits between the kicks glow in proportion, at most this bright; a long 808
# over a sidechained sub leaks the most.
LEAK = {('sidechained sub', '808'): .7}


@pytest.mark.parametrize('kick', KICKS)
@pytest.mark.parametrize('bass', ['none', 'offbeat', 'one plucked note', 'walking', 'rolling', 'sidechained sub', 'reese'])
def test_kicks_are_brightest_and_the_bass_between_them_glows_dimmer(bass, kick):
    audio, kicks = song(bass, kick)
    times, values = levels(audio)
    assert np.percentile(at_kicks(times, values, kicks), 10) >= KICK_LEVEL.get((bass, kick), .9)
    assert between_kicks(times, values, kicks).max() <= LEAK.get((bass, kick), .45)
    if bass in ('none', 'rolling', 'sidechained sub') and kick != '808':
        assert between_kicks(times, values, kicks).max() <= .15  # Held or quick bass stays near dark.


@pytest.mark.parametrize('bass', ['offbeat', 'one plucked note', 'rolling', 'sidechained sub', 'walking'])
def test_a_bass_louder_than_the_kick_still_leaves_the_kick_brightest(bass):
    audio, kicks = song(bass, gain=1.5)
    times, values = levels(audio)
    assert np.percentile(at_kicks(times, values, kicks), 10) >= (.75 if bass == 'walking' else .9)
    assert between_kicks(times, values, kicks).max() <= .45


@pytest.mark.parametrize('bass', ['kickless stabs', 'kickless one plucked note', 'kickless walking',
                                  'kickless pad', 'kickless held sub'])
def test_a_breakdown_after_the_drop_glows_dimmer_than_its_kicks(bass):
    # The breakdown is measured against the drop's kicks, and a repeated plucked
    # note never becomes the track's kick: it does not drop in pitch.
    drop, _ = song('offbeat', bars=6)
    breakdown, _ = song(bass, bars=6)
    energy = KickEnergy()
    times, values = levels(drop + breakdown, energy)
    after = values[times > len(drop) / 2 / RATE + .2]
    assert np.percentile(after, 95) <= .55
    if bass in ('kickless pad', 'kickless held sub'):
        assert after.max() == 0
    assert energy.kick_print is not None and beats._falls(energy.kick_print)


@pytest.mark.parametrize('bass', ['kickless pad', 'kickless held sub'])
def test_held_sound_alone_never_lights_up(bass):
    energy = KickEnergy()
    assert levels(song(bass)[0], energy)[1].max() == 0
    assert energy.kick_print is None


def test_silence_is_dark():
    assert levels(array.array('h', bytes(4 * RATE * 2)))[1].max() == 0


@pytest.mark.parametrize('bpm', [128, 174, 210])
def test_every_kick_stands_apart_at_any_tempo(bpm):
    beat = 60 / bpm
    kicks = [.25 + i * beat for i in range(24)]
    times, values = levels(drums(kicks, kicks[-1] + .6))
    assert at_kicks(times, values, kicks).min() >= .9
    # The light falls to nothing between kicks, so even 210 BPM pulses, not glows.
    assert max(values[(times > a + .05) & (times < b - .03)].min() for a, b in zip(kicks, kicks[1:])) <= .05


def test_the_level_peaks_on_the_kick():
    audio, kicks = song('offbeat')
    times, values = levels(audio)
    offsets = [times[(times >= k - .05) & (times <= k + .05)][np.argmax(values[(times >= k - .05) & (times <= k + .05)])] - k
               for k in kicks]
    assert np.abs(np.median(offsets)) <= .012 and np.max(np.abs(offsets)) <= .03


def test_a_quieter_section_recovers_within_two_bars():
    loud, kicks = song('offbeat', bars=6)
    quiet, _ = song('offbeat', bars=6)
    quiet = array.array('h', (np.frombuffer(quiet, dtype=np.int16) * 10 ** (-9 / 20)).astype(np.int16).tobytes())
    offset = len(loud) / 2 / RATE
    times, values = levels(loud + quiet)
    later = at_kicks(times, values, [offset + k for k in kicks])
    assert later[8:].min() >= .9


def test_a_seek_keeps_the_kick_and_the_loudness_it_learned():
    audio, kicks = song('offbeat')
    energy = KickEnergy()
    levels(audio[:2 * RATE * 6], energy)
    learned = energy.kick_print
    assert learned is not None
    energy.seeked()
    assert energy.levels(0, 100)[2] == []
    start = 13.0
    times, values = levels(audio[2 * int(start * RATE):], energy, start=start)
    assert energy.kick_print is not None
    # Kicks after the fresh analysis' 130 ms warm-up light up at once.
    later = [k for k in kicks if k > start + .15]
    assert at_kicks(times, values, later).min() >= .9


def test_a_level_is_ready_a_fifth_of_a_second_after_its_sound():
    energy = KickEnergy()
    audio = drums([.3], 1)
    ready = None
    for offset in range(0, len(audio), 882):  # 10 ms chunks.
        energy.feed(audio[offset:offset + 882], offset / 2 / RATE)
        first, step, values = energy.levels(.29, .34)
        if ready is None and values and max(values) >= .9:
            ready = (offset + 882) / 2 / RATE
    # Well inside the player's 0.4 s of decoded lookahead.
    assert ready is not None and ready - .3 <= .25


def test_readers_get_a_bounded_window():
    energy = KickEnergy()
    levels(drums([.25, .7], 1.2), energy)
    first, step, values = energy.levels(.5, .8)
    # Levels between dark and full included: Qt cannot pass a numpy float to the window.
    everything = energy.levels(-1, 100)[2]
    assert {type(v) for v in everything} == {float} and any(0 < v < 1 for v in everything)
    assert step == pytest.approx(.01, abs=.001)
    assert .5 - step < first <= .5 + step and len(values) == pytest.approx(30, abs=2)
    assert energy.levels(50, 60)[2] == []


def test_channel_phase_and_callback_boundaries_do_not_change_levels():
    audio = drums([.25, .7, 1.15], 1.6)
    inverted = array.array('h', (-v if i % 2 else v for i, v in enumerate(audio)))
    reference = levels(audio)[1]
    for pcm in (audio, inverted):
        for chunk in (882, 2054, 4410):
            assert levels(pcm, chunk=chunk)[1] == pytest.approx(reference, abs=1e-6)


def modulated_bass(kind, seconds=4):
    """Kickless amplitude wobble, pitch sweeps and a harmonic growl at 4 then 8 Hz."""
    time = np.arange(int(seconds * RATE)) / RATE
    rate = np.where(time < 2, 4, 8)
    modulation = (1 - np.cos(2 * np.pi * rate * time)) / 2
    amplitude = .65 * (.08 + .92 * modulation) if kind == 'wobble' else .5
    frequency = 55 if kind == 'wobble' else 45 + 155 * modulation
    phase = np.cumsum(2 * np.pi * np.broadcast_to(frequency, time.shape) / RATE)
    sound = np.sin(phase)
    if kind == 'growl':
        sound = (sound + .3 * np.sin(3 * phase)) / .9
    return array.array('h', np.repeat(amplitude * sound * 30000, 2).astype(np.int16).tobytes())


@pytest.mark.parametrize('kind', ['sweep', 'growl'])
def test_kicks_still_light_up_over_a_bass_sweeping_through_them(kind):
    kicks = [.25 + i * .45 for i in range(8)]
    bass = np.frombuffer(modulated_bass(kind, 4.5), dtype=np.int16)
    drum = np.frombuffer(drums(kicks, 4.5), dtype=np.int16)
    mixed = array.array('h', np.clip(bass.astype(int) // 2 + drum, -32767, 32767).astype(np.int16).tobytes())
    times, values = levels(mixed)
    assert np.median(at_kicks(times, values, kicks)) >= .5


@pytest.mark.parametrize('gain', [1, 4, 8, 12])
def test_heavy_limiting_does_not_hide_kicks(gain):
    kicks = [.25 + i * .5 for i in range(8)]
    pcm = np.asarray(drums(kicks, 4.5), dtype=float) / 32768
    limited = array.array('h', (np.tanh(pcm * gain) * 28000).astype('int16').tobytes())
    times, values = levels(limited)
    assert at_kicks(times, values, kicks).min() >= .9


def test_a_clap_on_the_kick_does_not_hide_it():
    # A short 200-1500 Hz clap 10 ms ahead of each kick outweighs its bass at the
    # attack, as in DHS - The House of God.
    kicks = [.25 + i * BEAT for i in range(8)]
    drum = np.asarray(drums(kicks, 4.2), dtype=float).reshape(-1, 2)[:, 0] / 32768
    spectrum = np.fft.rfft(np.random.default_rng(1).standard_normal(len(drum)))
    frequencies = np.fft.rfftfreq(len(drum), 1 / RATE)
    spectrum[(frequencies < 200) | (frequencies > 1500)] = 0
    clap = np.fft.irfft(spectrum, len(drum))
    clap /= np.abs(clap).max()
    envelope = np.zeros(len(drum))
    for t in kicks:
        start = int((t - .01) * RATE)
        envelope[start:start + int(.2 * RATE)] = np.exp(-np.arange(int(.2 * RATE)) / RATE * 70)
    mixed = np.clip(.5 * drum + 1.5 * clap * envelope, -1, 1)
    times, values = levels(array.array('h', np.repeat((mixed * 32767).astype('int16'), 2).tobytes()))
    assert at_kicks(times, values, kicks).min() >= .9


def test_the_generic_kick_drops_in_pitch():
    assert GENERIC.shape == (80,) and np.linalg.norm(GENERIC) == pytest.approx(1)
    assert beats._falls(GENERIC)
