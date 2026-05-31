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


# Column ORDER HINT per table (not an allow-list of which columns to show).
# The explorer displays EVERY real column the table has — see
# `resolve_columns()`. Columns named here appear first, in this order, so the
# meaningful fields lead and the UUID/FK columns trail; any column NOT named
# here (e.g. a freshly-added one, or fields like user_query we didn't bother
# to order) is appended after, so nothing is ever hidden.
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


# ── Live column resolution ───────────────────────────────────────────────────
# The explorer shows EVERY column a table actually has, introspected from
# `information_schema.columns`. This guarantees the UI can never silently drift
# from the schema (the previous hand-curated lists were missing columns like
# agent_traces.user_query). TABLE_COLUMNS above is now only an ordering hint.
#
# Result is cached process-wide with a short TTL so a column added by a live
# migration appears within ~30 s without a restart, while we don't hit
# information_schema on every request.

import time  # noqa: E402 — kept local to this feature block

_LIVE_COLUMNS: dict[str, list[str]] = {}
_LIVE_COLUMNS_AT: float = 0.0
_LIVE_COLUMNS_TTL = 30.0


def _merge_order(table: str, real_cols: list[str]) -> list[str]:
    """Hint columns first (in hint order, if they exist), then the rest of the
    real columns in their natural ordinal order."""
    hint = TABLE_COLUMNS.get(table, [])
    ordered = [c for c in hint if c in real_cols]
    ordered += [c for c in real_cols if c not in hint]
    return ordered or list(real_cols)


async def resolve_columns(force: bool = False) -> dict[str, list[str]]:
    """Introspect and cache the real column list for every explorer table."""
    global _LIVE_COLUMNS, _LIVE_COLUMNS_AT
    now = time.time()
    if not force and _LIVE_COLUMNS and (now - _LIVE_COLUMNS_AT) < _LIVE_COLUMNS_TTL:
        return _LIVE_COLUMNS

    # Lazy import to keep this module import-safe before the DB layer is set up.
    from sqlalchemy import text
    from acme_app.infrastructure.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                text("""
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = ANY(:tables)
                    ORDER BY table_name, ordinal_position
                """),
                {'tables': EXPLORER_TABLES},
            )).all()
    except Exception:
        # On failure keep what we had; if nothing yet, fall back to the hints
        # so the explorer still works (just possibly missing new columns).
        if not _LIVE_COLUMNS:
            _LIVE_COLUMNS = {t: list(cols) for t, cols in TABLE_COLUMNS.items()}
            _LIVE_COLUMNS_AT = now
        return _LIVE_COLUMNS

    real: dict[str, list[str]] = {}
    for table_name, column_name in rows:
        real.setdefault(table_name, []).append(column_name)
    _LIVE_COLUMNS = {t: _merge_order(t, real.get(t, [])) for t in EXPLORER_TABLES}
    _LIVE_COLUMNS_AT = now
    return _LIVE_COLUMNS


def columns_for(table: str) -> list[str]:
    """Sync accessor for the last-resolved column list. Callers must have
    awaited resolve_columns() earlier in the same request. Falls back to the
    hint (or, ultimately, an empty list) when the cache is cold."""
    return _LIVE_COLUMNS.get(table) or list(TABLE_COLUMNS.get(table, []))


# ── Editability spec (D-021) ─────────────────────────────────────────────────
# Which tables can be edited/appended from the DB Explorer, and how each column
# is edited. Audit tables (agent_traces, trace_events, tool_call_logs,
# rbac_decisions) and eval_* are NOT here — they stay read-only (D-017).
#
# Column kinds the UI understands:
#   system  — auto-generated, never user-editable (id, created_at, ...).
#   bool    — toggle true/false.
#   enum    — fixed dropdown; `options` lists the choices.
#   fk      — dropdown of rows from another table; `fk` = (table, value_col,
#             label_col). Options resolved live by the API.
#   text    — free text. `ai` = True adds the "AI suggest" button.
#   text[]  — multi-select of `options` (Postgres TEXT[] column).
#   json    — JSON object/array, edited as text (with validation).
#   int     — integer input.
#
# `auto` on a system column = server-synth strategy on append:
#   uuid | now | null | ref:ISS  (ref:ISS -> next ISS-#### style ref)


