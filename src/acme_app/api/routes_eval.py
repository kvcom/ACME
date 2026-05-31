from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from acme_app.api._view_helpers import badge_class_for
from acme_app.auth.current_user import CurrentUser, get_current_user
from acme_app.config import settings
from acme_app.evaluation.eval_cases import EVAL_CASES
from acme_app.infrastructure.db.session import get_db_session


router = APIRouter(prefix='/eval', tags=['eval'])


_BADGE_FROM_NOTES = {
    'denied': 'Permission Denied',
    'created': 'Action Created',
    'clarify': 'Clarification Required',
    'blocked': 'Adversarial Input Blocked',
    'insufficient': 'Insufficient Evidence',
    'proposed': 'Confirmation Required',
    'grounded': 'Grounded',
}

_CASE_BY_ID = {case.id: case for case in EVAL_CASES}


def _require_admin(user: CurrentUser) -> CurrentUser:
    if 'admin' not in user.roles:
        raise HTTPException(status_code=403, detail='Admin role required')
    return user


def _badge_for_case(case_id: str, expected_status: str | None) -> str:
    if expected_status:
        return expected_status
    if 'adversarial' in case_id or 'case_11' in case_id:
        return 'Adversarial Input Blocked'
    return 'Grounded'


def _case_sort_key(case_id: str) -> tuple[str, int, str]:
    prefix, sep, suffix = case_id.rpartition('_')
    if sep and suffix.isdigit():
        return (prefix, int(suffix), '')
    return (case_id, 0, case_id)


def _result_run_number(result_case_id: str) -> int:
    _, sep, suffix = result_case_id.rpartition('-r')
    return int(suffix) if sep and suffix.isdigit() else 1


async def _eval_turn_traces(
    session: AsyncSession,
    *,
    case_id: str,
    run_number: int,
    role: str,
    turn_count: int,
) -> list[dict[str, Any]]:
    """Return the latest traces for one eval case run.

    Eval conversations are deterministic (`EVAL-case_8-r2`). Older eval runs
    can reuse the same conversation_ref, so we take the latest N turns for the
    scenario and then restore chronological order.
    """
    conversation_ref = f'EVAL-{case_id}-r{run_number}'
    rows = (await session.execute(text("""
        SELECT t.trace_ref, t.user_query, t.final_status, t.created_at
        FROM agent_traces t
        JOIN conversations c ON c.id = t.conversation_id
        WHERE c.conversation_ref = :conv
          AND t.username = :username
        ORDER BY t.created_at DESC
        LIMIT :limit
    """), {
        'conv': conversation_ref,
        'username': f'eval-{role}',
        'limit': max(1, turn_count),
    })).all()
    traces = [
        {
            'trace_ref': r[0],
            'query': r[1],
            'status': r[2],
            'created_at': r[3].isoformat() if r[3] else None,
        }
        for r in reversed(rows)
    ]
    for idx, trace in enumerate(traces, start=1):
        trace['turn'] = idx
    return traces


