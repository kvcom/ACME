import re

PATTERNS = [
    re.compile(r'ignore previous', re.I),
    re.compile(r'you are now', re.I),
    re.compile(r'system:', re.I),
]


def check_query(text: str) -> tuple[bool, list[str]]:
    if len(text) > 4096:
        return False, ['Query exceeds maximum length']
    flags = [rx.pattern for rx in PATTERNS if rx.search(text)]
    return True, flags
