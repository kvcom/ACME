from pydantic import BaseModel


class TraceSummary(BaseModel):
    trace_ref: str
    status: str
    badge: str
    intent: str | None = None
    total_tokens: int
    cost_usd: float
    latency_ms: int


class TraceEventDTO(BaseModel):
    event_type: str
    event_name: str
    payload: dict
    latency_ms: int | None = None
    status: str
    created_at: str
