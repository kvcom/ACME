#!/usr/bin/env python3
"""Fail if any tracked text file begins with a UTF-8 BOM."""
import sys
from pathlib import Path

BOM = b"\xef\xbb\xbf"
bad: list[str] = []

for path in sys.argv[1:]:
    p = Path(path)
    try:
        if p.is_file() and p.read_bytes().startswith(BOM):
            bad.append(path)
    except (IsADirectoryError, PermissionError, FileNotFoundError):
        continue

if bad:
    print("Files contain UTF-8 BOM:")
    for path in bad:
        print(f"  {path}")
    raise SystemExit(1)
