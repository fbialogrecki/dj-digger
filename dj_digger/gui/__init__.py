"""Optional desktop entry point. Importing this module does not import Qt."""


def main():
    import os
    import sys
    from pathlib import Path

    try:
        from PySide6.QtCore import QLocale, QTimer, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
    except ImportError:
        print('Install the desktop extra: pip install "dj-digger[gui,play,analyze]"', file=sys.stderr)
        return 1
    from ..logging_setup import close_logging, configure_logging
    from .bridge import Bridge

    if getattr(sys, 'frozen', False):
        from ..bundled import resource_root
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(resource_root() / 'browsers')
    # Libraries sometimes write diagnostics directly when pythonw has no streams.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    if '--runtime-test' in sys.argv:
        from .runtime_smoke import run
        run(sys.argv[sys.argv.index('--runtime-test') + 1])
        return 0
    QQuickStyle.setStyle('Basic')
    app = QGuiApplication(sys.argv)
    app.setApplicationName('dj-digger')
    app.setOrganizationName('dj-digger')
    from ..bundled import hold
    install_guard = hold()
    configure_logging('INFO')
    engine = QQmlApplicationEngine()
    bridge = Bridge(engine)
    engine.rootContext().setContextProperty('desktop', bridge)
    engine.rootContext().setContextProperty('systemLanguage', 'pl' if QLocale.system().name().startswith('pl') else 'en')
    bridge.closed.connect(lambda: app.exit(0))
    app.setQuitOnLastWindowClosed(False)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'qml' / 'Main.qml')))
    if not engine.rootObjects():
        bridge.backend.close()
        bridge.backend.thread.join(timeout=5)
        import shiboken6
        shiboken6.delete(engine)
        close_logging()
        if install_guard:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(install_guard))
        return 1
    # Ctrl+C in the terminal: Python only runs its signal handler between
    # bytecodes, and app.exec() is C++, so a timer hands control back every
    # 200 ms and the handler closes the window the same way the title-bar
    # button does - settings saved, workers cancelled, three-second deadline.
    import signal
    window = engine.rootObjects()[0]
    for name in ('SIGINT', 'SIGTERM'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: window.close())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(200)
    # CI smoke uses isolated HOME/XDG and never starts network/media operations.
    if '--smoke-test' in sys.argv:
        QTimer.singleShot(1000, window.close)
    try:
        return app.exec()
    finally:
        bridge.backend.thread.join(timeout=5)
        import shiboken6
        shiboken6.delete(engine)
        close_logging()
        if install_guard:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(install_guard))
