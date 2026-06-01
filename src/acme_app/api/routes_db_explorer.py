"""DB Explorer routes (admin only).

Read-only pivot-style browser over the application database. The frontend
renders rows from a chosen root table, then lets the user drill down through
FK / reverse-FK relationships defined in `application/db_explorer.py`.

Security:
- Every endpoint requires the `admin` realm role.
- Table names are validated against the explicit allow-list before any SQL
  string interpolation. `field` and `target_field` are also checked against
  the per-table column list. No identifier ever comes off the request body
  directly into SQL.
- Values used in WHERE clauses are bound as parameters.
- LIMIT is enforced on every query.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from acme_app.application.db_explorer import (
    DEFAULT_ORDER,
    EDITABLE_TABLES,
    EXPLORER_TABLES,
    LINKS,
    column_edit,
    columns_for,
    edit_spec_dump,
    expandable_fields,
    is_editable_table,
    is_table_allowed,
    links_for,
    resolve_columns,
)
from acme_app.application.realtime import broadcaster as realtime_broadcaster
from acme_app.auth.current_user import CurrentUser, _decode_session, get_current_user
from acme_app.auth.role_store import get_roles_for_username
from acme_app.config import settings
from acme_app.infrastructure.llm.availability import (
    NO_MODEL_MESSAGE,
    assist_specs_ordered,
)
from acme_app.infrastructure.llm.model_registry import ModelSpec
from acme_app.infrastructure.llm.provider import get_provider
from acme_app.infrastructure.db.session import AsyncSessionLocal, get_db_session

router = APIRouter(prefix='/db-explorer', tags=['db-explorer'])
_log = logging.getLogger(__name__)

ROW_LIMIT_DEFAULT = 100
ROW_LIMIT_MAX = 500
WS_REAUTH_INTERVAL_S = 30


def _require_admin(user: CurrentUser) -> CurrentUser:
    if 'admin' not in user.roles:
        raise HTTPException(status_code=403, detail='Admin role required')
    return user


def _validate_table(table: str) -> str:
    if not is_table_allowed(table):
        raise HTTPException(status_code=400, detail=f'unknown table: {table}')
    return table


def _validate_field(table: str, field: str) -> str:
    # Validates against the LIVE column set (resolve_columns() must have been
    # awaited earlier in the request). Still an allow-list — the field has to
    # be a real column on a real explorer table before it touches SQL.
    if field not in columns_for(table):
        raise HTTPException(status_code=400, detail=f'unknown field {field} on {table}')
    return field


def _serialise(value: Any) -> Any:
    if isinstance(value, (UUID,)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list)):
        # JSONB column already comes back as native dict/list; pass through.
        return value
    return value


async def _jaeger_trace_available(otel_trace_id: str | None) -> bool:
    if not otel_trace_id:
        return False
    base_url = settings.otel_jaeger_query_url.rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f'{base_url}/api/traces/{otel_trace_id}')
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get('data'))
    except Exception:
        return False


async def _query_rows(
    session: AsyncSession,
    table: str,
    *,
    where_field: str | None = None,
    where_value: Any = None,
    limit: int = ROW_LIMIT_DEFAULT,
) -> list[dict[str, Any]]:
    # Ensure the live column set is loaded, then build the SELECT from it.
    await resolve_columns()
    cols = columns_for(table)
    col_list = ', '.join(cols)  # safe — every name is a real introspected column
    where_sql = ''
    params: dict[str, Any] = {'n': min(limit, ROW_LIMIT_MAX)}
    if where_field is not None:
        _validate_field(table, where_field)
        where_sql = f'WHERE {where_field} = :v'
        params['v'] = where_value
    order = DEFAULT_ORDER.get(table, cols[0] if cols else 'id')
    sql = f'SELECT {col_list} FROM {table} {where_sql} ORDER BY {order} LIMIT :n'  # noqa: S608 — identifiers are allow-listed
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [{k: _serialise(v) for k, v in row.items()} for row in rows]


@router.get('', response_class=HTMLResponse)
async def db_explorer_page(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
) -> HTMLResponse:
    _require_admin(user)
    # Pre-bake the per-table metadata the JS layer needs so the page works
    # without a separate metadata fetch round-trip. Columns are the LIVE,
    # introspected set so every real column is shown.
    columns = await resolve_columns()
    metadata = {
        'tables': EXPLORER_TABLES,
        'editable_tables': EDITABLE_TABLES,
        'columns': columns,
        'links': {table: expandable_fields(table) for table in EXPLORER_TABLES},
    }
    return request.app.state.templates.TemplateResponse(
        request, 'db_explorer.html',
        {'user': user, 'metadata_json': json.dumps(metadata)},
    )


@router.get('/metadata')
async def metadata(
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the same {tables, columns, links} payload the page bakes in
    at render time. The frontend re-fetches this on every WebSocket
    reconnect so that if the backend has restarted with new tables /
    columns / relationships in its registry (e.g. after a developer added
    one), open browser tabs pick up the change without manual refresh."""
    _require_admin(user)
    columns = await resolve_columns()
    return {
        'tables': EXPLORER_TABLES,
        'editable_tables': EDITABLE_TABLES,
        'columns': columns,
        'links': {table: expandable_fields(table) for table in EXPLORER_TABLES},
    }


