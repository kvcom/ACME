"""DB Explorer metadata.

Defines the allow-list of inspectable tables and the relationship map used to
render "+" expand markers on FK columns. Hard-coded rather than introspected
from `pg_constraint` for two reasons:

  1. We can expose REVERSE relationships too (drill from customers into
     their issues, even though the FK column lives on issues), which the
     catalog doesn't surface in a single direction.
  2. The allow-list is explicit — the explorer can never query tables that
     aren't enumerated here, which means no accidental exposure of any
     future internal table.

A `LINK` is the unit the UI clicks on: it sits on a (table, field) pair and
describes one related set of rows to fetch. A single field can have multiple
links (e.g. `users.id` fans out into conversations, traces, next_actions, …).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Link:
    """One related-rowset reachable from a (table, field) cell."""

    # 'belongs_to' — value is a UUID pointing at target.target_field (one row).
    # 'has_many'   — value is a UUID and target.target_field references it (many rows).
    # 'lookup'     — value is a string/text and target.target_field matches it (many rows).
    kind: str
    target: str           # table being expanded into
    target_field: str     # column on `target` we match against
    label: str            # short human label rendered in the UI


# Display column order per table — first three are treated as primary columns
# the UI will show by default; the rest are revealed in expanded view.
TABLE_COLUMNS: dict[str, list[str]] = {
    'users': [
        'username', 'display_name', 'email', 'is_active', 'keycloak_subject',
        'created_at', 'deleted_at', 'id',
    ],
    'user_roles': [
        'role_name', 'is_active', 'granted_at', 'granted_by',
        'revoked_at', 'revoked_by', 'user_id', 'id',
    ],
    'customers': [
        'name', 'industry', 'tier', 'region', 'customer_timezone',
        'account_owner', 'status', 'created_at', 'id',
    ],
    'issues': [
        'issue_ref', 'title', 'severity', 'status', 'sla_status',
        'owner', 'opened_at', 'updated_at', 'customer_id', 'id',
    ],
    'issue_updates': [
        'update_type', 'update_text', 'created_by', 'created_at',
        'issue_id', 'id',
    ],
    'next_actions': [
        'action_ref', 'action_type', 'title', 'priority', 'status',
        'owner_role', 'owner_name', 'due_at', 'created_by', 'created_by_role',
        'created_at', 'completed_at',
        'customer_id', 'issue_id', 'created_by_user_id', 'id',
    ],
    'action_catalogue': [
        'action_type', 'label', 'description', 'side_effect_level',
        'allowed_roles', 'requires_confirmation', 'is_active',
    ],
    'action_recommendation_rules': [
        'rule_ref', 'recommender', 'priority_order', 'conditions',
        'action_type', 'recommended_priority', 'rationale_template',
        'is_active', 'notes', 'created_at', 'id',
    ],
    'conversations': [
        'conversation_ref', 'username', 'title',
        'last_message_at', 'last_message_preview', 'message_count',
        'started_at', 'deleted_at', 'user_id', 'id',
    ],
    'agent_traces': [
        'trace_ref', 'username', 'user_role', 'detected_intent',
        'final_status', 'llm_provider', 'llm_model',
        'total_tokens', 'estimated_cost_usd', 'total_latency_ms', 'created_at',
        'conversation_id', 'user_id', 'id',
    ],
    'trace_events': [
        'event_type', 'event_name', 'status', 'latency_ms', 'created_at',
        'trace_id', 'id',
    ],
    'tool_call_logs': [
        'tool_name', 'status', 'latency_ms', 'created_at',
        'trace_id', 'id',
    ],
    'rbac_decisions': [
        'username', 'role_name', 'operation', 'resource', 'allowed',
        'reason', 'created_at', 'trace_id', 'id',
    ],
    'eval_runs': [
        'eval_run_ref', 'llm_provider', 'llm_model', 'cases_total',
        'cases_passed', 'started_at', 'completed_at', 'total_cost_usd',
        'git_sha', 'id',
    ],
    'eval_results': [
        'case_id', 'role_name', 'query',
        'tool_selection_pass', 'grounding_pass', 'rbac_pass',
        'action_reasonableness_pass', 'adversarial_pass',
        'latency_ms', 'cost_usd', 'notes', 'created_at',
        'eval_run_id', 'user_id', 'id',
    ],
}


# (table, field) → list of Links that can be expanded from a value in that cell.
# Forward FK ("belongs_to"): value points at target.target_field, returns 1 row.
# Reverse FK ("has_many"):  rows in `target` where target.target_field == value.
# Lookup:                   text-keyed reverse link (e.g. created_by → users.username).
LINKS: dict[tuple[str, str], list[Link]] = {
    # ── users ──
    ('users', 'id'): [
        Link('has_many', 'user_roles',    'user_id',            'roles'),
        Link('has_many', 'conversations', 'user_id',            'conversations'),
        Link('has_many', 'agent_traces',  'user_id',            'traces'),
        Link('has_many', 'next_actions',  'created_by_user_id', 'actions proposed'),
        Link('has_many', 'eval_results',  'user_id',            'eval results'),
    ],
    ('users', 'username'): [
        Link('lookup', 'conversations',  'username',     'conversations (by username)'),
        Link('lookup', 'agent_traces',   'username',     'traces (by username)'),
        Link('lookup', 'rbac_decisions', 'username',     'rbac decisions'),
        Link('lookup', 'issue_updates',  'created_by',   'issue updates by user'),
    ],

    # ── user_roles ──
    ('user_roles', 'user_id'): [
        Link('belongs_to', 'users', 'id', 'user'),
    ],

    # ── customers ──
    ('customers', 'id'): [
        Link('has_many', 'issues',       'customer_id', 'issues'),
        Link('has_many', 'next_actions', 'customer_id', 'next actions'),
    ],

    # ── issues ──
    ('issues', 'customer_id'): [
        Link('belongs_to', 'customers', 'id', 'customer'),
    ],
    ('issues', 'id'): [
        Link('has_many', 'issue_updates', 'issue_id', 'updates'),
        Link('has_many', 'next_actions',  'issue_id', 'next actions'),
    ],

    # ── issue_updates ──
    ('issue_updates', 'issue_id'): [
        Link('belongs_to', 'issues', 'id', 'issue'),
    ],
    ('issue_updates', 'created_by'): [
        Link('lookup', 'users', 'username', 'user'),
    ],

    # ── next_actions ──
    ('next_actions', 'customer_id'): [
        Link('belongs_to', 'customers', 'id', 'customer'),
    ],
    ('next_actions', 'issue_id'): [
        Link('belongs_to', 'issues', 'id', 'issue'),
    ],
    ('next_actions', 'created_by_user_id'): [
        Link('belongs_to', 'users', 'id', 'user'),
    ],
    ('next_actions', 'action_type'): [
        Link('belongs_to', 'action_catalogue', 'action_type', 'action definition'),
    ],

    # ── action_catalogue ──
    ('action_catalogue', 'action_type'): [
        Link('has_many', 'next_actions', 'action_type', 'actions of this type'),
        Link('has_many', 'action_recommendation_rules', 'action_type',
             'recommendation rules that propose this action'),
    ],

    # ── action_recommendation_rules ──
    ('action_recommendation_rules', 'action_type'): [
        Link('belongs_to', 'action_catalogue', 'action_type', 'action definition'),
    ],

    # ── conversations ──
    ('conversations', 'user_id'): [
        Link('belongs_to', 'users', 'id', 'user'),
    ],
    ('conversations', 'id'): [
        Link('has_many', 'agent_traces', 'conversation_id', 'traces'),
    ],

    # ── agent_traces ──
    ('agent_traces', 'user_id'): [
        Link('belongs_to', 'users', 'id', 'user'),
    ],
    ('agent_traces', 'conversation_id'): [
        Link('belongs_to', 'conversations', 'id', 'conversation'),
    ],
    ('agent_traces', 'id'): [
        Link('has_many', 'trace_events',    'trace_id', 'events'),
        Link('has_many', 'tool_call_logs',  'trace_id', 'tool calls'),
        Link('has_many', 'rbac_decisions',  'trace_id', 'rbac decisions'),
    ],

    # ── trace_events ──
    ('trace_events', 'trace_id'): [
        Link('belongs_to', 'agent_traces', 'id', 'trace'),
    ],
    # ── tool_call_logs ──
    ('tool_call_logs', 'trace_id'): [
        Link('belongs_to', 'agent_traces', 'id', 'trace'),
    ],
    # ── rbac_decisions ──
    ('rbac_decisions', 'trace_id'): [
        Link('belongs_to', 'agent_traces', 'id', 'trace'),
    ],

    # ── eval_runs ──
    ('eval_runs', 'id'): [
        Link('has_many', 'eval_results', 'eval_run_id', 'cases'),
    ],
    # ── eval_results ──
    ('eval_results', 'eval_run_id'): [
        Link('belongs_to', 'eval_runs', 'id', 'run'),
    ],
    ('eval_results', 'user_id'): [
        Link('belongs_to', 'users', 'id', 'persona'),
    ],
}


# Tables exposed in the picker. Anything not in this list is unreachable
# from the explorer — extra safety on top of the role gate. Sorted so the
# sidebar reads alphabetically regardless of how TABLE_COLUMNS is ordered
# in the source (it's grouped by domain there for editor readability).
EXPLORER_TABLES: list[str] = sorted(TABLE_COLUMNS.keys())


# Default ORDER BY column per table for stable listings.
DEFAULT_ORDER: dict[str, str] = {
    'users': 'username',
    'user_roles': 'granted_at DESC',
    'customers': 'name',
    'issues': 'opened_at DESC',
    'issue_updates': 'created_at DESC',
    'next_actions': 'created_at DESC',
    'action_catalogue': 'action_type',
    'action_recommendation_rules': 'recommender, priority_order',
    'conversations': 'last_message_at DESC',
    'agent_traces': 'created_at DESC',
    'trace_events': 'created_at DESC',
    'tool_call_logs': 'created_at DESC',
    'rbac_decisions': 'created_at DESC',
    'eval_runs': 'started_at DESC',
    'eval_results': 'created_at DESC',
}


def links_for(table: str, field: str) -> list[Link]:
    return LINKS.get((table, field), [])


def expandable_fields(table: str) -> dict[str, list[dict[str, str]]]:
    """Return {field: [{kind, target, label}, ...]} for the JS layer."""
    out: dict[str, list[dict[str, str]]] = {}
    for (tbl, field), links in LINKS.items():
        if tbl != table:
            continue
        out[field] = [
            {'kind': lk.kind, 'target': lk.target, 'label': lk.label}
            for lk in links
        ]
    return out


def is_table_allowed(table: str) -> bool:
    return table in EXPLORER_TABLES
