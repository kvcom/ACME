"""Unit tests for the recommendation engine (D-020).

These exercise the matcher and template renderer with synthetic rules —
no DB hit. The integration tests (the existing skill/tool tests) cover
that the production rule set still produces the same recommendations
as the old hardcoded branches.
"""
from __future__ import annotations

from acme_app.policy import recommendation_engine
from acme_app.policy.recommendation_engine import Rule


def _set_rules(rules):
    recommendation_engine._rules = rules


def setup_function(_):
    _set_rules([])


def test_first_match_wins_by_priority_order():
    _set_rules([
        Rule('r1', 'rec', 10, {'severity': 'P1'}, 'PREPARE_RECOVERY_PLAN', 'Critical', None),
        Rule('r2', 'rec', 20, {'severity': 'P1'}, 'ESCALATE_ISSUE', 'High', None),
    ])
    rec = recommendation_engine.evaluate('rec', {'severity': 'P1'})
    assert rec.action_type == 'PREPARE_RECOVERY_PLAN'
    assert rec.matched_rule_ref == 'r1'


def test_operator_in_list():
    _set_rules([
        Rule('r1', 'rec', 10, {'tier': {'in': ['Enterprise', 'Strategic']}}, 'PREPARE_RECOVERY_PLAN', 'High', None),
    ])
    assert recommendation_engine.evaluate('rec', {'tier': 'Enterprise'}).action_type == 'PREPARE_RECOVERY_PLAN'
    assert recommendation_engine.evaluate('rec', {'tier': 'Mid-market'}) is None


def test_operator_not_in():
    _set_rules([
        Rule('r1', 'rec', 10, {'sla': {'not_in': ['Within SLA']}}, 'ESCALATE_ISSUE', 'High', None),
    ])
    assert recommendation_engine.evaluate('rec', {'sla': 'Breached'}).action_type == 'ESCALATE_ISSUE'
    assert recommendation_engine.evaluate('rec', {'sla': 'Within SLA'}) is None


def test_operator_null():
    _set_rules([
        Rule('r1', 'rec', 10, {'owner': {'null': True}}, 'ASSIGN_OWNER', 'Medium', None),
    ])
    assert recommendation_engine.evaluate('rec', {'owner': None}).action_type == 'ASSIGN_OWNER'
    assert recommendation_engine.evaluate('rec', {'owner': 'sam'}) is None


def test_operator_not_null():
    _set_rules([
        Rule('r1', 'rec', 10, {'owner': {'not_null': True}}, 'CUSTOMER_FOLLOW_UP', 'Medium', None),
    ])
    assert recommendation_engine.evaluate('rec', {'owner': 'sam'}).action_type == 'CUSTOMER_FOLLOW_UP'
    assert recommendation_engine.evaluate('rec', {'owner': None}) is None


def test_empty_conditions_always_matches():
    _set_rules([
        Rule('r1', 'rec', 999, {}, 'SCHEDULE_REVIEW', 'Low', None),
    ])
    assert recommendation_engine.evaluate('rec', {}).action_type == 'SCHEDULE_REVIEW'
    assert recommendation_engine.evaluate('rec', {'anything': 'random'}).action_type == 'SCHEDULE_REVIEW'


def test_no_rules_returns_none():
    assert recommendation_engine.evaluate('rec', {'severity': 'P1'}) is None


def test_recommender_isolation():
    _set_rules([
        Rule('r1', 'recA', 10, {}, 'PREPARE_RECOVERY_PLAN', 'Critical', None),
        Rule('r2', 'recB', 10, {}, 'SCHEDULE_REVIEW', 'Low', None),
    ])
    assert recommendation_engine.evaluate('recA', {}).action_type == 'PREPARE_RECOVERY_PLAN'
    assert recommendation_engine.evaluate('recB', {}).action_type == 'SCHEDULE_REVIEW'


def test_rationale_template_substitutes_facts():
    _set_rules([
        Rule('r1', 'rec', 10, {}, 'PREPARE_RECOVERY_PLAN', 'Critical',
             '{tier} customer, {severity} issue, SLA {sla}.'),
    ])
    rec = recommendation_engine.evaluate('rec', {'tier': 'Enterprise', 'severity': 'P1', 'sla': 'Breached'})
    assert rec.rationale == 'Enterprise customer, P1 issue, SLA Breached.'


def test_rationale_template_missing_fact_renders_question_mark():
    _set_rules([
        Rule('r1', 'rec', 10, {}, 'ESCALATE_ISSUE', 'High', '{tier} customer.'),
    ])
    rec = recommendation_engine.evaluate('rec', {})
    assert rec.rationale == '? customer.'


def test_unknown_operator_fails_closed():
    # Defensive: typo'd operator should NOT silently match everything.
    _set_rules([
        Rule('r1', 'rec', 10, {'tier': {'oops_typo': ['Enterprise']}}, 'ESCALATE_ISSUE', 'High', None),
        Rule('r2', 'rec', 20, {}, 'SCHEDULE_REVIEW', 'Low', None),  # fallback
    ])
    rec = recommendation_engine.evaluate('rec', {'tier': 'Enterprise'})
    assert rec.action_type == 'SCHEDULE_REVIEW'  # fell through to fallback


def test_production_rules_for_escalation_summary_compile():
    """Sanity check: the seeded rules for customer_escalation_summary should
    reproduce the old hardcoded branches for representative inputs."""
    _set_rules([
        Rule('a', 'customer_escalation_summary', 10, {'risk': 'Critical'},                     'PREPARE_RECOVERY_PLAN', 'Critical', None),
        Rule('b', 'customer_escalation_summary', 20, {'risk': 'High', 'severity': 'P1'},       'PREPARE_RECOVERY_PLAN', 'High',     None),
        Rule('c', 'customer_escalation_summary', 30, {'risk': 'High'},                         'ESCALATE_ISSUE',        'High',     None),
        Rule('d', 'customer_escalation_summary', 40, {'risk': 'Medium', 'has_owner': False},   'ASSIGN_OWNER',          'Medium',   None),
        Rule('e', 'customer_escalation_summary', 50, {'risk': 'Medium'},                       'CUSTOMER_FOLLOW_UP',    'Medium',   None),
        Rule('f', 'customer_escalation_summary', 999, {},                                      'SCHEDULE_REVIEW',       'Low',      None),
    ])
    cases = [
        ({'risk': 'Critical', 'severity': 'P3', 'has_owner': True}, 'PREPARE_RECOVERY_PLAN'),
        ({'risk': 'High', 'severity': 'P1', 'has_owner': True},     'PREPARE_RECOVERY_PLAN'),
        ({'risk': 'High', 'severity': 'P2', 'has_owner': True},     'ESCALATE_ISSUE'),
        ({'risk': 'Medium', 'severity': 'P2', 'has_owner': False},  'ASSIGN_OWNER'),
        ({'risk': 'Medium', 'severity': 'P2', 'has_owner': True},   'CUSTOMER_FOLLOW_UP'),
        ({'risk': 'Low', 'severity': 'P3', 'has_owner': True},      'SCHEDULE_REVIEW'),
    ]
    for facts, expected in cases:
        rec = recommendation_engine.evaluate('customer_escalation_summary', facts)
        assert rec.action_type == expected, f'{facts} -> {rec.action_type}, expected {expected}'
