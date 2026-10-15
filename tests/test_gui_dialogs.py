"""Welcome onboarding, profile import results and the generic dialogs' wording (offline)."""
# ruff: noqa: F811 - the backend fixtures come from test_gui and are taken as arguments by name.
import json
import threading

import pytest

from dj_digger.db import Database
from dj_digger.services.profile_import import import_profile, profile_url
from dj_digger.soundcloud import API_ROOT
from dj_digger.soundcloud_errors import SoundCloudError

pytest.importorskip('PySide6')
from test_gui import (  # noqa: E402,F401
    FakeLink,
    backend,
    extension,
    next_of,
    online_rows,
    run_in,
    wait_event,
)

from dj_digger.gui.backend import failure_reason, profile_summary  # noqa: E402


@pytest.mark.parametrize('value', [
    'https://soundcloud.com/digger', 'soundcloud.com/digger', 'http://www.soundcloud.com/digger/',
    'm.soundcloud.com/digger/sets', 'https://soundcloud.com/digger/sets/one?si=abc#x', '  SOUNDCLOUD.COM/digger  ',
])
def test_profile_links_are_normalised(value):
    assert profile_url(value) == 'https://soundcloud.com/digger'


@pytest.mark.parametrize('value', [
    '', 'digger', 'https://soundcloud.com/', 'https://evil.example/soundcloud.com/digger',
    'https://soundcloud.com.evil.example/digger', 'https://on.soundcloud.com/AbCd', 'https://api.soundcloud.com/digger',
    'javascript:alert(1)', 'https://user@soundcloud.com.evil/x', 'https://soundcloud.com/%2e%2e', 'ftp://soundcloud.com/digger',
])
def test_other_links_are_refused(value):
    with pytest.raises(ValueError):
        profile_url(value)


class Profile:
    """Two pages of sets: one by the profile, one by someone else, one private, one SoundCloud cannot serve."""
    oauth_token = ''

    def resolve(self, url):
        assert url == 'https://soundcloud.com/owner'
        return dict(id=5, kind='user')

    def _request(self, url, params):
        if 'cursor' not in url:
            return {'collection': [dict(id=10, user_id=5, title='Mine'), dict(id=11, user_id=6, title='Liked')],
                    'next_href': f'{API_ROOT}/users/5/playlists?cursor=2'}
        return {'collection': [dict(id=12, user_id=5, title='Secret', sharing='private'), dict(id=13, user_id=5, title='Broken')],
                'next_href': None}

    def _get(self, path, **params):
        if path == '/playlists/13':
            raise SoundCloudError('SoundCloud returned HTTP 500')
        if path == '/tracks':
            return [dict(id=1, title='One', permalink_url='https://soundcloud.com/owner/one')]
        return dict(id=10, title='Mine', track_count=1, tracks=[{'id': 1}], permalink_url='https://soundcloud.com/owner/sets/mine')


def test_profile_import_reports_every_set_with_progress(tmp_path):
    db = Database(tmp_path / 'test.db')
    seen = []
    try:
        report = import_profile(Profile(), db, 'soundcloud.com/owner/sets', progress=lambda done, total: seen.append((done, total)))
    finally:
        db.close()
    assert [(item['title'], item['status']) for item in report] == [
        ('Mine', 'imported'), ('Liked', 'skipped'), ('Secret', 'skipped'), ('Broken', 'failed')]
    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)], 'all pages are read before the first save'
    rows = profile_summary(report)
    assert rows[0]['value'].format(*rows[0]['args']) == 'Imported 1 / 4 playlists'
    assert rows[1]['value'].format(*rows[1]['args']) == 'Skipped: 2. Failed: 1.'
    assert rows[-1]['value'].splitlines() == ['Liked: skipped, created by someone else', 'Secret: skipped, private playlist',
                                              'Broken: failed, SoundCloud returned HTTP 500']


@pytest.mark.parametrize('group, message, text, human', [
    ('captcha', 'Hypeddit CAPTCHA requires browser completion', 'needs a CAPTCHA solved (Hypeddit CAPTCHA requires browser completion)', True),
    ('download', 'the gate page no longer exists', 'the gate is gone (the gate page no longer exists)', True),
    ('download', 'Read timed out', 'timed out (Read timed out)', False),
    ('other', 'something odd', 'something odd', False),
    ('left', '', 'not started; run it again', False),
    ('login', '', 'needs a SoundCloud sign-in', True),
])
def test_download_failures_read_as_plain_reasons(group, message, text, human):
    assert failure_reason(group, message) == (text, human)


def answer_titles(events, worker, answers, until):
    """Answer each question by title until `until` returns True; return the titles in order."""
    import queue
    import time
    asked, deadline = [], time.monotonic() + 10
    while not until():
        assert time.monotonic() < deadline, asked
        try:
            kind, value = events.get(timeout=.1)
        except queue.Empty:
            continue
        if kind == 'error':
            pytest.fail(value['text'])
        if kind == 'question':
            asked.append(value['title'])
            worker.answer(value['id'], answers[value['title']](value) if callable(answers[value['title']]) else answers[value['title']])
    return asked


