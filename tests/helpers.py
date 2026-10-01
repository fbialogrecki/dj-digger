"""Builders shared across test modules - plain functions, imported rather than injected."""

import array

import numpy as np

from dj_digger.models import Crate, Track


def a_crate(
    count=3,
    *,
    source="https://soundcloud.com/a/sets/b",
    title="A crate",
    declared_count=None,
    **track_fields,
):
    """A crate of ``count`` tracks with ids 100, 101, ...; ``track_fields`` go on each."""

    return Crate(
        source=source,
        title=title,
        declared_count=declared_count,
        tracks=[
            Track(
                title=f"T{index}",
                permalink_url=f"https://soundcloud.com/a/{index}",
                id=100 + index,
                **track_fields,
            )
            for index in range(count)
        ],
    )



def drums(kicks, seconds, *, snares=(), bass=0.0, bass_notes=()):
    """Stereo 16-bit audio at 44.1 kHz: a pitched-down kick at each time over hats,
    noise-burst snares, short 55 Hz bass notes and an optional held 45 Hz bass."""

    rate = 44100
    noise = np.random.default_rng(1)
    time = np.arange(int(seconds * rate)) / rate
    out = .15 * noise.uniform(-1, 1, len(time)) + bass * np.sin(2 * np.pi * 45 * time)

    def add(start, length, sound):
        first = int(start * rate)
        x = np.arange(max(0, min(int(length * rate), len(out) - first))) / rate
        out[first:first + len(x)] += sound(x)

    for start in kicks:
        add(start, .25, lambda x: .6 * np.exp(-x * 12) * np.sin(2 * np.pi * (50 * x + 2 * (1 - np.exp(-x * 40)))))
    for start in snares:
        add(start, .25, lambda x: .4 * np.exp(-x * 25) * noise.uniform(-1, 1, len(x)))
    for start in bass_notes:
        add(start, .1, lambda x: .25 * np.sin(2 * np.pi * 55 * x))
    return array.array("h", (np.repeat(np.clip(out, -1, 1), 2) * 32767).astype(np.int16).tobytes())


RATE = 44100
BEAT = 60 / 128


def stereo(mono):
    return array.array('h', (np.repeat(np.tanh(1.2 * mono), 2) * 32767).astype(np.int16).tobytes())


def kick_sound(start=130, end=50, sweep=40, decay=12, click=0., drive=1., length=.4):
    """A pitched-down kick. Every one here differs from the detector's generic kicks."""
    x = np.arange(int(length * RATE)) / RATE
    kick = np.exp(-x * decay) * np.sin(2 * np.pi * (end * x + (start - end) * (1 - np.exp(-x * sweep)) / sweep))
    kick += click * np.random.default_rng(3).standard_normal(len(x)) * np.exp(-x * 400)
    return np.tanh(drive * kick) / np.tanh(drive)


KICKS = {'plain': kick_sound(), '808': kick_sound(100, 43, 20, 2.5, length=.9),
         'punch': kick_sound(240, 58, 70, 20, click=.25), 'driven': kick_sound(170, 48, 50, 9, drive=2.5),
         'deep': kick_sound(85, 40, 28, 6)}


def note(frequency, length, decay=0.):
    """A bass note: saw-like harmonics, 5 ms attack, 10 ms release, optional pluck decay."""
    x = np.arange(int(length * RATE)) / RATE
    tone = sum(np.sin(2 * np.pi * frequency * h * x) / h for h in (1, 2, 3)) / 1.5
    return tone * np.minimum(1, x / .005) * np.minimum(1, (length - x) / .01) * np.exp(-x * decay)


def add(out, time, sound, gain=1.):
    first = int(time * RATE)
    count = max(0, min(len(sound), len(out) - first))
    out[first:first + count] += gain * sound[:count]


def song(bass, kick='plain', bars=12, gain=1., kicks_from=0):
    """Kicks on every beat of 128 BPM over offbeat hats and one kind of bass, soft-limited."""
    rng = np.random.default_rng(0)
    out = np.zeros(int((bars * 4 * BEAT + .6) * RATE))
    time = np.arange(len(out)) / RATE
    beats = [.25 + i * BEAT for i in range(bars * 4)]
    kicks = [] if bass.startswith('kickless') else beats[4 * kicks_from:]
    for at in kicks:
        add(out, at, .5 * KICKS[kick])
    hat = .08 * rng.standard_normal(int(.03 * RATE)) * np.exp(-np.arange(int(.03 * RATE)) / RATE * 150)
    for at in beats:
        add(out, at + BEAT / 2, hat)
    if bass in ('offbeat', 'kickless stabs'):
        for at in beats:  # Notes on the offbeat, or on the beat where no kick plays.
            add(out, at + BEAT / 2 * (bass == 'offbeat'), note(rng.choice([55, 65, 73]), .15), .35 * gain)
    elif bass in ('one plucked note', 'kickless one plucked note'):
        for at in beats:
            add(out, at + BEAT / 2, note(55, BEAT / 2, decay=10), .45 * gain)
    elif bass in ('walking', 'kickless walking'):
        for i in range(bars * 8):
            add(out, .26 + i * BEAT / 2, note(rng.uniform(40, 100), BEAT / 2, decay=6), .35 * gain)
    elif bass == 'rolling':
        for at in beats:
            for sixteenth in (1, 2, 3):
                add(out, at + sixteenth * BEAT / 4, note(55, .1, decay=8), .35 * gain)
    elif bass == 'sidechained sub':
        duck = np.ones(len(out))
        for at in kicks:
            first, count = int(at * RATE), int(.25 * RATE)
            duck[first:first + count] = np.minimum(duck[first:first + count], .1 + .9 * np.arange(count) / count)
        out += .5 * gain * np.sin(2 * np.pi * 50 * time) * duck
    elif bass == 'reese':
        out += .25 * gain * (np.sin(2 * np.pi * 49.5 * time) + np.sin(2 * np.pi * 50.5 * time)) * (.7 + .3 * np.sin(np.pi * time))
    elif bass == 'kickless pad':
        out += .15 * np.sin(2 * np.pi * 55 * time) * np.minimum(1, time) * (.8 + .2 * np.sin(np.pi * time / 2))
    elif bass == 'kickless held sub':
        out += .5 * np.sin(2 * np.pi * 45 * time)
    return stereo(out), kicks


def matched(found, truth, tolerance=.025):
    """(true hits, false hits, missed kicks, worst timing error in seconds)."""
    remaining, errors = list(truth), []
    for time in found:
        near = min(remaining, key=lambda t: abs(t - time), default=None)
        if near is not None and abs(near - time) <= tolerance:
            remaining.remove(near)
            errors.append(abs(near - time))
    return len(errors), len(found) - len(errors), len(remaining), max(errors, default=0)


