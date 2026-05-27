from pydantic import BaseModel


class PlanStep(BaseModel):
    step_type: str
    name: str
    arguments: dict
    rationale: str = ''


class AgentPlan(BaseModel):
    intent: str
    requires_clarification: bool = False
    clarification_question: str | None = None
    steps: list[PlanStep]
    write_requested: bool = False