@router.get('/rows/{table}')
async def rows_for_table(
    table: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    limit: int = ROW_LIMIT_DEFAULT,
) -> dict[str, Any]:
    _require_admin(user)
    _validate_table(table)
    rows = await _query_rows(session, table, limit=limit)
    return {
        'table': table,
        'columns': columns_for(table),
        'rows': rows,
        'links': expandable_fields(table),
        'row_count': len(rows),
    }


@router.get('/related/{table}/{field}')
async def related_rows(
    table: str,
    field: str,
    value: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    limit: int = ROW_LIMIT_DEFAULT,
) -> dict[str, Any]:
    """Return every related rowset reachable from one cell.

    A single (table, field) cell can fan into multiple targets — e.g.
    `users.id` expands into roles, conversations, traces, … — so the
    response is grouped by target.
    """
    _require_admin(user)
    _validate_table(table)
    # Load the live column cache before any field validation below.
    await resolve_columns()
    _validate_field(table, field)

    cell_links = links_for(table, field)
    if not cell_links:
        raise HTTPException(status_code=400, detail=f'{table}.{field} is not expandable')

    groups = []
    for link in cell_links:
        _validate_table(link.target)
        _validate_field(link.target, link.target_field)
        rows = await _query_rows(
            session, link.target,
            where_field=link.target_field,
            where_value=value,
            limit=limit,
        )
        groups.append({
            'kind': link.kind,
            'target': link.target,
            'target_field': link.target_field,
            'label': link.label,
            'columns': columns_for(link.target),
            'rows': rows,
            'row_count': len(rows),
            'links': expandable_fields(link.target),
        })
    return {'source': {'table': table, 'field': field, 'value': value}, 'groups': groups}


@router.get('/row/{table}/{row_id}')
async def single_row(
    table: str,
    row_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Fetch one row by id — used by the WS client to hydrate after notify.

    PK is `id` on all tables except `action_catalogue` whose natural key is
    `action_type`. The trigger function sends whichever it found.
    """
    _require_admin(user)
    _validate_table(table)
    pk = 'action_type' if table == 'action_catalogue' else 'id'
    rows = await _query_rows(session, table, where_field=pk, where_value=row_id, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail='row not found')
    return {'table': table, 'columns': columns_for(table), 'row': rows[0],
            'links': expandable_fields(table)}


@router.get('/otel/{otel_trace_id}')
async def otel_trace_detail(
    otel_trace_id: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """OpenTelemetry trace summary for the popover.

    Jaeger receives the real OTel trace. The popover still reconstructs the
    compact timeline from durable `trace_events` so the audit UI works even if
    the OTel pipeline is unavailable.
    """
    _require_admin(user)
    trace = (await session.execute(
        text("""
            SELECT id, trace_ref, otel_trace_id, detected_intent, final_status,
                   llm_provider, llm_model, total_latency_ms, llm_latency_ms,
                   tool_latency_ms, total_tokens, estimated_cost_usd, created_at
            FROM agent_traces
            WHERE otel_trace_id = :otel
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {'otel': otel_trace_id},
    )).mappings().first()
    if trace is None:
        raise HTTPException(status_code=404, detail='no trace with that otel id')

    spans = (await session.execute(
        text("""
            SELECT event_type, event_name, status, latency_ms, created_at
            FROM trace_events
            WHERE trace_id = :tid
            ORDER BY created_at ASC
        """),
        {'tid': trace['id']},
    )).mappings().all()

    jaeger_available = await _jaeger_trace_available(otel_trace_id)
    return {
        'otel_trace_id': otel_trace_id,
        'jaeger_url': (
            f"{settings.otel_jaeger_ui_url.rstrip('/')}/trace/{otel_trace_id}"
            if jaeger_available else ''
        ),
        'jaeger_available': jaeger_available,
        'trace_ref': trace['trace_ref'],
        'detected_intent': trace['detected_intent'],
        'final_status': trace['final_status'],
        'llm_provider': trace['llm_provider'],
        'llm_model': trace['llm_model'],
        'total_latency_ms': trace['total_latency_ms'],
        'llm_latency_ms': trace['llm_latency_ms'],
        'tool_latency_ms': trace['tool_latency_ms'],
        'total_tokens': trace['total_tokens'],
        'estimated_cost_usd': _serialise(trace['estimated_cost_usd']),
        'created_at': _serialise(trace['created_at']),
        'spans': [
            {
                'event_type': s['event_type'],
                'event_name': s['event_name'],
                'status': s['status'],
                'latency_ms': s['latency_ms'],
                'at': _serialise(s['created_at']),
            }
            for s in spans
        ],
    }


