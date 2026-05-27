from acme_app.auth.current_user import CurrentUser, _decode_session, _encode_session
from acme_app.auth.jwt_validator import extract_roles


def test_session_roundtrip():
    user = CurrentUser(subject='abc', username='sam.support', roles=['support_user'], access_token='t')
    cookie = _encode_session(user)
    decoded = _decode_session(cookie)
    assert decoded is not None
    assert decoded.username == 'sam.support'
    assert decoded.roles == ['support_user']


def test_primary_role_order():
    u = CurrentUser('1', 'admin.acme', ['sales_user', 'admin'])
    assert u.primary_role == 'admin'
    u2 = CurrentUser('1', 'sarah.sales', ['sales_user'])
    assert u2.primary_role == 'sales_user'


def test_extract_roles_filters_unknown():
    claims = {'realm_access': {'roles': ['admin', 'random_role', 'support_user']}}
    assert sorted(extract_roles(claims)) == ['admin', 'support_user']


def test_decode_session_invalid_returns_none():
    assert _decode_session('not-a-real-cookie') is None