@dataclass(frozen=True)
class ColumnEdit:
    kind: str                              # system|bool|enum|fk|text|text[]|json|int
    options: tuple[str, ...] = ()
    fk: tuple[str, str, str] | None = None  # (table, value_col, label_col)
    ai: bool = False
    auto: str | None = None
    required: bool = True
    placeholder: str = ''


_ROLES = ('sales_user', 'support_user', 'admin')
_SEVERITY = ('P1', 'P2', 'P3', 'P4')
_ISSUE_STATUS = ('Open', 'In Progress', 'Waiting for Customer', 'Escalated', 'Resolved', 'Closed')
_SLA = ('Within SLA', 'At Risk', 'Breached')
_PRIORITY = ('Low', 'Medium', 'High', 'Critical')
_SIDE_EFFECT = ('low', 'medium', 'high')
_INDUSTRY = ('Energy', 'Retail', 'Logistics', 'Manufacturing', 'Healthcare',
             'Aerospace', 'Finance', 'Telecom', 'Technology', 'Public Sector')
_TIER = ('Enterprise', 'Mid-market', 'Strategic', 'SMB')
_REGION = ('UK', 'Netherlands', 'Germany', 'France', 'US', 'Ireland', 'Spain', 'Nordics')
_TIMEZONE = ('Europe/London', 'Europe/Amsterdam', 'Europe/Berlin', 'Europe/Paris',
             'Europe/Madrid', 'Europe/Dublin', 'America/New_York', 'UTC')
_CUSTOMER_STATUS = ('active', 'archived')
_UPDATE_TYPE = ('customer_update', 'engineering_update', 'internal_note')
_RECOMMENDER = ('recommend_next_action_tool', 'customer_escalation_summary', 'closure_readiness_check')
_REQUIRED_FIELDS = ('owner_name', 'description', 'due_at', 'issue_ref', 'new_status')


