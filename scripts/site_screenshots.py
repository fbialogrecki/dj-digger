"""Screenshots of the real desktop app and extension panel for the project website.

Everything runs offline against an isolated demo profile: a fixed folder under /tmp stands in
for HOME and every XDG/AppData directory, so the user's library, settings, keyring and music
are never read. The track that plays is a real one, "Drift Away" by JMHBM (CC BY 4.0), given
with --track or DJD_SITE_TRACK and never downloaded here; the other tracks and artists are
invented and their audio synthesised (kick, hats, bass and pads, encoded to FLAC with the system
ffmpeg). No track has artwork, so the record shows its plain label.
The desktop is the real QML window with the real backend and player (on miniaudio's silent
null device); only the browser cart and the gate pass are replaced by the fakes the tests use,
since those need a live browser. The panel is the real popup.html with a stubbed chrome.*,
fed the state the backend publishes for it, shot with headless Chromium.

    uv run --extra gui --extra analyze python scripts/site_screenshots.py --track drift-away.mp3 [de|es|fr|it|pl|ru]

With a language, the app runs in it and only the window shots and crops are written, under
screenshots/<language>/; the panels (the extension has no translations) are shared. Without one (English), writes, dark theme throughout:
- site/src/assets/screenshots/*.png: eight whole windows at 2x (1280x800 logical), and the panel
  playing (extension-panel.png) and during the cart job (extension-panel-job.png);
- site/src/assets/screenshots/crops/*.png: parts of those same frames at 2x, cut along the scene
  rectangles of the QML items they show, whole and with a margin (player, collect, stores,
  decisions, cart, gates, library, sidebar, export, connect).
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import wave
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
parser.add_argument('language', nargs='?', default='en', choices=('en', 'de', 'es', 'fr', 'it', 'pl', 'ru'))
parser.add_argument('--track', type=Path, default=os.environ.get('DJD_SITE_TRACK'),
                    help='the MP3 of "Drift Away" by JMHBM (or DJD_SITE_TRACK)')
ARGS = parser.parse_args()
if not ARGS.track or not Path(ARGS.track).is_file():
    raise SystemExit('Give the track with --track PATH or DJD_SITE_TRACK: "Drift Away" by JMHBM, CC BY 4.0, '
                     'https://freemusicarchive.org/music/beat-mekanik/single/drift-away/ (nothing is downloaded here)')
SOURCE_TRACK = Path(ARGS.track).resolve()
LANGUAGE = ARGS.language
OUT = REPO / 'site' / 'src' / 'assets' / 'screenshots'
OUT = OUT if LANGUAGE == 'en' else OUT / LANGUAGE
CROPS = OUT / 'crops'
# A fixed, neutral path: folders shown in the dialogs read /tmp/dj-digger-demo/..., never a real home.
DEMO = Path('/tmp/dj-digger-demo')
HOME = DEMO / 'home'
WIDTH, HEIGHT = 1280, 800
TITLE, ARTIST = 'Drift Away', 'JMHBM'

if (DEMO / '.demo').is_file():
    shutil.rmtree(DEMO)
DEMO.mkdir(parents=True)  # refuses to reuse a folder this script did not make
(DEMO / '.demo').touch()
# A Chrome profile folder, so Connect browser finds a browser to register with (inside the demo HOME).
for name in ('Music', 'Downloads', '.config/google-chrome'):
    (HOME / name).mkdir(parents=True)
os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), LOCALAPPDATA=str(HOME / 'Local'), APPDATA=str(HOME / 'Roaming'),
                  XDG_DATA_HOME=str(HOME / '.local/share'), XDG_CONFIG_HOME=str(HOME / '.config'),
                  XDG_CACHE_HOME=str(HOME / '.cache'), XDG_STATE_HOME=str(HOME / '.local/state'),
                  XDG_RUNTIME_DIR=str(DEMO / 'run'), XDG_MUSIC_DIR=str(HOME / 'Music'), XDG_DOWNLOAD_DIR=str(HOME / 'Downloads'),
                  PYTHON_KEYRING_BACKEND='keyring.backends.fail.Keyring', LANG='en_US.UTF-8', LANGUAGE='en',
                  QT_QPA_PLATFORM=f'offscreen:configfile={DEMO / "screen.json"}', QT_SCALE_FACTOR='2',
                  QT_QUICK_BACKEND='software')
# The window takes at most the screen: one 2x screen exactly the window's size.
(DEMO / 'screen.json').write_text(json.dumps({'screens': [dict(name='demo', x=0, y=0, width=WIDTH * 2, height=HEIGHT * 2, logicalDpi=96)]}))
(DEMO / 'run').mkdir(mode=0o700)
# No session bus and no display: nothing here can reach the desktop's keyring or open the person's browser.
for name in ('SOUNDCLOUD_OAUTH_TOKEN', 'DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'WAYLAND_DISPLAY', 'BROWSER'):
    os.environ.pop(name, None)

import numpy as np  # noqa: E402
import requests  # noqa: E402
from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


def offline(*args, **kwargs):
    raise RuntimeError('site_screenshots.py is offline')


requests.Session.request = offline
webbrowser.get = offline  # dj_digger.browser opens links through webbrowser.get(...)
import miniaudio  # noqa: E402

_device = miniaudio.PlaybackDevice
# The real player and decoder, heard by nobody.
miniaudio.PlaybackDevice = lambda **kw: _device(backends=[miniaudio.Backend.NULL], **kw)

from dj_digger.cart_models import CartBatchOutcome, CartResult  # noqa: E402
from dj_digger.config import AppConfig  # noqa: E402
from dj_digger.crate_models import CrateRecord  # noqa: E402
from dj_digger.gate_models import GateCaptchaRequired  # noqa: E402
from dj_digger.gui.backend import Backend  # noqa: E402
from dj_digger.gui.bridge import Bridge  # noqa: E402
from dj_digger.gui.model import HEADERS  # noqa: E402
from dj_digger.models import Track  # noqa: E402
from dj_digger.services.downloads import FileBatchResult  # noqa: E402
from dj_digger.state import TrackState  # noqa: E402

QQuickStyle.setStyle('Basic')
app = QGuiApplication(sys.argv[:1])
app.setApplicationName('dj-digger')
app.setOrganizationName('dj-digger')

# title, artist, label, genre, BPM, Camelot key, root note (Hz); the first six exist as synthesised local files.
TRACKS = [
    ('Slow Orbit', 'Nightglass', 'Halfmoon Tapes', 'Deep House', 122, '8A', 55.0),
    ('Basalt Steps', 'Coral Engine', 'Porch Light', 'Tech House', 126, '9A', 49.0),
    ('Glasshouse Dub', 'Sable Fern', 'Low Arc', 'Dub Techno', 120, '7A', 58.3),
    ('Night Ferry', 'Tidewell', 'Halfmoon Tapes', 'Deep House', 123, '8B', 65.4),
    ('Copper Rain', 'Ostra Lumen', 'Brass Hour', 'Minimal', 125, '10A', 61.7),
    ('Sodium Lights', 'Pale Circuit', 'Kiln Audio', 'Techno', 130, '5A', 51.9),
    ('Fold the Map', 'Brine Atlas', '', 'Breaks', 0, '', 0),
    ('Low Tide Motel', 'Moth Signal', '', 'Electro', 0, '', 0),
    ('Paper Engines', 'Kiln & Loom', '', 'House', 0, '', 0),
    ('Second Hearth', 'Vesna Radi', '', 'Deep House', 0, '', 0),
    ('Velvet Gradient', 'Hollow Paw', '', 'Downtempo', 0, '', 0),
    ('Harbor Code', 'Ilsa Morrow', '', 'Techno', 0, '', 0),
    ('Fern Signal', 'Okapi Choir', '', 'Afro House', 0, '', 0),
    ('Quiet Voltage', 'Lune Varga', '', 'Minimal', 0, '', 0),
]
MUSIC = HOME / 'Music' / 'Digging 2026'


def synth(path, bpm, root, seconds=200, rate=44100):
    """Kick and hats with a bass line and a pad breakdown: enough shape for a waveform."""
    out = np.zeros(int(seconds * rate), np.float32)
    beat, rng = 60 / bpm, np.random.default_rng(int(bpm * root))
    x = np.arange(int(.35 * rate)) / rate
    kick = np.exp(-x * 9) * np.sin(2 * np.pi * (45 * x + 3 * (1 - np.exp(-x * 30))))
    hat = np.exp(-x[:2000] * 300) * rng.uniform(-1, 1, 2000)
    bass = np.exp(-x[:int(beat * rate / 2)] * 6) * np.sin(2 * np.pi * root * x[:int(beat * rate / 2)])
    bars = int(seconds / beat / 4)
    for b in range(int(seconds / beat)):
        bar, start = b // 4, int(b * beat * rate)
        breakdown = bars * .4 <= bar < bars * .55
        for sound, at, gain, on in ((kick, 0, .9, not breakdown), (hat, .5, .25, True),
                                    (bass, .5, .45, bars * .15 <= bar and not breakdown)):
            i = start + int(at * beat * rate)
            if on and i < len(out):
                piece = sound[:len(out) - i]
                out[i:i + len(piece)] += gain * piece * (.4 if breakdown and sound is hat else 1)
    t = np.arange(len(out)) / rate
    swell = np.clip(np.sin(np.pi * (t / seconds - .38) / .2), 0, None) * ((t / seconds > .38) & (t / seconds < .58))
    out += .18 * swell * sum(np.sin(2 * np.pi * root * 4 * r * t) for r in (1, 1.189, 1.498))
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((out / np.abs(out).max() * .9 * 32767).astype('<i2').tobytes())


def library():
    """The six synthesised FLACs, then the real track with its own two tags, the audio stream copied as is."""
    MUSIC.mkdir(parents=True)
    files = []
    for n, (title, artist, label, genre, bpm, key, root) in enumerate(TRACKS[:6]):
        raw = DEMO / f'{n}.wav'
        synth(raw, bpm, root)
        target = MUSIC / f'{artist} - {title}.flac'
        tags = dict(title=title, artist=artist, genre=genre, date='2026', label=label, bpm=bpm, initialkey=key)
        subprocess.run(['ffmpeg', '-v', 'error', '-i', raw, '-c:a', 'flac',
                        *[a for k, v in tags.items() for a in ('-metadata', f'{k.upper()}={v}')], target], check=True)
        files.append(target)
    target = MUSIC / f'{TITLE}.mp3'  # the window titles a local file by its name
    subprocess.run(['ffmpeg', '-v', 'error', '-i', SOURCE_TRACK, '-map', '0:a', '-c', 'copy', '-map_metadata', '-1',
                    '-metadata', f'title={TITLE}', '-metadata', f'artist={ARTIST}', target], check=True)
    return files + [target]


def seed(files):
    config = AppConfig()
    config.user_name, config.user_email, config.onboarded = 'Demo Digger', 'digger@example.com', True
    config.scan_directories, config.download_directory = [str(HOME / 'Music')], str(HOME / 'Downloads')
    config.save()
    state = TrackState()
    stores = ['https://nightglass.bandcamp.com/track/slow-orbit', 'https://www.beatport.com/track/basalt-steps/90000002',
              'https://hypeddit.com/sablefern/glasshousedub', 'https://www.traxsource.com/track/90000004/night-ferry',
              'https://ostralumen.bandcamp.com/track/copper-rain', 'https://www.beatport.com/track/sodium-lights/90000006',
              'https://hypeddit.com/brineatlas/foldthemap', 'https://www.junodownload.com/products/low-tide-motel/90000008-02/',
              'https://kilnandloom.bandcamp.com/track/paper-engines', 'https://toneden.io/vesnaradi/post/second-hearth',
              'https://music.apple.com/album/velvet-gradient/90000011', 'https://hypeddit.com/ilsamorrow/harborcode',
              'https://www.beatport.com/track/fern-signal/90000013', 'https://lunevarga.bandcamp.com/track/quiet-voltage']
    tracks = []
    for n, (title, artist, label, genre, *_rest) in enumerate(TRACKS):
        tracks.append(Track(title=title, artist=artist, id=90000001 + n, genre=genre, label_name=label, release_year=2026,
                            permalink_url=f'https://soundcloud.com/{artist.lower().replace(" ", "").replace("&", "and")}/{title.lower().replace(" ", "-")}',
                            purchase_url=stores[n], purchase_title='Free download' if 'hypeddit' in stores[n] else 'Buy',
                            duration=(200 if n < 6 else 300 + n * 17) * 1000,
                            local_path=str(files[n]) if n in (0, 1, 3) else None))
    playlists = [('https://soundcloud.com/demo-digger/sets/late-night-selects', 'Late Night Selects', tracks),
                 ('https://soundcloud.com/demo-digger/sets/warehouse-warmups', 'Warehouse Warmups', tracks[5:11]),
                 ('https://soundcloud.com/demo-digger/sets/sunday-balcony', 'Sunday Balcony', tracks[2:6])]
    for source, title, items in playlists:
        state.db.save_crate(CrateRecord(source, title, list(items), imported_at='2026-11-01T20:00:00+00:00').to_json())
    for n in (0, 1, 3):
        state.set_local_file(tracks[n].key, files[n])
    state.set_many([(tracks[n].key, status) for n, status in
                    ((2, 'played'), (4, 'opened'), (6, 'skip'), (8, 'opened'), (10, 'skip'), (11, 'played'))])
    return playlists[0][0], tracks


# QML helpers for crop rectangles, each [left, top, right, bottom] in window coordinates: an item, a table
# column from its header cell down to the last row, and a header cell found by its English title.
GEOMETRY = '''
function rect(i, h) { let r = i.mapToItem(null, 0, 0, i.width, h ?? i.height); return [r.x, r.y, r.x + r.width, r.y + r.height] }
function down(i) { return rect(i, table.mapToItem(i, 0, Math.min(table.height, table.contentHeight)).y) }
function rows(n) { return rect(sidebar, table.mapToItem(sidebar, 0, n * table.rowHeight).y) }
function column(name) { return header.contentItem.children.find(c => c.visible && c.display === name) }
function folders() { try { return foldersScroll } catch (e) { return sidebarScroll } }  // 1.6.0: one scroll for playlists and folders
'''


def wait(condition, what, timeout=30):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise SystemExit(f'Timed out waiting for {what}')
        QTest.qWait(40)


class Cart:
    """The browser cart session, as tests/test_gui.py fakes it: no browser, no store."""
    async def run_batch(self, requests, cancel, *, approve, nyp_price, manual=None):
        results = []
        for n, request in enumerate(requests):
            store, url = request.links[0]
            status, code = (('playlist_ready', 'playlist_ready') if store == 'beatport'
                            else ('already_in_cart', '') if n == 1 else ('added', ''))
            results.append(CartResult(request.track.key, request.track.label, store, status, code=code, url=url))
        return CartBatchOutcome(tuple(results), ('bandcamp',))

    async def focus_carts(self):
        pass

    async def close(self):
        pass


class Client:
    """SoundCloud for the gate download: every gate asks for a person, as tests/test_gui.py fakes it."""
    session = None

    def download_track(self, track, directory, **kwargs):
        raise GateCaptchaRequired('Hypeddit CAPTCHA requires browser completion')

    def close(self):
        pass


class Panel:
    """Receives the state the backend publishes for the extension's player panel."""
    state = None

    def publish_player(self, state):
        Panel.state = state

    def connections(self):
        return []