def test_welcome_imports_the_profile_in_the_background_then_asks_the_basics(extension, tmp_path, monkeypatch):
    from dj_digger.services import profile_import
    worker, events, *_ = extension
    worker.services.config.first_run = True
    assert not worker.services.config.onboarded
    imported = []

    def fake_import(client, db, url, *, private, cancel, current, progress):
        imported.append((url, private))
        progress(1, 2)
        progress(2, 2)
        return [dict(id=1, title='A', status='imported'), dict(id=2, title='B', status='imported')]

    monkeypatch.setattr(profile_import, 'import_profile', fake_import)
    summary = []
    answers = {
        'Welcome to dj-digger': lambda q: {'url': 'm.soundcloud.com/digger/sets'},
        'Downloads and gates': lambda q: {'download_directory': str(tmp_path), 'user_name': 'Digger', 'user_email': 'dj@example.com'},
        'Connect your browser': None,
        'Profile import': lambda q: summary.append(q) or {},
    }
    worker.submit('welcome', {})
    asked = answer_titles(events, worker, answers, lambda: summary and worker.services.config.onboarded)
    assert imported == [('https://soundcloud.com/digger', False)]
    assert asked.count('Welcome to dj-digger') == 1 and 'Connect your browser' in asked
    fields = {f['name']: f for f in summary[0]['fields']}
    assert fields['summary']['value'].format(*fields['summary']['args']) == 'Imported 2 / 2 playlists'
    assert fields['report']['kind'] == 'path'
    assert json.loads((tmp_path / 'config.json').read_text())['onboarded'] is True
    assert worker.services.config.user_email == 'dj@example.com' and not worker.services.config.first_run


def test_welcome_skipped_everywhere_is_not_shown_again(extension, tmp_path):
    worker, events, *_ = extension
    worker.submit('welcome', {})
    asked = answer_titles(events, worker, {'Welcome to dj-digger': None, 'Downloads and gates': None, 'Connect your browser': None},
                          lambda: worker.services.config.onboarded)
    assert asked == ['Welcome to dj-digger', 'Downloads and gates', 'Connect your browser']
    welcome = []
    worker.submit('welcome', {})
    worker.submit('logs', {})  # a later action; Welcome would have asked before it
    while (event := events.get(timeout=10))[0] != 'question':
        pass
    welcome.append(event[1]['title'])
    assert welcome == ['Diagnostics']


def test_welcome_rejects_a_link_that_is_not_a_profile(extension):
    worker, events, *_ = extension
    worker.submit('welcome', {})
    question = wait_event(events, 'question')
    assert question['ok'] == 'Import all my playlists' and question['cancel'] == 'Skip'
    assert [f['name'] for f in question['fields']] == ['url'], 'private sets need a sign-in, so no checkbox'
    worker.answer(question['id'], {'url': 'https://evil.example/digger'})
    retry = wait_event(events, 'question')
    assert retry['error'] == 'Paste a profile link like https://soundcloud.com/your-name'
    assert retry['fields'][0]['value'] == 'https://evil.example/digger'


def test_ready_asks_for_welcome_only_until_done(tmp_path, monkeypatch):
    import queue

    from dj_digger.config import AppConfig
    from dj_digger.gui.backend import Backend
    from dj_digger.services.runtime import ApplicationServices
    from dj_digger.state import TrackState
    for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        monkeypatch.setenv(name, str(tmp_path / name))
    flags = []
    for onboarded in (False, True):
        config = AppConfig(tmp_path / 'config.json')
        config.onboarded = onboarded
        events = queue.Queue()
        worker = Backend(lambda kind, value: events.put((kind, value)),
                         lambda: ApplicationServices(state=TrackState(tmp_path / 'test.sqlite'), config=config))
        worker.submit('start')
        flags.append(wait_event(events, 'ready')['welcome'])
        worker.close()
        worker.thread.join(timeout=10)
    assert flags == [True, False]


def test_connect_browser_says_so_while_the_extension_is_missing_then_names_the_browser(extension):
    from dj_digger.webext.link import ExtensionMissing
    worker, events, *_ = extension
    hello = dict(browser='chrome', browserVersion='141.0.7390.54', version='0.3.1', protocol=1)
    tries = []

    class Link(FakeLink):
        def connection(self, timeout):
            tries.append(timeout)
            if len(tries) == 1:
                raise ExtensionMissing('nobody said hello')
            return type('Connection', (), dict(hello=hello))()

    worker.services.browser_link = Link()
    worker.submit('connect_browser', {})
    first = wait_event(events, 'question')
    assert first['error'] == '' and first['cancel'] == 'Not now'
    worker.answer(first['id'], {})
    second = wait_event(events, 'question')
    assert second['error'] == "The extension still isn't answering. Install/enable it, then click Check connection again."
    worker.answer(second['id'], {})
    message = next_of(events, 'message', 'error')[1]
    assert message['text'].format(*message['args']) == 'Connected: Chrome 141. SoundCloud sign-in, gates and carts will open there.'


