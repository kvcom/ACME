"""Regex-based PII redaction stub.

Production extension to Microsoft Presidio is captured in DECISION_LOG.md.
The trace viewer renders user_query_redacted; the raw query is kept in the DB
behind an admin reveal toggle for audit.
"""
import re

EMAIL_RE = re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b')
# Card check must run before phone, otherwise a 16-digit card matches phone.
CARD_RE = re.compile(r'\b\d{16}\b')
ID_RE = re.compile(r'\b\d{9}\b')
PHONE_RE = re.compile(
    r'\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)\d{3,4}[-.\s]?\d{3,4}\b'
)


def redact(text: str) -> str:
    if not text:
        return text
    text = EMAIL_RE.sub('[REDACTED-EMAIL]', text)
    text = CARD_RE.sub('[REDACTED-CARD]', text)
    text = ID_RE.sub('[REDACTED-ID]', text)
    text = PHONE_RE.sub('[REDACTED-PHONE]', text)
    return text


def has_pii(text: str) -> bool:
    return redact(text) != text
