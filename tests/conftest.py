"""Test fixtures.

Tests run without Docker by default. Anything that needs a real DB or Redis is
marked with the `integration` marker and skipped when those services are not up.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root / 'src'))
sys.path.insert(0, str(_root / 'mcp_server' / 'src'))

os.environ.setdefault('LLM_PROVIDER', 'stub')
os.environ.setdefault('CONFIRMATION_HMAC_SECRET', 'test-secret')


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(reason='integration test; requires running services')
    if not os.environ.get('RUN_INTEGRATION_TESTS'):
        for item in items:
            if 'integration' in item.keywords:
                item.add_marker(skip_integration)