def test_connect_browser_opens_the_chrome_web_store_and_asks_again(extension, monkeypatch):
    from dj_digger import browser
    from dj_digger.webext.register import CHROME_STORE_URL
    worker, events, *_ = extension
    worker.services.browser_link = FakeLink(dict(browser='edge', browserVersion='141.0', version='0.3.1', protocol=1))
    opened = []
    monkeypatch.setattr(browser, 'open_url', lambda url, choice: opened.append(url) or True)
    worker.submit('connect_browser', {})
    first = wait_event(events, 'question')
    fields = {f['name']: f for f in first['fields']}
    assert fields['chrome_page']['value'] == 'chrome://extensions' and fields['section_developer']['collapsed'] is True
    assert fields['next']['options'][0] == ['store', 'Open Chrome Web Store']
    worker.answer(first['id'], {'next': 'store'})
    second = wait_event(events, 'question')
    assert opened == [CHROME_STORE_URL] == ['https://chromewebstore.google.com/detail/dj-digger/ndimmdooibgcajgnoncdnckolhlmnnkb']
    assert second['title'] == 'Connect browser' and second['error'] == ''
    worker.answer(second['id'], {'next': 'check'})
    assert next_of(events, 'message', 'error')[1]['text'].startswith('Connected: ')


def test_signing_in_names_the_account_and_the_browser(extension):
    from dj_digger.services.accounts import AuthenticationResult
    worker, events, *_ = extension
    worker.services.browser_link = FakeLink(dict(browser='firefox', browserVersion='131.0', version='0.3.1', protocol=1))
    worker.services.accounts.authenticate = lambda *args: AuthenticationResult(token='secret', username='digger')
    worker.services.adopt_login = lambda token: True
    worker.submit('login', {})
    message = next_of(events, 'message', 'error')[1]
    assert message['text'].format(*message['args']) == 'Signed in as digger in Firefox 131'


def test_settings_keep_a_browser_this_computer_lacks_and_fold_the_folder_lists(backend, tmp_path):
    worker, events = backend
    worker.services.config.browser = 'netsurf'
    worker.submit('settings', {})
    question = wait_event(events, 'question')
    fields = {f['name']: f for f in question['fields']}
    assert fields['browser']['value'] == 'netsurf'
    assert fields['browser']['options'][-1] == ['netsurf', '{0} (not available on this computer)', 'netsurf']
    assert fields['section_folders']['collapsed'] is True and fields['gate_social_actions']['hint']
    names = [f['name'] for f in question['fields']]
    assert names.index('section_folders') < names.index('scan_directories')
    answer = {f['name']: f['value'] for f in question['fields']}
    worker.answer(question['id'], dict(answer, download_directory=str(tmp_path)))
    wait_event(events, 'sidebar')
    saved = json.loads((tmp_path / 'config.json').read_text())
    assert saved['browser'] == 'netsurf' and not any(name.startswith('section_') for name in saved)


def test_skipping_first_run_settings_still_downloads(backend, tmp_path):
    worker, events = backend
    worker.rows = online_rows(1)
    worker.services.config.download_directory = str(tmp_path)
    worker.services.config.first_run = True
    [row] = worker.rows
    row.track.downloadable = row.track.has_downloads_left = True

    class Client:
        def download_track(self, track, directory, **kwargs):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'one.wav').write_bytes(b'audio')
            return directory / 'one.wav'

        def close(self):
            pass

    worker.services._client = Client()
    worker.submit('download', {'keys': [row.track.key], 'generation': worker.generation})
    question = wait_event(events, 'question')
    assert question['title'] == 'Settings'
    worker.answer(question['id'], None)
    message = next_of(events, 'message', 'error')[1]
    assert message['text'] == 'Downloading to {0}; change the folder in Settings'
    wait_event(events, 'rows')
    assert worker.services.state.get(row.track.key) == 'got' and not worker.services.config.first_run


def test_download_results_open_first_when_a_person_must_act(backend):
    worker, events = backend
    [row] = online_rows(1)
    future = run_in(worker, worker.download_results([(row.track, 'https://hypeddit.com/x')],
                                                    {row.track.key: ('captcha', 'CAPTCHA')}, set(), False, threading.Event()))
    question = wait_event(events, 'question')
    choice = next(f for f in question['fields'] if f['kind'] == 'buttons')
    assert choice['value'] == 'open' and [o[0] for o in choice['options']] == ['open', 'retry']
    assert question['cancel'] == 'Close' and 'needs a CAPTCHA solved' in question['fields'][0]['value']
    worker.answer(question['id'], None)
    assert future.result(timeout=10) == [], 'Close ends the download quietly'
