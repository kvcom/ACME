"""OpenTelemetry bootstrap.

Best-effort: if the collector is unreachable the SDK drops spans silently and
the app continues. trace_events are persisted to PostgreSQL regardless, so the
custom trace viewer never depends on the OTel pipeline being up.
"""
from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from acme_app.config import settings


_log = logging.getLogger(__name__)
_initialised = False


def setup_otel() -> None:
    global _initialised
    if _initialised:
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider = TracerProvider(resource=Resource.create({'service.name': settings.otel_service_name}))
        exporter = OTLPSpanExporter(endpoint=f'{settings.otel_exporter_otlp_endpoint}/v1/traces')
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    except Exception as exc:  # collector unreachable, dependency missing
        _log.warning('OpenTelemetry setup failed (%s); spans will be no-ops', exc)
    _initialised = True


def get_tracer(name: str = 'acme.agent') -> trace.Tracer:
    return trace.get_tracer(name)


def current_trace_id_hex() -> str | None:
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if not ctx or not ctx.is_valid:
        return None
    return f'{ctx.trace_id:032x}'


def set_span_attributes(attrs: dict[str, Any]) -> None:
    span = trace.get_current_span()
    if span is None:
        return
    for k, v in attrs.items():
        try:
            span.set_attribute(k, v)
        except Exception:
            continue
