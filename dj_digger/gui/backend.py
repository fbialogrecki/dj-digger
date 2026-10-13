"""Desktop orchestration. Qt receives detached values, never service objects."""
import asyncio
import json
import logging
import os
import stat
import threading
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .. import links
from ..analysis import key_names
from ..cart_models import CartCancelled
from ..diagnostics import log_safe_text
from ..models import GOT, NEW, SKIP, Cancelled, Crate, check_cancelled, is_cancelled
from ..paths import playlist_download_directory
from ..services.collection import DigOptions
from ..services.local_library import LocalLibrary, media_tracks
from ..services.runtime import ApplicationServices
from ..soundcloud import is_soundcloud_url

LOGGER = logging.getLogger(__name__)
RELEASES_URL = 'https://github.com/fbialogrecki/dj-digger/releases'
# Opening or downloading more tracks than this asks first.
CONFIRM_ABOVE = 10
# The browser extension's panel sends at most one player.state per this many seconds.
BROWSER_DEBOUNCE = .2


def cover_hash(text):
    """``cover.hash`` in Main.qml (32-bit multiply-add over UTF-16 units, then its magnitude):
    the browser panel draws the same record from it without ever seeing the track key."""
    value = 7
    for unit in memoryview(text.encode('utf-16-le')).cast('H'):
        value = (value * 31 + unit) & 0xFFFFFFFF
    return abs(value - (1 << 32) if value & 0x80000000 else value)


