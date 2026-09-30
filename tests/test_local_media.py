import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from dj_digger.db import Database
from dj_digger.decks import Profile, compatibility
from dj_digger.export import execute, plan_export, prepare, recover, replace_one
from dj_digger.instance import InstanceLock
from dj_digger.media import probe, signature
from dj_digger.models import Track, track_key
from dj_digger.schema import DDL
from dj_digger.services.local_library import LocalLibrary, media_track
from dj_digger.wav import inspect


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / 'library.db')
    yield database
    database.close()


def audio(tmp_path, name='source.wav', codec='pcm_s24le', rate=96000):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg is not installed')
    path = tmp_path / name
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.2', '-ar', str(rate), '-ac', '2', '-c:a', codec, str(path)], check=True)
    return path


def test_identity_and_central_manual_values(tmp_path, db):
    path = audio(tmp_path)
    other = audio(tmp_path, 'other.wav')
    library = LocalLibrary(db)
    a, b = library.register(path), library.register(other)
    assert a.key != b.key
    assert a.key == track_key({'local_id': a.local_id})
    assert Track('remote', 'url', id=3).key == '3'
    for source in ('local-playlist:a', 'local-playlist:b'):
        db.save_local_playlist(source, source, [a.local_id])
    db.save_local_playlist('local-playlist:a', 'renamed', [b.local_id])
    assert len(db.local_playlist_media('local-playlist:a')) == 2
    db.set_media_manual(a.local_id, {'bpm': 128})
    db.save_analysis(a.local_id, signature(path), 'test', {'bpm': 64})
    assert media_track(db, db.media(a.local_id)).bpm == 128
    assert not db.save_analysis(a.local_id, 'stale', 'test', {'bpm': 180})


def test_delete_preserves_playlist_identity_and_manual_values(tmp_path, db):
    path = tmp_path / 'delete.wav'
    path.write_bytes(b'fixture')
    library = LocalLibrary(db)
    track = library.register(path)
    db.save_local_playlist('local-playlist:test', 'Test', [track.local_id])
    db.set_media_manual(track.local_id, {'bpm': 128})
    library.delete(track.local_id, path, signature(path))
    assert not path.exists()
    assert not db.media(track.local_id)['available']
    assert '128' in db.media_values(track.local_id)['manual_json']
    assert len(db.local_playlist_media('local-playlist:test')) == 1


def test_delete_rejects_changed_and_leased_files(tmp_path, db):
    from dj_digger.local_audio import LEASE_LOCK, LEASES
    from dj_digger.media import MediaError
    path = tmp_path / 'protected.wav'
    path.write_bytes(b'fixture')
    library = LocalLibrary(db)
    track = library.register(path)
    expected = signature(path)
    path.write_bytes(b'new content')
    with pytest.raises(MediaError, match='changed'):
        library.delete(track.local_id, path, expected)
    with LEASE_LOCK:
        LEASES[path] = 1
    try:
        with pytest.raises(MediaError, match='Close the player'):
            library.delete(track.local_id, path, signature(path))
    finally:
        with LEASE_LOCK:
            LEASES.pop(path)
    assert path.read_bytes() == b'new content'


def symlink(link, target):
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable')
    return link


def test_a_link_beside_its_file_is_one_row(tmp_path, db):
    (tmp_path / 'b.wav').write_bytes(b'fixture')
    symlink(tmp_path / 'a-link.wav', tmp_path / 'b.wav')
    tracks, _, total, _ = LocalLibrary(db).page(tmp_path)
    assert total == 2
    assert [Path(t.local_path).name for t in tracks] == ['b.wav']


def test_a_selected_link_is_refused_before_anything_is_deleted(tmp_path, db):
    from dj_digger.media import MediaError
    folder = tmp_path / 'links'
    folder.mkdir()
    target = tmp_path / 'target.wav'
    target.write_bytes(b'fixture')
    library = LocalLibrary(db)
    link = library.register(symlink(folder / 'link.wav', target))
    with pytest.raises(MediaError, match='symbolic link'):
        library.delete_targets([link])
    assert target.exists()


def test_delete_many_deletes_nothing_unless_every_file_is_safe(tmp_path, db):
    from dj_digger.media import MediaError
    library = LocalLibrary(db)
    paths = [tmp_path / name for name in ('one.wav', 'two.wav')]
    for path in paths:
        path.write_bytes(b'fixture')
    tracks = [library.register(path) for path in paths]
    files = library.delete_targets(tracks + tracks)
    assert len(files) == 2
    paths[1].write_bytes(b'changed after confirmation')
    with pytest.raises(MediaError, match='changed'):
        library.delete_many(files)
    assert all(path.exists() for path in paths)


