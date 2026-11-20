"""Write through one pinned browser connection; verify persisted membership with read-only SoundCloud requests."""
import re
from time import monotonic

from ..crate_models import CrateRecord
from ..models import Crate, check_cancelled
from .opening import soundcloud_track_url


def account_profile(client):
    me = client._get('/me')
    if (not isinstance(me, dict) or type(me.get('id')) is not int
            or not re.fullmatch(r'https://soundcloud\.com/[\w-]+', str(me.get('permalink_url')), re.ASCII)):
        raise ValueError('Sign in to SoundCloud to save a playlist')
    return me['id'], me['permalink_url']


def playlist_detail(client, owner, identifier=None, url=None):
    data = client._get(f'/playlists/{identifier}') if identifier else client.resolve(url)
    account_id, profile = owner
    if (not isinstance(data, dict) or data.get('kind') != 'playlist'
            or type(data.get('id')) is not int or data['id'] <= 0
            or (identifier and data['id'] != identifier) or data.get('user_id') != account_id
            or not re.fullmatch(re.escape(profile) + r'/sets/[\w-]+', str(data.get('permalink_url')), re.ASCII)):
        raise ValueError('SoundCloud could not confirm the playlist and its owner')
    rows = data.get('tracks')
    if (not isinstance(rows, list) or len(rows) != data.get('track_count')
            or not all(isinstance(row, dict) and type(row.get('id')) is int for row in rows)):
        raise ValueError('SoundCloud could not confirm all playlist members')
    return data, [row['id'] for row in rows]


def _write(session, args, cancel):
    _, future = session.submit('playlist.writeTrack', args)
    deadline = monotonic() + 25
    try:
        while monotonic() < deadline:
            check_cancelled(cancel)
            try:
                return future.result(.25)
            except TimeoutError:
                if future.done():
                    raise
        raise ValueError('SoundCloud did not confirm the change')
    finally:
        if not future.done():
            future.cancel()


def write_tracks(client, session, owner, tracks, db, generations, cancel, *, identifier=None, title='', sharing='private', progress):
    account_id, profile = owner
    token = client.oauth_token

    def check():
        check_cancelled(cancel)
        if client.oauth_token != token or session.error:
            raise ValueError('SoundCloud account or browser connection changed')

    unique = {}
    for track in tracks:
        url = soundcloud_track_url(track.permalink_url)
        if not url or type(track.id) is not int or track.id <= 0:
            raise ValueError('Select tracks with a confirmed SoundCloud identity')
        if track.id in unique and unique[track.id] != url:
            raise ValueError('SoundCloud could not confirm a selected track')
        unique[track.id] = url
    if (not unique or len(unique) > 500 or
            (not identifier and (not 0 < len(title.strip()) <= 200 or sharing not in ('private', 'public')))):
        raise ValueError('Check the playlist title and the 500-track limit')
    check()
    if account_profile(client) != owner:
        raise ValueError('SoundCloud account or browser connection changed')
    identities = {track.id: track.permalink_url for track in client.hydrate_tracks(list(unique), cancel=cancel)}
    if identities != unique:
        raise ValueError('SoundCloud could not confirm a selected track')
    check()
    data, members = playlist_detail(client, owner, identifier) if identifier else ({}, [])
    if len(members) + len(unique.keys() - members) > 500:
        raise ValueError('This would exceed SoundCloud’s 500-track limit')
    added = skipped = 0
    for track_id, url in unique.items():
        check()
        if identifier:
            data, members = playlist_detail(client, owner, identifier)
        if track_id in members:
            skipped += 1
        else:
            if len(members) >= 500:
                raise ValueError('SoundCloud playlist is full')
            check()
            result = _write(session, dict(track=url, account=profile, destination=data.get('permalink_url', ''),
                                          title=title, sharing=sharing), cancel)
            check()
            dest = result.get('destination') if isinstance(result, dict) else None
            if not isinstance(dest, str) or not re.fullmatch(re.escape(profile) + r'/sets/[\w-]+', dest, re.ASCII):
                raise ValueError('The browser could not confirm the destination')
            data, members = playlist_detail(client, owner, identifier, dest)
            if data['permalink_url'] != dest or track_id not in members:
                raise ValueError('SoundCloud did not confirm the saved track')
            if not identifier and (data.get('sharing') != sharing or data.get('title') != title):
                raise ValueError('Check the new playlist and its visibility on SoundCloud')
            identifier = data['id']
            added += 1
        progress(added, skipped, len(unique))
    # Bring the server-confirmed playlist into the sidebar, retaining the local copy separately.
    check()
    hydrated = {t.id: t for t in client.hydrate_tracks(list(dict.fromkeys(members)), cancel=cancel)}
    if any(i not in hydrated for i in members):
        raise ValueError('SoundCloud saved the changes but the local view could not be refreshed')
    record = CrateRecord.from_crate(Crate(data['permalink_url'], [hydrated[i] for i in members], data['title']))
    incoming = dict(record.to_json(), owner_id=account_id, provider_id=identifier, preserve_order=True)
    check()
    db.remember_provider_playlist(identifier, incoming, generations)
    return added, skipped
