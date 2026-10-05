"""Local file identities, lazy folder pages and centrally hydrated metadata."""

import json
import os
import re
from pathlib import Path

from ..media import FORMATS, MediaError, probe, signature
from ..models import Track, _year, check_cancelled

PAGE_SIZE = 250


def _is_audio_entry(entry):
    """Shared by the visible page and complete export selection."""
    return (entry.name.lower().endswith(tuple(FORMATS))
            and not re.search(r'\.[0-9a-f]{32}\.partial\.', entry.name)
            and entry.is_file())


def media_analysis_values(db, record: dict) -> dict:
    """Resolve each displayed value with its source; presentation only, never serialized."""
    return _media_values(db, record)[0]


def _media_values(db, record: dict):
    """The resolved BPM and key with their sources, and the artist: set by hand, else tagged."""
    metadata = json.loads(record['metadata_json'])
    values = db.media_values(record['id'])
    manual = json.loads(values.get('manual_json', '{}'))
    analysis = (json.loads(values.get('result_json', '{}'))
                if values.get('signature') == record['signature'] else {})
    tags = metadata.get('tags', {})
    try:
        bpm = float(tags.get('bpm') or tags.get('tbpm') or 0) or None
    except (TypeError, ValueError):
        bpm = None
    embedded = {'bpm': bpm, 'key': tags.get('initialkey', '')}
    resolved = {}
    for field in ('bpm', 'key'):
        resolved[field] = (None if field == 'bpm' else '', 'Not available')
        for source, candidates in (('Manual', manual), ('Analysis (estimate)', analysis), ('File tag', embedded)):
            if candidates.get(field):
                resolved[field] = (candidates[field], source)
                break
    return resolved, str(manual.get('artist') or tags.get('artist') or '')


def filename_artist(stem: str) -> str:
    """"Artist - Title" names carry the artist before the first dash; a track number is not one."""
    artist, dash, _ = stem.partition(' - ')
    artist = artist.strip()
    return artist if dash and artist and not artist.isdigit() else ''


def media_track(db, record: dict) -> Track:
    # The name is the file's own name on disk, so renaming it in the table renames the file.
    metadata = json.loads(record['metadata_json'])
    resolved, artist = _media_values(db, record)
    stem = Path(record['path']).stem
    # Tag names as FFprobe reports them, lower-cased: ID3 TPUB is "publisher", Vorbis has LABEL or ORGANIZATION.
    tags = metadata.get('tags', {})
    year = next((y for name in ('date', 'year', 'originaldate', 'original_year') if (y := _year(str(tags.get(name) or '')))), None)
    label = next((str(tags[name]).strip() for name in ('label', 'publisher', 'organization') if str(tags.get(name) or '').strip()), '')
    return Track(title=stem, permalink_url='', artist=artist or filename_artist(stem),
                 local_id=record['id'], local_path=record['path'], duration=int(metadata.get('duration', 0) * 1000),
                 bpm=resolved['bpm'][0], key_signature=resolved['key'][0],
                 genre=str(tags.get('genre') or '').strip(), release_year=year, label_name=label)


def valid_file_name(name: str) -> str:
    """A new name for a file in the same folder: one visible path component, nothing else."""
    name = name.strip()
    if not name or name in {'.', '..'} or name.startswith('.') or any(c in name for c in '/\\\0'):
        raise MediaError('Enter a file name without slashes that does not start with a dot')
    if len(name.encode('utf-8')) > 240:
        raise MediaError('The file name is too long')
    return name


def media_tracks(db, media_ids) -> dict[str, Track]:
    """Current tracks for local media ids, in one query; ids no longer indexed are left out."""
    return {media_id: media_track(db, record) for media_id, record in db.media_many(media_ids).items()}


