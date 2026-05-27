"""Skill registry.

Skills are versioned, schema-based, reusable. The orchestrator looks up by name;
unknown names are rejected before invocation.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from acme_app.skills import closure_readiness_check, customer_escalation_summary


SKILLS: dict[str, Callable[..., dict[str, Any]]] = {
    'customer_escalation_summary': customer_escalation_summary.run,
    'closure_readiness_check': closure_readiness_check.run,
}

VERSIONS: dict[str, str] = {
    'customer_escalation_summary': customer_escalation_summary.VERSION,
    'closure_readiness_check': closure_readiness_check.VERSION,
}


def known(name: str) -> bool:
    return name in SKILLS
