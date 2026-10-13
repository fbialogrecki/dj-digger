"""Owner playlist import without expensive external shop/gate resolution."""
import re
from collections import OrderedDict
from itertools import batched
from threading import Event
from urllib.parse import urlparse

from ..crate_models import CrateRecord
from ..models import Crate, Track, check_cancelled
from ..soundcloud import (
    API_ROOT,
    HYDRATE_BATCH,
    SoundCloudClient,
    create_requests_session,
    is_soundcloud_url,
)
from ..soundcloud_errors import SoundCloudError

PROFILE_HOSTS = frozenset({'soundcloud.com', 'www.soundcloud.com', 'm.soundcloud.com'})
PROFILE_NAME = re.compile(r'[A-Za-z0-9_-]+')


def profile_url(value):
    """``soundcloud.com/name/sets?x`` and the like as ``https://soundcloud.com/name``; ValueError otherwise."""
    text = str(value or '').strip()
    if not text:
        raise ValueError('Paste your SoundCloud profile link')
    if '://' not in text:
        text = 'https://' + text
    try:
        parsed = urlparse(text)
        host = (parsed.hostname or '').lower()
    except ValueError:
        parsed, host = None, ''
    # Only the profile name is kept: SoundCloud's own resolver is asked about it, never this host.
    name = next((part for part in parsed.path.split('/') if part), '') if host in PROFILE_HOSTS else ''
    if not is_soundcloud_url(text) or not PROFILE_NAME.fullmatch(name):
        raise ValueError('Paste a profile link like https://soundcloud.com/your-name')
    return 'https://soundcloud.com/' + name


def _resolve_owner(client, url, private, check):
    owner = client.resolve(url)
    check()
    if owner.get('kind') != 'user' or not owner.get('id'):
        raise SoundCloudError('This link is not a SoundCloud profile')
    if private:
        if not client.oauth_token:
            raise SoundCloudError('Sign into the profile owner account to import private playlists')
        me = client._get('/me')
        check()
        if me.get('id') != owner['id']:
            raise SoundCloudError('Private playlists require the signed-in profile owner')
    return owner['id']


def _link_legacy_playlists(client, db, url, owner_id, generations, check):
    # Only a provider-confirmed playlist and owner can acquire an old URL alias.
    profile_path = urlparse(url).path.rstrip('/') + '/sets/'
    for source in db.unaliased_playlists():
        if is_soundcloud_url(source) and urlparse(source).path.startswith(profile_path):
            check()
            try:
                legacy = client.resolve(source)
            except SoundCloudError:
                continue
            check()
            if legacy.get('kind') == 'playlist' and _owner_id(legacy) == owner_id:
                db.link_playlist_alias(legacy['id'], source, generations)


def _owner_id(payload):
    return payload.get('user_id') or (payload.get('user') or {}).get('id')


def _playlist_headers(client, owner_id, check):
    endpoint = f"{API_ROOT}/users/{owner_id}/playlists"
    cursors, playlist_ids = set(), set()
    while endpoint:
        check()
        if endpoint in cursors:
            raise SoundCloudError('SoundCloud repeated a page cursor; existing playlists have been kept')
        cursors.add(endpoint)
        payload = client._request(endpoint, {'limit': 200, 'linked_partitioning': 1})
        check()
        if not isinstance(payload, dict) or not isinstance(payload.get('collection'), list):
            raise SoundCloudError('Incomplete playlist page; existing playlists have been kept')
        before_page = len(playlist_ids)
        for header in payload['collection']:
            check()
            identifier = header.get('id')
            if identifier in playlist_ids:
                continue
            if not identifier or not _owner_id(header):
                raise SoundCloudError('Playlist owner or ID missing from response')
            playlist_ids.add(identifier)
            yield header
        endpoint = payload.get('next_href')
        if endpoint and len(playlist_ids) == before_page:
            raise SoundCloudError('SoundCloud pagination made no progress; import stopped')


def _hydrate_tracks(client, ids, cache, check):
    missing = list(dict.fromkeys(value for value in ids if value not in cache))
    resolved = {}
    for chunk in batched(missing, HYDRATE_BATCH):
        check()
        tracks = client._get('/tracks', ids=','.join(map(str, chunk)))
        check()
        if not isinstance(tracks, list):
            raise SoundCloudError('Incomplete track hydration response')
        for item in tracks:
            if isinstance(item, dict) and item.get('id') in chunk:
                resolved[item['id']] = Track.from_api(item)
    unavailable = [value for value in ids if value not in resolved and value not in cache]
    if unavailable:
        return [], unavailable
    tracks = [resolved[value] if value in resolved else cache[value] for value in ids]
    cache.update(resolved)
    while len(cache) > 1000:
        cache.popitem(last=False)
    return tracks, []


