"""Private, rotating local diagnostics. No telemetry or network logging."""
import faulthandler
import logging
import os
import platform
import stat
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .diagnostics import RedactingFormatter, log_safe_text
from .paths import log_dir


class DiagnosticHandler(RotatingFileHandler):
    capture_faults = False
    last_error = ''

    def handleError(self, record):
        # Logging must not print an unredacted record or overwrite the TUI when
        # its disk fills up. The diagnostics screen exposes this failure.
        self.last_error = log_safe_text(sys.exc_info()[1])

    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                             | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError('Log destination must be a regular file')
            if os.name != 'nt':
                os.fchmod(descriptor, 0o600)
            return os.fdopen(descriptor, 'a', encoding='utf-8')
        except BaseException:
            os.close(descriptor)
            raise

    def format(self, record):
        # A provider response or traceback must not defeat the size limit.
        return super().format(record)[:16000]

    def doRollover(self):
        if self.capture_faults:
            faulthandler.disable()
        try:
            super().doRollover()
        finally:
            if self.capture_faults and self.stream is not None:
                faulthandler.enable(self.stream)

    def close(self):
        if self.capture_faults:
            faulthandler.disable()
            self.capture_faults = False
        super().close()


def close_logging():
    for logger in (logging.getLogger(), logging.getLogger('dj_digger')):
        for handler in list(logger.handlers):
            if isinstance(handler, DiagnosticHandler):
                logger.removeHandler(handler)
                handler.close()


def current_log_path() -> Path | None:
    for logger in (logging.getLogger('dj_digger'), logging.getLogger()):
        for handler in logger.handlers:
            if isinstance(handler, DiagnosticHandler):
                return Path(handler.baseFilename)
    return None


def current_log_error() -> str:
    for logger in (logging.getLogger('dj_digger'), logging.getLogger()):
        for handler in logger.handlers:
            if isinstance(handler, DiagnosticHandler):
                return handler.last_error
    return ''


def open_log_folder(path: Path):
    if sys.platform == 'win32':
        os.startfile(str(path.parent))
    else:
        command = 'open' if sys.platform == 'darwin' else 'xdg-open'
        subprocess.run([command, str(path.parent)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)


def read_log_tail(path: Path) -> str:
    with path.open('rb') as source:
        source.seek(0, os.SEEK_END)
        source.seek(max(0, source.tell() - 65536))
        return source.read(65536).decode('utf-8', errors='replace')


def configure_logging(level_name: str, log_path: str | None = None) -> Path | None:
    close_logging()
    destination = Path(log_path).expanduser() if log_path else log_dir() / 'dj-digger.log'
    ours, root = logging.getLogger('dj_digger'), logging.getLogger()
    level = getattr(logging, level_name.upper(), logging.INFO)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handler = DiagnosticHandler(destination, maxBytes=2 * 1024 * 1024, backupCount=4, encoding='utf-8')
    except OSError as exc:
        # Logging failure cannot make a readable music library unusable.
        fallback = logging.StreamHandler()
        fallback.setFormatter(RedactingFormatter('%(levelname)s: %(message)s'))
        fallback.emit(logging.LogRecord('dj_digger', logging.WARNING, '', 0,
                      'File logging unavailable: %s', (str(exc),), None))
        return None
    handler.setFormatter(RedactingFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    faulthandler.enable(handler.stream)
    handler.capture_faults = True
    ours.setLevel(level)
    ours.propagate = level == logging.DEBUG
    if level == logging.DEBUG:
        root.setLevel(level)
        root.addHandler(handler)
    else:
        ours.addHandler(handler)
    if not root.handlers:
        root.addHandler(logging.NullHandler())
    ours.info('Starting dj-digger %s; Python %s; %s', __version__, platform.python_version(), platform.system())
    return destination.absolute()
