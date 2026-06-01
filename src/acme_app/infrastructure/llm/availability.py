"""Which LLM models can actually be used *right now*.

A model is usable when its provider has working credentials:
- anthropic / openai / google  → the corresponding API key is set
- ollama (local)               → the server is actually reachable

This is the single source of truth for the two places that must degrade
gracefully when the reviewer hasn't supplied any model:

1. The composer's model dropdown (greys out unusable models; shows a
   "add a key to .env" banner when nothing is configured).
2. The DB Explorer AI assist, which picks any available model instead of
   assuming a local Ollama is running.

Key presence is cheap and synchronous. Ollama reachability requires a network
probe, so it is cached briefly to keep the dropdown/render path fast.
"""
from __future__ import annotations

import time

import httpx

from acme_app.config import settings
from acme_app.infrastructure.llm.model_registry import MODEL_REGISTRY, ModelSpec

# Shown to the user (UI + API) when no model is configured. Lists the exact
# env vars so the fix is obvious.
NO_MODEL_MESSAGE = (
    'No language model is configured. Add an API key to your .env '
    '(ANTHROPIC_API_KEY, OPENAI_API_KEY or GOOGLE_API_KEY) and restart, '
    'or start a local Ollama server (OLLAMA_BASE_URL). If a key is set but '
    'this keeps happening, check the key is valid and the account has credit.'
)

_OLLAMA_TTL_S = 30.0
_ollama_probe: dict[str, float | bool | None] = {'ok': None, 'ts': 0.0}


def provider_key_present(provider: str) -> bool:
    """True if the non-local provider has a credential configured. Ollama is
    handled separately because 'configured' there means 'reachable'."""
    if provider == 'anthropic':
        return bool(settings.anthropic_api_key)
    if provider == 'openai':
        return bool(settings.openai_api_key)
    if provider == 'google':
        return bool(settings.google_api_key)
    if provider == 'ollama':
        return bool(settings.ollama_base_url)
    return False


async def ollama_reachable() -> bool:
    """Probe the configured Ollama server (cached ~30s, 1s timeout).

    Unlike a key check, a local model is only 'available' if the server is
    actually up — a reviewer with the default OLLAMA_BASE_URL but no Ollama
    installed must see 'local' as unavailable, not falsely offered."""
    if not settings.ollama_base_url:
        return False
    now = time.time()
    cached = _ollama_probe['ok']
    if cached is not None and (now - float(_ollama_probe['ts'])) < _OLLAMA_TTL_S:
        return bool(cached)
    ok = False
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f'{settings.ollama_base_url.rstrip("/")}/api/tags')
            ok = resp.status_code == 200
    except Exception:
        ok = False
    _ollama_probe['ok'] = ok
    _ollama_probe['ts'] = now
    return ok


async def availability_by_provider() -> dict[str, bool]:
    """provider name -> usable right now."""
    return {
        'anthropic': provider_key_present('anthropic'),
        'openai': provider_key_present('openai'),
        'google': provider_key_present('google'),
        'ollama': await ollama_reachable(),
    }


async def model_availability() -> dict[str, bool]:
    """model_key -> usable, for every visible model in the registry."""
    by_provider = await availability_by_provider()
    return {
        key: bool(by_provider.get(spec.provider))
        for key, spec in MODEL_REGISTRY.items()
        if spec.visible
    }


async def available_specs() -> list[ModelSpec]:
    by_provider = await availability_by_provider()
    return [
        spec for spec in MODEL_REGISTRY.values()
        if spec.visible and by_provider.get(spec.provider)
    ]


async def any_model_available() -> bool:
    return bool(await available_specs())


async def assist_specs_ordered() -> list[ModelSpec]:
    """Available models ordered for a low-stakes assist call (DB Explorer field
    generation): a reachable local model first (zero cost), then the cheapest
    available cloud models by output price. The caller tries them in order and
    falls through to the next on failure, so a flaky local model never blocks
    the feature when a cloud key is also configured."""
    specs = await available_specs()
    return sorted(
        specs,
        key=lambda s: (0 if s.provider == 'ollama' else 1, s.output_per_1k, s.input_per_1k),
    )


async def pick_assist_spec() -> ModelSpec | None:
    """First-choice assist model (reachable local, else cheapest cloud), or
    None when nothing is configured. See assist_specs_ordered for the full
    fall-through order."""
    ordered = await assist_specs_ordered()
    return ordered[0] if ordered else None
