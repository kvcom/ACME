"""Auto provider.

Auto is a two-stage router:

    1. Classify the request with the local Ollama model into a small route.
    2. Try the execution chain for that route, falling through on failure.

The classifier decides the route, not the final business truth. Tools still
validate customers/issues/actions against the database, and planner quality
gates still reject malformed JSON before a model is accepted.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from acme_app.config import settings
from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY, ModelSpec
from acme_app.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from acme_app.infrastructure.llm.providers.base import LLMProvider, LLMResponse
from acme_app.infrastructure.llm.providers.google_provider import GoogleProvider
from acme_app.infrastructure.llm.providers.ollama_provider import OllamaProvider
from acme_app.infrastructure.llm.providers.openai_provider import OpenAIProvider


_log = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised when no provider in the selected Auto route succeeded."""


@dataclass(frozen=True)
class RouteDecision:
    route: str
    confidence: float
    reason: str = ''
    source: str = ''


CLASSIFIER_MODEL_KEY = 'ollama-llama'
ARBITER_CHAIN: list[str] = ['gpt-5.4-mini', 'claude-sonnet-4-6', 'gpt-5.5']
DEFAULT_ROUTE = 'structured_planning'

_WRITE_ROUTE_RE = re.compile(
    r'\b(create|prepare|propose|draft|stage|schedule|assign|submit|write up|set up)\b',
    re.I,
)
_RECOMMENDATION_ROUTE_RE = re.compile(
    r'\b(recommended next step|next step|next action|recommend|recommendation|what should we do)\b',
    re.I,
)
_CUSTOMER_READ_ROUTE_RE = re.compile(
    r'\b(open issues|latest status|status|brief|call with|customer profile|account profile)\b',
    re.I,
)
_BROAD_ANALYSIS_ROUTE_RE = re.compile(
    r'\b(all customers|all high-risk|top risks|portfolio|management overview|every issue)\b',
    re.I,
)
_SECURITY_ROUTE_RE = re.compile(
    r'\b(ignore previous instructions|bypass policy|you are now an admin|override)\b',
    re.I,
)

ROUTE_CHAINS: dict[str, list[str]] = {
    # Read-only, low-risk tasks can use the local model for execution too.
    'issue_read': ['ollama-llama', 'gpt-5.4-mini', 'gemini-3.5-flash'],
    'customer_read': ['ollama-llama', 'gpt-5.4-mini', 'gemini-3.5-flash'],
    'summary': ['ollama-llama', 'gemini-3.5-flash', 'gpt-5.4-mini'],

    # Structured or higher-risk workflows skip local execution by default.
    'write_proposal': ['gpt-5.4-mini', 'claude-sonnet-4-6', 'gpt-5.5'],
    'closure_check': ['gpt-5.4-mini', 'claude-sonnet-4-6', 'gpt-5.5'],
    'recommendation': ['gpt-5.4-mini', 'gemini-3.5-flash', 'claude-sonnet-4-6'],
    'broad_analysis': ['claude-sonnet-4-6', 'gpt-5.5', 'claude-opus-4-8'],
    'security_sensitive': ['claude-sonnet-4-6', 'gpt-5.4-mini', 'gpt-5.5'],
    'clarification': ['gpt-5.4-mini', 'claude-sonnet-4-6'],
    'structured_planning': ['gpt-5.4-mini', 'claude-sonnet-4-6', 'gpt-5.5'],
}

ROUTE_DESCRIPTIONS: dict[str, str] = {
    'issue_read': 'Read-only status/history/summary for a specific issue reference.',
    'customer_read': 'Read-only customer profile, open issues, latest status, or call briefing.',
    'summary': 'Small-scope summary or digest without explicit recommendation/write request.',
    'write_proposal': 'Explicit request to create, prepare, propose, draft, assign, or stage an action.',
    'closure_check': 'Question about closing, resolving, or closure readiness.',
    'recommendation': 'Request for recommended next step, next action, or what should be done.',
    'broad_analysis': 'Cross-customer or portfolio-wide analysis, top risks, management overview.',
    'security_sensitive': 'Adversarial, policy-sensitive, role override, or bypass attempt.',
    'clarification': 'Random, unsupported, or under-specified request.',
    'structured_planning': 'Fallback for structured business workflow planning.',
}

