"""Variance reporting across multiple runs.

A case with all five axes passing in every run is "stable". Any axis flipping
across runs is variance and is flagged for human review.
"""
from __future__ import annotations

from collections import defaultdict


def aggregate(results: list[dict]) -> dict[str, dict]:
    """Group results by case_id and compute pass-rate and variance."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        grouped[row['case_id']].append(row)

    out: dict[str, dict] = {}
    for case_id, rows in grouped.items():
        passed = sum(1 for r in rows if r.get('overall_pass'))
        axes = ('tool_selection_pass', 'grounding_pass', 'rbac_pass',
                'action_reasonableness_pass', 'adversarial_pass')
        axis_variance: dict[str, bool] = {}
        for axis in axes:
            seen = {r.get(axis) for r in rows if r.get(axis) is not None}
            axis_variance[axis] = len(seen) > 1
        out[case_id] = {
            'runs': len(rows),
            'passed': passed,
            'pass_rate': f'{passed}/{len(rows)}',
            'variance_axes': [a for a, v in axis_variance.items() if v],
        }
    return out
