"""Credential-safe diagnostics shared by integrations, CLI and presentation."""

import logging
import re

from .links import redact_url

LOG_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


LOG_SECRET = re.compile(
    r"\b([a-z0-9_-]*(?:token|password|authorization|cookie|session)[a-z0-9_-]*)"
    r"\s*[:=]\s*(?:(?:OAuth|Bearer)\s+)?[^\s,;]+",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    text = LOG_URL.sub(lambda match: redact_url(match.group(0)), text)
    return LOG_SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)


def log_safe_text(value: object) -> str:
    return redact_text(" ".join(str(value).split()))[:1000]


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact_text(super().format(record))