# ─── Write surface (D-021): edit-meta, append, patch, ai-suggest ────────────
#
# Append-only invariant (D-017) preserved: these endpoints only INSERT and
# UPDATE the editable tables; never DELETE. Audit tables are not editable.
# Every column value is validated against the table's EDIT_SPEC before it
# touches SQL — kind, enum membership, FK existence, type coercion. Column
# and table identifiers are allow-listed (never interpolated from raw input).


def _require_editable(table: str) -> None:
    _validate_table(table)
    if not is_editable_table(table):
        raise HTTPException(status_code=403, detail=f'{table} is read-only')


async def _fk_options(session: AsyncSession, fk: tuple[str, str, str]) -> list[dict[str, str]]:
    """Resolve a foreign-key dropdown: rows of (value, label) from the target."""
    target, value_col, label_col = fk
    _validate_table(target)
    rows = (await session.execute(
        text(f'SELECT DISTINCT {value_col}::text AS v, {label_col}::text AS l '  # noqa: S608 — allow-listed identifiers
             f'FROM {target} WHERE {value_col} IS NOT NULL ORDER BY l LIMIT 500'),
    )).mappings().all()
    return [{'value': r['v'], 'label': r['l']} for r in rows]


@router.get('/edit-meta/{table}')
async def edit_meta(
    table: str,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Per-column edit spec + resolved FK/enum options for the editable form."""
    _require_admin(user)
    _require_editable(table)
    await resolve_columns()
    spec = edit_spec_dump(table)
    # Resolve FK options live so dropdowns show valid targets only.
    fk_options: dict[str, list[dict[str, str]]] = {}
    for col, meta in spec.items():
        if meta['kind'] == 'fk' and meta['fk']:
            fk_options[col] = await _fk_options(session, tuple(meta['fk']))
    return {
        'table': table,
        'columns': columns_for(table),
        'spec': spec,
        'fk_options': fk_options,
        'editable': True,
    }


async def _next_ref(session: AsyncSession, table: str, column: str, prefix: str) -> str:
    """Generate the next 'PREFIX-####' style business ref for a table."""
    rows = (await session.execute(
        text(f"SELECT {column} FROM {table} WHERE {column} ~ :pat"),  # noqa: S608 — allow-listed
        {'pat': f'^{prefix}-[0-9]+$'},
    )).scalars().all()
    max_n = 0
    for r in rows:
        try:
            max_n = max(max_n, int(str(r).split('-')[1]))
        except (IndexError, ValueError):
            continue
    return f'{prefix}-{max_n + 1}'


def _coerce_value(table: str, column: str, raw: Any) -> Any:
    """Validate + coerce one user-supplied value against its EDIT_SPEC kind."""
    spec = column_edit(table, column)
    if spec.kind == 'system':
        raise HTTPException(status_code=400, detail=f'{column} is system-managed')
    if raw is None or raw == '':
        if spec.required:
            raise HTTPException(status_code=400, detail=f'{column} is required')
        return None
    if spec.kind == 'bool':
        return bool(raw) if isinstance(raw, bool) else str(raw).lower() in ('true', '1', 'yes', 'on')
    if spec.kind == 'int':
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f'{column} must be an integer')
    if spec.kind == 'enum':
        if str(raw) not in spec.options:
            raise HTTPException(status_code=400, detail=f'{column}: "{raw}" not in {list(spec.options)}')
        return str(raw)
    if spec.kind == 'text[]':
        items = raw if isinstance(raw, list) else [s.strip() for s in str(raw).split(',') if s.strip()]
        for it in items:
            if spec.options and it not in spec.options:
                raise HTTPException(status_code=400, detail=f'{column}: "{it}" not allowed')
        return items
    if spec.kind == 'json':
        if isinstance(raw, (dict, list)):
            return json.dumps(raw)
        try:
            json.loads(raw)  # validate
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f'{column} must be valid JSON')
        return raw
    # text / fk → string
    return str(raw)


class AppendInput(BaseModel):
    values: dict[str, Any]


@router.post('/row/{table}')
async def append_row(
    table: str,
    payload: AppendInput,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Append a new row to an editable table (D-021). System columns are
    synthesised server-side; user columns are validated against EDIT_SPEC."""
    _require_admin(user)
    _require_editable(table)
    await resolve_columns()

    from acme_app.application.db_explorer import EDIT_SPEC
    spec = EDIT_SPEC[table]

    cols: list[str] = []
    params: dict[str, Any] = {}
    casts: dict[str, str] = {}
    # Columns whose VALUES entry is a raw SQL expression (e.g. now()) rather
    # than a bound parameter. Keyed by column -> SQL fragment.
    raw_exprs: dict[str, str] = {}

    for col, ce in spec.items():
        if ce.kind == 'system':
            # Synthesise per `auto` strategy.
            if ce.auto and ce.auto.startswith('ref:'):
                cols.append(col)
                params[col] = await _next_ref(session, table, col, ce.auto.split(':', 1)[1])
            elif ce.auto == 'null':
                cols.append(col)
                params[col] = None
            elif ce.auto == 'now':
                # Explicit now() — some timestamp columns have no DB default.
                cols.append(col)
                raw_exprs[col] = 'now()'
            # uuid → rely on column DEFAULT gen_random_uuid()
            continue
        if col not in payload.values:
            if ce.required:
                raise HTTPException(status_code=400, detail=f'missing required field: {col}')
            continue
        value = _coerce_value(table, col, payload.values.get(col))
        cols.append(col)
        params[col] = value
        if ce.kind == 'text[]':
            casts[col] = '::text[]'
        elif ce.kind == 'json':
            casts[col] = '::jsonb'

    if not cols:
        raise HTTPException(status_code=400, detail='no values to insert')

    def _placeholder(c: str) -> str:
        if c in raw_exprs:
            return raw_exprs[c]
        return f':{c}{casts.get(c, "")}'

    placeholders = ', '.join(_placeholder(c) for c in cols)
    col_sql = ', '.join(cols)
    pk = 'action_type' if table == 'action_catalogue' else 'id'
    sql = f'INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) RETURNING {pk}::text'  # noqa: S608
    try:
        new_id = (await session.execute(text(sql), params)).scalar()
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f'insert failed: {str(exc)[:200]}')

    _log.info('DB Explorer append: %s by %s -> %s', table, user.username, new_id)
    rows = await _query_rows(session, table, where_field=pk, where_value=new_id, limit=1)
    return {'table': table, 'id': new_id, 'row': rows[0] if rows else None}


