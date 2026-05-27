from pydantic import BaseModel


class TraceSummary(BaseModel):
    trace_ref: str
    status: str
    badge: str
    total_tokens: int
    cost_usd: float
    latency_ms: int
