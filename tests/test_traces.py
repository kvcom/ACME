from acme_app.observability.decision_ledger import Ledger, summarise_output


def test_ledger_event_collection():
    ledger = Ledger(trace_ref='TRC-T', username='u', user_role='admin')
    ledger.event('agent_plan', 'plan.created', {'intent': 'x'})
    ledger.tool('get_customer_profile', {'customer_name': 'Acme'},
                {'name': 'Acme'}, 'ok', 12)
    ledger.rbac('admin', 'create_action', 'PREPARE_RECOVERY_PLAN', True, 'ok')
    assert len(ledger.events) == 1
    assert len(ledger.tool_calls) == 1
    assert len(ledger.rbac_decisions) == 1


def test_summarise_output_compresses_lists():
    out = summarise_output({'issues': [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}], 'name': 'x' * 200})
    assert out['issues']['kind'] == 'list'
    assert out['issues']['length'] == 2
    assert len(out['name']) <= 120
