"""Single gate that combines RBAC and the action catalogue.

All write paths go through this module — including the eval runner — so denials
are uniform and auditable.
"""
import hashlib
import hmac
import time

from acme_app.config import settings
from acme_app.policy import action_catalogue
from acme_app.policy.rbac import check


def can_propose(role: str, action_type: str) -> tuple[bool, str]:
    if not action_catalogue.validate_action_type(action_type):
        return False, f'Unknown action_type: {action_type}'
    if not action_catalogue.role_allowed(role, action_type):
        return False, f'{role} not in allowed_roles for {action_type}'
    decision = check(role, 'create_action')
    return decision.allowed, decision.reason


def can_update_action(role: str, target_status: str) -> tuple[bool, str]:
    if target_status == 'Cancelled':
        decision = check(role, 'cancel_action')
        return decision.allowed, decision.reason
    decision = check(role, 'update_action')
    return decision.allowed, decision.reason


def can_update_issue_status(role: str) -> tuple[bool, str]:
    decision = check(role, 'update_issue_status')
    return decision.allowed, decision.reason


def idempotency_key(trace_ref: str, action_type: str, issue_ref: str) -> str:
    raw = f'{trace_ref}|{action_type}|{issue_ref}'.encode()
    return hashlib.sha256(raw).hexdigest()


def mint_confirmation_token(trace_ref: str, action_type: str, issue_ref: str, expires_in_s: int = 600) -> str:
    expires_at = int(time.time()) + expires_in_s
    payload = f'{trace_ref}|{action_type}|{issue_ref}|{expires_at}'
    sig = hmac.new(settings.confirmation_hmac_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f'{payload}|{sig}'


def verify_confirmation_token(token: str) -> tuple[bool, str, dict[str, str]]:
    parts = token.split('|')
    if len(parts) != 5:
        return False, 'token malformed', {}
    trace_ref, action_type, issue_ref, expires_at_s, sig = parts
    payload = f'{trace_ref}|{action_type}|{issue_ref}|{expires_at_s}'
    expected = hmac.new(settings.confirmation_hmac_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False, 'signature mismatch', {}
    try:
        expires_at = int(expires_at_s)
    except ValueError:
        return False, 'expiry malformed', {}
    if time.time() > expires_at:
        return False, 'token expired', {}
    return True, 'ok', {'trace_ref': trace_ref, 'action_type': action_type, 'issue_ref': issue_ref}