@pytest.mark.parametrize('version', [0, 1])
def test_v2_migration_backs_up_committed_wal(tmp_path, version):
    path = tmp_path / 'library.db'
    with sqlite3.connect(path) as connection:
        for statement in DDL:
            connection.execute(statement)
        connection.execute(f'PRAGMA user_version={version}')
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute("INSERT INTO track_states VALUES ('x', 'got', '')")
        connection.commit()
        database = Database(path)
        assert database.all_track_statuses() == {'x': 'got'}
        database.close()
    assert list((tmp_path / 'backups').iterdir())


def test_lock_rejects_second_instance(tmp_path):
    first = InstanceLock(tmp_path / 'lock')
    try:
        with pytest.raises(RuntimeError):
            InstanceLock(tmp_path / 'lock')
    finally:
        first.close()
    InstanceLock(tmp_path / 'lock').close()


def test_mixed_export_keeps_mp3_bytes_and_canonicalises_wav(tmp_path, db):
    wav = audio(tmp_path)
    mp3 = audio(tmp_path, 'lossy.mp3', 'libmp3lame', 44100)
    flac = audio(tmp_path, 'source.flac', 'flac')
    plan = plan_export([wav, mp3, flac], tmp_path / 'exports')
    assert [item.action for item in plan.items] == ['convert', 'copy', 'convert']
    report = execute(plan, db)
    assert report['status'] == 'complete', report
    assert Path(plan.items[1].destination).read_bytes() == mp3.read_bytes()
    for item in (plan.items[0], plan.items[2]):
        info = inspect(Path(item.destination))
        assert (info['code'], info['bits'], info['rate']) == (1, item.bits, 48000)
    assert all(value == 'compatible' for value in report['compatibility'].values())
    assert wav.exists() and flac.exists()


@pytest.mark.parametrize('stage', ['prepared', 'original_saved', 'installed', 'committed', 'done'])
def test_replacement_recovers_every_commit_boundary(tmp_path, db, stage):
    path = audio(tmp_path)
    record = db.register_media(str(path), signature(path))
    plan = plan_export([path], tmp_path, mode='replace')
    item = plan.items[0]
    result = tmp_path / 'prepared.wav'
    prepare(item, Profile(), result)

    def failpoint(current):
        if current == stage:
            raise RuntimeError('power failure')

    with pytest.raises(RuntimeError):
        replace_one(db, record['id'], item, result, failpoint=failpoint)
    assert recover(db) == []
    assert path.is_file()
    assert not list(tmp_path.glob('*.original'))
    assert probe(path)['rate'] in (48000, 96000)
    assert db.media(record['id']) is not None


def test_changed_source_refused_and_lossy_32khz_shows_actual_compatibility(tmp_path, db):
    path = audio(tmp_path)
    plan = plan_export([path], tmp_path / 'out')
    path.write_bytes(path.read_bytes() + b'changed')
    assert execute(plan, db)['status'] == 'partial'
    mp3 = audio(tmp_path, '32.mp3', 'libmp3lame', 32000)
    # Kept as it is only for decks that all play it; a CDJ-3000 does not play 32 kHz MP3,
    # and a lossy file is never transcoded, so it becomes a listed exception.
    assert plan_export([mp3], tmp_path / 'out2', decks=['CDJ-2000NXS2', 'CDJ-350']).items[0].action == 'copy'
    item = plan_export([mp3], tmp_path / 'out3', decks=['CDJ-3000', 'CDJ-350']).items[0]
    assert item.action == 'exception' and 'every chosen deck' in item.reason
    states = compatibility([probe(mp3)])
    assert states['CDJ-3000'] == 'incompatible'


def test_symbolic_link_inplace_refused(tmp_path):
    path = audio(tmp_path)
    link = tmp_path / 'link.wav'
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip('symlinks unavailable')
    assert plan_export([link], tmp_path, mode='replace').items[0].action == 'exception'


def test_unavailable_folder_retains_records(tmp_path, db):
    path = audio(tmp_path)
    local = LocalLibrary(db)
    track = local.register(path)
    with pytest.raises(FileNotFoundError):
        local.page(tmp_path / 'unmounted')
    assert db.media(track.local_id)['available'] == 1


