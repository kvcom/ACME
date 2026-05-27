import re

EMAIL_RE = re.compile(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b')
PHONE_RE = re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{3,4}\b')
ID_RE = re.compile(r'\b\d{9}\b')
CARD_RE = re.compile(r'\b\d{16}\b')


def redact(text: str) -> str:
    text = EMAIL_RE.sub('[REDACTED-EMAIL]', text)
    text = PHONE_RE.sub('[REDACTED-PHONE]', text)
    text = ID_RE.sub('[REDACTED-ID]', text)
    text = CARD_RE.sub('[REDACTED-CARD]', text)
    return text
