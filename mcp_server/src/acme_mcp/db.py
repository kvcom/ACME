"""Sync psycopg connection helper for the MCP server.

The MCP server is intentionally narrow: it handles one HTTP request per tool
call, runs one or two SQL statements, and returns. Sync is fine here.
"""
from __future__ import annotations

import os
import psycopg


def get_conn() -> psycopg.Connection:
    url = os.getenv('SYNC_DATABASE_URL', 'postgresql://acme:acme@postgres:5432/acme')
    return psycopg.connect(url, autocommit=False)
