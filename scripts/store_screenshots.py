"""Chrome Web Store screenshots (1280x800) and small promo tile (440x280) of the dj-digger extension.

The real popup.html/welcome.html with a stubbed chrome.* API, framed on a branded background,
shot with headless Chromium (PySide6 draws the sample artwork). Refresh them when the panel changes:

    uv run --extra gui python scripts/store_screenshots.py dj_digger/webext/extension docs/store/screenshots
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QByteArray, QPointF  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QRadialGradient  # noqa: E402

ext, out = Path(sys.argv[1]), Path(sys.argv[2])
work = Path(tempfile.mkdtemp(prefix='store-shots-'))
for name in ('popup.css', 'popup.js', 'welcome.js'):
    shutil.copy(ext / name, work / name)
shutil.copytree(ext / 'icons', work / 'icons')
out.mkdir(parents=True, exist_ok=True)

app = QGuiApplication([])
image = QImage(400, 400, QImage.Format_RGB32)
p = QPainter(image)
g = QRadialGradient(QPointF(120, 110), 380)
g.setColorAt(0, QColor('#ffd166'))
g.setColorAt(.45, QColor('#ef476f'))
g.setColorAt(1, QColor('#26547c'))
p.fillRect(image.rect(), g)
p.setPen(QColor(255, 255, 255, 200))
for i in range(0, 400, 22):
    p.drawLine(0, i, 400, 400 - i)
p.end()
encoded = QByteArray()
buffer = QBuffer(encoded)
buffer.open(QBuffer.WriteOnly)
image.save(buffer, 'JPEG', 80)
art = 'data:image/jpeg;base64,' + base64.b64encode(bytes(encoded)).decode()

pairs = {True: ['#b8286a', '#3a5fd6', '#ff2fa8', '#1e9bff'], False: ['#ff2187', '#4d77ff', '#ff0094', '#42b1ff']}
idle = dict(busy=False, label='', done=0, total=0)


def track(dark, **extra):
    base = dict(title='Midnight Groove (Extended Mix)', artist='DJ Example', playing=True, position=83.0, duration=312.0,
                seed=926833366, artwork=art, colors=pairs[dark], dark=dark, jobs=idle, needs=0)
    return dict(base, **extra)


def popup_page(name, state, url, kind):
    stub = ('const djdState = ' + json.dumps(state) + '; djdState.at = Date.now() / 1000;'
            'globalThis.chrome = {runtime: {sendMessage: async (m) => (m.op === "page" ? {kind: ' + json.dumps(kind) + '} : undefined)},'
            ' tabs: {query: async () => [{url: ' + json.dumps(url) + '}]},'
            ' storage: {session: {get: async () => ({djdPlayer: djdState}), onChanged: {addListener() {}}}}};')
    (work / f'{name}.js').write_text(stub)
    html = (ext / 'popup.html').read_text()
    (work / f'{name}.html').write_text(html.replace('<script src="popup.js">', f'<script src="{name}.js"></script><script src="popup.js">'))
    return f'{name}.html'


def welcome_page():
    stub = ('globalThis.chrome = {runtime: {getManifest: () => ({host_permissions: []})},'
            ' permissions: {contains: async () => true, onAdded: {addListener() {}}, onRemoved: {addListener() {}}},'
            ' storage: {session: {get: async () => ({djdStatus: {connected: true}}), onChanged: {addListener() {}}}}};')
    (work / 'welcome-stub.js').write_text(stub)
    html = (ext / 'welcome.html').read_text()
    (work / 'welcome-shot.html').write_text(html.replace('<script src="welcome.js">', '<script src="welcome-stub.js"></script><script src="welcome.js">'))
    return 'welcome-shot.html'


FRAME = """<!doctype html><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;font-family:system-ui,sans-serif;color:#fff}}
body{{background:radial-gradient(circle at 18% 22%,#ff2fa855,transparent 55%),radial-gradient(circle at 85% 80%,#1e9bff55,transparent 55%),#141418;
display:flex;align-items:center;justify-content:center;gap:{gap}px}}
.copy{{max-width:{copyw}px}} h1{{font-size:{h1}px;line-height:1.15;margin:0 0 18px}} p{{font-size:{ps}px;line-height:1.45;margin:0;color:#c9ccd3}}
.brand{{display:flex;align-items:center;gap:12px;font-size:{bs}px;font-weight:700;margin-bottom:24px}}
iframe{{border:0;border-radius:14px;box-shadow:0 24px 60px rgb(0 0 0/.55);transform:scale({scale});transform-origin:center;background:#fff}}
</style><div class="copy"><div class="brand"><img src="icons/icon-128.png" width="{icon}" height="{icon}">dj-digger</div>
<h1>{title}</h1><p>{text}</p></div>{frame}"""


def shoot(name, html, w, h):
    (work / f'{name}-frame.html').write_text(html)
    chromium = shutil.which('chromium-browser') or shutil.which('chromium')
    subprocess.run([chromium, '--headless', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1',
                    '--allow-file-access-from-files', f'--window-size={w},{h}', '--virtual-time-budget=2000',
                    f'--screenshot={out / (name + ".png")}', (work / f'{name}-frame.html').as_uri()],
                   check=True, capture_output=True, timeout=60)
    print(out / (name + '.png'))


def shot(name, title, text, src, fw, fh, scale):
    frame = f'<iframe src="{src}" width="{fw}" height="{fh}" style="margin:0 {int(fw * (scale - 1) / 2)}px"></iframe>'
    shoot(name, FRAME.format(w=1280, h=800, gap=90, copyw=420, h1=44, ps=20, bs=26, icon=48, scale=scale,
                             title=title.replace('-', '\u2011'), text=text, frame=frame), 1280, 800)


shot('1-player', 'Control the desktop player from your toolbar',
     'Play, pause, skip and seek the track playing in the dj-digger desktop app without leaving the browser.',
     popup_page('player', track(True), 'https://example.com/', None), 320, 560, 1.25)
shot('2-import', 'Import a SoundCloud playlist in one click',
     'Open the panel on a SoundCloud playlist or profile and send it to dj-digger for digging.',
     popup_page('import', track(False), 'https://soundcloud.com/some-dj/sets/deep-cuts', 'set'), 320, 540, 1.3)
shot('3-jobs-gates', 'See jobs and gates waiting for you',
     'Follow import and download progress, cancel a job, and decide what to do with download gates that need you.',
     popup_page('gates', track(True, jobs=dict(busy=True, label='Importing playlists', done=7, total=20), needs=3),
                'https://example.com/', None), 320, 620, 1.15)
shot('4-welcome', 'Connects to the dj-digger desktop app',
     'The extension works only together with the free dj-digger desktop app. The status page shows the link to the app and site access.',
     welcome_page(), 560, 560, 1.0)
shoot('promo-small-440x280', FRAME.format(w=440, h=280, gap=0, copyw=380, h1=24, ps=14, bs=22, icon=40, scale=1,
                                          title='Crate digging, wired into your browser',
                                          text='Companion extension for the dj-digger desktop app.', frame=''), 440, 280)