def _import_playlist(client, db, identifier, cache, generations, check):
    detail = client._get(f'/playlists/{identifier}')
    check()
    raw, count = detail.get('tracks'), detail.get('track_count')
    if not isinstance(raw, list) or count is None or len(raw) != count:
        return {'id': identifier, 'status': 'incomplete', 'message': 'Old playlist retained'}
    ids = [item.get('id') for item in raw]
    if any(not isinstance(value, int) for value in ids):
        return {'id': identifier, 'status': 'incomplete', 'message': 'Missing track IDs; old playlist retained'}
    tracks, unavailable = _hydrate_tracks(client, ids, cache, check)
    if unavailable:
        # Missing stubs may indicate a partial API response; retain the snapshot.
        return {'id': identifier, 'status': 'incomplete', 'missing_tracks': unavailable}
    source = detail.get('permalink_url')
    if not is_soundcloud_url(source or ''):
        raise SoundCloudError('Playlist permalink missing')
    incoming = CrateRecord.from_crate(Crate(source, tracks, detail.get('title') or source)).to_json()
    incoming['preserve_order'] = True
    incoming['provider_id'] = identifier
    check()
    saved = db.remember_provider_playlist(identifier, incoming, generations)
    return {'id': identifier, 'status': 'imported' if saved else 'locally_changed', 'tracks': len(tracks)}


def _import_profile(client, db, url, *, private=False, cancel=None, current=lambda: True, progress=lambda done, total: None, _report=None):
    try:
        url = profile_url(url)
    except ValueError as exc:
        raise SoundCloudError(str(exc)) from exc
    generations = db.snapshot_generations()
    token = client.oauth_token

    def check():
        check_cancelled(cancel)
        if not current() or client.oauth_token != token:
            raise SoundCloudError('SoundCloud session changed; import stopped')

    owner_id = _resolve_owner(client, url, private, check)
    _link_legacy_playlists(client, db, url, owner_id, generations, check)
    cache = OrderedDict()
    report = _report if _report is not None else []
    # Every page first, so the count is known before the first playlist is saved.
    headers = list(_playlist_headers(client, owner_id, check))
    for done, header in enumerate(headers, 1):
        if _owner_id(header) != owner_id:
            # The endpoint lists the sets the profile created; a liked or reposted one stays out.
            result = {'id': header['id'], 'status': 'skipped', 'message': 'created by someone else'}
        elif not private and header.get('sharing') == 'private':
            result = {'id': header['id'], 'status': 'skipped', 'message': 'private playlist'}
        else:
            try:
                result = _import_playlist(client, db, header['id'], cache, generations, check)
            except SoundCloudError as exc:
                check()  # a changed session still stops the whole import
                result = {'id': header['id'], 'status': 'failed', 'message': str(exc)}
        report.append(dict(result, title=str(header.get('title') or header['id'])))
        progress(done, len(headers))
    return report


class _ImportClient(SoundCloudClient):
    """Import-only retry policy: bounded, interruptible exponential backoff."""
    def _request(self, url, params=None):
        for attempt in range(4):
            check_cancelled(self.import_cancel)
            try:
                return super()._request(url, params)
            except SoundCloudError as exc:
                if exc.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
                if self.import_cancel.wait(min(8, 2 ** attempt)):
                    check_cancelled(self.import_cancel)
        raise AssertionError('unreachable')


def import_profile(client, db, url, *, private=False, cancel=None, current=lambda: True, progress=lambda done, total: None):
    report = []

    def perform(adapter, stop, valid):
        try:
            return _import_profile(adapter, db, url, private=private, cancel=stop, current=valid, progress=progress, _report=report)
        except SoundCloudError as exc:
            exc.report = report
            raise

    if not isinstance(client, SoundCloudClient):
        return perform(client, cancel, current)
    token = client.oauth_token
    with _ImportClient(session=create_requests_session(max_retries=0), client_id=client.client_id,
                       oauth_token=token or '', config=client.config, timeout=client._timeout) as isolated:
        isolated.import_cancel = cancel if cancel is not None else Event()
        return perform(isolated, isolated.import_cancel, lambda: current() and client.oauth_token == token)
