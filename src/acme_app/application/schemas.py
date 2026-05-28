from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    step_type: str = Field(pattern=r'^(tool|skill)$')
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ''


class AgentPlan(BaseModel):
    intent: str
    requires_clarification: bool = False
    clarification_question: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)
    write_requested: bool = False
    narration_kind: str = 'general'
    adversarial_flags: list[str] = Field(default_factory=list)


class ProposedActionDTO(BaseModel):
    action_type: str
    title: str
    description: str
    priority: str
    issue_ref: str
    customer_id: str | None = None
    customer_name: str | None = None
    rationale: str = ''
    evidence: list[str] = Field(default_factory=list)
    due_at: str | None = None
    confirmation_token: str
    idempotency_key: str
    trace_ref: str
    expires_at: int


class ChatResponse(BaseModel):
    trace_ref: str
    intent: str | None = None
    answer: str
    badge: str
    evidence: list[str] = Field(default_factory=list)
    proposed_action: ProposedActionDTO | None = None
    tools_called: list[str] = Field(default_factory=list)
    skills_invoked: list[str] = Field(default_factory=list)
    risk_level: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    cost_usd: float = 0.0
    total_tokens: int = 0
    latency_ms: int = 0
    provider: str = 'auto'
    model: str = ''
    plan_model: str = ''
    narration_model: str = ''
    route: str | None = None
    route_confidence: float | None = None
    route_source: str | None = None
    used_external_llm: bool = False
    query_redacted: str = ''
