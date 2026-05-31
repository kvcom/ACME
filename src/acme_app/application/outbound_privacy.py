"""Outbound LLM privacy helpers.

External LLMs get minimized prompts/facts: personal identifiers are redacted
and customer names are replaced with stable database IDs. The app translates
those IDs back after the model responds so user-facing output stays readable.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY
from acme_app.policy.local_screener import apply_extra_redactions
from acme_app.policy.pii_redactor import redact


_PERSON_FIELD_HINTS = {
    'account_owner',
    'assigned_to',
    'created_by',
    'created_by_user',
    'customer_contact',
    'email',
    'owner',
    'phone',
    'username',
}


@dataclass(frozen=True)
class CustomerAlias:
    customer_id: str
    name: str
    token: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class UserAlias:
    user_id: str
    username: str
    display_name: str
    token: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class OutboundPrivacyContext:
    external: bool
    customers: tuple[CustomerAlias, ...]
    users: tuple[UserAlias, ...] = ()
    pii_substrings: tuple[str, ...] = ()

    @property
    def token_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for customer in self.customers:
            out[customer.token] = customer.name
            out[customer.customer_id] = customer.name
        for user in self.users:
            readable = user.display_name or user.username
            out[user.token] = readable
            out[user.user_id] = readable
        return out


@dataclass(frozen=True)
class SanitizedText:
    text: str
    customer_replacements: tuple[dict[str, Any], ...] = ()
    user_replacements: tuple[dict[str, Any], ...] = ()
    pii_redactions: tuple[dict[str, Any], ...] = ()


def is_external_model(model_key_or_provider: str | None) -> bool:
    key = (model_key_or_provider or '').lower()
    spec = MODEL_REGISTRY.get(key)
    if spec:
        return spec.provider != 'ollama'
    return key in {'anthropic', 'openai', 'google'}


def build_privacy_context(
    *,
    model_key_or_provider: str | None,
    customers: list[dict[str, Any]],
    users: list[dict[str, Any]] | None = None,
    pii_substrings: list[str] | None,
) -> OutboundPrivacyContext:
    aliases: list[CustomerAlias] = []
    first_tokens = Counter(
        parts[0].lower()
        for customer in customers
        for parts in [[part for part in re.split(r'\s+', str(customer.get('name') or '').strip()) if len(part) > 2]]
        if parts
    )
    for customer in customers:
        customer_id = str(customer.get('customer_id') or '').strip()
        name = str(customer.get('name') or '').strip()
        if not customer_id or not name:
            continue
        parts = [part for part in re.split(r'\s+', name) if len(part) > 2]
        customer_aliases = [name]
        # First-token shorthand covers common prompts like "Northwind", but
        # only when that shorthand is unambiguous across the customer table.
        if parts and first_tokens[parts[0].lower()] == 1:
            customer_aliases.append(parts[0])
        compact = name.replace(' ', '')
        if compact != name:
            customer_aliases.append(compact)
        aliases.append(CustomerAlias(
            customer_id=customer_id,
            name=name,
            token=f'customers.id={customer_id}',
            aliases=tuple(dict.fromkeys(customer_aliases)),
        ))
    user_aliases: list[UserAlias] = []
    for user in users or []:
        user_id = str(user.get('user_id') or '').strip()
        username = str(user.get('username') or '').strip()
        display_name = str(user.get('display_name') or '').strip()
        if not user_id or not username:
            continue
        candidates = [username, display_name]
        user_aliases.append(UserAlias(
            user_id=user_id,
            username=username,
            display_name=display_name,
            token=f'users.id={user_id}',
            aliases=tuple(dict.fromkeys(c for c in candidates if c)),
        ))
    return OutboundPrivacyContext(
        external=is_external_model(model_key_or_provider),
        customers=tuple(aliases),
        users=tuple(user_aliases),
        pii_substrings=tuple(pii_substrings or ()),
    )


def sanitize_text_with_report(text: str | None, privacy: OutboundPrivacyContext) -> SanitizedText:
    if not text:
        return SanitizedText(text or '')
    out = str(text)
    customer_replacements: list[dict[str, Any]] = []
    user_replacements: list[dict[str, Any]] = []
    for customer in sorted(privacy.customers, key=lambda c: len(c.name), reverse=True):
        matched: list[str] = []
        for alias in sorted(customer.aliases, key=len, reverse=True):
            pattern = re.compile(r'\b' + re.escape(alias) + r'\b', flags=re.I)
            if pattern.search(out):
                matched.append(alias)
                out = pattern.sub(customer.token, out)
        if matched:
            customer_replacements.append(_customer_replacement(customer, matched))
    for user in sorted(privacy.users, key=lambda u: max((len(a) for a in u.aliases), default=0), reverse=True):
        matched = []
        for alias in sorted(user.aliases, key=len, reverse=True):
            if '@' in alias or '.' in alias:
                if alias in out:
                    matched.append(alias)
                out = out.replace(alias, user.token)
            else:
                pattern = re.compile(r'\b' + re.escape(alias) + r'\b', flags=re.I)
                if pattern.search(out):
                    matched.append(alias)
                    out = pattern.sub(user.token, out)
        if matched:
            user_replacements.append(_user_replacement(user, matched))
    out = _redact_preserving_tokens(out, privacy)
    pii_redactions: list[dict[str, Any]] = []
    if privacy.pii_substrings:
        pii_redactions = [
            {'internal': '[PII substring]', 'outbound': '[REDACTED-LLM]', 'length': len(s)}
            for s in privacy.pii_substrings
            if s in out or s in (text or '')
        ]
        out = apply_extra_redactions(out, list(privacy.pii_substrings))
    return SanitizedText(
        out,
        customer_replacements=tuple(customer_replacements),
        user_replacements=tuple(user_replacements),
        pii_redactions=tuple(pii_redactions),
    )


def sanitize_text_for_llm(text: str | None, privacy: OutboundPrivacyContext) -> str:
    return sanitize_text_with_report(text, privacy).text
    return out


def _redact_preserving_tokens(text: str, privacy: OutboundPrivacyContext) -> str:
    tokens = [c.token for c in privacy.customers] + [u.token for u in privacy.users]
    protected: dict[str, str] = {}
    out = text
    for idx, token in enumerate(tokens):
        placeholder = f'__ACME_PRIVACY_TOKEN_{idx}__'
        if token in out:
            protected[placeholder] = token
            out = out.replace(token, placeholder)
    out = redact(out)
    for placeholder, token in protected.items():
        out = out.replace(placeholder, token)
    return out


def restore_text_from_llm(text: str | None, privacy: OutboundPrivacyContext) -> str:
    if not text:
        return text or ''
    out = str(text)
    for token, name in privacy.token_map.items():
        out = out.replace(token, name)
    out = _scrub_internal_token_leaks(out)
    return out


def _scrub_internal_token_leaks(text: str) -> str:
    """Remove privacy implementation details if a model repeats instructions."""
    out = re.sub(r'\bcustomers\.id=<uuid>', 'customer record token', text, flags=re.I)
    out = re.sub(r'\busers\.id=<uuid>', 'user record token', out, flags=re.I)
    out = re.sub(r'\bcustomers\.id=[0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4}\b', '[customer record]', out, flags=re.I)
    out = re.sub(r'\busers\.id=[0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4}\b', '[user record]', out, flags=re.I)
    out = re.sub(r'\bdatabase record IDs?\b', 'customer names', out, flags=re.I)
    out = re.sub(r'\bDB record IDs?\b', 'customer names', out, flags=re.I)
    out = re.sub(r'\bPrivacy mode:?\s*', '', out, flags=re.I)
    return out


def translate_customer_args_to_names(plan: Any, privacy: OutboundPrivacyContext) -> None:
    token_map = privacy.token_map
    for step in getattr(plan, 'steps', []) or []:
        args = getattr(step, 'arguments', None)
        if not isinstance(args, dict):
            continue
        customer_name = str(args.get('customer_name') or '').strip()
        if customer_name in token_map:
            args['customer_name'] = token_map[customer_name]


def sanitize_facts_for_llm(facts: dict[str, Any], privacy: OutboundPrivacyContext) -> dict[str, Any]:
    return _sanitize_value(copy.deepcopy(facts), privacy, key_hint='')


def privacy_manifest(
    privacy: OutboundPrivacyContext,
    *,
    applied: SanitizedText | None = None,
    include_dictionary: bool = False,
) -> dict[str, Any]:
    customer_replacements = (
        list(applied.customer_replacements)
        if applied is not None else []
    )
    user_replacements = (
        list(applied.user_replacements)
        if applied is not None else []
    )
    pii_redactions = (
        list(applied.pii_redactions)
        if applied is not None else [
            {'internal': '[PII substring]', 'outbound': '[REDACTED-LLM]', 'length': len(s)}
            for s in privacy.pii_substrings
        ]
    )
    manifest = {
        'external': privacy.external,
        'customer_replacements': customer_replacements,
        'user_replacements': user_replacements,
        'pii_redactions': pii_redactions,
        'available_dictionary_counts': {
            'customers': len(privacy.customers),
            'users': len(privacy.users),
        },
    }
    if not include_dictionary:
        return manifest
    manifest['available_dictionary'] = {
        'customers': [_customer_replacement(c, list(c.aliases)) for c in privacy.customers],
        'users': [_user_replacement(u, list(u.aliases)) for u in privacy.users],
    }
    return manifest


def _customer_replacement(customer: CustomerAlias, aliases: list[str]) -> dict[str, Any]:
    return {
        'internal': customer.name,
        'outbound': customer.token,
        'aliases': list(dict.fromkeys(aliases)),
    }


def _user_replacement(user: UserAlias, aliases: list[str]) -> dict[str, Any]:
    return {
        'internal': user.display_name or user.username,
        'outbound': user.token,
        'aliases': list(dict.fromkeys(aliases)),
    }


def privacy_diff(
    *,
    readable_query: str,
    outbound_query: str,
    readable_facts: dict[str, Any] | None = None,
    outbound_facts: dict[str, Any] | None = None,
    inbound_text: str | None = None,
    restored_text: str | None = None,
    privacy: OutboundPrivacyContext,
    applied: SanitizedText | None = None,
) -> dict[str, Any]:
    return {
        **privacy_manifest(privacy, applied=applied),
        'query': {
            'readable': readable_query,
            'outbound': outbound_query,
            'changed': readable_query != outbound_query,
        },
        'facts': {
            'readable_keys': sorted((readable_facts or {}).keys()),
            'outbound_keys': sorted((outbound_facts or {}).keys()),
            'changed': outbound_facts is not None and readable_facts != outbound_facts,
        },
        'inbound': {
            'raw_model_text': inbound_text,
            'restored_text': restored_text,
            'changed': (inbound_text or '') != (restored_text or ''),
        },
    }


def _sanitize_value(value: Any, privacy: OutboundPrivacyContext, key_hint: str) -> Any:
    key_l = key_hint.lower()
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _is_person_field(str(key)):
                out[key] = _sanitize_person_value(item, privacy)
            else:
                out[key] = _sanitize_value(item, privacy, str(key))
        return out
    if isinstance(value, list):
        return [_sanitize_value(item, privacy, key_hint) for item in value]
    if isinstance(value, str):
        if _is_person_field(key_l):
            sanitized = sanitize_text_for_llm(value, privacy)
            return sanitized if sanitized != value else '[REDACTED-PERSON]'
        return sanitize_text_for_llm(value, privacy)
    return value


def _is_person_field(key: str) -> bool:
    key_l = key.lower()
    return any(hint == key_l or key_l.endswith('_' + hint) or hint in key_l for hint in _PERSON_FIELD_HINTS)


def _redacted_value(value: Any) -> Any:
    if isinstance(value, list):
        return ['[REDACTED-PERSON]' for _ in value]
    if isinstance(value, dict):
        return {key: '[REDACTED-PERSON]' for key in value}
    if value in (None, ''):
        return value
    return '[REDACTED-PERSON]'


def _sanitize_person_value(value: Any, privacy: OutboundPrivacyContext) -> Any:
    if isinstance(value, str):
        sanitized = sanitize_text_for_llm(value, privacy)
        return sanitized if sanitized != value else '[REDACTED-PERSON]'
    if isinstance(value, list):
        return [_sanitize_person_value(item, privacy) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_person_value(item, privacy) for key, item in value.items()}
    return _redacted_value(value)