def test_same_file_rename_preserves_uuid_and_manual_values(tmp_path, db):
    path = audio(tmp_path)
    library = LocalLibrary(db)
    first = library.register(path)
    db.set_media_manual(first.local_id, {'bpm': 125})
    renamed = tmp_path / 'renamed.wav'
    path.rename(renamed)
    after = library.register(renamed)
    assert after.local_id == first.local_id
    assert after.bpm == 125


def test_copy_resume_verifies_finished_files(tmp_path, db):
    from threading import Event
    first = audio(tmp_path)
    second = audio(tmp_path, 'second.wav')
    plan = plan_export([first, second], tmp_path / 'out')
    stopped = Event()
    report = execute(plan, db, cancel=stopped, progress=lambda done, total: stopped.set())
    assert report['status'] == 'cancelled'
    assert len(report['missing']) == 1
    output = Path(plan.items[0].destination)
    before = output.read_bytes()
    assert execute(plan, db, resume=True)['status'] == 'complete'
    assert output.read_bytes() == before
    assert len(list(Path(plan.folder).glob('*.wav'))) == 2


def test_exclusive_install_never_overwrites_foreign_file(tmp_path):
    from dj_digger.media import install_new
    source, target = tmp_path / 'new', tmp_path / 'foreign'
    source.write_bytes(b'new')
    target.write_bytes(b'owned by someone else')
    with pytest.raises(FileExistsError):
        install_new(source, target)
    assert target.read_bytes() == b'owned by someone else'
    assert source.read_bytes() == b'new'


def test_missing_media_identity_preserves_recovery_original(tmp_path, db):
    path = audio(tmp_path)
    item = plan_export([path], tmp_path, mode='replace').items[0]
    output = tmp_path / 'verified.wav'
    prepare(item, Profile(), output)
    with pytest.raises(ValueError, match='identity'):
        replace_one(db, 'missing-record', item, output)
    assert list(tmp_path.glob('*.original'))
    assert recover(db)
    assert list(tmp_path.glob('*.original'))


@pytest.mark.parametrize('version', [0, 1])
def test_backup_failure_leaves_schema_untouched(tmp_path, monkeypatch, version):
    from dj_digger import schema
    path = tmp_path / 'v1.db'
    with sqlite3.connect(path) as connection:
        for statement in DDL:
            connection.execute(statement)
        connection.execute(f'PRAGMA user_version={version}')
    monkeypatch.setattr(schema, 'backup', lambda *args: (_ for _ in ()).throw(OSError('backup denied')))
    with pytest.raises(OSError, match='backup denied'):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == version
        assert schema.signature(connection) == schema.expected_signature()


def test_analysis_abstention_keeps_tag_fallback(tmp_path, db):
    path = audio(tmp_path)
    track = LocalLibrary(db).register(path)
    db.update_media_metadata(track.local_id, signature(path), {'tags': {'bpm': '126', 'initialkey': 'Am'}})
    db.save_analysis(track.local_id, signature(path), 'test', {'bpm': None, 'key': ''})
    loaded = media_track(db, db.media(track.local_id))
    assert (loaded.bpm, loaded.key_signature) == (126, 'Am')
    db.set_media_manual(track.local_id, {'bpm': 130, 'key': 'C'})
    loaded = media_track(db, db.media(track.local_id))
    assert (loaded.bpm, loaded.key_signature) == (130, 'C')


def test_rename_does_not_reuse_identity_from_changed_mount(tmp_path, db):
    path = audio(tmp_path)
    library = LocalLibrary(db)
    before = library.register(path)
    root_stat = tmp_path.stat()
    db.observe_root(str(tmp_path), root_stat.st_dev, root_stat.st_ino + 1)
    renamed = tmp_path / 'renamed.wav'
    path.rename(renamed)
    after = library.register(renamed)
    assert after.local_id != before.local_id
    assert db.media(before.local_id)['path'] == str(path)


def test_analysis_sources_are_per_field_and_do_not_extend_track_serialization(tmp_path, db):
    from dataclasses import asdict

    from dj_digger.services.local_library import media_analysis_values
    path = audio(tmp_path)
    track = LocalLibrary(db).register(path, inspect=True)
    record = db.media(track.local_id)
    db.save_analysis(track.local_id, record['signature'], 'test', {'bpm': 64, 'key': 'Am'})
    db.set_media_manual(track.local_id, {'bpm': 128})
    resolved = media_analysis_values(db, record)
    assert resolved == {'bpm': (128, 'Manual'), 'key': ('Am', 'Analysis (estimate)')}
    stale = {**record, 'signature': 'changed'}
    assert media_analysis_values(db, stale)['key'] == ('', 'Not available')
    assert 'bpm_source' not in asdict(media_track(db, record))
    db.set_media_manual(track.local_id, {})
    assert media_analysis_values(db, record)['bpm'] == (64, 'Analysis (estimate)')


