import logging

from dj_digger.diagnostics import RedactingFormatter, log_safe_text


def test_diagnostics_remove_signed_urls_and_authorization_values():
    message = 'GET https://user:pass@cdn.example/file.wav?signature=private Authorization: OAuth abc123 cookie=session-value'
    safe = log_safe_text(message)
    assert safe == 'GET https://cdn.example/file.wav Authorization=<redacted> cookie=<redacted>'
    record = logging.LogRecord('test', logging.ERROR, __file__, 1, '%s', (message,), None)
    assert RedactingFormatter('%(message)s').format(record) == safe
