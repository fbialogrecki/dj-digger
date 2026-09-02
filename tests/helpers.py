"""Builders shared across test modules - plain functions, imported rather than injected."""

import json

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


def page_with_hydration(payload: list) -> str:
    """A saved SoundCloud page whose ``window.__sc_hydration`` blob is ``payload``."""

    return (
        "<html><head><title>My Set | SoundCloud</title></head><body>"
        "<script>window.__sc_hydration = " + json.dumps(payload) + ";</script>"
        "</body></html>"
    )