class LocalLibrary:
    def __init__(self, db):
        self.db = db

    def delete(self, media_id, path: Path, expected: str):
        self.delete_many([(media_id, path, expected)])

    def delete_targets(self, tracks):
        """One confirmation entry per file; a symbolic link is refused before anyone confirms."""
        files = {}
        for track in tracks:
            if not track.local_id or track.local_id in files:
                continue
            path = Path(track.local_path)
            if path.is_symlink():
                raise MediaError('Select the original file rather than a symbolic link')
            record = self.db.media(track.local_id)
            if record:
                files[track.local_id] = (track.local_id, path, record['signature'])
        return list(files.values())

    def delete_many(self, files, cancel=None):
        """Delete only confirmed files, and none unless all of them are still safe to delete."""
        from ..local_audio import LEASE_LOCK
        with LEASE_LOCK:
            for media_id, path, expected in files:
                self._deletable(media_id, path, expected)
            for media_id, path, expected in files:
                check_cancelled(cancel)
                resolved = self._deletable(media_id, path, expected)
                resolved.unlink()
                try:
                    self.db.mark_media_deleted(media_id, str(resolved))
                except Exception as exc:
                    raise MediaError('File deleted, but the library could not be updated; reopen its folder') from exc

    def rename(self, track, name: str) -> Path:
        """Give a local file a new name in its folder, keeping its extension; never replaces a file."""
        from ..local_audio import LEASE_LOCK, LEASES
        name = valid_file_name(name)
        path = Path(track.local_path)
        if path.is_symlink():
            raise MediaError('Select the original file rather than a symbolic link')
        with LEASE_LOCK:
            old = path.resolve(strict=True)
            record = self.db.media(track.local_id)
            if record is None or record['path'] != str(old) or signature(old) != record['signature']:
                raise MediaError('File changed since selection; select it again')
            # The player and the prefetch read the file by name while they hold it.
            if old in LEASES or path in LEASES:
                raise MediaError('Stop playback before renaming a loaded or prefetched file')
            new = old.with_name(name + old.suffix)
            if new == old:
                return old
            if new.exists() and not os.path.samefile(old, new):
                raise MediaError('A file with this name already exists')
            try:
                # A hard link fails rather than replace a file created since the check.
                os.link(old, new)
            except FileExistsError:
                if not os.path.samefile(old, new):
                    raise MediaError('A file with this name already exists') from None
                os.rename(old, new)  # Only the letter case changed, on a case-insensitive disk.
            except OSError:
                # ponytail: check-then-rename on disks without hard links (FAT, exFAT); a file
                # appearing between the check and the rename would be replaced.
                os.rename(old, new)
            else:
                os.unlink(old)
            self.db.rename_media(record['id'], str(old), str(new), record['signature'], signature(new))
        return new

    def _deletable(self, media_id, path: Path, expected: str) -> Path:
        """Call with LEASE_LOCK held; loaded and prefetched audio is protected."""
        from ..local_audio import LEASES
        if path.is_symlink():
            raise MediaError('Select the original file rather than a symbolic link')
        resolved = path.resolve(strict=True)
        record = self.db.media(media_id)
        if (record is None or record['path'] != str(resolved)
                or signature(resolved) != expected):
            raise MediaError('File changed since selection; select it again')
        if resolved in LEASES:
            raise MediaError('Close the player before deleting a loaded or prefetched file')
        return resolved

    def register(self, path: Path, *, inspect=False, cancel=None) -> Track:
        selected_path = path.absolute()
        path = path.resolve(strict=True)
        if path.suffix.lower() not in FORMATS:
            raise MediaError('Unsupported audio file')
        current_signature = signature(path)
        for existing in self.db.media_at_identity(current_signature):
            if (existing['path'] != str(path) and json.loads(existing['signature'])[:4] == json.loads(current_signature)[:4]):
                from ..scanner import confirmed_missing
                if confirmed_missing(Path(existing['path']), self.db):
                    self.db.relocate_media(existing['id'], existing['path'], str(path), existing['signature'], current_signature)
        record = self.db.register_media(str(path), current_signature)
        if inspect and record['metadata_json'] == '{}':
            metadata = probe(path, cancel)
            if not self.db.update_media_metadata(record['id'], record['signature'], metadata):
                raise MediaError('File changed during inspection')
            record = self.db.media(record['id'])
        track = media_track(self.db, record)
        track.local_path = str(selected_path)
        return track

    def page(self, folder: Path, offset=0, *, cancel=None):
        """Read direct entries only. Permission errors never mark records deleted.

        Sorting names uses O(directory entries) memory, but probing/rows are paged.
        No recursion, hashing or decoding is triggered by opening a directory.
        """
        names, directories = [], []
        with os.scandir(folder) as entries:
            for entry in entries:
                check_cancelled(cancel)
                if entry.is_dir(follow_symlinks=False):
                    directories.append(entry.name)
                elif _is_audio_entry(entry):
                    names.append(entry.name)
        check_cancelled(cancel)
        folder = folder.resolve(strict=True)
        folder_stat = folder.stat()
        if self.db.observe_root(str(folder), folder_stat.st_dev, folder_stat.st_ino):
            self.db.mark_directory_missing(str(folder), {str(folder / name) for name in names}, folder_stat.st_dev)
        names.sort(key=str.casefold)
        directories.sort(key=str.casefold)
        tracks, failures, rows = [], [], {}
        for name in names[offset:offset + PAGE_SIZE]:
            check_cancelled(cancel)
            try:
                track = self.register(folder / name)
            except OSError as exc:
                failures.append(f'{name}: {exc}')
                continue
            # A link to a file on this page shares its identity, so it would share its selection.
            if track.local_id in rows:
                if Path(track.local_path).is_symlink():
                    continue
                tracks[rows[track.local_id]] = track
            else:
                rows[track.local_id] = len(tracks)
                tracks.append(track)
        return tracks, directories, len(names), failures

    def selection(self, folder: Path, *, recursive=False, cancel=None):
        """Frozen complete selection, independent of the currently displayed page."""
        result, seen = [], set()
        pending = [folder]
        while pending:
            current = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    check_cancelled(cancel)
                    if recursive and entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.dj-digger-'):
                        pending.append(Path(entry.path))
                    elif _is_audio_entry(entry):
                        path = Path(entry.path).resolve(strict=True)
                        stat = path.stat()
                        identity = (stat.st_dev, stat.st_ino)
                        if identity not in seen:
                            seen.add(identity)
                            result.append(Path(entry.path).absolute())
        return tuple(sorted(result, key=lambda path: str(path).casefold()))
