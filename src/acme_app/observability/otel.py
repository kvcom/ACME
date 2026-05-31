"""OpenTelemetry bootstrap.

Best-effort: if the collector is unreachable the SDK drops spans silently and
the app continues. trace_events are persisted to PostgreSQL regardless, so the
custom trace viewer never depends on the OTel pipeline being up.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from acme_app.config import settings

_log = logging.getLogger(__name__)
_initialised = False
_instrumented = False

_request_counter: Any = None
_error_counter: Any = None
_token_counter: Any = None
_cost_counter: Any = None
_request_latency_histogram: Any = None
_llm_latency_histogram: Any = None
_tool_latency_histogram: Any = None
_tool_call_counter: Any = None
_tool_call_latency_histogram: Any = None


def setup_otel() -> None:
    global _initialised
    if _initialised:
        return
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        resource = Resource.create({'service.name': settings.otel_service_name})

        tracer_provider = TracerProvider(resource=resource)
        span_exporter = OTLPSpanExporter(endpoint=f'{settings.otel_exporter_otlp_endpoint}/v1/traces')
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f'{settings.otel_exporter_otlp_endpoint}/v1/metrics'),
            export_interval_millis=5000,
        )
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))
        _build_metric_instruments()

        logger_provider = LoggerProvider(resource=resource)
        log_exporter = OTLPLogExporter(endpoint=f'{settings.otel_exporter_otlp_endpoint}/v1/logs')
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        handler = LoggingHandler(level=logging.WARNING, logger_provider=logger_provider)
        logging.getLogger().addHandler(handler)
    except Exception as exc:  # collector unreachable, dependency missing
        _log.warning('OpenTelemetry setup failed (%s); telemetry will be no-ops', exc)
    _initialised = True


def _build_metric_instruments() -> None:
    global _request_counter, _error_counter, _token_counter, _cost_counter
    global _request_latency_histogram, _llm_latency_histogram, _tool_latency_histogram
    global _tool_call_counter, _tool_call_latency_histogram

    meter = metrics.get_meter('acme.agent')
    _request_counter = meter.create_counter(
        'acme_agent_requests_total',
        description='Agent requests completed by the orchestrator',
    )
    _error_counter = meter.create_counter(
        'acme_agent_errors_total',
        description='Agent requests that completed with an error-like status',
    )
    _token_counter = meter.create_counter(
        'acme_agent_tokens_total',
        unit='tokens',
        description='Prompt plus completion tokens consumed by agent requests',
    )
    _cost_counter = meter.create_counter(
        'acme_agent_cost_usd_total',
        unit='USD',
        description='Estimated LLM cost accumulated by agent requests',
    )
    _request_latency_histogram = meter.create_histogram(
        'acme_agent_request_latency_ms',
        unit='ms',
        description='End-to-end agent request latency',
    )
    _llm_latency_histogram = meter.create_histogram(
        'acme_agent_llm_latency_ms',
        unit='ms',
        description='LLM latency per agent request',
    )
    _tool_latency_histogram = meter.create_histogram(
        'acme_agent_tool_latency_ms',
        unit='ms',
        description='Total tool latency per agent request',
    )
    _tool_call_counter = meter.create_counter(
        'acme_agent_tool_calls_total',
        description='MCP tool calls made by the agent',
    )
    _tool_call_latency_histogram = meter.create_histogram(
        'acme_agent_tool_call_latency_ms',
        unit='ms',
        description='Latency for individual MCP tool calls',
    )


def instrument_app(app: FastAPI) -> None:
    """Attach OTel auto-instrumentation without making startup depend on it."""
    global _instrumented
    if _instrumented:
        return
    instrumentors = (
        ('FastAPI', lambda: __import__(
            'opentelemetry.instrumentation.fastapi',
            fromlist=['FastAPIInstrumentor'],
        ).FastAPIInstrumentor.instrument_app(app)),
        ('HTTPX', lambda: __import__(
            'opentelemetry.instrumentation.httpx',
            fromlist=['HTTPXClientInstrumentor'],
        ).HTTPXClientInstrumentor().instrument()),
        ('asyncpg', lambda: __import__(
            'opentelemetry.instrumentation.asyncpg',
            fromlist=['AsyncPGInstrumentor'],
        ).AsyncPGInstrumentor().instrument()),
    )
    for name, install in instrumentors:
        try:
            install()
        except Exception as exc:
            _log.warning('OpenTelemetry %s auto-instrumentation skipped (%s)', name, exc)
    _instrumented = True


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


def _record(instrument: Any, value: int | float, attrs: dict[str, Any]) -> None:
    if instrument is None:
        return
    try:
        if hasattr(instrument, 'record'):
            instrument.record(value, attrs)
        else:
            instrument.add(value, attrs)
    except Exception:
        return


def record_agent_request(
    *,
    role: str,
    intent: str | None,
    status: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    estimated_cost_usd: float,
    total_latency_ms: int,
    llm_latency_ms: int,
    tool_latency_ms: int,
) -> None:
    attrs = {
        'user.role': role or 'unknown',
        'agent.intent': intent or 'unknown',
        'agent.status': status or 'unknown',
        'llm.provider': provider or 'unknown',
        'llm.model': model or 'unknown',
    }
    total_tokens = max(0, prompt_tokens) + max(0, completion_tokens)
    _record(_request_counter, 1, attrs)
    if status and status.lower() in {'error', 'permission denied', 'adversarial input blocked'}:
        _record(_error_counter, 1, attrs)
    _record(_token_counter, total_tokens, attrs)
    _record(_cost_counter, max(0.0, estimated_cost_usd), attrs)
    _record(_request_latency_histogram, max(0, total_latency_ms), attrs)
    _record(_llm_latency_histogram, max(0, llm_latency_ms), attrs)
    _record(_tool_latency_histogram, max(0, tool_latency_ms), attrs)


def record_tool_call(*, tool_name: str, status: str, latency_ms: int) -> None:
    attrs = {'tool.name': tool_name, 'tool.status': status or 'unknown'}
    _record(_tool_call_counter, 1, attrs)
    _record(_tool_call_latency_histogram, max(0, latency_ms), attrs)
