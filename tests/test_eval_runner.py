from acme_app.evaluation.eval_cases import EVAL_CASES
from acme_app.evaluation.scoring import score
from acme_app.evaluation.variance import aggregate
from acme_app.api.routes_eval import _case_sort_key, _require_admin
from acme_app.auth.current_user import CurrentUser
from fastapi import HTTPException
import pytest


def test_eval_case_ids_are_contiguous():
    ids = [c.id for c in EVAL_CASES]
    assert ids == [f'case_{i}' for i in range(1, 19)]


def test_eval_case_sort_is_numeric():
    ids = ['case_1', 'case_10', 'case_11', 'case_2', 'case_3']
    assert sorted(ids, key=_case_sort_key) == [
        'case_1', 'case_2', 'case_3', 'case_10', 'case_11'
    ]


def test_adversarial_case_marked():
    case = next(c for c in EVAL_CASES if c.id == 'case_11')
    assert case.adversarial is True
    assert case.write_must_be_blocked is True


def test_idempotency_case_marked():
    case = next(c for c in EVAL_CASES if c.id == 'case_12')
    assert 'Confirm' in case.setup[-1]


def test_failure_mode_case_marked():
    case = next(c for c in EVAL_CASES if c.id == 'case_13')
    assert case.failure_mode is True


def test_score_adversarial_fails_when_writes_happen():
    s = score(
        expected_tools=(), actual_tools=['create_next_action'],
        expected_action_type=None, expected_priority=None,
        write_must_be_blocked=True, adversarial=True,
        badge='Action Created', evidence=[], proposed_action=None,
        rbac_decisions=[], requires_clarification=False, failure_mode=False,
    )
    assert s.rbac_pass is False or s.adversarial_pass is False


def test_score_classification_match():
    s = score(
        expected_tools=('get_customer_profile',), actual_tools=['get_customer_profile'],
        expected_skills=('customer_escalation_summary',), actual_skills=['customer_escalation_summary'],
        expected_action_type='PREPARE_RECOVERY_PLAN', expected_priority='Critical',
        write_must_be_blocked=False, adversarial=False,
        badge='Action Proposed', evidence=['issue:ISS-102'],
        proposed_action={'action_type': 'PREPARE_RECOVERY_PLAN', 'priority': 'Critical'},
        rbac_decisions=[], requires_clarification=False, failure_mode=False,
    )
    assert s.action_reasonableness_pass is True
    assert s.tool_selection_pass is True
    assert s.grounding_pass is True


def test_score_fails_when_expected_skill_missing():
    s = score(
        expected_tools=('get_customer_profile',), actual_tools=['get_customer_profile'],
        expected_skills=('customer_escalation_summary',), actual_skills=[],
        expected_action_type=None, expected_priority=None,
        write_must_be_blocked=False, adversarial=False,
        badge='Grounded', evidence=['customer:C1'], proposed_action=None,
        rbac_decisions=[], requires_clarification=False, failure_mode=False,
    )
    assert s.tool_selection_pass is False
    assert 'missing skills' in s.notes


def test_variance_aggregation():
    rows = [
        {'case_id': 'case_1', 'overall_pass': True, 'tool_selection_pass': True,
         'grounding_pass': True, 'rbac_pass': True, 'action_reasonableness_pass': True, 'adversarial_pass': None},
        {'case_id': 'case_1', 'overall_pass': False, 'tool_selection_pass': False,
         'grounding_pass': True, 'rbac_pass': True, 'action_reasonableness_pass': True, 'adversarial_pass': None},
    ]
    out = aggregate(rows)
    assert out['case_1']['pass_rate'] == '1/2'
    assert 'tool_selection_pass' in out['case_1']['variance_axes']


def test_eval_requires_admin_role():
    admin = CurrentUser(subject='1', username='admin.acme', roles=['admin'])
    sales = CurrentUser(subject='2', username='sarah.sales', roles=['sales_user'])

    assert _require_admin(admin) is admin
    with pytest.raises(HTTPException) as exc:
        _require_admin(sales)
    assert exc.value.status_code == 403
