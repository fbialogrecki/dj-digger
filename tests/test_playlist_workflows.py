import json
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dj_digger.db import Database
from dj_digger.models import Track
from dj_digger.services import collection
from dj_digger.services.opening import soundcloud_track_url, track_search_url
from dj_digger.services.soundcloud_playlists import write_tracks

OWNER = (7, 'https://soundcloud.com/owner')
DEST = OWNER[1] + '/sets/my-set'
ME = dict(id=OWNER[0], permalink_url=OWNER[1])


def song(i):
    return Track(f'Track {i}', f'https://soundcloud.com/artist/track-{i}', id=i)


def test_track_search_and_direct_links_are_separate():
    for provider, prefix in [('YouTube', 'https://www.youtube.com/results?search_query='),
                             ('YouTube Music', 'https://music.youtube.com/search?q='),
                             ('Bandcamp', 'https://bandcamp.com/search?q='),
                             ('Beatport', 'https://www.beatport.com/search?q='),
                             ('Spotify', 'https://open.spotify.com/search/')]:
        assert track_search_url(provider, 'A & B', 'X/Y').startswith(prefix)
        assert '%26' in track_search_url(provider, 'A & B', 'X/Y')
        assert '%2F' in track_search_url(provider, 'A & B', 'X/Y')
    assert soundcloud_track_url('https://m.soundcloud.com/a/b?tracking=1') == 'https://soundcloud.com/a/b'
    for invalid in ['https://example.org/song', 'https://soundcloud.com.example/a/b',
                    'https://soundcloud.com/you/likes', 'https://soundcloud.com/a/sets/b',
                    'https://soundcloud.com/a/b/s-private', 'https://soundcloud.com/a%2Fb/c']:
        assert soundcloud_track_url(invalid) is None


def test_recommendation_snapshot_requires_every_member_and_keeps_each_day_and_source(tmp_path, monkeypatch):
    target = 'https://soundcloud.com/discover/sets/weekly::listener'
    ids = [2, 1, 2]
    payload = {'data': {'permalink_url': target, 'kind': 'system-playlist',
                        'tracks': [dict(id=i) for i in ids]}, 'hydratable': 'systemPlaylist'}
    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, n):
            yield ('<script>window.__sc_hydration = ' + json.dumps([payload]) + ';</script>').encode()

    page = Mock(return_value=Response())
    monkeypatch.setattr('requests.get', page)
    hydrate = Mock(return_value=[song(1), song(2)])
    monkeypatch.setattr(collection.soundcloud.SoundCloudClient, 'hydrate_tracks', hydrate)
    db = Database(tmp_path / 'snap.db')
    service = collection.CollectionService(lambda: db)

    def collect(day):
        return service.collect(target, collection.DigOptions(), db.snapshot_generations(), 'none', None, None, None,
                               snapshot_date=day).record
    try:
        first = collect('2026-11-17')
        assert first.title == 'Weekly Wave — 2026-11-17'
        assert [t.id for t in first.active_tracks] == ids
        assert collect('2026-11-17') == first and page.call_count == 1
        hydrate.return_value = [song(2)]
        with pytest.raises(ValueError, match='Incomplete'):
            collect('2026-11-18')
        assert len(db.list_crate_headers()) == 1
        payload['data']['tracks'] = [dict(id=2)]
        assert collect('2026-11-18').source != first.source
        target = target.replace('listener', 'second-listener')
        payload['data']['permalink_url'] = target
        assert collect('2026-11-18').source != first.source
        assert len(db.list_crate_headers()) == 3
    finally:
        db.close()


def test_soundcloud_write_checks_identity_membership_and_preserves_order(tmp_path):
    tracks = [song(2), song(1)]
    members = []
    created = []
    client = Mock(oauth_token='session')

    def detail():
        return dict(id=11, kind='playlist', user_id=OWNER[0], permalink_url=DEST, title='My set', sharing='private',
                    tracks=[dict(id=i) for i in members], track_count=len(members))

    def submit(command, args):
        assert command == 'playlist.writeTrack' and args['account'] == OWNER[1]
        i = next(t.id for t in tracks if t.permalink_url == args['track'])
        assert i not in members
        members.append(i)
        created.append(args['destination'])
        f = Future()
        f.set_result(dict(destination=DEST))
        return 1, f

    client.resolve.side_effect = lambda url: detail() if url == DEST else None
    client._get.side_effect = lambda path: ME if path == '/me' else detail()
    client.hydrate_tracks.side_effect = lambda ids, cancel: [song(i) for i in ids]
    session = SimpleNamespace(error=None, submit=submit)
    db = Database(tmp_path / 'writes.db')
    try:
        def write(picked, **kwargs):
            return write_tracks(client, session, OWNER, picked, db, db.snapshot_generations(), None,
                                progress=lambda *args: None, **kwargs)
        assert write(tracks, title='My set') == (2, 0)
        assert created == ['', DEST] and members == [2, 1]
        assert write(tracks, identifier=11) == (0, 2)
        assert len(created) == 2
        assert db.load_crate(DEST)['owner_id'] == 7
        client._get.side_effect = lambda path: ME if path == '/me' else dict(detail(), user_id=99)
        with pytest.raises(ValueError, match='owner'):
            write(tracks, identifier=11)
        assert len(created) == 2
    finally:
        db.close()


def test_browser_failure_after_create_is_not_retried():
    track = song(42)
    client = Mock(oauth_token='session')
    client._get.return_value = ME
    client.hydrate_tracks.return_value = [track]
    session = Mock(error=None)
    f = Future()
    f.set_exception(RuntimeError('connection lost'))
    session.submit.return_value = (1, f)
    with pytest.raises(RuntimeError, match='connection lost'):
        write_tracks(client, session, OWNER, [track], Mock(), {}, None, title='Mine', progress=Mock())
    session.submit.assert_called_once()


def test_write_refuses_changed_account_and_excess_tracks_before_mutation():
    client = Mock(oauth_token='session')
    client._get.return_value = dict(id=99, permalink_url='https://soundcloud.com/other')
    session = Mock(error=None)
    with pytest.raises(ValueError, match='account'):
        write_tracks(client, session, OWNER, [song(1)], Mock(), {}, None, title='Mine', progress=Mock())
    with pytest.raises(ValueError, match='500'):
        write_tracks(client, session, OWNER, [song(i) for i in range(1, 502)], Mock(), {}, None, title='Mine', progress=Mock())
    client.hydrate_tracks.assert_not_called()
    session.submit.assert_not_called()


def test_full_destination_is_checked_before_any_form_write():
    client = Mock(oauth_token='session')
    full = dict(id=9, kind='playlist', user_id=OWNER[0], permalink_url=DEST, track_count=500,
                tracks=[dict(id=i) for i in range(1, 501)])
    client._get.side_effect = lambda path: ME if path == '/me' else full
    selected = [song(1), song(501)]
    client.hydrate_tracks.return_value = selected
    session = Mock(error=None)
    with pytest.raises(ValueError, match='500-track limit'):
        write_tracks(client, session, OWNER, selected, Mock(), {}, None, identifier=9, progress=Mock())
    session.submit.assert_not_called()


def test_summary_track_id_must_match_its_canonical_permalink():
    client = Mock(oauth_token='session')
    client._get.return_value = ME
    client.hydrate_tracks.return_value = [Track('Other', OWNER[1] + '/other', id=1)]
    session = Mock(error=None)
    with pytest.raises(ValueError, match='selected track'):
        write_tracks(client, session, OWNER, [song(1)], Mock(), {}, None, title='Mine', progress=Mock())
    session.submit.assert_not_called()