def test_the_chosen_decks_pick_the_format(tmp_path):
    from dj_digger.decks import best_profile
    from dj_digger.export import resume_plan

    assert best_profile(['CDJ-3000', 'CDJ-2000NXS2']) == Profile('flac', 24, 96000)
    assert best_profile(['CDJ-3000', 'CDJ-350']) == Profile('wav', 24, 48000)
    with pytest.raises(ValueError, match='at least one deck'):
        best_profile([])
    hires = audio(tmp_path, 'hires.flac', 'flac')  # 96 kHz FLAC
    # Every high-resolution deck plays it as it is; an older one in the set needs 48 kHz.
    modern = plan_export([hires], tmp_path / 'modern', decks=['CDJ-3000', 'CDJ-2000NXS2'])
    assert modern.items[0].action == 'copy' and modern.profile.format == 'flac'
    mixed = plan_export([hires], tmp_path / 'mixed', decks=['CDJ-3000', 'CDJ-2000'])
    assert (mixed.items[0].action, mixed.items[0].rate, mixed.profile.format) == ('convert', 48000, 'wav')
    assert mixed.items[0].destination.endswith('.wav')
    assert set(mixed.compatibility()) == {'CDJ-2000', 'CDJ-3000'}
    record = {'plan': {**__import__('dataclasses').asdict(mixed)}}
    assert resume_plan(record).decks == ('CDJ-2000', 'CDJ-3000')
    del record['plan']['decks']  # Saved before decks could be chosen.
    assert len(resume_plan(record).decks) == 7


def test_the_review_warns_about_a_drive_the_decks_cannot_read(tmp_path, monkeypatch):
    from dj_digger import export
    from dj_digger.export import mount_type

    assert mount_type('/dev/sdb1 /run/media/me/MY\\040USB vfat rw 0 0\n', Path('/run/media/me/MY USB')) == 'vfat'
    source = audio(tmp_path, 'deep.wav', 'pcm_s16le', 48000)
    nested = tmp_path.joinpath(*'abcdefghij')
    nested.mkdir(parents=True)
    deep = nested / 'deep.wav'
    source.rename(deep)
    usb = tmp_path / 'usb'
    usb.mkdir()
    monkeypatch.setattr(export, 'destination_drive', lambda folder: (usb, 'exfat'))
    plan = plan_export([deep], usb, decks=['CDJ-3000', 'CDJ-3000X'])
    assert plan.notes == ('The destination drive uses exFAT, which CDJ-3000 cannot read; FAT32 works on every deck.',)
    shallow = plan_export([deep], usb, decks=['CDJ-3000X'])
    assert shallow.notes == ()  # One file, one folder deep in the new export folder, on a drive it reads.
    monkeypatch.setattr(export, 'destination_drive', lambda folder: (tmp_path, 'fat'))
    replacing = plan_export([deep], usb, decks=['CDJ-3000'], mode='replace')
    assert replacing.notes == ('1 file sits more than 8 folders below the drive root, where decks do not show them.',)
    monkeypatch.setattr(export, 'destination_drive', lambda folder: (tmp_path, None))
    assert plan_export([deep], usb, decks=['CDJ-3000'], mode='replace').notes == ()


def test_waveform_peaks_match_the_frame_by_frame_fold():
    import array as array_module
    import random

    import numpy as np

    from dj_digger.local_audio import _fold_peaks

    rng = random.Random(1)
    blocks = []
    for size in (0, 7, 3001, 64):
        samples = array_module.array('h', [rng.randint(-32768, 32767) for _ in range(size * 2)])
        if size:
            samples[0] = -32768
        blocks.append(samples.tobytes())
    expected, frame = [0] * 1024, 0
    for block in blocks:
        values = array_module.array('h')
        values.frombytes(block)
        for index in range(0, len(values), 2):
            bucket = min(1023, frame // 3)
            expected[bucket] = max(expected[bucket], abs(values[index]), abs(values[index + 1]))
            frame += 1
    peaks, frame = np.zeros(1024, dtype=np.int32), 0
    for block in blocks:
        frame = _fold_peaks(peaks, block, frame, 3)
    assert peaks.tolist() == expected
