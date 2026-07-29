from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dj_digger.models import Track

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def track_payloads() -> List[Dict[str, Any]]:
    """Four real tracks, trimmed, covering every categorisation branch."""

    return load_fixture("tracks.json")


@pytest.fixture
def tracks(track_payloads: List[Dict[str, Any]]) -> List[Track]:
    return [Track.from_api(payload) for payload in track_payloads]


@pytest.fixture
def tracks_by_id(tracks: List[Track]) -> Dict[int, Track]:
    return {track.id: track for track in tracks if track.id}


@pytest.fixture
def playlist_payload() -> Dict[str, Any]:
    """A real /resolve reply: full envelope, id-only track stubs."""

    return load_fixture("playlist_resolve.json")