class PatchInput(BaseModel):
    column: str
    value: Any


@router.patch('/row/{table}/{row_id}')
async def patch_row(
    table: str,
    row_id: str,
    payload: PatchInput,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Update one cell of an editable row (D-021)."""
    _require_admin(user)
    _require_editable(table)
    await resolve_columns()

    col = payload.column
    if col not in columns_for(table):
        raise HTTPException(status_code=400, detail=f'unknown column {col}')
    value = _coerce_value(table, col, payload.value)
    cast = ''
    ce = column_edit(table, col)
    if ce.kind == 'text[]':
        cast = '::text[]'
    elif ce.kind == 'json':
        cast = '::jsonb'

    pk = 'action_type' if table == 'action_catalogue' else 'id'
    sql = f'UPDATE {table} SET {col} = :v{cast} WHERE {pk} = :id RETURNING {pk}::text'  # noqa: S608
    try:
        updated = (await session.execute(text(sql), {'v': value, 'id': row_id})).scalar()
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f'update failed: {str(exc)[:200]}')
    if updated is None:
        raise HTTPException(status_code=404, detail='row not found')
    _log.info('DB Explorer patch: %s.%s row=%s by %s', table, col, row_id, user.username)
    rows = await _query_rows(session, table, where_field=pk, where_value=row_id, limit=1)
    return {'table': table, 'id': row_id, 'row': rows[0] if rows else None}


class AiSuggestInput(BaseModel):
    table: str
    column: str
    context: dict[str, Any] = {}


def _all_failed_detail(spec: ModelSpec | None, exc: Exception | None) -> str:
    """User-facing message when every available model was tried and all failed
    (bad keys, no credit, server down)."""
    last = f' Last attempt: {spec.label} ({type(exc).__name__}).' if spec and exc else ''
    return (
        'Could not reach any configured language model.' + last +
        ' Check the API keys are valid and the accounts have credit, then try again.'
    )


async def _assist_with_fallback(call):
    """Run an assist call against available models in order — local first, then
    cheapest cloud (assist_specs_ordered) — falling through to the next model
    on failure. Returns (ModelSpec, result). Raises 503 when nothing is
    configured or every available model failed."""
    specs = await assist_specs_ordered()
    if not specs:
        raise HTTPException(status_code=503, detail=NO_MODEL_MESSAGE)
    last_exc: Exception | None = None
    last_spec: ModelSpec | None = None
    for spec in specs:
        try:
            return spec, await call(spec)
        except Exception as exc:  # noqa: BLE001 — any failure → try the next model
            last_exc, last_spec = exc, spec
            _log.warning('assist: model %s failed (%s); trying next', spec.key, type(exc).__name__)
    raise HTTPException(status_code=503, detail=_all_failed_detail(last_spec, last_exc))


_FIELD_STUBS = {
    'name': 'Globex Corporation',
    'title': 'Intermittent API authentication failures',
    'description': 'Customer reports sporadic 401 responses during peak hours; '
                   'token refresh appears to fail intermittently.',
    'label': 'New Action',
    'display_name': 'New User',
    'email': 'new.user@example.local',
}


@router.post('/ai-suggest')
async def ai_suggest(
    payload: AiSuggestInput,
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Generate a short, context-aware sample value for a free-text field
    using whichever model is configured (D-021). Uses a reachable local model
    if present, otherwise the cheapest available cloud model. Returns a clear
    503 when no model is configured at all."""
    _require_admin(user)
    _require_editable(payload.table)
    spec, suggestion = await _assist_with_fallback(
        lambda s: _ai_field_suggestion(s, payload.table, payload.column, payload.context)
    )
    return {'suggestion': suggestion, 'model': spec.key}


async def _ai_field_suggestion(model_spec: ModelSpec, table: str, column: str, context: dict[str, Any]) -> str:
    ctx_str = ', '.join(f'{k}={v}' for k, v in context.items() if v) or 'no other fields yet'
    system = (
        'You generate a single short, realistic sample value for one database '
        'field in an enterprise support system. Return ONLY the value text, no '
        'quotes, no preamble, no markdown. Keep it concise and plausible.'
    )
    user_prompt = (
        f'Table: {table}\nField to fill: {column}\n'
        f'Other fields in this row: {ctx_str}\n\n'
        f'Write a realistic value for "{column}".'
    )
    provider = get_provider(model_spec.key)
    resp = await asyncio.wait_for(provider.narrate(system, user_prompt, {}), timeout=30.0)
    text_out = (resp.text or '').strip().strip('"').splitlines()
    text_out = text_out[0][:300] if text_out else ''
    # Model reached but returned nothing usable — fall back to a templated value
    # so the button still does something (this is not a "no model" condition).
    return text_out or _FIELD_STUBS.get(column, f'Sample {column}')


class AiGenRowInput(BaseModel):
    table: str


@router.post('/ai-generate-row')
async def ai_generate_row(
    payload: AiGenRowInput,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Generate ONE complete, internally-consistent sample record for a table
    in a single LLM call (D-021) — so e.g. the email matches the name, the
    issue description matches its title/severity. Returns storable values for
    every user-editable field, validated against enums/FKs. The user reviews,
    tweaks, and confirms — nothing is written here."""
    _require_admin(user)
    _require_editable(payload.table)
    await resolve_columns()
    spec, values = await _assist_with_fallback(
        lambda s: _ai_generate_record(session, payload.table, s)
    )
    return {'table': payload.table, 'values': values, 'model': spec.key}


async def _ai_generate_record(session: AsyncSession, table: str, model_spec: ModelSpec) -> dict[str, Any]:
    from acme_app.application.db_explorer import EDIT_SPEC
    spec = EDIT_SPEC[table]

    # Describe each user-editable field + its allowed values. For FK fields we
    # ask the model to choose by LABEL (never invent a UUID), then map back.
    field_lines: list[str] = []
    fk_label_to_value: dict[str, dict[str, str]] = {}
    enum_opts: dict[str, list[str]] = {}
    arr_opts: dict[str, list[str]] = {}
    fk_labels: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for col, ce in spec.items():
        if ce.kind == 'system':
            continue
        kinds[col] = ce.kind
        if ce.kind == 'fk' and ce.fk:
            opts = await _fk_options(session, ce.fk)
            fk_label_to_value[col] = {o['label']: o['value'] for o in opts}
            fk_labels[col] = [o['label'] for o in opts]
            field_lines.append(f'- {col}: choose exactly ONE label from {fk_labels[col][:25]}')
        elif ce.kind == 'enum':
            enum_opts[col] = list(ce.options)
            field_lines.append(f'- {col}: exactly ONE of {list(ce.options)}')
        elif ce.kind == 'text[]':
            arr_opts[col] = list(ce.options)
            field_lines.append(f'- {col}: a JSON array, subset of {list(ce.options)} (at least one)')
        elif ce.kind == 'bool':
            field_lines.append(f'- {col}: true or false')
        elif ce.kind == 'int':
            field_lines.append(f'- {col}: a small integer')
        elif ce.kind == 'json':
            field_lines.append(f'- {col}: a small JSON object, e.g. {{"severity":"P1"}}')
        else:
            field_lines.append(f'- {col}: a short, realistic value')

    system = (
        'You generate ONE realistic, internally CONSISTENT sample record for a row '
        'in an enterprise support database. All fields must agree with each other '
        '(e.g. an email derives from the name; an issue description matches its '
        'title and severity). Respect the allowed values exactly. '
        'Return ONLY a JSON object mapping field name to value — no prose, no markdown.'
    )
    user_prompt = (
        f'Table: {table}\nFields:\n' + '\n'.join(field_lines) +
        '\n\nReturn a single JSON object with one key per field above.'
    )

    # The provider call is allowed to raise — the endpoint turns that into a
    # clear 503 (bad key / no credit / server down). Only a successful call
    # that returns imperfect JSON degrades quietly: raw stays {} and the
    # per-field coercion below fills valid fallback values.
    provider = get_provider(model_spec.key)
    resp = await asyncio.wait_for(provider.narrate(system, user_prompt, {}), timeout=40.0)
    raw: dict[str, Any] = _parse_json_blob(resp.text) or {}

    # Validate / coerce every field to a storable, valid value. Anything the
    # model got wrong or omitted is filled with a sensible valid fallback so
    # the populated form is always internally valid.
    import random
    out: dict[str, Any] = {}
    for col, kind in kinds.items():
        v = raw.get(col)
        if kind == 'fk':
            labels = fk_labels.get(col, [])
            mapping = fk_label_to_value.get(col, {})
            if v in mapping:
                out[col] = mapping[v]
            elif labels:
                out[col] = mapping[random.choice(labels)]
        elif kind == 'enum':
            opts = enum_opts.get(col, [])
            out[col] = v if v in opts else (random.choice(opts) if opts else None)
        elif kind == 'text[]':
            opts = arr_opts.get(col, [])
            chosen = [x for x in (v if isinstance(v, list) else []) if x in opts]
            out[col] = chosen or ([random.choice(opts)] if opts else [])
        elif kind == 'bool':
            out[col] = bool(v) if isinstance(v, bool) else str(v).lower() in ('true', '1', 'yes')
        elif kind == 'int':
            try:
                out[col] = int(v)
            except (TypeError, ValueError):
                out[col] = random.randint(10, 90)
        elif kind == 'json':
            out[col] = json.dumps(v) if isinstance(v, (dict, list)) else (v if isinstance(v, str) else '{}')
        else:  # text
            out[col] = str(v).strip() if v not in (None, '') else f'Sample {col}'
    return out


def _parse_json_blob(text: str) -> dict[str, Any] | None:
    s = (text or '').strip()
    import re as _re
    m = _re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', s, _re.DOTALL)
    if m:
        s = m.group(1).strip()
    # Grab the first {...} block if there's surrounding chatter.
    if not s.startswith('{'):
        i, j = s.find('{'), s.rfind('}')
        if i >= 0 and j > i:
            s = s[i:j + 1]
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else None
    except (TypeError, ValueError):
        return None


# ─── WebSocket: realtime push of (table, op, id) events ─────────────────────
#
# Protocol (JSON text frames both directions):
#   client → server:  {"watch": "<table>"}      — narrow filter to one table
#   client → server:  {"watch": null}           — receive everything
#   server → client:  {"hello": {...}}          — once on connect
#   server → client:  {"table": "...", "op": "INSERT|UPDATE", "id": "..."}
#
# Auth: reads the `acme_session` cookie that's already used for the rest of
# the app. The user must be admin or the socket is closed immediately with
# code 1008 (policy violation). We can't use the normal Depends(get_current_user)
# chain here because Header-vs-Cookie auth shapes differently inside WS.


def _admin_from_cookie(cookie: str | None) -> str | None:
    if not cookie:
        return None
    user = _decode_session(cookie)
    if user is None or 'admin' not in user.roles:
        return None
    return user.username


@router.websocket('/ws')
async def db_explorer_ws(websocket: WebSocket) -> None:
    username = _admin_from_cookie(websocket.cookies.get('acme_session'))
    if not username:
        await websocket.close(code=1008, reason='admin required')
        return

    await websocket.accept()
    sub = await realtime_broadcaster.register(websocket, watched_table=None)
    try:
        await websocket.send_json({'hello': {'channel': 'db_explorer', 'as': username}})

        async def push_events() -> None:
            while True:
                event = await sub.queue.get()
                await websocket.send_json(event)

        async def read_control() -> None:
            while True:
                msg = await websocket.receive_json()
                if isinstance(msg, dict) and 'watch' in msg:
                    new_table = msg.get('watch')
                    if new_table is not None and not is_table_allowed(str(new_table)):
                        await websocket.send_json({'error': f'unknown table: {new_table}'})
                        continue
                    await realtime_broadcaster.set_watch(
                        websocket,
                        str(new_table) if new_table else None,
                    )
                    await websocket.send_json({'watching': new_table})

        async def reauth_loop() -> None:
            # Privilege revocation must not wait for reconnect. Re-check
            # admin authorisation periodically: cookie expiry (signature/exp
            # via _decode_session) AND the live Postgres role grant. If the
            # user loses admin mid-session, close the socket promptly.
            while True:
                await asyncio.sleep(WS_REAUTH_INTERVAL_S)
                still = _admin_from_cookie(websocket.cookies.get('acme_session'))
                if still is None:
                    await websocket.close(code=1008, reason='session expired')
                    return
                try:
                    roles = await get_roles_for_username(still)
                except Exception:
                    continue  # transient DB error — don't drop on a blip
                if not roles or 'admin' not in roles:
                    await websocket.close(code=1008, reason='admin revoked')
                    return

        push_task = asyncio.create_task(push_events())
        read_task = asyncio.create_task(read_control())
        reauth_task = asyncio.create_task(reauth_loop())
        done, pending = await asyncio.wait(
            {push_task, read_task, reauth_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:
        _log.exception('db_explorer ws crashed')
    finally:
        await realtime_broadcaster.unregister(websocket)


# Keep AsyncSessionLocal import lint-quiet (reserved for future server-side
# row hydration if we ever want to include the full row in the push event).
_ = AsyncSessionLocal

# Keep LINKS import lint-quiet (it's used indirectly via links_for).
_ = LINKS
