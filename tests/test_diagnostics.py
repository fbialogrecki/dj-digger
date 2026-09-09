import json
import logging
import os

import pytest

from dj_digger.analysis_report import AnalysisReport, report_path
from dj_digger.diagnostics import RedactingFormatter, log_safe_text
from dj_digger.logging_setup import DiagnosticHandler, configure_logging, current_log_path
from dj_digger.models import Cancelled
from dj_digger.paths import log_dir


def test_diagnostics_remove_signed_urls_and_authorization_values():
    message = 'GET https://user:pass@cdn.example/file.wav?signature=private Authorization: OAuth abc123 cookie=session-value'
    safe = log_safe_text(message)
    # Authentication headers consume the rest of their line, including cookies.
    assert safe == 'GET https://cdn.example/file.wav Authorization=<redacted>'
    record = logging.LogRecord('test', logging.ERROR, __file__, 1, '%s', (message,), None)
    assert RedactingFormatter('%(message)s').format(record) == safe


def test_rotation_is_bounded_private_and_rebinds_native_fault_output(tmp_path, monkeypatch):
    bound = []
    monkeypatch.setattr('dj_digger.logging_setup.faulthandler.enable', bound.append)
    monkeypatch.setattr('dj_digger.logging_setup.faulthandler.disable', lambda: None)
    path = configure_logging('INFO', str(tmp_path / 'app.log'))
    logger = logging.getLogger('dj_digger')
    handler = next(h for h in logger.handlers if isinstance(h, DiagnosticHandler))
    handler.maxBytes = 300
    for index in range(40):
        logger.info('Event %s: %s', index, 'x' * 70)
    files = list(tmp_path.glob('app.log*'))
    assert len(files) == 5
    assert all(p.stat().st_size <= 300 for p in files)
    assert bound[-1] is handler.stream
    assert len(bound) > 1
    if os.name != 'nt':
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    assert 'Event 39' in path.read_text(encoding='utf-8')


def test_logs_redact_headers_structured_secrets_and_tracebacks(tmp_path):
    path = configure_logging('DEBUG', str(tmp_path / 'private.log'))
    logger = logging.getLogger('dj_digger')
    logger.info("payload=%s", {'access_token': 'secret-token', 'password': 'secret phrase'})
    try:
        raise ValueError('Cookie: first=secret-cookie; second=another-secret')
    except ValueError:
        logger.exception('Analysis failed')
    content = path.read_text(encoding='utf-8')
    for secret in ('secret-token', 'secret phrase', 'secret-cookie', 'another-secret'):
        assert secret not in content
    assert 'Traceback' in content and 'ValueError' in content
    assert '<redacted>' in content


def test_logging_setup_failure_does_not_prevent_startup(tmp_path, capsys):
    parent = tmp_path / 'not-a-directory'
    parent.write_text('keep me')
    assert configure_logging('INFO', str(parent / 'app.log')) is None
    assert current_log_path() is None
    assert 'File logging unavailable' in capsys.readouterr().err
    assert parent.read_text() == 'keep me'


def test_logging_reconfiguration_closes_old_handler_without_duplicates(tmp_path):
    path = tmp_path / 'app.log'
    configure_logging('DEBUG', str(path))
    root = logging.getLogger()
    old = next(h for h in root.handlers if isinstance(h, DiagnosticHandler))
    configure_logging('INFO', str(path))
    logging.getLogger('dj_digger').info('unique-event')
    assert old.stream is None
    assert path.read_text(encoding='utf-8').count('unique-event') == 1


def read_report():
    entries = [json.loads(line) for line in report_path().read_text(encoding="utf-8").splitlines()]
    return [item for item in entries if item["type"] == "track"], entries[-1]


def test_report_distinguishes_results_without_losing_errors():
    with AnalysisReport() as report:
        for index in range(101):
            report.record(f'{index}.wav', {'key': 'Am', 'bpm': 128})
        report.record('ambiguous.wav', {'key': '', 'bpm': 120, 'key_reason': 'ambiguous_key'})
        report.record('broken.wav', error=ValueError('password=private-value'))
    rows, summary = read_report()
    assert len(rows) == 103
    assert (summary['keys'], summary['no_key'], summary['errors']) == (101, 1, 1)
    assert 'similar scores' in rows[-2]['reason']
    assert rows[-1]['status'] == 'errors'
    assert 'private-value' not in report_path().read_text(encoding='utf-8')
    if os.name != 'nt':
        assert report_path().stat().st_mode & 0o777 == 0o600


def test_cancelled_report_keeps_completed_rows_and_next_run_replaces_it():
    with pytest.raises(Cancelled):
        with AnalysisReport() as report:
            report.record('done.wav', {'key': 'C', 'bpm': 120})
            raise Cancelled()
    rows, summary = read_report()
    assert summary['status'] == 'cancelled' and rows[0]['file'] == 'done.wav'
    with AnalysisReport() as report:
        report.record('new.wav', {'key': '', 'key_reason': 'no_tonal_evidence'})
    rows, summary = read_report()
    assert len(rows) == 1 and rows[0]['file'] == 'new.wav'
    assert summary['status'] == 'complete'
    assert not list(report_path().parent.glob('.analysis-*'))


@pytest.mark.parametrize('platform,suffix', [
    ('linux', 'state/dj-digger'), ('win32', 'appdata/dj-digger/Logs'), ('darwin', 'Library/Logs/dj-digger'),
])
def test_log_directories_follow_the_platform(tmp_path, monkeypatch, platform, suffix):
    from pathlib import Path
    monkeypatch.setattr('dj_digger.paths.sys.platform', platform)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    assert log_dir() == tmp_path / suffix


def test_failed_report_publication_keeps_previous_report(monkeypatch):
    with AnalysisReport() as report:
        report.record('previous.wav', {'key': 'Am'})
    before = report_path().read_bytes()
    def denied(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr('dj_digger.analysis_report.os.replace', denied)
    with pytest.raises(OSError):
        with AnalysisReport() as report:
            report.record('new.wav', {'key': 'C'})
    assert not report.published
    assert report_path().read_bytes() == before
    assert not list(report_path().parent.glob('.analysis-*'))
