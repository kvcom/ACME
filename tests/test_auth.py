import pytest

from acme_app.auth.current_user import CurrentUser, _decode_session, _encode_session
from acme_app.auth.jwt_validator import extract_roles
from acme_app.api.routes_auth import _friendly_login_error


@pytest.fixture
def stub_db_roles(monkeypatch):
    """Stand in for the Postgres role lookup so auth tests don't need a live DB.

    Mirrors the seeded users in infra/postgres/seed.sql.
    """
    from acme_app.api import routes_auth

    table = {
        'sarah.sales': ['sales_user'],
        'sam.support': ['support_user'],
        'admin.acme': ['admin'],
    }

    async def fake_lookup(username):
        return table.get(username)

    async def fake_link(_username, _subject):
        return None

    monkeypatch.setattr(routes_auth, 'get_roles_for_username', fake_lookup)
    monkeypatch.setattr(routes_auth, 'link_keycloak_subject', fake_link)
    return table


def test_session_roundtrip():
    user = CurrentUser(subject='abc', username='sam.support', roles=['support_user'], access_token='t')
    cookie = _encode_session(user)
    decoded = _decode_session(cookie)
    assert decoded is not None
    assert decoded.username == 'sam.support'
    assert decoded.roles == ['support_user']
    assert decoded.auth_source == 'keycloak'


def test_demo_session_roundtrip_keeps_auth_source():
    user = CurrentUser(
        subject='demo-sam.support',
        username='sam.support',
        roles=['support_user'],
        auth_source='demo_fallback',
    )
    decoded = _decode_session(_encode_session(user))

    assert decoded is not None
    assert decoded.auth_source == 'demo_fallback'


def test_primary_role_order():
    u = CurrentUser('1', 'admin.acme', ['sales_user', 'admin'])
    assert u.primary_role == 'admin'
    u2 = CurrentUser('1', 'sarah.sales', ['sales_user'])
    assert u2.primary_role == 'sales_user'


def test_extract_roles_filters_unknown():
    claims = {'realm_access': {'roles': ['admin', 'random_role', 'support_user']}}
    assert sorted(extract_roles(claims)) == ['admin', 'support_user']


def test_friendly_login_error_hides_keycloak_json():
    raw = 'login failed (401): {"error":"invalid_grant","error_description":"Invalid user credentials"}'

    assert _friendly_login_error(raw) == 'Username or password is incorrect.'


def test_friendly_login_error_for_unavailable_service():
    assert (
        _friendly_login_error('Keycloak unavailable and demo auth fallback is disabled')
        == 'Sign-in service is unavailable. Please try again in a moment.'
    )


def test_decode_session_invalid_returns_none():
    assert _decode_session('not-a-real-cookie') is None


def test_decode_session_expired_returns_none(monkeypatch):
    import base64
    import json

    payload = {'sub': 'abc', 'u': 'sam.support', 'r': ['support_user'], 't': '', 'exp': 1}
    cookie = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    monkeypatch.setattr('time.time', lambda: 10)

    assert _decode_session(cookie) is None


def test_decode_session_without_expiry_returns_none():
    import base64
    import json

    payload = {'sub': 'abc', 'u': 'sam.support', 'r': ['support_user'], 't': ''}
    cookie = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    assert _decode_session(cookie) is None


@pytest.mark.asyncio
async def test_keycloak_rejection_does_not_fall_back_to_demo(monkeypatch, stub_db_roles):
    from acme_app.api import routes_auth
    from acme_app.auth.keycloak_client import KeycloakLoginRejected

    async def rejected(_username, _password):
        raise KeycloakLoginRejected('login failed (401)')

    monkeypatch.setattr(routes_auth, 'keycloak_login', rejected)

    user, error = await routes_auth._resolve_login('sarah.sales', 'password')

    assert user is None
    assert '401' in error


@pytest.mark.asyncio
async def test_keycloak_account_not_ready_uses_demo_fallback(monkeypatch, stub_db_roles):
    from acme_app.api import routes_auth
    from acme_app.auth.keycloak_client import KeycloakAccountNotReady
    from acme_app.config import settings

    async def not_ready(_username, _password):
        raise KeycloakAccountNotReady('Account is not fully set up')

    monkeypatch.setattr(routes_auth, 'keycloak_login', not_ready)
    monkeypatch.setattr(settings, 'demo_auth_fallback_enabled', True)

    user, error = await routes_auth._resolve_login('sarah.sales', 'password')

    assert error is None
    assert user is not None
    assert user.auth_source == 'demo_fallback'


@pytest.mark.asyncio
async def test_keycloak_unavailable_uses_demo_fallback(monkeypatch, stub_db_roles):
    from acme_app.api import routes_auth
    from acme_app.auth.keycloak_client import KeycloakUnavailable
    from acme_app.config import settings

    async def unavailable(_username, _password):
        raise KeycloakUnavailable('transport error')

    monkeypatch.setattr(routes_auth, 'keycloak_login', unavailable)
    monkeypatch.setattr(settings, 'demo_auth_fallback_enabled', True)

    user, error = await routes_auth._resolve_login('sarah.sales', 'password')

    assert error is None
    assert user is not None
    assert user.auth_source == 'demo_fallback'


@pytest.mark.asyncio
async def test_keycloak_unavailable_without_demo_fallback_denies(monkeypatch, stub_db_roles):
    from acme_app.api import routes_auth
    from acme_app.auth.keycloak_client import KeycloakUnavailable
    from acme_app.config import settings

    async def unavailable(_username, _password):
        raise KeycloakUnavailable('transport error')

    monkeypatch.setattr(routes_auth, 'keycloak_login', unavailable)
    monkeypatch.setattr(settings, 'demo_auth_fallback_enabled', False)

    user, error = await routes_auth._resolve_login('sarah.sales', 'password')

    assert user is None
    assert 'fallback is disabled' in error