async def _latest_run_summary(session: AsyncSession) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run_row = (await session.execute(text(
        """
        SELECT id, eval_run_ref, llm_provider, llm_model, started_at, completed_at,
               cases_total, cases_passed, total_cost_usd
        FROM eval_runs ORDER BY started_at DESC LIMIT 1
        """
    ))).first()
    if run_row is None:
        return ({'run_ref': None, 'model': settings.llm_model,
                 'passed': 0, 'total': 0, 'variance': 0, 'cost': 0.0, 'wall': None,
                 'cases_total': len(EVAL_CASES), 'runs_per_case': 3, 'notes': None}, [])

    rows = (await session.execute(text(
        """
        SELECT case_id, query, role_name, tool_selection_pass, grounding_pass,
               rbac_pass, action_reasonableness_pass, adversarial_pass,
               latency_ms, cost_usd, notes
        FROM eval_results WHERE eval_run_id = :rid ORDER BY case_id
        """
    ), {'rid': run_row[0]})).all()

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        # case_id stored as "case_03-r1"; trim run suffix for grouping.
        base = r[0].split('-r')[0]
        grouped[base].append({
            'tool_sel': r[3], 'grounding': r[4], 'rbac': r[5],
            'action': r[6], 'adv': r[7],
            'query': r[1], 'role': r[2],
            'latency': r[8], 'cost': r[9] or 0,
            'run_number': _result_run_number(r[0]),
        })

    cases: list[dict[str, Any]] = []
    variance_count = 0
    for case_id in sorted(grouped, key=_case_sort_key):
        runs = grouped[case_id]
        passes = [
            r['tool_sel'] and r['grounding'] and r['rbac'] and r['action']
            and (r['adv'] in (None, True))
            for r in runs
        ]
        first = runs[0]
        case_def = _CASE_BY_ID.get(case_id)
        setup = list(case_def.setup) if case_def else []
        scenario_steps = []
        for idx, query in enumerate(setup, start=1):
            label = 'Initial request' if idx == 1 else f'Follow-up {idx - 1}'
            scenario_steps.append({'kind': 'conversation', 'label': label, 'query': query})
        scenario_steps.append({'kind': 'assertion', 'label': 'Final test query', 'query': first['query']})
        run_traces = []
        for r in sorted(runs, key=lambda item: item['run_number']):
            run_traces.append({
                'run': r['run_number'],
                'traces': await _eval_turn_traces(
                    session,
                    case_id=case_id,
                    run_number=r['run_number'],
                    role=first['role'],
                    turn_count=len(scenario_steps),
                ),
            })
        case_passed = all(passes)
        if len(set(passes)) > 1:
            variance_count += 1
        cases.append({
            'case_id': case_id,
            'description': case_def.description if case_def else '',
            'query': first['query'],
            'setup': setup,
            'scenario_steps': scenario_steps,
            'role': first['role'],
            'runs': passes,
            'run_traces': run_traces,
            'badge': _badge_for_case(case_id, None),
            'badge_class': badge_class_for(_badge_for_case(case_id, None)),
            'cost': sum(float(r['cost']) for r in runs),
            'latency': f"{sum(r['latency'] or 0 for r in runs) // max(1, len(runs))} ms",
            'variance': not case_passed,
        })

    wall = None
    if run_row[5] and run_row[4]:
        delta = (run_row[5] - run_row[4]).total_seconds()
        wall = f'{int(delta // 60)}m {int(delta % 60)}s'

    summary = {
        'run_ref': run_row[1],
        'model': run_row[3],
        'passed': run_row[7],
        'total': run_row[6],
        'variance': variance_count,
        'cost': float(run_row[8] or 0),
        'wall': wall,
        'cases_total': len({c['case_id'] for c in cases}) or len(EVAL_CASES),
        'runs_per_case': len(next(iter(grouped.values()))) if grouped else 3,
        'notes': None,
    }
    return summary, cases


@router.get('', response_class=HTMLResponse)
async def eval_page(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    _require_admin(user)
    try:
        summary, cases = await _latest_run_summary(session)
    except Exception:
        summary, cases = ({'passed': 0, 'total': 0, 'variance': 0, 'cost': 0.0,
                           'wall': None, 'run_ref': None, 'cases_total': len(EVAL_CASES),
                           'runs_per_case': 3, 'model': settings.llm_model, 'notes': None}, [])
    return request.app.state.templates.TemplateResponse(
        request, 'eval.html', {'user': user, 'summary': summary, 'cases': cases},
    )


@router.get('/latest')
async def latest_eval(user: CurrentUser = Depends(get_current_user)) -> dict:
    _require_admin(user)
    p = Path('EVAL_RESULTS.md')
    return {'exists': p.exists(), 'content': p.read_text(encoding='utf-8') if p.exists() else ''}
