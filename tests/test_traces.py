from acme_app.observability.decision_ledger import Ledger, summarise_output
from acme_app.api.routes_traces import _can_read_trace
from acme_app.auth.current_user import CurrentUser


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


def test_trace_visibility_owner_or_admin_only():
    owner = CurrentUser(subject='1', username='sam.support', roles=['support_user'])
    other = CurrentUser(subject='2', username='sarah.sales', roles=['sales_user'])
    admin = CurrentUser(subject='3', username='admin.acme', roles=['admin'])
    trace = {'username': 'sam.support'}

    assert _can_read_trace(owner, trace)
    assert _can_read_trace(admin, trace)
    assert not _can_read_trace(other, trace)
