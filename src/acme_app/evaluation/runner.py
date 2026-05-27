import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from acme_app.application.orchestrator import run_agent
from acme_app.evaluation.eval_cases import EVAL_CASES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=3)
    return parser.parse_args()


async def run_eval(runs: int) -> list[dict]:
    rows: list[dict] = []
    for run in range(1, runs + 1):
        for case in EVAL_CASES:
            provider = 'ollama' if case['id'] == 'case_13' else 'anthropic'
            try:
                result = await run_agent(case['query'], provider, case['role'], f"EVAL-{run}")
            except Exception as exc:
                result = {
                    'badge': 'LLM unavailable',
                    'trace_ref': f"TRC-EVAL-{run}-{case['id']}",
                    'cost_usd': 0.0,
                    'latency_ms': 0,
                    'error': str(exc),
                }
            rows.append({'run': run, 'case_id': case['id'], 'role': case['role'], 'badge': result['badge'], 'trace_ref': result['trace_ref'], 'cost_usd': result['cost_usd'], 'latency_ms': result['latency_ms']})
    return rows


def write_report(rows: list[dict]) -> None:
    lines = [
        '# Evaluation Results',
        '',
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        '',
        '| Run | Case | Role | Badge | Cost USD | Latency ms |',
        '|---|---|---|---|---:|---:|',
    ]
    for r in rows:
        lines.append(f"| {r['run']} | {r['case_id']} | {r['role']} | {r['badge']} | {r['cost_usd']} | {r['latency_ms']} |")
    Path('EVAL_RESULTS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    args = parse_args()
    rows = asyncio.run(run_eval(args.runs))
    write_report(rows)
    print(f"Evaluation complete: {len(rows)} rows")