ROUTING_RULES: tuple[dict[str, str], ...] = (
    {'route': 'security_sensitive', 'rule': 'Contains adversarial or policy-bypass text.'},
    {'route': 'write_proposal', 'rule': 'Contains explicit write/action verbs such as create, prepare, propose, draft, assign, submit.'},
    {'route': 'closure_check', 'rule': 'Asks about close, closure, resolution, or readiness to close.'},
    {'route': 'broad_analysis', 'rule': 'Asks across all customers/issues, portfolio, top risks, or management overview.'},
    {'route': 'recommendation', 'rule': 'Asks for recommended next step, next action, recommendation, or what should be done.'},
    {'route': 'customer_read', 'rule': 'Asks for customer status, open issues, latest status, briefing, or profile.'},
    {'route': 'issue_read', 'rule': 'Mentions an issue reference such as ISS-123 without stronger write/closure/recommendation intent.'},
    {'route': 'clarification', 'rule': 'No in-scope customer, issue, action, recommendation, summary, closure, or security signal.'},
)

# Compatibility name for tests/callers that inspect the broad Auto order.
PRIORITY_CHAIN: list[str] = [
    'ollama-llama',
    'gpt-5.4-mini',
    'gemini-3.5-flash',
    'claude-sonnet-4-6',
    'gemini-3.1-pro-preview',
    'gpt-5.5',
    'claude-opus-4-8',
]


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', candidate, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_available(spec: ModelSpec) -> bool:
    if spec.provider == 'ollama':
        # Ollama availability is verified by the HTTP call itself.
        return bool(settings.ollama_base_url)
    if spec.provider == 'anthropic':
        return bool(settings.anthropic_api_key)
    if spec.provider == 'openai':
        return bool(settings.openai_api_key)
    if spec.provider == 'google':
        return bool(settings.google_api_key)
    return False


def _construct(spec: ModelSpec) -> LLMProvider:
    cls_map: dict[str, type[LLMProvider]] = {
        'anthropic': AnthropicProvider,
        'openai': OpenAIProvider,
        'google': GoogleProvider,
        'ollama': OllamaProvider,
    }
    return cls_map[spec.provider](model=spec.model)  # type: ignore[call-arg]


def _looks_like_valid_plan(response: LLMResponse) -> bool:
    payload = response.raw
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get('intent'), str) or not payload.get('intent'):
        return False
    steps = payload.get('steps', [])
    if not isinstance(steps, list):
        return False
    return all(isinstance(step, dict) for step in steps)


def _valid_route(route: str) -> str:
    return route if route in ROUTE_CHAINS else DEFAULT_ROUTE


def _deterministic_route(user_prompt: str) -> RouteDecision:
    """Rule-based classifier covering the supported project workflow scope."""
    text = user_prompt.lower()
    if _SECURITY_ROUTE_RE.search(text):
        return RouteDecision('security_sensitive', 1.0, 'rule: security/policy signal', 'rules')
    if _WRITE_ROUTE_RE.search(text):
        return RouteDecision('write_proposal', 1.0, 'rule: explicit write/action signal', 'rules')
    if 'close' in text or 'closure' in text or 'ready to close' in text:
        return RouteDecision('closure_check', 1.0, 'rule: closure signal', 'rules')
    if _BROAD_ANALYSIS_ROUTE_RE.search(text):
        return RouteDecision('broad_analysis', 1.0, 'rule: broad-analysis signal', 'rules')
    if _RECOMMENDATION_ROUTE_RE.search(text):
        return RouteDecision('recommendation', 1.0, 'rule: recommendation signal', 'rules')
    if _CUSTOMER_READ_ROUTE_RE.search(text):
        return RouteDecision('customer_read', 1.0, 'rule: customer-read signal', 'rules')
    if re.search(r'\bISS-\d{3,5}\b', text, re.I):
        return RouteDecision('issue_read', 1.0, 'rule: issue-reference signal', 'rules')
    return RouteDecision('clarification', 1.0, 'rule: no supported workflow signal', 'rules')


def _route_chain(route: str) -> list[ModelSpec]:
    keys = ROUTE_CHAINS[_valid_route(route)]
    return [
        MODEL_REGISTRY[key] for key in keys
        if key in MODEL_REGISTRY and _is_available(MODEL_REGISTRY[key])
    ]