class Backend:
    def __init__(self, emit, services_factory=ApplicationServices, connect_browser=False):
        self.emit = emit
        self.factory = services_factory
        # Only the desktop, holding the instance lock, opens the extension channel.
        self.connect_browser_link = connect_browser
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
        # Title-only scan matches: local preview only, never ownership, download or export.
        self.preview_paths = {}
        # The playlist being imported, listed in the sidebar before it is saved.
        self.importing = None
        self.saved_playlists = []
        self.mark_lock = asyncio.Lock()
        self.player_lock = asyncio.Lock()
        self.waveform_lock = asyncio.Lock()
        self.waveform_cancel = threading.Event()
        # Seeks arriving faster than the decoder can honor them collapse into the latest.
        self.seek_request = None
        self.seeking = False
        self.seeks = 0
        # The browser extension's panel (webext player.state): the window's colours and theme,
        # the details last shown, the small cover for the loaded key, and what was last queued.
        self.browser_look = {}
        self.now_playing = {}
        self.browser_artwork = ('', '')
        self.browser_state = None
        self.browser_timer = None
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.close()
        self.send('closed', {})

    def send(self, kind, values, copy=True):
        # copy=False only for payloads built in the call from immutable values.
        detached = deepcopy(values) if copy else values
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

    async def ask(self, title, body='', fields=(), cancel=None, ok='', error='', dismiss=''):
        """``dismiss`` names the Cancel button; answering with it raises Cancelled."""
        ident = uuid4().hex
        future = self.loop.create_future()
        self.questions[ident] = future
        self.send('question', dict(id=ident, title=title, body=body, fields=list(fields), ok=ok, error=error, cancel=dismiss))
        try:
            while not future.done():
                if self.closing or (is_cancelled(cancel)):
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

    async def form(self, title, body, fields, validate, cancel=None, ok='', dismiss=''):
        """Re-open the same dialog with the entered values until validation passes."""
        error = ''
        shown = {f['name'] for f in fields if f['kind'] in DISPLAY_KINDS}
        while True:
            answer = await self.ask(title, body, fields, cancel, ok, error, dismiss)
            # Read-only rows and section headers are not answers.
            answer = {name: value for name, value in answer.items() if name not in shown}
            try:
                return validate(answer)
            except ValueError as exc:
                error = str(exc)
                fields = [f if f['name'] in shown else dict(f, value=answer.get(f['name'], f['value'])) for f in fields]

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
                if self.connect_browser_link:
                    try:
                        await self.io(self.services.connect_browser)
                        # The panel's buttons arrive on a connection thread and take the window's path.
                        self.services.browser_link.on_player_command = lambda op: self.submit('remote', {'op': op})
                        self.publish_browser()
                    except Exception as exc:
                        LOGGER.warning('Browser extension channel unavailable: %s', log_safe_text(exc))
                self.local = LocalLibrary(self.services.state.db)
                from ..export import recover
                messages = await self.io(recover, self.services.state.db)
                await self.sidebar()
                if messages:
                    self.send('message', {'text': '\n'.join(messages)})
                config = self.services.config
                # The window asks for Welcome (action_welcome) once it is up: first start, or nothing imported yet.
                self.send('ready', {'volume': config.volume,
                                    'welcome': not config.onboarded and (config.first_run or not self.saved_playlists)})
                await self.action_account({})
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
        except (Cancelled, CartCancelled, asyncio.CancelledError):
            self.send('message', {'text': 'Cancelled'})
        except Exception as exc:
            LOGGER.exception('Desktop action failed: %s', action)
            self.send('error', {'text': log_safe_text(exc)})

    async def sidebar(self):
        headers = await self.io(self.services.library.headers)
        self.saved_playlists = [dict(title=h.title, source=h.source, count=h.count) for h in headers]
        self.send_sidebar()

    def send_sidebar(self):
        pending = [self.importing] if self.importing else []
        self.send('sidebar', {'items': pending + [item for item in self.saved_playlists
                                                  if not pending or item['source'] != pending[0]['source']],
                              'pinned': list(self.services.config.pinned_directories),
                              'hidden': list(self.services.config.hidden_directories)})

    async def publish(self, tracks, title, generation):
        from ..rows import Row
        if generation != self.generation:
            return
        self.rows = [Row(i, t, links.categorise(t)) for i, t in enumerate(tracks)]
        self.send('view', {'generation': generation, 'title': title, 'source': self.record.source if self.record else '',
                          'local': self.folder is not None or bool(self.record and self.record.source.startswith('local-playlist:')),
                          'rows': [self.row_value(r) for r in self.rows]}, copy=False)

    def row_value(self, row):
        t = row.track
        classic, camelot = key_names(t.key_signature) or ('', '')
        return dict(key=t.key, title=t.title, artist=t.artist, genre=t.genre_label,
                    bpm=t.bpm or '', keySignature=t.key_signature, classicKey=classic, camelot=camelot, year=t.release_year or '',
                    label=t.label_name, duration=t.duration, status=self.services.state.get(t.key),
                    format=t.audio_format, comment=t.comment,
                    stores=', '.join(row.categories), storeNames=', '.join(row.names), url=t.permalink_url, local=bool(t.local_path), path=t.local_path or self.preview_paths.get(t.key, ''),
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
        local_ids = [row.track.local_id for row in rows if row.track.local_id]
        local = await self.io(media_tracks, self.services.state.db, local_ids) if local_ids else {}
        for row in rows:
            if row.track.local_id:
                row.track = local.get(row.track.local_id, row.track)
            elif path := self.services.state.local_file(row.track.key):
                row.track.local_path = path
        if generation == self.generation:
            self.rows = rows
            self.send('rows', {'generation': generation, 'rows': [self.row_value(r) for r in rows]}, copy=False)
            player = self.services.player
            # Analysis or an edit may have given the playing track its BPM or key.
            if player.loaded and (row := next((r for r in rows if r.track.key == player.loaded.track.key), None)):
                self.publish_now_playing(row.track)

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
        # Metadata hydration uses private copies and cannot overwrite a later view. Only files
        # never probed are inspected; an indexed file already carries its tags and duration.
        # ponytail: duration 0 stands for "never probed"; a file ffprobe reads as 0 s is retried per visit.
        if not any(track.duration == 0 for track in tracks):
            return
        hydrated = []
        shown = {track.key: (track.local_path, track.title, track.artist) for track in tracks}
        for track in tracks:
            if generation != self.generation:
                return
            if track.duration == 0:
                try:
                    track = await self.io(self.local.register, Path(track.local_path), inspect=True)
                except Exception as exc:
                    LOGGER.info('Metadata unavailable: %s', log_safe_text(exc))
            hydrated.append(track)
        from ..rows import Row
        if generation == self.generation:
            # A row renamed or given an artist meanwhile keeps that edit over this older reading.
            current = {row.track.key: row.track for row in self.rows}
            def newest(track):
                now = current.get(track.key)
                return now if now and (now.local_path, now.title, now.artist) != shown[track.key] else track
            hydrated = [newest(track) for track in hydrated]
            # Same files in the same order: an update, not a new view, so the table keeps its state.
            self.rows = [Row(i, t, links.categorise(t)) for i, t in enumerate(hydrated)]
            self.send_rows(generation)

    async def job(self, name, work, lane='main'):
        handle = self.services.operations.start(name, lane=lane)
        self.send('busy', {'value': True, 'text': name})
        try:
            return await work(handle)
        finally:
            self.services.operations.finish(handle)
            self.send('busy', {'value': self.services.operations.visible is not None, 'text': ''})

    async def action_dig(self, values):
        target = values.get('target')
        # A refresh keeps its rows until the new ones are in; a new import shows itself at once.
        adding = not target
        if adding:
            target = (await self.form('Add playlist', '', [field('target', 'Paste a link to your SoundCloud playlist')],
                                      soundcloud_link('target'), ok='Add'))['target'].strip()
            self.generation += 1
            self.record, self.folder = None, None
            self.importing = dict(title=target, source=target, pending=True)
            self.show_import(Crate(source=target, tracks=[], title=target), self.generation)
            await self.sidebar()
        generation = self.generation
        last = [0.0]

        def progress(stage, done, total):
            # Called per track while link hubs open; ten updates a second are plenty to watch.
            now = time.monotonic()
            if done == total or now - last[0] > .1:
                last[0] = now
                self.loop.call_soon_threadsafe(self.send, 'importProgress', dict(stage=stage, done=done, total=total or 0))

        shown = [0.0]

        def arrived(crate):
            # Each batch of 50 rebuilt the whole table; once a second is enough to watch it
            # fill, and the complete list always shows before the link hubs are opened.
            now = time.monotonic()
            complete = bool(crate.declared_count) and len(crate.tracks) >= crate.declared_count
            if adding and (complete or now - shown[0] >= 1):
                shown[0] = now
                self.loop.call_soon_threadsafe(self.show_import, crate, generation)

        async def work(handle):
            result = await self.io(self.services.collection.collect, target, DigOptions(),
                                   self.services.state.db.snapshot_generations(), 'none', None,
                                   handle.cancel, progress, arrived)
            if result.record and generation == self.generation:
                self.record, self.folder = result.record, None
                self.generation += 1
                await self.publish(result.record.active_tracks, result.record.title, self.generation)
        try:
            await self.job('Collecting tracks', work)
        finally:
            if adding and generation == self.generation:
                # Nothing was saved (failed, cancelled or empty): withdraw the half-filled view.
                self.generation += 1
                self.rows = []
                self.send('view', {'generation': self.generation, 'title': '', 'source': '', 'local': False, 'rows': []})
            self.importing = None
            self.send('importProgress', {})
            await self.sidebar()

    def show_import(self, crate, generation):
        """The tracks of an import so far, shown under its provisional sidebar entry; not saved yet."""
        from ..rows import Row
        if generation != self.generation:
            return
        self.rows = [Row(i, t, links.categorise(t)) for i, t in enumerate(crate.tracks)]
        self.send('view', {'generation': generation, 'title': crate.title, 'source': crate.source, 'local': False,
                           'rows': [self.row_value(r) for r in self.rows]}, copy=False)
        if self.importing and self.importing['title'] != crate.title:
            self.importing['title'] = crate.title
            self.send_sidebar()

    async def action_refresh(self, values):
        # The sidebar refreshes the playlist it was asked about, shown first.
        if (source := values.get('source')) and not (self.record and self.record.source == source):
            await self.action_load({'source': source})
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
        generation = self.generation
        await self.io(self.services.state.set_many, [(key, status) for key, _ in previous])
        self.undo.append(previous)
        self.send_rows(generation)

    async def action_undo(self, values):
        if self.undo:
            generation = self.generation
            await self.io(self.services.state.set_many, self.undo.pop())
            self.send_rows(generation)

    def send_rows(self, generation):
        """Statuses changed but the crate did not: rebuild the row values from memory."""
        if generation == self.generation:
            self.send('rows', {'generation': generation, 'rows': [self.row_value(r) for r in self.rows]}, copy=False)

    async def action_open(self, values):
        rows = self.targets(values)
        urls = []
        stores = set(values.get('stores') or ())
        for row in rows:
            # A store filter names stores as the Stores column does, a gate by its site.
            candidates = [record for record, name in zip(row.records, row.names)
                          if (not stores or name in stores) and links.is_openable(record.link_url)]
            if candidates:
                urls.append((row.track.key, candidates[0].link_url))
        async def work(handle):
            if len(urls) > CONFIRM_ABOVE:
                await self.ask('Open in browser', '', [dict(field('count', '', 'Open {0} tabs in the browser?', 'note'), args=[len(urls)]),
                                                       field('urls', '', '\n'.join(url for _, url in urls), 'log')], handle.cancel, ok='Open')
            for key, url in urls:
                check_cancelled(handle.cancel)
                await self.io(self.services.opening.open_one, url, key, self.services.config.browser)
            await self.refresh_rows()
        if urls:
            await self.job('Open links', work)

    async def action_download(self, values):
        from ..services.downloads import DownloadRequest, DownloadWorkflow
        rows = self.targets(values)
        if not rows:
            return
        if self.services.config.first_run:
            # Asked once: Skip keeps the defaults and the download goes ahead.
            self.services.config.first_run = False
            if not await self.skippable(self.action_settings({})):
                self.send('message', {'text': 'Downloading to {0}; change the folder in Settings',
                                      'args': [self.services.config.download_directory]})
        source = self.record.source if self.record else ''
        directory = playlist_download_directory(self.services.config.download_directory,
                                                self.record.title if self.record else '')
        request = DownloadRequest(source, self.services.state.db.crate_generation(source),
                                  directory, 20)
        labels = {row.track.key: row.track.label for row in rows}
        async def work(handle):
            if len(rows) > CONFIRM_ABOVE:
                await self.ask('Download', '', [dict(field('count', '', 'Download {0} tracks?', 'note'), args=[len(rows)])],
                               handle.cancel, ok='Download')
            def prerequisites(profile, auth):
                async def configure():
                    # Each step can be skipped on its own; its rows then end in the results with why.
                    ready = []
                    for items, step, group in ((profile, lambda: self.action_settings({}), 'profile'),
                                               (auth, lambda: self.authenticate(handle.cancel), 'login')):
                        if items and await self.skippable(step(), handle.cancel):
                            ready += items
                        else:
                            for track, _url in items:
                                failed[track.key] = (group, '')
                                put(track.key, -1)
                    check_cancelled(handle.cancel)
                    return ready
                return asyncio.run_coroutine_threadsafe(configure(), self.loop).result()
            def attend(reasons):
                # Nothing of the browser is shown unless the person asks for it here (plan Z2).
                async def question():
                    gates = '\n'.join(labels.get(key, key) + ': ' + reason for key, reason in reasons.items())
                    try:
                        answer = await self.ask('Gates need you', 'These gates want something only you can do, '
                                                'such as a login or a CAPTCHA.',
                                                [field('gates', '', gates, 'log'),
                                                 field('next', '', 'open', 'buttons',
                                                       [['open', 'Open in browser'], ['skip', 'Skip these']])],
                                                handle.cancel, dismiss='Stop download')
                    except Cancelled:
                        # Stop download ends the whole download, not just this question.
                        self.services.operations.cancel(handle)
                        raise
                    return answer['next'] == 'open'
                return asyncio.run_coroutine_threadsafe(question(), self.loop).result()
            put, flushing = self.progress_batch(values)
            failed, waiting, settled, browser = {}, set(), set(), [False]
            def emit(event):
                if event.kind == 'progress':
                    put(event.key, event.progress)
                elif event.kind == 'waiting':
                    waiting.add(event.key)
                    put(event.key, .5)  # handed on: the browser pass, a wizard or the next run
                elif event.kind == 'downloaded':
                    settled.add(event.key)
                    put(event.key, 1.0)
                elif event.kind in {'failed', 'unrecorded', 'cancelled'}:
                    settled.add(event.key)
                    put(event.key, -1)
                    if event.kind != 'cancelled':
                        failed[event.key] = (event.group, event.message)
                elif event.kind == 'browser_started':
                    browser[0] = True
                    self.send('busy', {'value': True, 'text': 'Downloading gates in the background'})
                elif event.kind == 'status':
                    self.send('message', {'text': event.message})
            workflow = DownloadWorkflow(self.services.downloads, request, handle,
                                        client=lambda: self.services.client, config=self.services.config,
                                        emit=emit, prerequisites=prerequisites, attend=attend)
            remote = await self.settle_local(rows, request.directory, handle.cancel)
            while remote:
                async with flushing():
                    await self.io(workflow.run_batch, remote)
                await self.refresh_rows()
                # Rows the batch handed on and never settled (more gates than one browser batch takes)
                # are retried with the failed ones.
                left = waiting - settled
                remote = await self.download_results(remote, failed, left, browser[0], handle.cancel)
                for seen in (failed, waiting, settled):
                    seen.clear()
            await self.refresh_rows()
        self.services.client
        await self.job('Downloading', work)

    async def download_results(self, remote, failed, left, browser_used, cancel):
        """What a gate batch could not get, and what to do about it (plan Z3): the items to run again."""
        keys = list(failed) + [key for key in left if key not in failed]
        if not keys or is_cancelled(cancel):  # a cancelled download ends without another question
            return []
        by_key = {track.key: (track, url) for track, url in remote}
        lines, human = [], False
        for key in keys:
            if key in by_key:
                text, needs_person = failure_reason(*failed.get(key, ('left', '')))
                lines.append(by_key[key][0].label + ': ' + text)
                human = human or needs_person
        # The first button is the default: the browser when a person must act, else another try.
        options = [['open', 'Open in browser'], ['retry', 'Retry these']]
        if not human:
            options.reverse()
        if browser_used and not (self.services.browser_link and self.services.browser_link.connections()):
            options.append(['connect', 'Connect browser and retry'])
        try:
            answer = await self.ask('Download results', '', [field('results', '', '\n'.join(lines), 'log'),
                                                              field('next', '', options[0][0], 'buttons', options)],
                                    cancel, dismiss='Close')
        except Cancelled:
            if self.closing or is_cancelled(cancel):
                raise
            return []
        chosen = [by_key[key] for key in keys if key in by_key]
        if answer['next'] == 'open':
            for track, url in chosen:
                check_cancelled(cancel)
                await self.io(self.services.opening.open_one, url or track.permalink_url, track.key,
                              self.services.config.browser)
            return []
        if answer['next'] == 'connect':
            await self.connect_browser(cancel)
        return chosen if answer['next'] in {'retry', 'connect'} else []

    def progress_batch(self, values):
        """Download threads `put` progress; inside `flushing()` the loop sends it every 100 ms."""
        pending = {}
        pending_lock = threading.Lock()
        def put(key, progress):
            with pending_lock:
                pending[key] = progress
        @asynccontextmanager
        async def flushing():
            settled = asyncio.Event()
            async def flush():
                while not settled.is_set():
                    with pending_lock:
                        updates = dict(pending)
                        pending.clear()
                    if updates:
                        self.send('progress', {'generation': values['generation'], 'updates': updates})
                    await asyncio.sleep(.1)
            monitor = self.loop.create_task(flush())
            try:
                yield
            finally:
                settled.set()
                await monitor
        return put, flushing

    async def settle_local(self, rows, directory, cancel):
        """Copy or mark rows already on disk; return the rest with their gate links."""
        from ..services.downloads import find_gate_url
        remote = []
        for row in rows:
            if row.track.local_path:
                if await self.io(self.services.library.needs_copy, row.track.local_path, directory):
                    await self.io(self.services.downloads.copy, row.track.key, Path(row.track.local_path), directory, cancel)
                else:
                    await self.io(self.services.library.mark_existing, row.track)
            else:
                remote.append((row.track, find_gate_url(row.records)))
        return remote

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
        await self.update_manual(track.local_id, bpm=bpm, key=key)
        await self.refresh_rows()

    async def update_manual(self, media_id, **values):
        """Change some manual values of a local file; an empty value goes back to tags or analysis."""
        db = self.services.state.db
        manual = json.loads((await self.io(db.media_values, media_id)).get('manual_json') or '{}')
        for name, value in values.items():
            if value:
                manual[name] = value
            else:
                manual.pop(name, None)
        await self.io(db.set_media_manual, media_id, manual)

    async def action_rename(self, values):
        """Edit a local file's name (renaming it on disk) or its artist, from the table."""
        from ..services.local_library import media_track
        rows = self.targets(values)
        if len(rows) != 1 or not rows[0].track.local_id:
            return
        track, value = rows[0].track, str(values.get('value', ''))
        if values.get('field') == 'title':
            await self.io(self.local.rename, track, value)
        elif values.get('field') == 'artist':
            await self.update_manual(track.local_id, artist=value.strip())
        else:
            raise ValueError('Unknown field')
        # A new name can also bring a new artist, read off it until one is set by hand.
        fresh = media_track(self.services.state.db, await self.io(self.services.state.db.media, track.local_id))
        changes = dict(title=fresh.title, artist=fresh.artist, local_path=fresh.local_path)
        for row in self.rows:
            if row.track.key == track.key:
                row.track = replace(row.track, **changes)
                player = self.services.player
                if player.loaded and player.loaded.track.key == track.key:
                    self.publish_now_playing(row.track)
        self.send_rows(values['generation'])

    async def action_export(self, values):
        from ..export import execute, plan_export
        if source := values.get('source'):
            # A local playlist from the sidebar exports all of its files, opened or not.
            if not source.startswith('local-playlist:'):
                raise ValueError('Only local playlists can be exported')
            tracks = [m['path'] for m in await self.io(self.services.state.db.local_playlist_media, source)]
            folder = None
        else:
            tracks = [r.track.local_path for r in self.targets(values) if r.track.local_path]
            folder = self.folder
        if not tracks and folder is None:
            return
        config = self.services.config
        answer = await self.export_form(config)
        config.export_decks = answer['decks']
        await self.io(config.save)
        async def work(handle):
            paths = await self.export_paths(tracks, folder, values, answer, handle.cancel)
            # A replacement plan only falls back to this folder when the sources share no root.
            destination = (Path(answer['folder']).expanduser() if answer['folder']
                           else paths[0].parent if paths else Path())
            plan = await self.io(plan_export, paths, destination, decks=answer['decks'], mode=answer['mode'], cancel=handle.cancel)
            summary = [*(f'Warning: {note}' for note in plan.notes), f'{plan.profile.label()}, for files that need converting',
                       *(f'{deck}: {state}' for deck, state in plan.compatibility().items()), '']
            await self.ask('Review export', 'Replacing originals permanently removes them after verification.' if plan.mode == 'replace' else '',
                           [field('plan', '', '\n'.join([*summary, *(f'{i.action}: {i.source} → {i.destination} ({i.reason})' for i in plan.items)]), 'log')],
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
                await self.io(write_private_json, data_dir() / ('export-' + plan.id + '.json'), report, durable=True)
            self.send(*export_notice(report))
            await self.refresh_rows()
        await self.job('Exporting audio', work)

    async def export_form(self, config):
        from ..decks import DECK_GROUPS
        def validate(answer):
            # Replacing works in place, so only a copy needs somewhere to go.
            answer['folder'] = str(answer['folder']).strip()
            if answer['mode'] == 'copy' and not answer['folder']:
                raise ValueError('This field is required')
            picked = {str(index) for index in answer.get('decks') or ()}
            decks = [name for index, group in enumerate(DECK_GROUPS) if str(index) in picked for name in group]
            if not decks:
                raise ValueError('Choose at least one deck')
            # A copy: a retry re-opens the dialog with the ticked groups, not deck names.
            return dict(answer, decks=decks)
        return await self.form(
            'Export audio', 'Choose the decks these files must play on; the best format they all play is picked for you.',
            [field('decks', 'Decks', [str(index) for index, group in enumerate(DECK_GROUPS) if set(group) & set(config.export_decks)],
                   'checks', [(str(index), ', '.join(group)) for index, group in enumerate(DECK_GROUPS)]),
             field('folder', 'Destination folder (Copy only)', kind='folder'),
             field('mode', 'Mode', 'copy', 'choice', [['copy', 'Copy'], ['replace', 'Replace originals']]),
             field('recursive', 'Include subfolders', False, 'bool')], validate, ok='Export')

    async def export_paths(self, tracks, folder, values, answer, cancel):
        """The chosen files, or the whole folder view narrowed by its search and hide filters."""
        paths = tuple(Path(path) for path in tracks)
        if folder and not values.get('selected'):
            paths = await self.io(self.local.selection, folder, recursive=answer['recursive'], cancel=cancel)
            if values.get('search') or values.get('statuses'):
                from ..playlist import filter_rows
                from ..rows import Row
                matching = []
                for path in paths:
                    track = await self.io(self.local.register, path, inspect=bool(values.get('search')), cancel=cancel)
                    if filter_rows([Row(0, track, [])], values.get('search', ''), values.get('statuses', ()), lambda row: self.services.state.get(row.track.key)):
                        matching.append(path)
                paths = tuple(matching)
        return paths

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

    async def action_add_to_playlist(self, values):
        """Tracks dropped on a local playlist in the sidebar join it at the end."""
        source = values['source']
        if not source.startswith('local-playlist:'):
            raise ValueError('Tracks can only be added to a local playlist')
        ids = [r.track.local_id for r in self.targets(values) if r.track.local_id]
        headers = await self.io(self.services.library.headers)
        if not ids or not any(h.source == source for h in headers):
            return
        await self.io(self.services.state.db.save_local_playlist, source, '', ids)
        await self.sidebar()
        if self.record and self.record.source == source:
            await self.action_load({'source': source})

    async def action_rename_playlist(self, values):
        source = values['source']
        headers = await self.io(self.services.library.headers)
        current = next((h.title for h in headers if h.source == source), None)
        if current is None:
            return
        answer = await self.form('Rename playlist', '', [field('title', 'Playlist name', current)], required('title'), ok='Rename')
        await self.io(self.services.state.db.rename_crate, source, answer['title'].strip())
        await self.sidebar()
        if self.record and self.record.source == source:
            await self.action_load({'source': source})

    async def action_duplicate_playlist(self, values):
        """A copy of a local playlist under a new name; the files are shared, not copied."""
        source = values['source']
        if not source.startswith('local-playlist:'):
            raise ValueError('Only local playlists can be duplicated')
        db = self.services.state.db
        media = await self.io(db.local_playlist_media, source)
        headers = await self.io(self.services.library.headers)
        title = next((h.title for h in headers if h.source == source), '')
        answer = await self.form('Duplicate playlist', '', [field('title', 'Playlist name', title)], required('title'), ok='Duplicate')
        await self.io(db.save_local_playlist, 'local-playlist:' + uuid4().hex, answer['title'].strip(), [m['id'] for m in media])
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
        """Import every playlist a profile created; Welcome passes the link it already asked for."""
        from ..paths import data_dir
        from ..private_json import write_private_json
        from ..services.profile_import import import_profile
        from ..soundcloud_errors import SoundCloudError
        if values.get('url'):
            answer = profile_answer(values)
        else:
            answer = await self.form('Import profile playlists', '', [
                field('url', 'Your SoundCloud profile link', hint='For example https://soundcloud.com/your-name'),
                field('private', 'Include private playlists', False, 'bool',
                      hint='Only when you are signed in to SoundCloud as this profile')], profile_answer, ok='Import')
        client = self.services.client
        async def work(handle):
            def progress(done, total):
                self.loop.call_soon_threadsafe(self.send, 'importProgress', dict(stage='Importing playlists', done=done, total=total))
            error = ''
            try:
                report = await self.io(import_profile, client, self.services.state.db, answer['url'],
                                       private=answer['private'], cancel=handle.cancel,
                                       current=lambda: self.services._client is client, progress=progress)
            except SoundCloudError as exc:
                report, error = getattr(exc, 'report', []), str(exc)
            finally:
                self.send('importProgress', {})
                await self.sidebar()
            output = data_dir() / ('profile-import-' + uuid4().hex + '.json')
            await self.io(write_private_json, output, {'profile': answer['url'], 'results': report, 'error': error})
            return report, error, output
        report, error, output = await self.job('Importing playlists', work)
        await self.skippable(self.ask('Profile import', answer['url'], [*profile_summary(report, error),
                                                                        field('report', 'Full report', str(output), 'path')], ok='Close'))

    async def action_settings(self, values):
        config = self.services.config
        from ..config import is_real_email
        choices, _ = await self.io(self.services.accounts.browser_choices)
        def validate(answer):
            if answer['user_email'] and not is_real_email(answer['user_email']):
                raise ValueError('Enter a valid email')
            if not answer['download_directory'].strip():
                raise ValueError('Specify a download folder')
            answer['default_artwork'] = answer['default_artwork'].strip()
            if answer['default_artwork'] and not Path(answer['default_artwork']).expanduser().is_file():
                raise ValueError('The default cover is not a file')
            answer.pop('browser_status', None)
            for name in ('scan_directories', 'pinned_directories', 'hidden_directories', 'custom_comments'):
                answer[name] = [line.strip() for line in answer[name].splitlines() if line.strip()]
            return answer
        status, status_args = self.browser_status()
        browsers = [[value, label] for value, label in choices]
        if config.browser and not any(config.browser == value for value, _ in choices):
            # Kept as saved, not quietly replaced: it may be back on another day or computer.
            browsers.append([config.browser, '{0} (not available on this computer)', config.browser])
        answer = await self.form('Settings', '', [
            section('section_downloads', 'Downloads'),
            field('download_directory', 'Download folder', config.download_directory, 'folder',
                  hint='Each playlist gets its own folder inside it.'),
            section('section_gates', 'Gates and carts',
                    'Gates are free downloads you get for a follow, a like or your email; dj-digger fills them in for you. '
                    'Gates may submit your name and email. Social actions may follow, like, repost or comment, '
                    'and connect Spotify, Deezer or Apple Music, on the accounts logged in to your browser.'),
            field('user_name', 'Name', config.user_name),
            field('user_email', 'Email', config.user_email if config.has_real_email() else '',
                  hint='Some gates ask for one before the download.'),
            field('gate_social_actions', 'Allow social actions', config.gate_social_actions, 'bool',
                  hint='Off: gates that want a follow, like, repost or comment are left for you to do in the browser.'),
            field('custom_comments', 'Gate comments (one per line)', '\n'.join(config.custom_comments), 'multiline',
                  hint='One is picked at random when a gate asks for a comment.'),
            field('nyp_price', 'Bandcamp name-your-price items go into the cart at', config.nyp_price, 'choice',
                  [['minimum', 'The minimum price'], ['suggested', 'The price the artist suggests']],
                  hint='Nothing is bought: you pay in the store yourself.'),
            section('section_browser', 'Browser'),
            dict(field('browser_status', 'Browser automation', status, 'note'), args=status_args),
            field('browser', 'Open links in', config.browser, 'choice', browsers,
                  hint='Store and artist links open here. SoundCloud sign-in, gates and carts open in the browser '
                       'with the dj-digger extension (Settings → Connect browser…).'),
            section('section_player', 'Player'),
            field('default_artwork', 'Default cover (tracks without artwork)', config.default_artwork, 'image'),
            section('section_folders', 'Library folders (advanced)', collapsed=True),
            field('scan_directories', 'Scan folders (one per line)', '\n'.join(config.scan_directories), 'multiline',
                  hint='Where dj-digger looks for music you already have.'),
            field('pinned_directories', 'Pinned folders (one per line)', '\n'.join(config.pinned_directories), 'multiline',
                  hint='Listed under Folders in the sidebar.'),
            field('hidden_directories', 'Hidden folders (one per line)', '\n'.join(config.hidden_directories), 'multiline',
                  hint='Left out of the folder tree.')], validate, ok='Save')
        await self.io(self.services.accounts.save_preferences, answer)
        self.services.config.first_run = False
        await self.sidebar()

    async def authenticate(self, cancel):
        self.services.accounts.begin_authentication()
        result = await self.with_extension(
            lambda: self.io(self.services.accounts.authenticate, 'browser', '', cancel, lambda text: None), cancel)
        if result.error:
            raise ValueError(result.error)
        if result.token:
            self.services.adopt_login(result.token)
            browser = self.connected_browser()
            if result.username and browser:
                self.send('message', {'text': 'Signed in as {0} in {1}', 'args': [result.username, browser]})
            elif result.username:
                self.send('message', {'text': 'Signed in as {0}', 'args': [result.username]})
            else:
                self.send('message', {'text': 'Signed in'})
            await self.action_account({})
        else:
            raise Cancelled()

    async def action_login(self, values):
        await self.job('Signing in', lambda handle: self.authenticate(handle.cancel))

    async def action_logout(self, values):
        from ..auth import clear_token
        await self.ask('Sign out', 'Remove the stored SoundCloud session from this computer. Your browser stays '
                       'signed in to SoundCloud; sign out there too if you want to.', ok='Sign out')
        await self.io(clear_token)
        self.services.adopt_login(None)
        await self.action_account({})

    async def action_account(self, values):
        """Who is signed in and which browser is connected, for the Settings menu; never the token."""
        from .. import auth
        token, info = await self.io(lambda: (auth.get_stored_token(), auth.get_stored_auth_info()))
        link = self.services.browser_link
        connected = [c for c in link.connections() if c.compatible] if link is not None else []
        browser = str(max(connected, key=lambda c: c.last_active).hello.get('browser') or 'browser').capitalize() if connected else ''
        # An environment token belongs to no saved name.
        name = str(info.get('username') or '') if token and info.get('oauth_token') == token else ''
        self.send('account', dict(signedIn=bool(token), name=name, browser=browser), copy=False)

    async def action_about(self, values):
        from .. import __version__
        from ..paths import data_dir
        await self.ask('About dj-digger', 'Dig purchase and free-download links out of SoundCloud playlists, likes and profiles.',
                       [field('version', 'Version', __version__, 'note'), field('licence', 'Licence', 'Apache License 2.0', 'note'),
                        field('data', 'Data folder', str(data_dir()), 'path')], ok='Close')

    async def action_updates(self, values):
        # The releases page in the browser; the app itself never checks over the network.
        from ..browser import open_url
        await self.io(open_url, RELEASES_URL, self.services.config.browser)

    async def player_call(self, function, *args):
        async with self.player_lock:
            return await self.io(function, *args)

    async def action_play(self, values):
        rows = self.targets(values)
        if not rows:
            return
        track = self.playable(rows[0].track)
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
                source = prepared.source
                loaded = await self.io(player.load, track, prepared.stream, None if track.local_path else self.services.client.session,
                              source)
                prepared.source = None
                if generation != self.play_generation or self.closing:
                    return
                # Playback starts now; the waveform and artwork follow once read or computed.
                for work in (self.load_waveform(loaded, source, waveform_cancel), self.load_artwork(loaded, waveform_cancel)):
                    task = self.loop.create_task(work)
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                await self.io(player.play)
                self.publish_audio()
                self.publish_now_playing()
            # Heard once, a track is no longer new.
            if await self.io(self.services.state.mark_played, track.key):
                self.send_rows(self.generation)
        finally:
            if prepared.source is not None:
                await self.io(prepared.close)
        if not hasattr(self, 'ticker') or self.ticker.done():
            self.ticker = self.loop.create_task(self.tick())

    async def load_waveform(self, loaded, source, cancel):
        from ..services.playback import track_waveform
        from ..waveform import drop
        envelope = dict(peaks=[], bands=[])
        try:
            async with self.waveform_lock:
                check_cancelled(cancel)
                envelope = await self.io(track_waveform, self.services.state.db, loaded.track, source, cancel)
        except (Cancelled, asyncio.CancelledError):
            return
        except Exception as exc:
            LOGGER.warning('Waveform unavailable: %s', log_safe_text(exc))
            if not cancel.is_set():
                self.send('error', {'text': 'Waveform unavailable: ' + log_safe_text(exc)})
        # Sent even when empty: the window stops waiting for it and shows the track.
        async with self.player_lock:
            if not cancel.is_set() and self.services.player.loaded is loaded:
                # ``drop`` is the fraction of the track where the first drop lands, -1 without one.
                self.send('waveform', dict(key=loaded.track.key, samples=envelope['peaks'], bands=envelope['bands'],
                                           drop=drop(envelope['peaks'], envelope['bands'])), copy=False)

    async def load_artwork(self, loaded, cancel):
        """The label picture: the track's own artwork, else the default cover from Settings."""
        from ..local_audio import artwork
        from ..services.playback import remote_artwork
        from .artwork import BROWSER_PIXELS, SQUARE_PIXELS, label_image
        track, url = loaded.track, loaded.stream.artwork_url
        def picture():
            data = None
            try:
                if track.local_path:
                    data = artwork(track.local_path, cancel)
                elif url:
                    data = remote_artwork(self.services.client.session, url)
            except Exception as exc:
                LOGGER.debug('Artwork unavailable: %s', log_safe_text(exc))
            label = label_image(data) if data else ''
            fallback = self.services.config.default_artwork
            if not label and fallback:
                try:
                    data = Path(fallback).read_bytes()
                    label = label_image(data)
                except OSError as exc:
                    LOGGER.debug('Default cover unavailable: %s', log_safe_text(exc))
            # The record label's circle, the same picture as a plain square, and a small one for the browser.
            if not label:
                return '', '', ''
            return label, label_image(data, SQUARE_PIXELS, circle=False), label_image(data, BROWSER_PIXELS, circle=False, jpeg=True)
        label, square, browser = await self.io(picture)
        async with self.player_lock:
            if not cancel.is_set() and self.services.player.loaded is loaded:
                self.send('artwork', dict(key=track.key, image=label, square=square), copy=False)
                self.browser_artwork = (track.key, browser)
                self.publish_browser()

    def publish_audio(self):
        # The per-tick snapshot stays small: the waveform travels separately and only when it changes.
        p = self.services.player
        # ``heard`` is the track time reaching the speaker at ``at`` (epoch seconds),
        # so the window can run its clock from there without the delivery delay.
        # ``seeks`` counts the seeks served, so the window tells a jump from a late snapshot.
        snapshot = dict(key=p.loaded.track.key, title=p.loaded.track.label, playing=p.playing, position=p.position,
                        duration=p.duration, heard=p.heard(), at=time.time(), seeks=self.seeks) if p.loaded else {}
        self.send('audio', snapshot)
        self.publish_browser()
        return snapshot

    def publish_browser(self):
        """The browser extension's panel: title, artist, playing, the record's seed, a small
        cover and the window's colours, queued when any of them changes (webext player.state)."""
        if getattr(self.services, 'browser_link', None) is None:
            return
        p = self.services.player
        state = dict(title='', artist='', playing=False, seed=cover_hash(''), artwork='', **self.browser_look)
        if p.loaded:
            track, key = p.loaded.track, p.loaded.track.key
            shown = self.now_playing if self.now_playing.get('key') == key else {}
            state.update(title=shown.get('name') or track.title or track.label, artist=shown.get('artist', track.artist or ''),
                         playing=bool(p.playing), seed=cover_hash(key),
                         artwork=self.browser_artwork[1] if self.browser_artwork[0] == key else '')
        if state == self.browser_state:
            return
        self.browser_state = state
        if self.browser_timer is None:
            self.browser_timer = self.loop.call_later(BROWSER_DEBOUNCE, self.flush_browser)

    def flush_browser(self):
        self.browser_timer = None
        if self.closing:
            return
        task = self.loop.create_task(self.io(self.services.browser_link.publish_player, self.browser_state))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def action_look(self, values):
        """The window's colour pair (rest foot, rest top, neon foot, neon top) and theme, for the panel."""
        colors = [c for c in values.get('colors') or [] if isinstance(c, str) and len(c) == 7 and c[0] == '#'
                  and all(x in '0123456789abcdefABCDEF' for x in c[1:])]
        self.browser_look = dict(colors=colors[:4], dark=bool(values.get('dark')))
        self.publish_browser()

    async def action_remote(self, values):
        """The panel's buttons (webext player.command), the same steps as the window's own."""
        if not self.services.player.loaded:
            return
        if values.get('op') == 'toggle':
            await self.action_transport({'operation': 'toggle'})
        elif values.get('op') in ('prev', 'next'):
            await self.action_step({'direction': -1 if values['op'] == 'prev' else 1})

    def publish_now_playing(self, track=None):
        """Details for the player panel, sent when a track loads or its details
        change; the small audio snapshot stays small."""
        p = self.services.player
        if not p.loaded:
            return
        t = track or p.loaded.track
        classic, camelot = key_names(t.key_signature) or ('', '')
        self.now_playing = dict(key=p.loaded.track.key, artist=t.artist or '', name=t.title or t.label,
                                bpm=float(t.bpm or 0), classicKey=classic, camelot=camelot)
        self.send('nowPlaying', self.now_playing)
        self.publish_browser()

    def playable(self, track):
        if track.local_path or track.key not in self.preview_paths:
            return track
        return replace(track, local_path=self.preview_paths[track.key])

    async def prepare_track(self, track):
        from ..services.playback import Prepared, resolve_stream
        if track.local_path:
            from ..local_audio import prepare_local
            return await self.io(prepare_local, track)
        if not track.id:
            raise ValueError('No track id, so there is nothing to stream')
        client = self.services.client
        def prepare():
            from ..player import open_source
            stream = resolve_stream(client, track.id)
            return Prepared(track, stream, open_source(client.session, stream.url, stream.protocol))
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
        sent_kicks = None
        while not self.closing:
            async with self.player_lock:
                p = self.services.player
                if not p.loaded:
                    break
                snapshot = self.publish_audio()
                if snapshot['playing']:
                    # Kick levels come from decoded audio before the device takes it,
                    # so they reach the window ahead of the speakers.
                    start, step, levels = p.kicks()
                    kicks = dict(key=snapshot['key'], start=start, step=step, levels=levels)
                    # Forty ticks a second; only new levels are worth crossing to the window.
                    if kicks != sent_kicks:
                        sent_kicks = kicks
                        self.send('kicks', kicks)
                event = p.take_event()
            if (snapshot['playing'] and snapshot['duration'] - snapshot['position'] < 15
                    and self.prepared is None and (self.prefetch_task is None or self.prefetch_task.done())
                    and p.loaded.track.key in self.play_order):
                index = self.play_order.index(p.loaded.track.key) + 1
                if index < len(self.play_order):
                    track = next((self.playable(r.track) for r in self.rows if r.track.key == self.play_order[index]), None)
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
            await asyncio.sleep(.025 if snapshot['playing'] else .5)

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
            self.publish_browser()
        elif operation == 'toggle':
            await self.player_call(player.toggle)
            self.publish_audio()
        elif operation in ('seek', 'nudge'):
            await self.apply_seek(operation, float(values['value']))
        elif operation in ('volume', 'mute'):
            if operation == 'volume':
                await self.player_call(player.set_volume, max(0, min(float(values['value']), 1)))
            else:
                await self.player_call(player.toggle_mute)
            # The player owns the level and mute; the window shows what it reports.
            self.send('volume', dict(volume=player.level, muted=player.muted))

    async def apply_seek(self, operation, value):
        """Serve the newest seek; nudges that pile up behind it add together."""
        pending = self.seek_request
        if operation == 'nudge' and pending is not None:
            self.seek_request = (pending[0], pending[1] + value)
        else:
            self.seek_request = (operation, value)
        if self.seeking:
            return
        self.seeking = True
        try:
            player = self.services.player
            while self.seek_request is not None:
                operation, value = self.seek_request
                self.seek_request = None
                if operation == 'seek':
                    await self.player_call(player.seek, max(0, min(value, player.duration)))
                else:
                    await self.player_call(player.nudge, value)
                self.seeks += 1
                self.publish_audio()
        finally:
            self.seeking = False

    async def action_logs(self, values):
        from ..logging_setup import current_log_path, read_log_tail
        path = current_log_path()
        text = await self.io(read_log_tail, path) if path else 'File logging unavailable'
        await self.ask('Diagnostics', str(path or ''), [field('log', '', text, 'log')], ok='Close')

    async def action_analyze_folder(self, values):
        # The sidebar names the folder it was asked about; the menu bar means the open one.
        folder = Path(values['path']) if values.get('path') else self.folder
        if folder is None:
            return
        paths = await self.io(self.local.selection, folder)
        await self.analyze_paths(paths)

    async def action_delete_files(self, values):
        files = await self.io(self.local.delete_targets, [row.track for row in self.targets(values)])
        if not files:
            return
        async def work(handle):
            await self.ask('Permanently delete files', '', [field('files', '', '\n'.join(str(p) for _, p, _ in files), 'log')], handle.cancel, ok='Delete')
            try:
                await self.io(self.local.delete_many, files, handle.cancel)
            finally:
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
        answer = await self.form('Import saved summary', '', [field('path', 'JSON file', kind='file')], required('path'), ok='Import')
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
                          'rows': [self.row_value(r) for r in self.rows]}, copy=False)

    async def pin(self, path):
        folders = list(dict.fromkeys([*self.services.config.pinned_directories, str(path)]))
        await self.io(self.services.accounts.save_preferences, {'pinned_directories': folders})
        await self.sidebar()

    async def action_hide_folder(self, values):
        """Leave a folder out of the folder tree; Settings lists it to bring it back."""
        path = str(Path(values['path']).expanduser().absolute())
        folders = list(dict.fromkeys([*self.services.config.hidden_directories, path]))
        await self.io(self.services.accounts.save_preferences, {'hidden_directories': folders})
        await self.sidebar()

    async def action_pin(self, values):
        folder = values.get('path') or self.folder
        if folder:
            await self.pin(Path(folder).expanduser().absolute())

    async def action_unpin(self, values):
        await self.forget_folder('pinned_directories', values['path'])

    async def action_unhide_folder(self, values):
        await self.forget_folder('hidden_directories', values['path'])

    async def forget_folder(self, preference, path):
        """Take a folder off a saved folder list, however its path was spelled there."""
        same = lambda value: os.path.normcase(os.path.normpath(os.path.expanduser(value)))  # noqa: E731
        kept = [folder for folder in getattr(self.services.config, preference) if same(folder) != same(path)]
        await self.io(self.services.accounts.save_preferences, {preference: kept})
        await self.sidebar()

    async def action_add_folder(self, values):
        path = await self.io(Path(values['path']).expanduser().resolve, strict=True)
        if not await self.io(path.is_dir):
            raise ValueError('Select a folder')
        if path.name.startswith('.') or getattr(await self.io(path.stat), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_HIDDEN:
            raise ValueError('Select a visible folder')
        await self.pin(path)
        await self.action_folder({'path': str(path)})

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

        async def work(handle):
            scanner = self.services.library.scanner(self.services.config.scan_directories)
            await self.io(scanner.scan, cancel=handle.cancel)
            check_cancelled(handle.cancel)
            paths = await self.io(self.services.library.match_tracks, tracks, scanner)
            for key, path in paths.items():
                if path and not self.services.state.local_file(key):
                    self.preview_paths[key] = path
                else:
                    self.preview_paths.pop(key, None)
            await self.refresh_rows()
        await self.job('Scanning', work, lane='scan')

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
            self.send(*export_notice(report))
        await self.job('Exporting audio', work)

    async def action_cart(self, values):
        from ..cart_models import CartBatchOutcome, CartRequest, CartResult
        rows = self.targets(values)
        requests = [CartRequest(row.track, tuple((r.category, r.link_url) for r in row.records
                    if r.category in {'bandcamp', 'beatport'})) for row in rows]
        # A ticked track with no store link is reported, never dropped (plan Z5).
        unlinked = tuple(CartResult(r.track.key, r.track.label, '', 'skipped', 'no Bandcamp or Beatport link', 'unavailable')
                         for r in requests if not r.links)
        requests = [r for r in requests if r.links]
        if not requests and not unlinked:
            return
        tracks = {row.track.key: row.track for row in rows}
        links = {(r.track.key, store): url for r in requests for store, url in r.links}
        session = self.services.cart
        record = self.record
        source = record.source if record else ''
        source_generation = self.services.state.db.crate_generation(source)
        def link_for(result):
            track = tracks[result.track_key]
            return (result.url or links.get((result.track_key, result.store))
                    or next((url for (key, _), url in links.items() if key == result.track_key), '')
                    or track.purchase_url or track.permalink_url)
        async def work(handle):
            async def approve(plan):
                return plan  # no review: the cart is not a purchase, its tab is the review (plan Z4)
            async def manual(items):
                await self.ask('Finish in browser', '\n'.join(i.track_label for i in items), cancel=handle.cancel)
                return True
            def batch(requests):
                # manual=None: an uncertain click is offered below, never opened in a window unasked (Z1)
                return self.with_extension(lambda: session.run_batch(requests, handle.cancel, approve=approve,
                                                                     nyp_price=self.services.config.nyp_price),
                                           handle.cancel)
            outcome = await batch(requests) if requests else CartBatchOutcome(())
            while True:
                if source and outcome.beatport_playlist_ready:
                    await self.io(self.services.library.remember_beatport, source, source_generation, outcome)
                results = unlinked + outcome.results
                missed = [r for r in results if r.track_key in tracks and r.status not in ('added', 'already_in_cart')]
                options = [['close', 'Close']]
                if outcome.cart_stores:
                    options.append(['focus', 'Show carts in browser'])
                if missed:
                    options.append(['links', 'Open the links that were not added in the browser'])
                if outcome.manual_candidates:
                    options.append(['manual', 'Finish in browser'])
                if outcome.retryable_targets:
                    options.append(['retry', 'Retry failed items'])
                if outcome.beatport_playlist_ready:
                    options.append(['playlist', 'Send Beatport playlist metadata to Soundiiz'])
                counts = dict(field('summary', '', 'Added to the carts: {0}. Not added: {1}.', 'note'),
                              args=[len(results) - len(missed), len(missed)])
                answer = await self.ask('Cart results', '',
                                        [counts, field('results', '', '\n'.join(r.track_label + ': ' + result_status(r) + ' ' + r.reason for r in results), 'log'),
                                         field('next', 'Next step', 'close', 'choice', options)], handle.cancel, ok='Continue')
                answer = {answer['next']: True}
                if answer.get('focus'):
                    await session.focus_carts()
                if answer.get('links'):
                    for result in missed:
                        check_cancelled(handle.cancel)
                        if url := link_for(result):
                            await self.io(self.services.opening.open_one, url, result.track_key, self.services.config.browser)
                if answer.get('playlist'):
                    from ..services.purchases import prepare_playlist
                    exported = await prepare_playlist(requests, outcome, record.title if record else '',
                                                      Path(self.services.config.download_directory),
                                                      self.services.config.browser, io=self.io)
                    if exported.limit_exceeded:
                        self.send('message', {'text': 'Soundiiz accepts at most 200 tracks; the playlist file was saved to {0}', 'args': [str(exported.path)]})
                    else:
                        self.send('message', {'text': str(exported.path or '') + (' — Soundiiz import failed' if exported.import_failed else '')})
                if answer.get('manual'):
                    results = await session.finish_manually(list(outcome.manual_candidates), manual, handle.cancel)
                    self.send('message', {'text': '\n'.join(r.track_label + ': ' + r.status for r in results)})
                if not answer.get('retry'):
                    if answer.get('close') or answer.get('links'):
                        break
                    continue
                retry = [replace(request, links=tuple((store, url) for store, url in request.links
                         if (request.track.key, store) in outcome.retryable_targets)) for request in requests]
                outcome = await batch([request for request in retry if request.links])
            await self.refresh_rows()
        await self.job('Adding to store carts', work)

    def browser_status(self):
        """The extension's state as a translatable template and its arguments."""
        link = self.services.browser_link
        connections = link.connections() if link is not None else []
        if not connections:
            return 'Not connected: install the dj-digger extension in your browser', []
        best = max(connections, key=lambda c: (c.compatible, c.last_active))
        if not best.compatible:
            return 'The extension is out of date: reload it in your browser', []
        hello = best.hello
        args = [str(hello.get('browser') or 'browser').capitalize(), str(hello.get('version') or '?')]
        if hello.get('permissions') is False:
            return '{0}, extension {1}: missing site access, allow it in the extension settings', args
        return '{0}, extension {1}: connected', args

    async def connect_browser(self, cancel=None):
        """Explain how to load the extension, then wait for it to say hello after "Check connection"."""
        from ..webext import link, register
        notes, folder = [], ''
        try:
            registration = await self.io(register.register)
            folder, notes = str(registration.extension_dir), registration.notes
        except register.RegistrationError as exc:
            notes = [str(exc)]
        if not folder:
            from ..paths import data_dir
            folder = str(data_dir() / 'browser-extension')
        error = ''
        while True:
            status, args = self.browser_status()
            # Browsers do not let a link open their own extensions page, so its address is there to copy.
            fields = [dict(field('status', 'Status', status, 'note'), args=args),
                      section('section_chrome', 'Chrome, Edge, Brave'),
                      field('chrome_steps', '', '1. Open a new tab and go to the address below.\n'
                            '2. Switch on Developer mode (top right).\n'
                            '3. Click Load unpacked and choose the extension folder below.', 'note'),
                      field('chrome_page', '', 'chrome://extensions', 'copy'),
                      section('section_firefox', 'Firefox'),
                      field('firefox_steps', '', '1. Open a new tab and go to the address below.\n'
                            '2. Click Load Temporary Add-on.\n'
                            '3. Choose manifest.json in the extension folder below.', 'note'),
                      field('firefox_page', '', 'about:debugging#/runtime/this-firefox', 'copy'),
                      field('folder', 'Extension folder', folder, 'path')]
            if notes:
                fields.insert(1, field('notes', 'Notes', '\n'.join(notes), 'note'))
            await self.ask('Connect browser', 'dj-digger controls your own browser through an extension, so it works with '
                           'the accounts you are logged in to. SoundCloud sign-in, gates and carts open in the browser where you install '
                           'the extension - pick the one you are signed in to.', fields, cancel, ok='Check connection',
                           error=error, dismiss='Not now')
            try:
                if self.services.browser_link is None:
                    raise link.ExtensionMissing('No extension channel')
                connection = await self.io(self.services.browser_link.connection, 5)
            except link.ExtensionMissing:
                error = "The extension still isn't answering. Install/enable it, then click Check connection again."
                continue
            browser = self.connected_browser(connection)
            self.send('message', {'text': 'Connected: {0}. SoundCloud sign-in, gates and carts will open there.',
                                  'args': [browser or 'browser']})
            await self.action_account({})
            return

    def connected_browser(self, connection=None):
        """``Chrome 141`` for the given (else the most recent compatible) extension connection; '' for none."""
        if connection is None:
            link = self.services.browser_link
            usable = [c for c in (link.connections() if link is not None else []) if c.compatible]
            connection = max(usable, key=lambda c: c.last_active) if usable else None
        hello = getattr(connection, 'hello', None) or {}
        name = str(hello.get('browser') or '').capitalize()
        return ' '.join(filter(None, [name, str(hello.get('browserVersion') or '').split('.')[0]])) if name else ''

    async def with_extension(self, call, cancel):
        """Browser automation needs the extension; until it says hello, show how to connect and retry."""
        from ..webext.link import ExtensionMissing
        while True:
            try:
                return await call()
            except ExtensionMissing:
                await self.connect_browser(cancel)

    async def action_connect_browser(self, values):
        await self.connect_browser()

    async def skippable(self, step, cancel=None):
        """Await a step the person may skip: True once done, False when they chose Skip (or Cancel)."""
        try:
            await step
            return True
        except Cancelled:
            if self.closing or is_cancelled(cancel):
                raise
            return False

    async def action_welcome(self, values):
        """First start: import the person's own playlists, then the settings that matter. Every step skips."""
        config = self.services.config
        if config.onboarded:
            return
        signed_in = await self.io(lambda: bool(self.services.client.oauth_token))
        fields = [field('url', 'Your SoundCloud profile link', hint='For example https://soundcloud.com/your-name')]
        if signed_in:  # private playlists come only with the owner's own sign-in
            fields.append(field('private', 'Include private playlists', False, 'bool',
                                hint='Only when you are signed in to SoundCloud as this profile'))
        answer = {}
        async def profile():
            answer.update(await self.form(
                'Welcome to dj-digger', 'dj-digger lists the tracks of your SoundCloud playlists with their free downloads '
                'and store links, so you can preview, download and buy them in one place. Start with your own playlists: '
                'paste your profile link and every playlist you created is imported.', fields, profile_answer,
                ok='Import all my playlists', dismiss='Skip'))
        if await self.skippable(profile()):
            # In the background: the next steps do not wait for it; its summary follows when done.
            self._start('profile', answer)
        def basics(answer):
            from ..config import is_real_email
            if answer['user_email'] and not is_real_email(answer['user_email']):
                raise ValueError('Enter a valid email')
            if not answer['download_directory'].strip():
                raise ValueError('Specify a download folder')
            return answer
        async def settings():
            await self.io(self.services.accounts.save_preferences, await self.form(
                'Downloads and gates', 'Where downloads go, and the name and email that gates (free downloads for a '
                'follow, a like or your email) may submit. Everything else is in Settings.',
                [field('download_directory', 'Download folder', config.download_directory, 'folder',
                       hint='Each playlist gets its own folder inside it.'),
                 field('user_name', 'Name', config.user_name),
                 field('user_email', 'Email', config.user_email if config.has_real_email() else '',
                       hint='Some gates ask for one before the download.')], basics, ok='Save and continue', dismiss='Skip'))
        await self.skippable(settings())
        if not self.connected_browser():
            async def browser():
                await self.ask('Connect your browser', 'SoundCloud sign-in, gates and carts need the dj-digger extension in '
                               'your browser. They open in the browser where it is installed, with the accounts you are '
                               'signed in to there.', ok='Connect browser', dismiss='Skip')
                await self.connect_browser()
            await self.skippable(browser())
        config.first_run = False
        await self.io(self.services.accounts.save_preferences, {'onboarded': True})

    async def action_disconnect_browsers(self, values):
        from ..webext import register
        await self.ask('Disconnect browsers', 'Remove dj-digger from the browsers it was registered with. '
                       'The extension stays installed but reports that the app is missing; dj-digger '
                       'registers again the next time it starts.', ok='Disconnect')
        removed = await self.io(register.unregister)
        # A relay already running would keep the menu saying "Connected".
        if self.services.browser_link is not None:
            for connection in self.services.browser_link.connections():
                await self.io(connection.close)
        await self.action_account({})
        self.send('message', {'text': 'Browsers disconnected: {0}', 'args': [len(removed)]})

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


def export_notice(report):
    """The event for a finished export; a partial one names its first failure."""
    args = [report['status'], len(report.get('missing', []))]
    failed = [result for result in report.get('results', []) if result.get('status') == 'failed']
    if not failed:
        return 'message', {'text': 'Export: {0}; missing files: {1}', 'args': args}
    first = failed[0]
    reason = log_safe_text(first.get('error', ''))[:300]
    if len(failed) > 1:
        reason += f' (+{len(failed) - 1})'
    args += [log_safe_text(Path(first['source']).name), reason]
    return ('error' if report['status'] == 'partial' else 'message'), {'text': 'Export: {0}; missing files: {1}; {2}: {3}', 'args': args}


def result_status(result):
    """A cart result's status as the results log shows it: in a store's cart apart from to a playlist."""
    if result.status in ('added', 'already_in_cart'):
        return ('in ' if result.status == 'added' else 'already in ') + (result.store or 'store').capitalize() + ' cart'
    return 'to playlist' if result.status == 'playlist_ready' else result.status


def field(name, label, value='', kind='text', options=(), hint=''):
    """A form row. ``hint`` is a line of plain-language help shown under it. Kinds besides inputs:
    ``note`` / ``path`` / ``copy`` read-only text (the last two with a Copy button), ``section`` a
    header (``collapsed=True`` folds the rows up to the next one), ``buttons`` one footer button
    per option that answers with its value (the dialog then has no OK button)."""
    return dict(name=name, label=label, value=value, kind=kind, options=[list(o) for o in options], hint=hint)


DISPLAY_KINDS = frozenset({'note', 'path', 'copy', 'section'})


def section(name, label, hint='', collapsed=False):
    return dict(field(name, label, kind='section', hint=hint), collapsed=collapsed)


def profile_answer(answer):
    """Form validation: the profile link, normalised (ValueError says what to paste)."""
    from ..services.profile_import import profile_url
    return dict(answer, url=profile_url(answer.get('url')), private=bool(answer.get('private')))


# Why a download failed, in plain words, by the service's failure group; True when only a person can fix it.
FAILURE_REASONS = {
    'auth': ('needs you to sign in on the gate site', True),
    'captcha': ('needs a CAPTCHA solved', True),
    'consent': ('wants a follow, like or comment (switched off in Settings)', True),
    'manual': ('needs a step only you can do', True),
    'rejected': ('the gate refused the download', True),
    'protocol': ('the gate is gone or has changed', True),
    'login': ('needs a SoundCloud sign-in', True),
    'profile': ('needs your name and email (Settings)', True),
    'download': ('the download failed', False),
    'left': ('not started; run it again', False),
}


def failure_reason(group, message):
    """(text, needs a person) for one failed row; the original message follows in brackets."""
    lowered = message.lower()
    if 'no longer exists' in lowered:
        phrase, human = 'the gate is gone', True
    elif 'timed out' in lowered or 'timeout' in lowered:
        phrase, human = 'timed out', False
    else:
        phrase, human = FAILURE_REASONS.get(group, ('', False))
    detail = log_safe_text(message)[:200]
    text = phrase + (f' ({detail})' if detail and detail != phrase else '') if phrase else detail or 'failed'
    return text, human


def profile_summary(report, error=''):
    """The rows of the import result: counts, then each playlist that did not come in and why."""
    reasons = {'locally_changed': 'deleted or changed here during the import; left as it is',
               'incomplete': 'SoundCloud sent it incomplete; the previous copy was kept'}
    imported = sum(item['status'] == 'imported' for item in report)
    skipped = [item for item in report if item['status'] in ('skipped', 'locally_changed')]
    failed = [item for item in report if item['status'] not in ('imported', 'skipped', 'locally_changed')]
    rows = [dict(field('summary', '', 'Imported {0} / {1} playlists', 'note'), args=[imported, len(report)]),
            dict(field('counts', '', 'Skipped: {0}. Failed: {1}.', 'note'), args=[len(skipped), len(failed)])]
    if error:
        rows.append(dict(field('stopped', '', 'The import stopped: {0}', 'note'), args=[log_safe_text(error)]))
    lines = [f"{item.get('title', item.get('id'))}: {verdict}, "
             f"{reasons.get(item['status']) or log_safe_text(item.get('message') or item['status'])}"
             for verdict, items in (('skipped', skipped), ('failed', failed)) for item in items]
    if lines:
        rows.append(field('details', 'Not imported', '\n'.join(lines), 'log'))
    return rows


def soundcloud_link(name):
    def validate(answer):
        value = str(answer.get(name, '')).strip()
        if not value:
            raise ValueError('This field is required')
        if not is_soundcloud_url(value):
            raise ValueError('Paste a link from soundcloud.com')
        return answer
    return validate


def required(name):
    def validate(answer):
        if not str(answer.get(name, '')).strip():
            raise ValueError('This field is required')
        return answer
    return validate
