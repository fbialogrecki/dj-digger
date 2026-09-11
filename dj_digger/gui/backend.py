"""Desktop orchestration. Qt receives detached values, never service objects."""
import asyncio
import logging
import threading
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .. import links
from ..diagnostics import log_safe_text
from ..models import GOT, NEW, SKIP, Cancelled, check_cancelled
from ..paths import playlist_download_directory
from ..services.collection import DigOptions
from ..services.local_library import LocalLibrary, media_track
from ..services.runtime import ApplicationServices

LOGGER = logging.getLogger(__name__)


class Backend:
    def __init__(self, emit, services_factory=ApplicationServices):
        self.emit = emit
        self.factory = services_factory
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name='gui-backend')
        self.ready = threading.Event()
        self.closing = False
        self.tasks = set()
        self.questions = {}
        self.rows = []
        self.record = None
        self.folder = None
        self.offset = 0
        self.generation = 0
        self.play_generation = 0
        self.play_order = []
        self.prepared = None
        self.prefetch_task = None
        self.prefetch_attempt = ''
        self.undo = []
        self.mark_lock = asyncio.Lock()
        self.player_lock = asyncio.Lock()
        self.waveform_lock = asyncio.Lock()
        self.waveform_cancel = threading.Event()
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()
        self.send('closed', {})

    def send(self, kind, values):
        detached = deepcopy(values)
        if kind in {'error', 'message'} and 'text' in detached:
            detached['text'] = log_safe_text(detached['text'])
        self.emit(kind, detached)

    def submit(self, action, values=None):
        self.ready.wait()
        self.loop.call_soon_threadsafe(self._start, action, deepcopy(values or {}))

    def _start(self, action, values):
        if self.closing:
            return
        task = self.loop.create_task(self.dispatch(action, values))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def answer(self, ident, values):
        def deliver():
            future = self.questions.get(ident)
            if future is not None and not future.done():
                future.set_result(deepcopy(values))
        self.loop.call_soon_threadsafe(deliver)

    async def ask(self, title, body='', fields=(), cancel=None, ok='', error=''):
        ident = uuid4().hex
        future = self.loop.create_future()
        self.questions[ident] = future
        self.send('question', dict(id=ident, title=title, body=body, fields=list(fields), ok=ok, error=error))
        try:
            while not future.done():
                if self.closing or (cancel is not None and cancel.is_set()):
                    raise Cancelled()
                await asyncio.wait({future}, timeout=.1)
            answer = future.result()
            if answer is None:
                raise Cancelled()
            check_cancelled(cancel)
            return answer
        finally:
            self.questions.pop(ident, None)
            self.send('dismiss', {'id': ident})

    async def form(self, title, body, fields, validate, cancel=None, ok=''):
        """Re-open the same dialog with the entered values until validation passes."""
        error = ''
        while True:
            answer = await self.ask(title, body, fields, cancel, ok, error)
            try:
                return validate(answer)
            except ValueError as exc:
                error = str(exc)
                fields = [dict(f, value=answer.get(f['name'], f['value'])) for f in fields]

    def ask_sync(self, *args, **kwargs):
        return asyncio.run_coroutine_threadsafe(self.ask(*args, **kwargs), self.loop).result()

    async def io(self, function, *args, **kwargs):
        return await self.services.io(function, *args, **kwargs)

    async def dispatch(self, action, values):
        try:
            if action == 'start':
                self.services = self.factory()
                # Construct lazy resources in their owning orchestration thread.
                self.services.state
                self.services.config
                self.services.library
                self.services.downloads
                self.services.accounts
                self.services.opening
                self.local = LocalLibrary(self.services.state.db)
                from ..export import recover
                messages = await self.io(recover, self.services.state.db)
                await self.sidebar()
                if messages:
                    self.send('message', {'text': '\n'.join(messages)})
                self.send('ready', {'volume': self.services.config.volume})
                return
            if not hasattr(self, 'services'):
                return
            if action == 'cancel':
                if handle := self.services.operations.visible:
                    self.services.operations.cancel(handle)
                return
            handler = getattr(self, 'action_' + action, None)
            if handler is None:
                raise ValueError('Unknown desktop action')
            if action in {'mark', 'undo'}:
                async with self.mark_lock:
                    await handler(values)
            else:
                await handler(values)
        except (Cancelled, asyncio.CancelledError):
            self.send('message', {'text': 'Cancelled'})
        except Exception as exc:
            LOGGER.exception('Desktop action failed: %s', action)
            self.send('error', {'text': log_safe_text(exc)})

    async def sidebar(self):
        headers = await self.io(self.services.library.headers)
        self.send('sidebar', {'items': [dict(title=h.title, source=h.source) for h in headers],
                              'pinned': list(self.services.config.pinned_directories)})

    async def publish(self, tracks, title, generation):
        from ..rows import Row
        if generation != self.generation:
            return
        self.rows = [Row(i, t, links.categorise(t)) for i, t in enumerate(tracks)]
        self.send('view', {'generation': generation, 'title': title, 'source': self.record.source if self.record else '',
                          'local': self.folder is not None or bool(self.record and self.record.source.startswith('local-playlist:')),
                          'rows': [self.row_value(r) for r in self.rows]})

    def row_value(self, row):
        t = row.track
        return dict(key=t.key, title=t.title, artist=t.artist, genre=t.genre_label,
                    bpm=t.bpm or '', keySignature=t.key_signature, year=t.release_year or '',
                    label=t.label_name, duration=t.duration, status=self.services.state.get(t.key),
                    stores=', '.join(row.categories), local=bool(t.local_id), path=t.local_path or '',
                    search=row.haystack)

    async def refresh_rows(self):
        generation = self.generation
        rows = deepcopy(self.rows)
        fresh = await self.io(self.services.library.load, self.record.source) if self.record else None
        by_key = {track.key: track for track in fresh.active_tracks} if fresh else {}
        for row in rows:
            if row.track.key in by_key:
                row.track = by_key[row.track.key]
                row.records = links.categorise(row.track)
            if row.track.local_id:
                record = await self.io(self.services.state.db.media, row.track.local_id)
                if record:
                    row.track = await self.io(media_track, self.services.state.db, record)
            elif path := self.services.state.local_file(row.track.key):
                row.track.local_path = path
        if generation == self.generation:
            self.rows = rows
            self.send('rows', {'generation': generation, 'rows': [self.row_value(r) for r in rows]})

    def targets(self, values):
        if values.get('generation') != self.generation:
            raise Cancelled()
        keys = set(values.get('keys', []))
        return deepcopy([row for row in self.rows if row.track.key in keys])

    async def action_load(self, values):
        self.generation += 1
        generation = self.generation
        record = await self.io(self.services.library.load, values['source'])
        if generation != self.generation or record is None:
            return
        self.record, self.folder = record, None
        await self.publish(record.active_tracks, record.title, generation)

    async def action_folder(self, values):
        self.generation += 1
        generation = self.generation
        folder = Path(values['path']).expanduser()
        offset = max(0, int(values.get('offset', 0)))
        tracks, directories, total, failures = await self.io(self.local.page, folder, offset)
        if generation != self.generation:
            return
        self.record, self.folder, self.offset = None, folder, offset
        await self.publish(tracks, str(folder), generation)
        self.send('folder', dict(path=str(folder), offset=offset, total=total,
                                directories=[str(folder / name) for name in directories[:1000]]))
        # Metadata hydration uses private copies and cannot overwrite a later view.
        hydrated = []
        for track in tracks:
            if generation != self.generation:
                return
            try:
                track = await self.io(self.local.register, Path(track.local_path), inspect=True)
            except Exception as exc:
                LOGGER.info('Metadata unavailable: %s', log_safe_text(exc))
            hydrated.append(track)
        await self.publish(hydrated, str(folder), generation)

    async def job(self, name, work):
        handle = self.services.operations.start(name)
        self.send('busy', {'value': True, 'text': name})
        try:
            return await work(handle)
        finally:
            self.services.operations.finish(handle)
            self.send('busy', {'value': self.services.operations.visible is not None, 'text': ''})

    async def action_dig(self, values):
        target = values.get('target')
        if not target:
            target = await self.form('Add playlist', '', [field('target', 'SoundCloud URL or saved HTML')], required('target'), ok='Add')
        generation = self.generation
        async def work(handle):
            result = await self.io(self.services.collection.collect, target, DigOptions(),
                                   self.services.state.db.snapshot_generations(), 'none', None,
                                   handle.cancel, lambda stage, done, total: None)
            if result.record and generation == self.generation:
                self.record, self.folder = result.record, None
                self.generation += 1
                await self.publish(result.record.active_tracks, result.record.title, self.generation)
            await self.sidebar()
        await self.job('Collecting tracks', work)

    async def action_refresh(self, values):
        if self.record and not self.record.source.startswith('local-playlist:'):
            await self.action_dig({'target': self.record.source})
        elif self.folder:
            await self.action_folder({'path': str(self.folder), 'offset': self.offset})

    async def action_mark(self, values):
        rows = self.targets(values)
        status = values['status']
        if status not in {NEW, GOT, SKIP}:
            raise ValueError('Invalid status')
        previous = [(r.track.key, self.services.state.get(r.track.key)) for r in rows]
        for key, _ in previous:
            await self.io(self.services.state.set, key, status)
        self.undo.append(previous)
        await self.refresh_rows()

    async def action_undo(self, values):
        if self.undo:
            for key, status in self.undo.pop():
                await self.io(self.services.state.set, key, status)
            await self.refresh_rows()

    async def action_open(self, values):
        rows = self.targets(values)
        urls = []
        for row in rows:
            candidates = [record for record in row.records
                          if (not values.get('store') or record.category == values['store'])
                          and links.is_openable(record.link_url)]
            if candidates:
                urls.append((row.track.key, candidates[0].link_url))
        async def work(handle):
            if len(urls) > 20:
                await self.ask('Open links', '', [field('urls', '', '\n'.join(url for _, url in urls), 'log')], handle.cancel, ok='Open')
            for key, url in urls:
                check_cancelled(handle.cancel)
                await self.io(self.services.opening.open_one, url, key, self.services.config.browser)
            await self.refresh_rows()
        if urls:
            await self.job('Open links', work)

    async def action_download(self, values):
        from ..services.downloads import DownloadRequest, DownloadWorkflow, find_gate_url
        rows = self.targets(values)
        if not rows:
            return
        if self.services.config.first_run:
            await self.action_settings({})
        source = self.record.source if self.record else ''
        directory = playlist_download_directory(self.services.config.download_directory,
                                                self.record.title if self.record else '')
        request = DownloadRequest(source, self.services.state.db.crate_generation(source),
                                  directory, 20)
        async def work(handle):
            def prerequisites(profile, auth):
                async def configure():
                    if profile:
                        await self.action_settings({})
                    if auth:
                        await self.authenticate(handle.cancel)
                    check_cancelled(handle.cancel)
                    return profile + auth
                return asyncio.run_coroutine_threadsafe(configure(), self.loop).result()
            pending = {}
            pending_lock = threading.Lock()
            settled = asyncio.Event()
            async def flush():
                while not settled.is_set():
                    with pending_lock:
                        updates = dict(pending)
                        pending.clear()
                    if updates:
                        self.send('progress', {'generation': values['generation'], 'updates': updates})
                    await asyncio.sleep(.1)
            def emit(event):
                if event.kind == 'progress':
                    with pending_lock:
                        pending[event.key] = event.progress
                elif event.kind in {'failed', 'unrecorded'}:
                    self.send('message', {'text': event.message})
            workflow = DownloadWorkflow(self.services.downloads, request, handle,
                                        client=lambda: self.services.client, config=self.services.config,
                                        emit=emit, prerequisites=prerequisites)
            remote = []
            for row in rows:
                if row.track.local_path:
                    if await self.io(self.services.library.needs_copy, row.track.local_path, request.directory):
                        await self.io(self.services.downloads.copy, row.track.key, Path(row.track.local_path), request.directory, handle.cancel)
                    else:
                        await self.io(self.services.library.mark_existing, row.track)
                else:
                    remote.append((row.track, find_gate_url(row.records)))
            monitor = self.loop.create_task(flush())
            try:
                if remote:
                    await self.io(workflow.run_batch, remote)
            finally:
                settled.set()
                await monitor
            await self.refresh_rows()
        self.services.client
        await self.job('Downloading', work)

    async def action_analyze(self, values):
        tracks = [r.track for r in self.targets(values) if r.track.local_id]
        paths = tuple(Path(t.local_path) for t in tracks)
        await self.analyze_paths(paths)

    async def analyze_paths(self, paths):
        from ..analysis import analyze_track
        from ..analysis_report import AnalysisReport
        async def work(handle):
            def analyze():
                from importlib.util import find_spec

                from ..media import binary
                if find_spec('librosa') is None:
                    raise ValueError('Install the analyze extra to analyze audio')
                binary('ffmpeg')
                binary('ffprobe')
                with AnalysisReport() as report:
                    for path in paths:
                        check_cancelled(handle.cancel)
                        try:
                            track = self.local.register(path, cancel=handle.cancel)
                            report.record(path, analyze_track(self.services.state.db, track, handle.cancel))
                        except Cancelled:
                            raise
                        except Exception as exc:
                            report.record(path, error=exc)
                return dict(report.counts)
            counts = await self.io(analyze)
            self.send('message', {'text': 'Analysis: {0} keys, {1} unresolved, {2} errors', 'args': [counts['keys'], counts['no_key'], counts['errors']]})
            await self.refresh_rows()
        await self.job('Analyzing audio', work)

    async def action_edit(self, values):
        import math
        rows = self.targets(values)
        if len(rows) != 1 or not rows[0].track.local_id:
            return
        track = rows[0].track
        self.generation += 1
        await self.publish([r.track for r in self.rows], self.record.title if self.record else str(self.folder or ''), self.generation)
        from ..services.local_library import media_analysis_values
        record = await self.io(self.services.state.db.media, track.local_id)
        resolved = await self.io(media_analysis_values, self.services.state.db, record)
        from ..analysis import NOTES, camelot
        KEYS = (*NOTES, *(note + "m" for note in NOTES))
        def validate(answer):
            bpm = float(answer['bpm']) if str(answer['bpm']).strip() else None
            if bpm is not None and (not math.isfinite(bpm) or not 0 < bpm <= 999):
                raise ValueError('BPM must be between 0 and 999')
            return bpm, answer['key']
        bpm, key = await self.form('Edit BPM / key', f"BPM: {resolved['bpm'][1]}; Key: {resolved['key'][1]}",
                                   [field('bpm', 'BPM', str(track.bpm or ''), 'number'),
                                    field('key', 'Key', track.key_signature if track.key_signature in KEYS else '', 'choice',
                                          [['', ''], *[[k, f'{k} / {camelot(k)}'] for k in KEYS]])], validate, ok='Save')
        await self.io(self.services.state.db.set_media_manual, track.local_id, {k: v for k, v in {'bpm': bpm, 'key': key}.items() if v})
        await self.refresh_rows()

    async def action_export(self, values):
        from ..decks import Profile
        from ..export import execute, plan_export
        tracks = [r.track for r in self.targets(values) if r.track.local_path]
        folder = self.folder
        if not tracks and folder is None:
            return
        answer = await self.form('Export audio', '', [field('folder', 'Destination folder', kind='folder'),
                                                     field('mode', 'Mode', 'copy', 'choice', [['copy', 'Copy'], ['replace', 'Replace originals']]),
                                                     field('format', 'Format', 'wav', 'choice', [[f, f.upper()] for f in ('wav', 'aiff', 'flac')]),
                                                     field('bits', 'Maximum bit depth', '24', 'choice', [['16', '16'], ['24', '24']]),
                                                     field('rate', 'Maximum sample rate', '48000', 'choice', [[str(r), str(r)] for r in (44100, 48000, 88200, 96000)]),
                                                     field('recursive', 'Include subfolders', False, 'bool')], required('folder'), ok='Export')
        async def work(handle):
            paths = tuple(Path(t.local_path) for t in tracks)
            if folder and not values.get('selected'):
                paths = await self.io(self.local.selection, folder, recursive=answer['recursive'], cancel=handle.cancel)
                if values.get('search') or values.get('hide'):
                    from ..playlist import filter_rows
                    from ..rows import Row
                    matching = []
                    for path in paths:
                        track = await self.io(self.local.register, path, inspect=bool(values.get('search')), cancel=handle.cancel)
                        if filter_rows([Row(0, track, [])], values.get('search', ''), values.get('hide', False), lambda row: self.services.state.get(row.track.key)):
                            matching.append(path)
                    paths = tuple(matching)
            plan = await self.io(plan_export, paths,
                                 Path(answer['folder']).expanduser(), Profile(answer['format'], int(answer['bits']), int(answer['rate'])), mode=answer['mode'], cancel=handle.cancel)
            await self.ask('Review export', 'Replacing originals permanently removes them after verification.' if plan.mode == 'replace' else '',
                           [field('plan', '', '\n'.join(f'{i.action}: {i.source} → {i.destination} ({i.reason})' for i in plan.items), 'log')],
                           handle.cancel, ok='Replace' if plan.mode == 'replace' else 'Export')
            from ..local_audio import LEASE_LOCK, LEASES
            def protected():
                with LEASE_LOCK:
                    return tuple(LEASES)
            report = await self.io(execute, plan, self.services.state.db, cancel=handle.cancel, protected=protected)
            await self.io(self.services.state.reload_file_paths)
            if plan.mode == 'replace':
                from ..paths import data_dir
                from ..private_json import write_private_json
                await self.io(write_private_json, data_dir() / ('export-' + plan.id + '.json'), report)
            self.send('message', {'text': 'Export: {0}; missing files: {1}', 'args': [report['status'], len(report.get('missing', []))]})
            await self.refresh_rows()
        await self.job('Exporting audio', work)

    async def action_save_playlist(self, values):
        tracks = [r.track for r in self.targets(values) if r.track.local_id]
        if not tracks:
            return
        answer = await self.form('Save local playlist', '', [field('title', 'Playlist name')], required('title'), ok='Save')
        title = answer['title'].strip()
        headers = await self.io(self.services.library.headers)
        matches = [h for h in headers if h.source.startswith('local-playlist:') and h.title == title]
        if len(matches) > 1:
            raise ValueError('Ambiguous playlist name')
        source = matches[0].source if matches else 'local-playlist:' + uuid4().hex
        await self.io(self.services.state.db.save_local_playlist, source, title, [t.local_id for t in tracks])
        await self.sidebar()

    async def action_remove(self, values):
        rows = self.targets(values)
        record = self.record
        if record is None:
            return
        generation = self.services.state.db.crate_generation(record.source)
        await self.ask('Remove from playlist', '\n'.join(r.track.label for r in rows), ok='Remove')
        await self.io(self.services.library.remove_tracks, record.source, generation,
                      [r.track.key for r in rows], removed=True)
        if self.record is record:
            await self.action_load({'source': record.source})

    async def action_delete_playlist(self, values):
        source = values['source']
        headers = await self.io(self.services.library.headers)
        await self.ask('Delete playlist', next((h.title for h in headers if h.source == source), source), ok='Delete')
        await self.io(self.services.library.delete, source)
        await self.sidebar()
        if self.record and self.record.source == source:
            self.record = None
            self.generation += 1
            await self.publish([], '', self.generation)

    async def action_profile(self, values):
        from ..services.profile_import import import_profile
        answer = await self.form('Import profile playlists', '', [field('url', 'SoundCloud profile URL'),
                                                                 field('private', 'Include private playlists', False, 'bool')], required('url'), ok='Import')
        client = self.services.client
        async def work(handle):
            report = await self.io(import_profile, client, self.services.state.db, answer['url'],
                                   private=answer['private'], cancel=handle.cancel,
                                   current=lambda: self.services._client is client)
            from ..paths import data_dir
            from ..private_json import write_private_json
            output = data_dir() / ('profile-import-' + uuid4().hex + '.json')
            await self.io(write_private_json, output, {'profile': answer['url'], 'results': report})
            self.send('message', {'text': 'Imported {0} / {1} playlists. Report: {2}', 'args': [sum(item['status'] == 'imported' for item in report), len(report), str(output)]})
            await self.sidebar()
        await self.job('Importing playlists', work)

    async def action_settings(self, values):
        config = self.services.config
        from ..config import is_real_email
        choices, _ = await self.io(self.services.accounts.browser_choices)
        def validate(answer):
            if answer['user_email'] and not is_real_email(answer['user_email']):
                raise ValueError('Enter a valid email')
            if not answer['download_directory'].strip():
                raise ValueError('Specify a download folder')
            for name in ('scan_directories', 'pinned_directories', 'custom_comments'):
                answer[name] = [line.strip() for line in answer[name].splitlines() if line.strip()]
            return answer
        answer = await self.form('Settings', 'Gates may submit your name and email. Social actions may follow, repost or comment on your behalf.',
                                 [field('download_directory', 'Download folder', config.download_directory, 'folder'),
                                  field('user_name', 'Name', config.user_name), field('user_email', 'Email', config.user_email),
                                  field('gate_social_actions', 'Allow social actions', config.gate_social_actions, 'bool'),
                                  field('scan_directories', 'Scan folders (one per line)', '\n'.join(config.scan_directories), 'multiline'),
                                  field('pinned_directories', 'Pinned folders (one per line)', '\n'.join(config.pinned_directories), 'multiline'),
                                  field('browser', 'Browser', config.browser if any(config.browser == v for v, _ in choices) else '', 'choice',
                                        [['', 'System default'], *[[value, label] for value, label in choices]]),
                                  field('custom_comments', 'Gate comments (one per line)', '\n'.join(config.custom_comments), 'multiline')],
                                 validate, ok='Save')
        await self.io(self.services.accounts.save_preferences, answer)
        self.services.config.first_run = False
        await self.sidebar()

    async def authenticate(self, cancel):
        self.services.accounts.begin_authentication()
        result = await self.io(self.services.accounts.authenticate, 'browser', '', cancel, lambda text: None)
        if result.error:
            raise ValueError(result.error)
        if result.token:
            self.services.adopt_login(result.token)
            self.send('message', {'text': 'Signed in'})
        else:
            raise Cancelled()

    async def action_login(self, values):
        await self.job('Signing in', lambda handle: self.authenticate(handle.cancel))

    async def action_logout(self, values):
        from ..auth import clear_token
        await self.ask('Sign out', 'Remove the stored SoundCloud session from this computer.', ok='Sign out')
        await self.io(clear_token)
        self.services.adopt_login(None)

    async def player_call(self, function, *args):
        async with self.player_lock:
            return await self.io(function, *args)

    async def action_play(self, values):
        rows = self.targets(values)
        if not rows:
            return
        track = rows[0].track
        self.play_order = values.get('order', [r.track.key for r in self.rows])
        player = self.services.player
        if player.loaded and player.loaded.track.key == track.key:
            await self.player_call(player.toggle)
            return
        self.play_generation += 1
        generation = self.play_generation
        self.prefetch_attempt = ''
        if self.prepared is not None and self.prepared.key == track.key:
            prepared, self.prepared = self.prepared, None
        else:
            if self.prepared is not None:
                old, self.prepared = self.prepared, None
                await self.io(old.close)
            prepared = await self.prepare_track(track)
        if generation != self.play_generation or self.closing:
            await self.io(prepared.close)
            return
        try:
            async with self.player_lock:
                if generation != self.play_generation or self.closing:
                    return
                self.waveform_cancel.set()
                self.waveform_cancel = waveform_cancel = threading.Event()
                loaded = await self.io(player.load, track, prepared.stream, None if track.local_path else self.services.client.session,
                              prepared.waveform, prepared.source)
                prepared.source = None
                if generation != self.play_generation or self.closing:
                    return
                if track.local_path:
                    task = self.loop.create_task(self.local_waveform(loaded, waveform_cancel))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                await self.io(player.play)
        finally:
            if prepared.source is not None:
                await self.io(prepared.close)
        if not hasattr(self, 'ticker') or self.ticker.done():
            self.ticker = self.loop.create_task(self.tick())

    async def local_waveform(self, loaded, cancel):
        from ..local_audio import waveform
        try:
            async with self.waveform_lock:
                check_cancelled(cancel)
                samples = await self.io(waveform, Path(loaded.track.local_path), cancel)
            async with self.player_lock:
                if not cancel.is_set() and self.services.player.loaded is loaded:
                    loaded.waveform = samples
                    self.publish_audio()
        except (Cancelled, asyncio.CancelledError):
            pass
        except Exception as exc:
            LOGGER.warning('Local waveform unavailable: %s', log_safe_text(exc))
            if not cancel.is_set():
                self.send('error', {'text': 'Waveform unavailable: ' + log_safe_text(exc)})

    def publish_audio(self):
        p = self.services.player
        snapshot = dict(key=p.loaded.track.key, title=p.loaded.track.label, playing=p.playing, position=p.position,
                        duration=p.duration, waveform=list(p.loaded.waveform)[::max(1, (len(p.loaded.waveform) + 1023) // 1024)]) if p.loaded else {}
        self.send('audio', snapshot)
        return snapshot

    async def prepare_track(self, track):
        from ..services.playback import Prepared, fetch_waveform, resolve_stream
        if track.local_path:
            from ..local_audio import prepare_local
            return await self.io(prepare_local, track)
        client = self.services.client
        def prepare():
            from ..player import open_source
            stream = resolve_stream(client, track.id)
            return Prepared(track, stream, fetch_waveform(client, stream.waveform_url),
                            open_source(client.session, stream.url, stream.protocol))
        return await self.io(prepare)

    async def prefetch(self, track, generation):
        try:
            prepared = await self.prepare_track(track)
        except Exception as exc:
            LOGGER.debug('Prefetch unavailable: %s', log_safe_text(exc))
            return
        if generation != self.play_generation or self.closing:
            await self.io(prepared.close)
        else:
            if self.prepared:
                await self.io(self.prepared.close)
            self.prepared = prepared

    async def tick(self):
        while not self.closing:
            async with self.player_lock:
                p = self.services.player
                if not p.loaded:
                    break
                snapshot = self.publish_audio()
                event = p.take_event()
            if (snapshot['playing'] and snapshot['duration'] - snapshot['position'] < 15
                    and self.prepared is None and (self.prefetch_task is None or self.prefetch_task.done())
                    and p.loaded.track.key in self.play_order):
                index = self.play_order.index(p.loaded.track.key) + 1
                if index < len(self.play_order):
                    track = next((r.track for r in self.rows if r.track.key == self.play_order[index]), None)
                    if track and self.prefetch_attempt != track.key:
                        self.prefetch_attempt = track.key
                        self.prefetch_task = self.loop.create_task(self.prefetch(deepcopy(track), self.play_generation))
                        self.tasks.add(self.prefetch_task)
                        self.prefetch_task.add_done_callback(self.tasks.discard)
            if event:
                if event.kind == 'error':
                    self.send('error', {'text': log_safe_text(event.message)})
                    await self.player_call(p.stop)
                else:
                    await self.action_step({'direction': 1})
            await asyncio.sleep(.1 if snapshot['playing'] else .5)

    async def action_step(self, values):
        player = self.services.player
        if not player.loaded or player.loaded.track.key not in self.play_order:
            return
        index = self.play_order.index(player.loaded.track.key) + int(values.get('direction', 1))
        if 0 <= index < len(self.play_order):
            await self.action_play(dict(keys=[self.play_order[index]], generation=self.generation, order=self.play_order))

    async def action_transport(self, values):
        player = self.services.player
        operation = values['operation']
        if operation == 'stop':
            self.waveform_cancel.set()
            self.play_generation += 1
            await self.player_call(player.unload)
            if self.prepared is not None:
                old, self.prepared = self.prepared, None
                await self.io(old.close)
            self.send('audio', {})
        elif operation == 'toggle':
            await self.player_call(player.toggle)
        elif operation == 'seek':
            await self.player_call(player.seek, max(0, min(float(values['value']), player.duration)))
        elif operation == 'volume':
            await self.player_call(player.set_volume, max(0, min(float(values['value']), 1)))
        elif operation == 'nudge':
            await self.player_call(player.nudge, float(values['value']))
        elif operation == 'mute':
            await self.player_call(player.toggle_mute)

    async def action_logs(self, values):
        from ..logging_setup import current_log_path, read_log_tail
        path = current_log_path()
        text = await self.io(read_log_tail, path) if path else 'File logging unavailable'
        await self.ask('Diagnostics', str(path or ''), [field('log', '', text, 'log')], ok='Close')

    async def action_analyze_folder(self, values):
        if self.folder is None:
            return
        folder = self.folder
        paths = await self.io(self.local.selection, folder)
        await self.analyze_paths(paths)

    async def action_delete_files(self, values):
        rows = self.targets(values)
        files = []
        for row in rows:
            track = row.track
            if track.local_id:
                record = await self.io(self.services.state.db.media, track.local_id)
                if record:
                    files.append((track.local_id, Path(track.local_path), record['signature']))
        if not files:
            return
        async def work(handle):
            await self.ask('Permanently delete files', '', [field('files', '', '\n'.join(str(p) for _, p, _ in files), 'log')], handle.cancel, ok='Delete')
            for ident, path, signature in files:
                check_cancelled(handle.cancel)
                await self.io(self.local.delete, ident, path, signature)
            await self.io(self.services.state.reload_file_paths)
            if self.folder:
                await self.action_folder({'path': str(self.folder), 'offset': self.offset})
        await self.job('Deleting files', work)

    async def action_summary(self, values):
        rows = self.targets(values)
        answer = await self.form('Export links', '', [field('path', 'Output file', kind='savefile'),
                                                     field('format', 'Format', 'json', 'choice', [['json', 'JSON'], ['csv', 'CSV']])], required('path'), ok='Export')
        path = Path(answer['path']).expanduser()
        if await self.io(path.exists):
            await self.ask('Replace existing file', str(path), ok='Replace')
        await self.io(links.export_records, [record for row in rows for record in row.records], answer['format'], path)

    async def action_import_summary(self, values):
        answer = await self.form('Import saved summary', '', [field('path', 'JSON or CSV file', kind='file')], required('path'), ok='Import')
        self.generation += 1
        generation = self.generation
        records = await self.io(links.load_summary, Path(answer['path']).expanduser())
        if generation != self.generation:
            return
        from ..rows import Row
        grouped = {}
        for record in records:
            grouped.setdefault(record.track.key, []).append(record)
        self.record, self.folder = None, None
        self.rows = [Row(i, group[0].track, group) for i, group in enumerate(grouped.values())]
        self.send('view', {'generation': generation, 'title': Path(answer['path']).name, 'local': False,
                          'rows': [self.row_value(r) for r in self.rows]})

    async def action_pin(self, values):
        if self.folder:
            folders = list(dict.fromkeys([*self.services.config.pinned_directories, str(self.folder)]))
            await self.io(self.services.accounts.save_preferences, {'pinned_directories': folders})
            await self.sidebar()

    async def action_restore(self, values):
        if self.record and self.record.removed_track_keys:
            record = self.record
            generation = self.services.state.db.crate_generation(record.source)
            await self.ask('Restore removed tracks', record.title, ok='Restore')
            await self.io(self.services.library.remove_tracks, record.source, generation, record.removed_track_keys, removed=False)
            if self.record is record:
                await self.action_load({'source': record.source})

    async def action_scan(self, values):
        tracks = deepcopy([r.track for r in self.rows])
        handle = self.services.operations.start('Scanning', lane='scan')
        self.send('busy', {'value': True, 'text': 'Scanning'})
        try:
            scanner = self.services.library.scanner(self.services.config.scan_directories)
            await self.io(scanner.scan, cancel=handle.cancel)
            check_cancelled(handle.cancel)
            await self.io(self.services.library.match_tracks, tracks, scanner)
            await self.refresh_rows()
        finally:
            self.services.operations.finish(handle)
            self.send('busy', {'value': self.services.operations.visible is not None, 'text': ''})

    async def action_resume(self, values):
        from ..export import execute, resume_plan
        records = await self.io(self.services.state.db.media_operations)
        plans = [resume_plan(r) for r in records.values() if r.get('kind') == 'copy' and r['stage'] != 'done']
        if not plans:
            self.send('message', {'text': 'No unfinished folder exports'})
            return
        plan = plans[-1]
        async def work(handle):
            await self.ask('Resume export', plan.folder, cancel=handle.cancel, ok='Resume')
            report = await self.io(execute, plan, self.services.state.db, resume=True, cancel=handle.cancel)
            self.send('message', {'text': 'Export: {0}; missing files: {1}', 'args': [report['status'], len(report.get('missing', []))]})
        await self.job('Exporting audio', work)

    async def action_cart(self, values):
        from dataclasses import replace
        from decimal import Decimal

        from ..cart_models import CartPlan, CartRequest
        rows = self.targets(values)
        requests = [CartRequest(row.track, tuple((r.category, r.link_url) for r in row.records
                    if r.category in {'bandcamp', 'beatport'})) for row in rows]
        requests = [r for r in requests if r.links]
        if not requests:
            return
        session = self.services.cart
        record = self.record
        source = record.source if record else ''
        source_generation = self.services.state.db.crate_generation(source)
        async def work(handle):
            async def approve(plan):
                fields = []
                for i, item in enumerate(plan.items):
                    fields.append(field('select_' + str(i), item.track_label, True, 'bool'))
                    if item.price_editable:
                        fields.append(field('price_' + str(i), item.currency, str(item.price)))
                def validate(answer):
                    selected = []
                    for i, item in enumerate(plan.items):
                        if not answer['select_' + str(i)]:
                            continue
                        if item.price_editable:
                            try:
                                price = Decimal(str(answer['price_' + str(i)]))
                            except ArithmeticError:
                                raise ValueError('Invalid price')
                            if not price.is_finite() or price < (item.minimum_price or Decimal(0)):
                                raise ValueError('Invalid price')
                            if item.price_step and price % item.price_step:
                                raise ValueError('Invalid price step')
                            item = replace(item, price=price)
                        selected.append(item)
                    return CartPlan(tuple(selected), plan.results)
                return await self.form('Review cart', plan.summary(), fields, validate, handle.cancel, ok='Continue')
            async def manual(items):
                await self.ask('Finish in browser', '\n'.join(i.track_label for i in items), cancel=handle.cancel)
                return True
            outcome = await session.run_batch(requests, handle.cancel, approve=approve, manual=manual)
            if source and outcome.beatport_playlist_ready:
                await self.io(self.services.library.remember_beatport, source, source_generation, outcome)
            while True:
                options = [['close', 'Close'], ['focus', 'Show carts in browser']]
                if outcome.beatport_playlist_ready:
                    options.append(['playlist', 'Send Beatport playlist metadata to Soundiiz'])
                if outcome.manual_candidates:
                    options.append(['manual', 'Finish in browser'])
                if outcome.retryable_targets:
                    options.append(['retry', 'Retry failed items'])
                answer = await self.ask('Cart results', '', [field('results', '', '\n'.join(r.track_label + ': ' + r.status + ' ' + r.reason for r in outcome.results), 'log'),
                                                            field('next', 'Next step', 'close', 'choice', options)], handle.cancel, ok='Continue')
                answer = {answer['next']: True}
                if answer.get('focus'):
                    await session.focus_carts()
                if answer.get('playlist'):
                    from ..services.purchases import prepare_playlist
                    exported = await prepare_playlist(requests, outcome, record.title if record else '',
                                                      Path(self.services.config.download_directory),
                                                      self.services.config.browser, io=self.io)
                    self.send('message', {'text': str(exported.path or '') + (' — Soundiiz import failed' if exported.import_failed else '')})
                if answer.get('manual'):
                    results = await session.finish_manually(list(outcome.manual_candidates), manual, handle.cancel)
                    self.send('message', {'text': '\n'.join(r.track_label + ': ' + r.status for r in results)})
                if not answer.get('retry'):
                    if answer.get('close'):
                        break
                    continue
                retry = [replace(request, links=tuple((store, url) for store, url in request.links
                         if (request.track.key, store) in outcome.retryable_targets)) for request in requests]
                outcome = await session.run_batch([request for request in retry if request.links], handle.cancel,
                                                  approve=approve, manual=manual)
                if source and outcome.beatport_playlist_ready:
                    await self.io(self.services.library.remember_beatport, source, source_generation, outcome)
            await self.refresh_rows()
        await self.job('Preparing cart', work)

    async def action_store_login(self, values):
        async def work(handle):
            await self.services.cart.setup_logins(('bandcamp', 'beatport'), handle.cancel)
        await self.job('Store accounts', work)

    def close(self):
        self.loop.call_soon_threadsafe(lambda: self.loop.create_task(self._close()))

    async def _close(self):
        self.closing = True
        self.waveform_cancel.set()
        self.play_generation += 1
        if hasattr(self, 'services'):
            self.services.operations.stop_accepting()
        for future in self.questions.values():
            if not future.done():
                future.set_result(None)
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if hasattr(self, 'ticker'):
            await self.ticker
        if self.prepared is not None:
            await self.io(self.prepared.close)
            self.prepared = None
        if hasattr(self, 'services'):
            if self.services._cart is not None:
                await self.services._cart.close()
            self.services.stop()
        self.loop.stop()


def field(name, label, value='', kind='text', options=()):
    return dict(name=name, label=label, value=value, kind=kind, options=[list(o) for o in options])


def required(name):
    def validate(answer):
        if not str(answer.get(name, '')).strip():
            raise ValueError('This field is required')
        return answer
    return validate
