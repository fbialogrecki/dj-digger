"""Builders shared across test modules - plain functions, imported rather than injected."""

import array
import math
import random

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
    noise = random.Random(1)
    out = [0.15 * (noise.random() * 2 - 1) + bass * math.sin(2 * math.pi * 45 * i / rate) for i in range(int(seconds * rate))]

    def add(start, length, sound):
        first = int(start * rate)
        for i in range(min(int(length * rate), len(out) - first)):
            out[first + i] += sound(i / rate)

    for start in kicks:
        add(start, .25, lambda x: .6 * math.exp(-x * 12) * math.sin(2 * math.pi * (50 * x + 2 * (1 - math.exp(-x * 40)))))
    for start in snares:
        add(start, .25, lambda x: .4 * math.exp(-x * 25) * (noise.random() * 2 - 1))
    for start in bass_notes:
        add(start, .1, lambda x: .25 * math.sin(2 * math.pi * 55 * x))
    return array.array("h", (int(max(-1, min(1, v)) * 32767) for v in out for _ in range(2)))
