"""Realtime change broadcaster for the DB Explorer (D-018).

A single asyncpg connection holds a Postgres `LISTEN db_explorer` and fans
incoming notifications out to every WebSocket the admin DB Explorer has
opened. Per-socket subscriptions narrow the firehose: a socket says
"watch: customers" and only receives events for that table.

Lifecycle:
    on app startup → `await broadcaster.start()` opens the LISTEN connection
                     and spawns the background reader task
    on app shutdown → `await broadcaster.stop()` cancels the reader and
                      closes the connection

Failure handling:
    The reader task auto-reconnects with exponential backoff if the
    connection drops (e.g. DB restart). Clients see no event during the
    outage but reconnect transparently afterwards.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from fastapi import WebSocket

InternalHandler = Callable[[dict[str, Any]], Awaitable[None]]

from acme_app.config import settings


_log = logging.getLogger(__name__)

CHANNEL = 'db_explorer'


def _asyncpg_dsn() -> str:
    """asyncpg wants a plain `postgresql://` DSN (no `+asyncpg`)."""
    url = settings.database_url
    return url.replace('postgresql+asyncpg://', 'postgresql://', 1)


@dataclass
class Subscriber:
    socket: WebSocket
    watched_table: str | None = None
    # Per-socket queue so a slow client doesn't block delivery to others.
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))


class Broadcaster:
    """Postgres LISTEN → WebSocket fan-out for the DB Explorer."""

    def __init__(self) -> None:
        self._conn: asyncpg.Connection | None = None
        self._reader_task: asyncio.Task | None = None
        self._subscribers: dict[WebSocket, Subscriber] = {}
        # In-process post-fan-out handlers. Used for things like the
        # action_catalogue refresh hook (D-019) that need to react to DB
        # changes server-side, not just push them to the browser.
        self._internal_handlers: list[InternalHandler] = []
        self._lock = asyncio.Lock()
        self._stopping = False

    def on_event(self, handler: InternalHandler) -> None:
        """Register a server-side handler called after every event fan-out."""
        self._internal_handlers.append(handler)

    async def start(self) -> None:
        """Open the listener connection and spawn the reader task."""
        self._stopping = False
        self._reader_task = asyncio.create_task(self._reader_loop())
        _log.info('Realtime broadcaster started')

    async def stop(self) -> None:
        """Cancel the reader task and close the listener connection."""
        self._stopping = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._conn is not None and not self._conn.is_closed():
            try:
                await self._conn.remove_listener(CHANNEL, self._on_notify)
            except Exception:
                pass
            await self._conn.close()
        self._conn = None
        _log.info('Realtime broadcaster stopped')

    async def register(self, socket: WebSocket, watched_table: str | None = None) -> Subscriber:
        async with self._lock:
            sub = Subscriber(socket=socket, watched_table=watched_table)
            self._subscribers[socket] = sub
        return sub

    async def unregister(self, socket: WebSocket) -> None:
        async with self._lock:
            self._subscribers.pop(socket, None)

    async def set_watch(self, socket: WebSocket, table: str | None) -> None:
        async with self._lock:
            sub = self._subscribers.get(socket)
            if sub:
                sub.watched_table = table

    # ── internals ────────────────────────────────────────────────────────────

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        """Called by asyncpg from the listener-connection task.

        Schedules the actual fan-out asynchronously so the asyncpg callback
        path stays non-blocking.
        """
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            _log.warning('db_explorer notify with non-JSON payload: %r', payload[:120])
            return
        asyncio.create_task(self._fan_out(event))

    async def _fan_out(self, event: dict[str, Any]) -> None:
        table = event.get('table')
        async with self._lock:
            targets = [
                sub for sub in self._subscribers.values()
                if sub.watched_table is None or sub.watched_table == table
            ]
        for sub in targets:
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                _log.warning('Subscriber queue full, dropping event for socket %s', id(sub.socket))
        # In-process handlers (action_catalogue refresh, etc.). These run
        # after WS delivery so browser clients don't wait on slow server-side
        # reactions. Failures are isolated per handler.
        for handler in self._internal_handlers:
            try:
                await handler(event)
            except Exception:
                _log.exception('internal event handler failed')

    async def _reader_loop(self) -> None:
        """Hold a long-lived LISTEN connection and reconnect on failure."""
        backoff = 1.0
        while not self._stopping:
            try:
                self._conn = await asyncpg.connect(dsn=_asyncpg_dsn())
                await self._conn.add_listener(CHANNEL, self._on_notify)
                _log.info('LISTEN %s established', CHANNEL)
                backoff = 1.0
                # Keep the coroutine alive while asyncpg drives the listener
                # callback in the background. We just need to not return.
                while not self._stopping:
                    await asyncio.sleep(5.0)
                    if self._conn.is_closed():
                        raise ConnectionError('listener connection closed')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning('LISTEN connection lost (%s); reconnecting in %.1fs',
                             type(exc).__name__, backoff)
                if self._conn is not None and not self._conn.is_closed():
                    try:
                        await self._conn.close()
                    except Exception:
                        pass
                self._conn = None
                if self._stopping:
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


# Module-level singleton — created once, started/stopped from app lifespan.
broadcaster = Broadcaster()
