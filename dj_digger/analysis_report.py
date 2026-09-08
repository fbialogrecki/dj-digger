"""Last analysis report: private, atomically replaced, streamed one file at a time."""
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone

from . import paths
from .diagnostics import log_safe_text

REASONS = {
    'no_tonal_evidence': 'Too little tonal information',
    'weak_key_match': 'No strong match to a major or minor key',
    'ambiguous_key': 'Several keys have similar scores',
    'conflicting_sections': 'Different sections suggest different keys',
    'insufficient_audio': 'Too little audio for tempo estimation',
    'no_reliable_pulse': 'No reliable rhythmic pulse',
}


def report_path():
    return paths.log_dir() / 'last-analysis.jsonl'


class AnalysisReport:
    def __init__(self):
        self.counts = Counter(keys=0, no_key=0, errors=0)
        self.path = report_path()
        self.published = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                               prefix='.analysis-', delete=False)
        try:
            self._write({'type': 'header', 'started': datetime.now(timezone.utc).isoformat()})
        except BaseException:
            self.file.close()
            os.unlink(self.file.name)
            raise
        return self

    def _write(self, value):
        self.file.write(json.dumps(value, ensure_ascii=True) + '\n')

    def record(self, path, result=None, error=None):
        result = result or {}
        status = 'errors' if error is not None else 'keys' if result.get('key') else 'no_key'
        self.counts[status] += 1
        reasons = []
        for field, label in (('key', 'Key'), ('bpm', 'BPM')):
            if not result.get(field):
                reason = REASONS.get(result.get(field + '_reason'), 'Not determined')
                reasons.append(f'{label}: {reason}')
        self._write({'type': 'track', 'file': log_safe_text(path), 'status': status,
                     'key': result.get('key', ''), 'bpm': result.get('bpm'),
                     'reason': log_safe_text(error) if error is not None else '; '.join(reasons)})

    def __exit__(self, kind, error, traceback):
        from .models import Cancelled
        status = 'cancelled' if isinstance(error, Cancelled) else 'failed' if error else 'complete'
        try:
            self._write({'type': 'summary', 'status': status, **self.counts})
            self.file.flush()
            os.fsync(self.file.fileno())
            self.file.close()
            os.replace(self.file.name, self.path)
            self.published = True
        finally:
            self.file.close()
            from pathlib import Path
            Path(self.file.name).unlink(missing_ok=True)

