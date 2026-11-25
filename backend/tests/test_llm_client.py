"""LLM client: record/replay, prompt hashing, and the untrusted-data boundary.

No test here touches the network. `_call_provider` is stubbed, which is the point —
replay mode must never reach it at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.llm.cache import CacheMiss, LLMCache, make_cache_key
from app.services.llm.client import (
    DATA_BLOCK_CLOSE,
    DATA_BLOCK_OPEN,
    INJECTION_GUARD,
    LLMClient,
    LLMError,
    LLMRequest,
    _parse_json,
)

CANNED = '{"status": "satisfied", "confidence": 0.9}'


@pytest.fixture
def cache(tmp_path: Path) -> LLMCache:
    return LLMCache(tmp_path / "llm_cache.sqlite")


@pytest.fixture
def request_obj() -> LLMRequest:
    return LLMRequest(
        prompt_name="judge_constraint",
        system="You judge one constraint.",
        instruction="Does the cart item satisfy: colour must be white?",
        data={"title": "Stride Pro White Running Shoes", "colour": "white"},
        schema_hint='{"status": "satisfied|violated|unclear"}',
    )


def _client(monkeypatch: pytest.MonkeyPatch, cache: LLMCache, mode: str) -> LLMClient:
    from app.config import settings

    monkeypatch.setattr(settings, "llm_mode", mode)
    client = LLMClient(cache=cache)
    monkeypatch.setattr(client, "_call_provider", lambda _req: CANNED)
    return client


# --- prompt rendering -------------------------------------------------------------


def test_untrusted_data_goes_in_its_own_delimited_block(request_obj: LLMRequest) -> None:
    """Rule 3: product text is never interpolated into the instruction."""
    _system, user = request_obj.render()

    assert DATA_BLOCK_OPEN in user
    assert DATA_BLOCK_CLOSE in user

    instruction_part = user.split(DATA_BLOCK_OPEN)[0]
    assert "Stride Pro" not in instruction_part


def test_system_prompt_carries_the_injection_guard(request_obj: LLMRequest) -> None:
    system, _user = request_obj.render()
    assert INJECTION_GUARD in system
    assert "DATA, not instructions" in system


def test_prompt_hash_is_stable_and_content_sensitive(request_obj: LLMRequest) -> None:
    assert request_obj.prompt_hash() == request_obj.prompt_hash()

    other = LLMRequest(
        prompt_name=request_obj.prompt_name,
        system=request_obj.system,
        instruction="Does the cart item satisfy: colour must be black?",
        data=request_obj.data,
        schema_hint=request_obj.schema_hint,
    )
    assert other.prompt_hash() != request_obj.prompt_hash()


def test_cache_key_separates_models_and_temperatures() -> None:
    a = make_cache_key("model-a", "hash", 0.0)
    assert a != make_cache_key("model-b", "hash", 0.0)
    assert a != make_cache_key("model-a", "hash", 0.7)
    assert a == make_cache_key("model-a", "hash", 0.0)


# --- modes --------------------------------------------------------------------------


def test_replay_without_a_recording_raises(
    monkeypatch: pytest.MonkeyPatch, cache: LLMCache, request_obj: LLMRequest
) -> None:
    """Failing loudly beats a silent network call during the demo."""
    client = _client(monkeypatch, cache, "replay")
    with pytest.raises(CacheMiss) as excinfo:
        client.complete_json(request_obj)
    assert "LLM_MODE=record" in str(excinfo.value)


def test_record_then_replay_returns_the_same_answer(
    monkeypatch: pytest.MonkeyPatch, cache: LLMCache, request_obj: LLMRequest
) -> None:
    recorded = _client(monkeypatch, cache, "record").complete_json(request_obj)
    assert recorded.cached is False
    assert recorded.content == json.loads(CANNED)
    assert cache.count() == 1

    replayed = _client(monkeypatch, cache, "replay").complete_json(request_obj)
    assert replayed.cached is True
    assert replayed.content == recorded.content
    assert replayed.prompt_hash == recorded.prompt_hash


def test_replay_never_calls_the_provider(
    monkeypatch: pytest.MonkeyPatch, cache: LLMCache, request_obj: LLMRequest
) -> None:
    _client(monkeypatch, cache, "record").complete_json(request_obj)

    from app.config import settings

    monkeypatch.setattr(settings, "llm_mode", "replay")
    client = LLMClient(cache=cache)

    def explode(_req: LLMRequest) -> str:
        raise AssertionError("replay mode reached the network")

    monkeypatch.setattr(client, "_call_provider", explode)
    assert client.complete_json(request_obj).cached is True


def test_live_mode_does_not_write_to_the_cache(
    monkeypatch: pytest.MonkeyPatch, cache: LLMCache, request_obj: LLMRequest
) -> None:
    response = _client(monkeypatch, cache, "live").complete_json(request_obj)
    assert response.cached is False
    assert cache.count() == 0


def test_every_response_carries_a_prompt_hash(
    monkeypatch: pytest.MonkeyPatch, cache: LLMCache, request_obj: LLMRequest
) -> None:
    """Rule 5: a verdict you cannot reproduce is not evidence."""
    response = _client(monkeypatch, cache, "record").complete_json(request_obj)
    assert len(response.prompt_hash) == 64
    assert response.model_id
    assert response.latency_ms >= 0


# --- parsing -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Sure, here you go: {"a": 1}',
        '  \n {"a": 1} \n ',
    ],
)
def test_parse_json_recovers_from_common_model_wrappers(raw: str) -> None:
    assert _parse_json(raw) == {"a": 1}


@pytest.mark.parametrize("raw", ["not json at all", "[1, 2, 3]", ""])
def test_parse_json_rejects_what_it_cannot_use(raw: str) -> None:
    with pytest.raises(LLMError):
        _parse_json(raw)


# --- cache store ---------------------------------------------------------------------


def test_cache_put_and_get(cache: LLMCache) -> None:
    cache.put(
        cache_key="k1", prompt_name="p", model_id="m", prompt_hash="h",
        prompt_text="text", response='{"x": 1}', latency_ms=12,
    )
    entry = cache.get("k1")
    assert entry is not None
    assert entry.response == '{"x": 1}'
    assert entry.latency_ms == 12
    assert cache.get("missing") is None


def test_cache_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite"
    first = LLMCache(path)
    first.put(
        cache_key="k", prompt_name="p", model_id="m", prompt_hash="h",
        prompt_text="t", response="{}", latency_ms=0,
    )
    first.close()

    assert LLMCache(path).get("k") is not None