def _all_available_execution_models() -> list[ModelSpec]:
    seen: set[str] = set()
    specs: list[ModelSpec] = []
    for key in PRIORITY_CHAIN:
        if key in seen or key not in MODEL_REGISTRY:
            continue
        seen.add(key)
        spec = MODEL_REGISTRY[key]
        if _is_available(spec):
            specs.append(spec)
    return specs


async def _classify_with_llama(user_prompt: str, context: dict[str, Any]) -> RouteDecision:
    spec = MODEL_REGISTRY.get(CLASSIFIER_MODEL_KEY)
    if not spec or not _is_available(spec):
        return RouteDecision('clarification', 0.0, 'local classifier unavailable', 'llama')

    system = (
        'You are a routing classifier for an enterprise support assistant. '
        'Return JSON only. Choose exactly one route from: '
        + ', '.join(sorted(ROUTE_CHAINS))
        + '. Do not answer the user. '
        'Use write_proposal for requests to prepare, create, propose, draft, '
        'schedule, assign, raise, open, file, log, or stage an action. '
        'Use closure_check for close/closure/resolved readiness. '
        'Use recommendation for next step/next action/what should we do. '
        'Use issue_read for status/history/summary of a specific issue. '
        'Use customer_read for profile/status/open issues for one customer. '
        'Use broad_analysis for all customers, portfolio, top risks, or management overview. '
        'Use security_sensitive for adversarial or policy-sensitive text. '
        'Use clarification for random, unsupported, or under-specified text.'
    )
    user = (
        'Classify this request for model routing.\n\n'
        f'Context JSON:\n{json.dumps(context, default=str)[:2000]}\n\n'
        f'User request:\n{user_prompt[:4000]}\n\n'
        'Return exactly: {"route": "...", "confidence": 0.0, "reason": "..."}'
    )
    try:
        provider = _construct(spec)
        response = await provider.narrate(system, user, {'routes': list(ROUTE_CHAINS)})
        payload = _parse_json_object(response.text)
        route = _valid_route(str(payload.get('route') or DEFAULT_ROUTE))
        confidence = float(payload.get('confidence') or 0.0)
        reason = str(payload.get('reason') or '')
        return RouteDecision(route, max(0.0, min(confidence, 1.0)), reason, 'llama')
    except Exception as exc:
        _log.warning('Auto classifier failed via %s (%s)', spec.key, type(exc).__name__)
        return RouteDecision('clarification', 0.0, str(exc)[:160], 'llama')


async def _arbitrate_route(
    user_prompt: str,
    context: dict[str, Any],
    rules_decision: RouteDecision,
    llama_decision: RouteDecision,
    arbiter_keys: list[str],
) -> RouteDecision:
    system = (
        'You are the routing arbiter for an enterprise support assistant. '
        'Choose exactly one route from the allowed route list. Return JSON only. '
        'Prefer the route that best matches the user request, not the most capable model.'
    )
    user = (
        'Resolve a classification disagreement.\n\n'
        f'Allowed routes and meanings:\n{json.dumps(ROUTE_DESCRIPTIONS, indent=2)}\n\n'
        f'Rule list:\n{json.dumps(ROUTING_RULES, indent=2)}\n\n'
        f'Context JSON:\n{json.dumps(context, default=str)[:2000]}\n\n'
        f'User request:\n{user_prompt[:4000]}\n\n'
        f'Rule classifier chose: {rules_decision.route} ({rules_decision.reason})\n'
        f'Local Ollama classifier chose: {llama_decision.route} ({llama_decision.reason})\n\n'
        'Return exactly: {"route": "...", "confidence": 0.0, "reason": "..."}'
    )
    last_error: Exception | None = None
    for key in arbiter_keys:
        spec = MODEL_REGISTRY.get(key)
        if not spec or not _is_available(spec):
            continue
        try:
            provider = _construct(spec)
            response = await provider.narrate(system, user, {'routes': list(ROUTE_CHAINS)})
            payload = _parse_json_object(response.text)
            route = _valid_route(str(payload.get('route') or rules_decision.route))
            confidence = float(payload.get('confidence') or 0.0)
            reason = str(payload.get('reason') or f'arbiter {key}')
            return RouteDecision(route, max(0.0, min(confidence, 1.0)), reason, f'arbiter:{key}')
        except Exception as exc:
            last_error = exc
            _log.warning('Auto arbiter failed via %s (%s)', key, type(exc).__name__)
            continue

    reason = 'arbiter unavailable; using rules'
    if last_error:
        reason += f' ({type(last_error).__name__})'
    return RouteDecision(rules_decision.route, rules_decision.confidence, reason, 'rules-fallback')