def desktop(files):
    source, tracks = seed(files)
    (HOME / '.config/dj-digger').mkdir(parents=True, exist_ok=True)
    (HOME / '.config/dj-digger/gui.json').write_text(json.dumps(dict(width=WIDTH, height=HEIGHT, theme='dark', animations=False, language=LANGUAGE)))
    engine = QQmlApplicationEngine()
    questions = []
    bridge = Bridge(engine, backend_factory=lambda emit: Backend(emit, connect_browser=False))
    bridge.question.connect(lambda values: questions.append(values['title']))
    engine.rootContext().setContextProperty('desktop', bridge)
    engine.rootContext().setContextProperty('systemLanguage', LANGUAGE)
    engine.load(QUrl.fromLocalFile(str(REPO / 'dj_digger/gui/qml/Main.qml')))
    window = engine.rootObjects()[0]
    window.show()
    qml = lambda expression: QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()[0]  # noqa: E731
    backend, model = bridge.backend, bridge.table
    wait(lambda: bridge.property('ready') and len(bridge.property('playlists')) == 3, 'the sidebar')
    assert (window.width(), window.height()) == (WIDTH, HEIGHT)

    def box(margin, *rects):
        """The union of QML rectangles in window coordinates (logical px), grown by margin, within the window."""
        # Columns are named in English here and found by the header the language shows.
        rects = [re.sub(r'column\("([^"]+)"\)', lambda m: f'column({json.dumps(model.headerName(HEADERS.index(m[1])))})', r)
                 for r in rects]
        found = qml(f'(() => {{ {GEOMETRY}; return [{", ".join(rects)}] }})()').toVariant()
        left, top = (min(r[k] for r in found) - margin for k in (0, 1))
        right, bottom = (max(r[k] for r in found) + margin for k in (2, 3))
        return max(0, int(left)), max(0, int(top)), min(WIDTH, math.ceil(right)), min(HEIGHT, math.ceil(bottom))

    def shoot(name, settle=700, **crops):
        """The whole window, then each crop: (margin, *rects) cut from the same frame at 2x, so always even."""
        QTest.qWait(settle)
        image = window.grabWindow()
        assert (image.width(), image.height()) == (WIDTH * 2, HEIGHT * 2), (image.width(), image.height())
        image.save(str(OUT / f'{name}.png'))
        print(OUT / f'{name}.png')
        for crop, (margin, *rects) in crops.items():
            left, top, right, bottom = box(margin, *rects)
            image.copy(left * 2, top * 2, (right - left) * 2, (bottom - top) * 2).save(str(CROPS / f'{crop}.png'))
            print(CROPS / f'{crop}.png')

    def select(*titles):
        model.clearSelection()
        for row, value in enumerate(model.visible):
            if value['title'] in titles:
                model.select(row, True)  # Ctrl: add to the selection

    def ask(title, action, *args):
        del questions[:]
        action(*args)
        wait(lambda: bridge.tr(title) in questions, title)  # the bridge translates the question it shows

    def dismiss():
        qml('dialog.reject()')
        wait(lambda: not bridge.property('busy') and not qml('dialog.visible'), 'the job to end', 60)

    def panel(what):
        """The state the backend publishes for the extension's panel right now, through a stand-in link."""
        Panel.state = None
        backend.loop.call_soon_threadsafe(lambda: (setattr(backend.services, 'browser_link', Panel()), backend.publish_browser()))
        wait(lambda: Panel.state and Panel.state['title'] == TITLE and what(Panel.state), 'the panel state')
        backend.loop.call_soon_threadsafe(setattr, backend.services, 'browser_link', None)
        return Panel.state

    def item(parent, name):
        return parent if parent.objectName() == name else next(
            (found for child in parent.childItems() if (found := item(child, name))), None)

    # The real track plays from its folder, past the intro and into the groove, through every shot.
    bridge.openFolder(str(MUSIC), 0)
    # Until a file is probed its artist comes from its name; the player takes the one it has at play.
    wait(lambda: any(r['title'] == TITLE and r.get('artist') == ARTIST for r in model.visible), 'the folder')
    select(TITLE)
    bridge.action('play')
    wait(lambda: bridge.property('waveform') and bridge.property('audio').get('title', '').endswith(TITLE), 'playback')
    bridge.transport('seek', 81)
    wait(lambda: bridge.property('audio').get('position', 0) >= 81, 'the seek')

    bridge.load(source)
    wait(lambda: model.rowCount() == len(tracks), 'the playlist')
    select('Copper Rain')
    shoot('desktop-player', player=(4, 'rect(player)'), collect=(6, 'rect(player)', 'rows(5)', 'rect(table, 5 * table.rowHeight)'))
    playing = panel(lambda state: state['playing'])

    qml('root.toggleStore("bandcamp"); root.toggleStore("beatport")')
    wait(lambda: model.rowCount() < len(tracks), 'the store filter')
    model.clearSelection()
    shoot('desktop-store-links', stores=(8, 'rect(storePills)', 'rect(moreFilters)', 'down(column("Status"))', 'down(column("Stores"))'))
    qml('root.resetFilters(true)')

    select('Sodium Lights')
    # Decisions live in the Status column, the Mark owned / Skip buttons and this filter.
    qml('statusBox.popup.open()')
    shoot('desktop-crates-decisions', decisions=(8, 'rect(statusBox)', 'rect(statusBox.popup.background)', 'rect(column("Status"))'))
    qml('statusBox.popup.close()')

    backend.services._cart = Cart()
    select('Slow Orbit', 'Copper Rain', 'Paper Engines', 'Sodium Lights', 'Quiet Voltage')
    ask('Cart results', bridge.action, 'cart')
    # The cart job is still running while its results wait for the person: the panel shows its card.
    job = panel(lambda state: state['jobs']['busy'])
    shoot('desktop-cart', cart=(24, 'rect(dialog.background)'))
    dismiss()

    backend.services._client = Client()

    def finish_gates(items, directory, cancel, *, config, status=None, attend=None):
        attend({key: 'the gate wants a CAPTCHA solved' for key in (t.key for t, _url in items)})
        return FileBatchResult()

    backend.services.downloads.finish_gates = finish_gates
    select('Glasshouse Dub', 'Fold the Map', 'Harbor Code')
    ask('Gates need you', bridge.action, 'download')
    shoot('desktop-jobs-gates', gates=(24, 'rect(dialog.background)'))
    dismiss()

    item(window.contentItem(), 'root-music').parentItem().setProperty('expanded', True)  # its chevron
    bridge.openFolder(str(MUSIC), 0)
    # The synthesised files carry BPM and key tags; the real track has only its title and artist.
    wait(lambda: model.rowCount() == len(files) and sum(bool(r['bpm']) for r in model.visible) == len(files) - 1, 'the folder')
    select('Pale Circuit - Sodium Lights')
    shoot('desktop-local-library', library=(4, 'down(header)'),
          sidebar=(6, 'rect(sidebar, folders().mapToItem(sidebar, 0, folders().contentHeight + 8).y)'))

    select(*(r['title'] for r in model.visible))
    ask('Export audio', bridge.action, 'export')
    shoot('desktop-export', export=(8, 'rect(dialog.background)'))
    dismiss()

    ask('Connect browser', backend.submit, 'connect_browser', {})
    shoot('desktop-browser-connect', connect=(8, 'rect(dialog.background)'))
    dismiss()

    window.close()
    # Until the window hears 'closed' the bridge's shutdown deadline runs, and it ends the process.
    wait(lambda: not backend.thread.is_alive() and bridge._close_timer.finished.is_set(), 'shutdown', 15)
    import shiboken6
    shiboken6.delete(engine)  # before the bridge goes, as the app does, so no binding reads a dead `desktop`
    return playing, job


