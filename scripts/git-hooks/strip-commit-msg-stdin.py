#!/usr/bin/env python3
"""Normalize a commit message read from stdin (for git history filters)."""
from __future__ import annotations

import sys

_STRIP_MARKERS = (
    'cursoragent@cursor.com',
    'made-with: cursor',
)


def _normalize_message(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        lower = line.lower()
        if lower.strip().startswith('co-authored-by:') and 'cursor' in lower:
            continue
        if any(marker in lower for marker in _STRIP_MARKERS):
            continue
        kept.append(line)
    return ''.join(kept)


if __name__ == '__main__':
    sys.stdout.write(_normalize_message(sys.stdin.read()))