EDIT_SPEC: dict[str, dict[str, 'ColumnEdit']] = {
    'action_catalogue': {
        'action_type':           ColumnEdit('text', placeholder='UPPER_SNAKE_CASE, e.g. REQUEST_LEGAL_REVIEW'),
        'label':                 ColumnEdit('text', ai=True, placeholder='Human label'),
        'description':           ColumnEdit('text', ai=True, placeholder='What this action does'),
        'allowed_roles':         ColumnEdit('text[]', options=_ROLES),
        'required_fields':       ColumnEdit('text[]', options=_REQUIRED_FIELDS, required=False),
        'side_effect_level':     ColumnEdit('enum', options=_SIDE_EFFECT),
        'requires_confirmation': ColumnEdit('bool'),
        'is_active':             ColumnEdit('bool'),
    },
    'action_recommendation_rules': {
        'id':                    ColumnEdit('system', auto='uuid'),
        'rule_ref':              ColumnEdit('text', placeholder='e.g. rule:legal_review'),
        'recommender':           ColumnEdit('enum', options=_RECOMMENDER),
        'priority_order':        ColumnEdit('int', placeholder='lower = checked first'),
        'conditions':            ColumnEdit('json', ai=True, required=False, placeholder='{"severity":"P1"}'),
        'action_type':           ColumnEdit('fk', fk=('action_catalogue', 'action_type', 'label')),
        'recommended_priority':  ColumnEdit('enum', options=_PRIORITY),
        'rationale_template':    ColumnEdit('text', ai=True, required=False),
        'is_active':             ColumnEdit('bool'),
        'notes':                 ColumnEdit('text', ai=True, required=False),
        'created_at':            ColumnEdit('system', auto='now'),
    },
    'customers': {
        'id':                    ColumnEdit('system', auto='uuid'),
        'name':                  ColumnEdit('text', ai=True, placeholder='Company name'),
        'industry':              ColumnEdit('enum', options=_INDUSTRY),
        'tier':                  ColumnEdit('enum', options=_TIER),
        'region':                ColumnEdit('enum', options=_REGION),
        'customer_timezone':     ColumnEdit('enum', options=_TIMEZONE),
        # account_owner is a TEXT column storing a person's display name, not a
        # real FK — but we surface a dropdown of existing user display names so
        # the operator picks rather than types. Stored as the display string.
        # Required at the UI level: every customer must have an owner (the DB
        # column stays nullable for backward compat, but the explorer gates it).
        'account_owner':         ColumnEdit('fk', fk=('users', 'display_name', 'display_name')),
        'status':                ColumnEdit('enum', options=_CUSTOMER_STATUS),
        'created_at':            ColumnEdit('system', auto='now'),
    },
    'issues': {
        'id':                    ColumnEdit('system', auto='uuid'),
        'issue_ref':             ColumnEdit('system', auto='ref:ISS'),
        'customer_id':           ColumnEdit('fk', fk=('customers', 'id', 'name')),
        'title':                 ColumnEdit('text', ai=True, placeholder='Short issue title'),
        'description':           ColumnEdit('text', ai=True, placeholder='Issue description'),
        'severity':              ColumnEdit('enum', options=_SEVERITY),
        'status':                ColumnEdit('enum', options=_ISSUE_STATUS),
        'sla_status':            ColumnEdit('enum', options=_SLA),
        'owner':                 ColumnEdit('fk', fk=('users', 'display_name', 'display_name'), required=False),
        'opened_at':             ColumnEdit('system', auto='now'),
        'updated_at':            ColumnEdit('system', auto='now'),
    },
    'issue_updates': {
        'id':                    ColumnEdit('system', auto='uuid'),
        'issue_id':              ColumnEdit('fk', fk=('issues', 'id', 'issue_ref')),
        'update_text':           ColumnEdit('text', ai=True, placeholder='Update note'),
        'update_type':           ColumnEdit('enum', options=_UPDATE_TYPE),
        # Author of the update. NOT NULL → required. Dropdown of users
        # (shows display name, stores username, matching existing data).
        'created_by':            ColumnEdit('fk', fk=('users', 'username', 'display_name')),
        'created_at':            ColumnEdit('system', auto='now'),
    },
    'users': {
        'id':                    ColumnEdit('system', auto='uuid'),
        'username':              ColumnEdit('text', placeholder='e.g. jane.doe'),
        'email':                 ColumnEdit('text', ai=True, required=False, placeholder='name@example.local'),
        'display_name':          ColumnEdit('text', ai=True, required=False, placeholder='Full name'),
        'keycloak_subject':      ColumnEdit('system', auto='null'),
        'is_active':             ColumnEdit('bool'),
        'created_at':            ColumnEdit('system', auto='now'),
        'deleted_at':            ColumnEdit('system', auto='null'),
    },
    'user_roles': {
        'id':                    ColumnEdit('system', auto='uuid'),
        'user_id':               ColumnEdit('fk', fk=('users', 'id', 'username')),
        'role_name':             ColumnEdit('enum', options=_ROLES),
        'is_active':             ColumnEdit('bool'),
        'granted_at':            ColumnEdit('system', auto='now'),
        'granted_by':            ColumnEdit('text', required=False, placeholder='admin'),
        'revoked_at':            ColumnEdit('system', auto='null'),
        'revoked_by':            ColumnEdit('system', auto='null'),
    },
}

EDITABLE_TABLES: list[str] = list(EDIT_SPEC.keys())


def is_editable_table(table: str) -> bool:
    return table in EDIT_SPEC


def column_edit(table: str, column: str) -> 'ColumnEdit':
    """Edit spec for one column; unknown columns default to read-only system."""
    return EDIT_SPEC.get(table, {}).get(column, ColumnEdit('system'))


def edit_spec_dump(table: str) -> dict[str, dict[str, Any]]:
    """Serialise a table's edit spec for the frontend (FK options resolved
    live by the API, not here)."""
    out: dict[str, dict[str, Any]] = {}
    for col, spec in EDIT_SPEC.get(table, {}).items():
        out[col] = {
            'kind': spec.kind, 'options': list(spec.options),
            'fk': list(spec.fk) if spec.fk else None, 'ai': spec.ai,
            'auto': spec.auto, 'required': spec.required, 'placeholder': spec.placeholder,
        }
    return out
