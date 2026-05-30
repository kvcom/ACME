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

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.application.realtime import broadcaster as realtime_broadcaster
from acme_app.auth.current_user import _decode_session
from acme_app.infrastructure.db.session import AsyncSessionLocal

from acme_app.application.db_explorer import (
    DEFAULT_ORDER,
    EXPLORER_TABLES,
    LINKS,
    TABLE_COLUMNS,
    expandable_fields,
    is_table_allowed,
    links_for,
)
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.infrastructure.db.session import get_db_session


router = APIRouter(prefix='/db-explorer', tags=['db-explorer'])
_log = logging.getLogger(__name__)

ROW_LIMIT_DEFAULT = 100
ROW_LIMIT_MAX = 500


def _require_admin(user: CurrentUser) -> CurrentUser:
    if 'admin' not in user.roles:
        raise HTTPException(status_code=403, detail='Admin role required')
    return user


def _validate_table(table: str) -> str:
    if not is_table_allowed(table):
        raise HTTPException(status_code=400, detail=f'unknown table: {table}')
    return table


def _validate_field(table: str, field: str) -> str:
    if field not in TABLE_COLUMNS.get(table, []):
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


async def _query_rows(
    session: AsyncSession,
    table: str,
    *,
    where_field: str | None = None,
    where_value: Any = None,
    limit: int = ROW_LIMIT_DEFAULT,
) -> list[dict[str, Any]]:
    cols = TABLE_COLUMNS[table]
    col_list = ', '.join(cols)  # safe — validated against allow-list
    where_sql = ''
    params: dict[str, Any] = {'n': min(limit, ROW_LIMIT_MAX)}
    if where_field is not None:
        _validate_field(table, where_field)
        where_sql = f'WHERE {where_field} = :v'
        params['v'] = where_value
    order = DEFAULT_ORDER.get(table, cols[0])
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
    # without a separate metadata fetch round-trip.
    metadata = {
        'tables': EXPLORER_TABLES,
        'columns': TABLE_COLUMNS,
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
    return {
        'tables': EXPLORER_TABLES,
        'columns': TABLE_COLUMNS,
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
        'columns': TABLE_COLUMNS[table],
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
            'columns': TABLE_COLUMNS[link.target],
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
    return {'table': table, 'columns': TABLE_COLUMNS[table], 'row': rows[0],
            'links': expandable_fields(table)}


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

        push_task = asyncio.create_task(push_events())
        read_task = asyncio.create_task(read_control())
        done, pending = await asyncio.wait(
            {push_task, read_task}, return_when=asyncio.FIRST_COMPLETED,
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