def extension(state, name):
    """The real popup at 2x on its own dark background, sized to its content."""
    ext, work = REPO / 'dj_digger/webext/extension', DEMO / 'popup'
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(ext, work)
    stub = ('const djdState = ' + json.dumps(state) + '; djdState.at = Date.now() / 1000;'
            'globalThis.chrome = {runtime: {sendMessage: async () => undefined},'
            ' tabs: {query: async () => [{url: "https://example.com/"}]},'
            ' storage: {session: {get: async () => ({djdPlayer: djdState}), onChanged: {addListener() {}}}}};')
    measure = 'setTimeout(() => document.body.dataset.height = Math.ceil(document.body.getBoundingClientRect().height), 1000);'
    (work / 'stub.js').write_text(stub)
    (work / 'measure.js').write_text(measure)
    html = (ext / 'popup.html').read_text()
    page = work / 'shot.html'
    chromium = shutil.which('chromium-browser') or shutil.which('chromium') or shutil.which('google-chrome')
    flags = [chromium, '--headless', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=2',
             '--disable-background-networking', '--no-first-run',
             '--allow-file-access-from-files', f'--user-data-dir={DEMO / "chromium"}', '--virtual-time-budget=3000']
    page.write_text(html.replace('<script src="popup.js">', '<script src="stub.js"></script><script src="popup.js">')
                    .replace('</body>', '<script src="measure.js"></script></body>'))
    dom = subprocess.run([*flags, '--window-size=320,1000', '--dump-dom', page.as_uri()],
                         check=True, capture_output=True, text=True, timeout=60).stdout
    height = int(re.search(r'data-height="(\d+)"', dom).group(1))
    target = OUT / name
    subprocess.run([*flags, f'--window-size=320,{height}', f'--screenshot={target}', page.as_uri()],
                   check=True, capture_output=True, timeout=60)
    image = QImage(str(target))
    assert (image.width(), image.height()) == (640, height * 2), (image.width(), image.height())
    print(target)


def main():
    CROPS.mkdir(parents=True, exist_ok=True)
    files = library()
    playing, job = desktop(files)
    if LANGUAGE == 'en':
        extension(playing, 'extension-panel.png')
        extension(job, 'extension-panel-job.png')
    shutil.rmtree(DEMO)


if __name__ == '__main__':
    main()