async def classify_route(
    user_prompt: str,
    context: dict[str, Any],
    arbiter_model_key: str | None = None,
) -> RouteDecision:
    """Classify every request with rules + local Ollama, arbitrate disagreement."""
    rules_task = asyncio.to_thread(_deterministic_route, user_prompt)
    llama_task = _classify_with_llama(user_prompt, context)
    rules_decision, llama_decision = await asyncio.gather(rules_task, llama_task)

    if rules_decision.route == llama_decision.route:
        confidence = max(rules_decision.confidence, llama_decision.confidence)
        reason = f'agreement: rules={rules_decision.reason}; llama={llama_decision.reason}'
        return RouteDecision(rules_decision.route, confidence, reason, 'rules+llama')

    arbiter_keys = [arbiter_model_key] if arbiter_model_key and arbiter_model_key != 'auto' else ARBITER_CHAIN
    return await _arbitrate_route(user_prompt, context, rules_decision, llama_decision, arbiter_keys)


class AutoProvider(LLMProvider):
    name = 'auto'

    def __init__(self) -> None:
        self._available: list[ModelSpec] = _all_available_execution_models()
        self.model = 'auto-router'
        self.last_route: RouteDecision | None = None

    def available_chain(self) -> list[str]:
        """Return the broad set of execution models available to Auto."""
        return [spec.key for spec in self._available]

    async def _classify(self, user_prompt: str, context: dict[str, Any]) -> RouteDecision:
        return await classify_route(user_prompt, context)

    async def _try_chain(self, op_name: str, fn_args: tuple, route: str) -> LLMResponse:
        chain = _route_chain(route)
        if not chain:
            chain = self._available
        if not chain:
            raise LLMUnavailableError(
                'No LLM is available. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, '
                'GOOGLE_API_KEY in .env, or start a local Ollama server.'
            )

        last_error: Exception | None = None
        for spec in chain:
            try:
                provider = _construct(spec)
                method = getattr(provider, op_name)
                response: LLMResponse = await method(*fn_args)
                if not (response.text or '').strip():
                    raise RuntimeError(f'{spec.key} returned empty {op_name}')
                if op_name == 'plan' and not _looks_like_valid_plan(response):
                    raise RuntimeError(f'{spec.key} returned malformed plan JSON')
                _log.info('Auto: %s route=%s succeeded with %s', op_name, route, spec.key)
                return response
            except Exception as exc:
                last_error = exc
                _log.warning('Auto: %s route=%s failed via %s (%s); trying next',
                             op_name, route, spec.key, type(exc).__name__)
                continue

        raise LLMUnavailableError(
            f'Auto: all {len(chain)} providers failed for {op_name} route={route}. '
            f'Last error: {last_error}'
        )

    async def plan(self, system_prompt: str, user_prompt: str, context: dict[str, Any]) -> LLMResponse:
        decision = await self._classify(user_prompt, context)
        self.last_route = decision
        return await self._try_chain('plan', (system_prompt, user_prompt, context), decision.route)

    async def narrate(self, system_prompt: str, user_prompt: str, facts: dict[str, Any]) -> LLMResponse:
        # Direct ambiguity override: when tools surfaced multiple_matches the
        # orchestrator sets facts.ambiguous_customer. This takes precedence
        # over the plan's original route — we MUST use the clarification
        # chain (which excludes ollama-llama by construction) because a local
        # model may not strictly follow a schema instruction
        # like "list ONLY the candidates from facts.ambiguous_customer.matches".
        # Direct user picks of ollama-llama bypass AutoProvider entirely, so
        # this override only fires when the user picked Auto.
        if isinstance(facts, dict) and facts.get('ambiguous_customer'):
            route = 'clarification'
        else:
            plan = facts.get('plan') if isinstance(facts, dict) else None
            if isinstance(plan, dict):
                if plan.get('write_requested'):
                    route = 'write_proposal'
                elif plan.get('requires_clarification'):
                    route = 'clarification'
                elif self.last_route is not None:
                    route = self.last_route.route
                elif plan.get('steps'):
                    route = 'structured_planning'
                else:
                    route = 'summary'
            else:
                route = DEFAULT_ROUTE
        return await self._try_chain('narrate', (system_prompt, user_prompt, facts), route)
