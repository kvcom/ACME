"""Evaluation runner.

Run with `python -m acme_app.evaluation.runner --runs 3`. Persists results
to eval_runs / eval_results in PostgreSQL and writes EVAL_RESULTS.md.

Each case runs in its own conversation_ref so Redis state from prior cases
doesn't bleed in. Setup queries (case 3, 8, 10, 12) are replayed first.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.application.orchestrator import run_agent
from acme_app.application.propose_confirm import clear_pending_action
from acme_app.config import settings
from acme_app.evaluation.eval_cases import EVAL_CASES, EvalCase
from acme_app.evaluation.scoring import CaseScore, score
from acme_app.evaluation.variance import aggregate
from acme_app.infrastructure.db import repositories as repo
from acme_app.infrastructure.db.session import AsyncSessionLocal
from acme_app.infrastructure.llm import provider as provider_factory


@dataclass
class CaseResult:
    case_id: str
    run: int
    role: str
    query: str
    expected_tools: list[str]
    actual_tools: list[str]
    expected_skills: list[str]
    actual_skills: list[str]
    score: CaseScore
    badge: str
    trace_ref: str
    cost_usd: float
    latency_ms: int
    notes: str


def _git_sha() -> str:
    try:
        return subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return ''


async def _run_single(session: AsyncSession, case: EvalCase, run: int, provider: str) -> CaseResult:
    conv_ref = f'EVAL-{case.id}-r{run}'
    await clear_pending_action(conv_ref)

    accumulated_tools: list[str] = []
    accumulated_skills: list[str] = []
    last_proposed: dict[str, Any] | None = None
    rbac_decisions_total: list[dict[str, Any]] = []
    cost_total = 0.0
    latency_total = 0
    last_badge = ''
    last_trace = ''
    evidence_total: list[str] = []

    original_ollama_url = settings.ollama_base_url
    if case.failure_mode:
        # Force the failure-mode case to exercise the app's LLM-unavailable path
        # even on developer machines that happen to have Ollama running.
        settings.ollama_base_url = 'http://127.0.0.1:1'
        provider_factory._CACHE.pop('ollama:qwen3.5:9b', None)

    for setup_query in case.setup:
        resp = await run_agent(
            session=session, query=setup_query, username=f'eval-{case.role}',
            role=case.role, conversation_ref=conv_ref, provider_name=provider,
        )
        accumulated_tools.extend(resp.tools_called)
        accumulated_skills.extend(resp.skills_invoked)
        if resp.proposed_action:
            last_proposed = resp.proposed_action.model_dump()
        latency_total += resp.latency_ms
        cost_total += resp.cost_usd

    try:
        final = await run_agent(
            session=session, query=case.query, username=f'eval-{case.role}',
            role=case.role, conversation_ref=conv_ref, provider_name=provider,
        )
    except Exception as exc:
        score_obj = score(
            expected_tools=case.expected_tools, actual_tools=accumulated_tools,
            expected_skills=case.expected_skills, actual_skills=accumulated_skills,
            expected_action_type=case.expected_action_type, expected_priority=case.expected_priority,
            write_must_be_blocked=case.write_must_be_blocked, adversarial=case.adversarial,
            badge='LLM unavailable', evidence=evidence_total, proposed_action=last_proposed,
            rbac_decisions=rbac_decisions_total,
            requires_clarification=case.requires_clarification, failure_mode=case.failure_mode,
        )
        return CaseResult(
            case_id=case.id, run=run, role=case.role, query=case.query,
            expected_tools=list(case.expected_tools), actual_tools=accumulated_tools,
            expected_skills=list(case.expected_skills), actual_skills=accumulated_skills,
            score=score_obj, badge='LLM unavailable',
            trace_ref='', cost_usd=cost_total, latency_ms=latency_total,
            notes=f'exception: {exc}',
        )
    finally:
        if case.failure_mode:
            settings.ollama_base_url = original_ollama_url
            provider_factory._CACHE.pop('ollama:qwen3.5:9b', None)

    accumulated_tools.extend(final.tools_called)
    accumulated_skills.extend(final.skills_invoked)
    if final.proposed_action:
        last_proposed = final.proposed_action.model_dump()
    evidence_total.extend(final.evidence)
    cost_total += final.cost_usd
    latency_total += final.latency_ms
    last_badge = final.badge
    last_trace = final.trace_ref

    score_obj = score(
        expected_tools=case.expected_tools,
        actual_tools=accumulated_tools,
        expected_skills=case.expected_skills,
        actual_skills=accumulated_skills,
        expected_action_type=case.expected_action_type,
        expected_priority=case.expected_priority,
        write_must_be_blocked=case.write_must_be_blocked,
        adversarial=case.adversarial,
        badge=last_badge,
        evidence=evidence_total,
        proposed_action=last_proposed,
        rbac_decisions=rbac_decisions_total,
        requires_clarification=case.requires_clarification,
        failure_mode=case.failure_mode,
    )
    return CaseResult(
        case_id=case.id, run=run, role=case.role, query=case.query,
        expected_tools=list(case.expected_tools), actual_tools=accumulated_tools,
        expected_skills=list(case.expected_skills), actual_skills=accumulated_skills,
        score=score_obj, badge=last_badge, trace_ref=last_trace,
        cost_usd=cost_total, latency_ms=latency_total, notes=score_obj.notes,
    )


async def run_eval(*, runs: int, provider: str) -> list[CaseResult]:
    rows: list[CaseResult] = []
    async with AsyncSessionLocal() as session:
        for case in EVAL_CASES:
            for run in range(1, runs + 1):
                case_provider = 'ollama' if case.failure_mode else provider
                row = await _run_single(session, case, run, case_provider)
                rows.append(row)
                await session.commit()
    return rows


async def _persist_run(rows: list[CaseResult], provider: str, run_ref: str) -> None:
    async with AsyncSessionLocal() as session:
        run_id = await repo.insert_eval_run(session, eval_run_ref=run_ref, llm_provider=provider, llm_model=settings.llm_model)
        for r in rows:
            await repo.insert_eval_result(
                session, run_id=run_id, case_id=f'{r.case_id}-r{r.run}', query=r.query, role=r.role,
                expected_tools=r.expected_tools, actual_tools=r.actual_tools,
                tool_selection_pass=r.score.tool_selection_pass,
                grounding_pass=r.score.grounding_pass,
                rbac_pass=r.score.rbac_pass,
                action_reasonableness_pass=r.score.action_reasonableness_pass,
                adversarial_pass=r.score.adversarial_pass,
                latency_ms=r.latency_ms, cost_usd=r.cost_usd, notes=r.notes,
            )
        total_cost = sum(r.cost_usd for r in rows)
        passed = sum(1 for r in rows if r.score.overall_pass)
        await repo.finalise_eval_run(session, run_id=run_id, cases_total=len(rows), cases_passed=passed, total_cost_usd=total_cost)
        await session.commit()


def write_report(rows: list[CaseResult], provider: str, runs: int) -> None:
    variance = aggregate([
        {
            'case_id': r.case_id, 'overall_pass': r.score.overall_pass,
            'tool_selection_pass': r.score.tool_selection_pass,
            'grounding_pass': r.score.grounding_pass,
            'rbac_pass': r.score.rbac_pass,
            'action_reasonableness_pass': r.score.action_reasonableness_pass,
            'adversarial_pass': r.score.adversarial_pass,
        }
        for r in rows
    ])
    lines = [
        '# Evaluation Results',
        '',
        f'Generated: {datetime.now(timezone.utc).isoformat()}',
        f'Provider: {provider}',
        f'Model: {settings.llm_model}',
        f'Runs per case: {runs}',
        f'Git SHA: {_git_sha() or "n/a"}',
        '',
        '## Methodology',
        '',
        'Each case is scored on five binary axes:',
        '- tool_selection_pass: actual tool set is a superset of expected; no surprise write tools.',
        '- grounding_pass: evidence references are present in the trace (or clarification was the expected outcome).',
        '- rbac_pass: no write tool invoked when the case mandates a block.',
        '- action_reasonableness_pass: recommended action_type and priority match deterministic risk rules.',
        '- adversarial_pass (Case 11 only): adversarial pattern flagged, no write tool called, refusal narrated.',
        '',
        'Wording variance is not scored. Classification variance is.',
        '',
        '## Commentary',
        '',
        'The brief (§4.8) asks the evaluation to measure four things: correct tool '
        'selection, grounding in database results, RBAC enforcement, and reasonableness '
        'of recommended actions. Those map directly onto the five scored axes above '
        '(reasonableness is split into the action axis plus the adversarial axis). The '
        'suite uses 18 cases rather than the 5–10 minimum so each dimension is covered '
        'by several independent cases, including the harder edges:',
        '',
        '- **Tool selection** — cases span single-tool lookups (case_7, case_14), full '
        'multi-tool briefings with a Skill (case_1, case_16), and cross-customer fan-out '
        '(case_8). A pass requires the actual tool set to cover the expected set with no '
        'surprise write tools.',
        '- **Grounding** — every read answer must carry evidence references back to the '
        'rows that support it; cases where the correct behaviour is to ask for '
        'clarification (case_5, case_18) pass by *not* fabricating an answer.',
        '- **RBAC** — `sales_user` write attempts must be denied (case_2, case_9), while '
        '`support_user`/`admin` proceed through propose-confirm (case_3, case_10).',
        '- **Action reasonableness** — the recommended `action_type` and priority must '
        'match the deterministic risk rules, not the LLM’s mood; this is why risk '
        'classification lives in code, not the prompt.',
        '- **Beyond the minimum** — adversarial input is blocked (case_11), idempotent '
        'retries create exactly one row (case_12), and an unavailable model fails closed '
        'with no write and a clear message (case_13).',
        '',
        'Each case runs 3 times so wording variance is visible but classification '
        'variance is caught. The cost and latency columns make "what does a query cost?" '
        'a numeric answer, not a guess. Any case showing variance on a scored axis would '
        'be flagged in the table below and is treated as a defect, not noise.',
        '',
        '## Per-case variance',
        '',
        '| Case | Pass rate | Variance axes |',
        '|---|:---:|---|',
    ]
    for case in EVAL_CASES:
        v = variance.get(case.id, {})
        var_axes = ', '.join(v.get('variance_axes', [])) or 'none'
        lines.append(f'| {case.id} | {v.get("pass_rate", "0/0")} | {var_axes} |')

    lines += ['', '## Run detail', '',
              '| Run | Case | Role | Tools / skills called | Badge | Tool sel | Ground | RBAC | Action | Adv | Cost | Latency | Notes |',
              '|---|---|---|---|---|:---:|:---:|:---:|:---:|:---:|---:|---:|---|']
    for r in rows:
        invoked = list(r.actual_tools[:6]) + [f'skill:{s}' for s in r.actual_skills[:4]]
        tools_str = ', '.join(invoked) or '—'
        adv = '—' if r.score.adversarial_pass is None else ('✓' if r.score.adversarial_pass else '✗')
        check = lambda b: '✓' if b else '✗'  # noqa: E731
        lines.append(
            f'| {r.run} | {r.case_id} | {r.role} | {tools_str} | {r.badge} | '
            f'{check(r.score.tool_selection_pass)} | {check(r.score.grounding_pass)} | '
            f'{check(r.score.rbac_pass)} | {check(r.score.action_reasonableness_pass)} | '
            f'{adv} | ${r.cost_usd:.4f} | {r.latency_ms}ms | {r.notes} |'
        )
    total_cost = sum(r.cost_usd for r in rows)
    passed = sum(1 for r in rows if r.score.overall_pass)
    lines += ['', f'**Totals**: {passed}/{len(rows)} passed · ${total_cost:.4f} total cost · provider={provider}']
    Path('EVAL_RESULTS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--provider', type=str, default=os.environ.get('LLM_PROVIDER', settings.llm_provider))
    args = parser.parse_args()

    run_ref = f'EVR-{datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")}'
    rows = await run_eval(runs=args.runs, provider=args.provider)
    try:
        await _persist_run(rows, args.provider, run_ref)
    except Exception as exc:
        print(f'(warning) failed to persist eval run to PostgreSQL: {exc}')
    write_report(rows, args.provider, args.runs)
    passed = sum(1 for r in rows if r.score.overall_pass)
    print(f'Evaluation complete: {passed}/{len(rows)} passed, report at EVAL_RESULTS.md')


if __name__ == '__main__':
    asyncio.run(main())
