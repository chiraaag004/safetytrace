"""Live-mode client logic tested against a fake OpenAI-compatible provider (no network)."""
import json
from types import SimpleNamespace

import pytest

from safetytrace import config
from safetytrace.llm import client


class FakeCompletions:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))],
                               usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7))


class HTTP400(Exception):
    status_code = 400


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "QUOTA_STATE_FILE", tmp_path / "quota_state.json")  # never touch the real one
    monkeypatch.setattr(client, "_exhausted", {})
    monkeypatch.setattr(config, "LLM_MODE", "live")
    monkeypatch.setattr(config, "GEMINI_API_KEYS", ["gem-key-1"])
    monkeypatch.setattr(config, "GROQ_API_KEYS", ["groq-key-1"])
    monkeypatch.setattr(client, "_cooling", {})
    monkeypatch.setattr(config, "USE_CACHE", False)
    monkeypatch.setattr(config, "RPM", {"gemini": 100000, "groq": 100000})
    monkeypatch.setattr(config, "GEMINI_API_STYLE", "openai")
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    return monkeypatch


def _install(monkeypatch, replies):
    fake = FakeCompletions(replies)
    monkeypatch.setattr(client, "_client", lambda provider, key=None: SimpleNamespace(chat=SimpleNamespace(completions=fake)))
    return fake


SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}}


def test_parses_json_and_records_usage(live):
    fake = _install(live, ['{"a": "x"}'])
    from safetytrace.models import CaseState
    s = CaseState(case_id="T")
    assert client.generate_json("extract", "sys", "user", SCHEMA, state=s) == {"a": "x"}
    rf = fake.calls[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"]["additionalProperties"] is False
    assert s.usage["requests"] == 1 and s.usage["prompt_tokens"] == 11


def test_falls_back_to_json_object_on_400(live):
    fake = _install(live, [HTTP400("schema not supported"), '```json\n{"a": "y"}\n```'])
    assert client.generate_json("verify", "sys", "user", SCHEMA) == {"a": "y"}
    assert fake.calls[1]["response_format"] == {"type": "json_object"}
    assert "JSON schema" in fake.calls[1]["messages"][0]["content"]


def test_retries_invalid_json_then_fails_loudly(live):
    _install(live, ["not json"] * 5)
    with pytest.raises(client.LLMError):
        client.generate_json("draft", "sys", "user", SCHEMA)


def test_roles_route_to_two_different_providers():
    assert config.ROLES["draft"][0] == "gemini" and config.ROLES["verify"][0] == "groq"


def test_sends_low_reasoning_effort_and_drops_it_if_rejected(live):
    class HTTP400R(Exception):
        status_code = 400
    fake = _install(live, [HTTP400R("Unknown parameter: reasoning_effort"), '{"a": "z"}'])
    client._reasoning_rejected.clear()
    assert client.generate_json("draft", "sys", "user", SCHEMA) == {"a": "z"}
    assert fake.calls[0].get("reasoning_effort") == "low"
    assert "reasoning_effort" not in fake.calls[1]


def test_native_gemini_path(live, monkeypatch):
    import httpx
    monkeypatch.setattr(config, "GEMINI_API_STYLE", "native")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    sent = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        sent.update(url=url, body=json, headers=headers)
        return SimpleNamespace(status_code=200, text="", json=lambda: {
            "candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"a": "n"}'}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "thoughtsTokenCount": 2}})
    monkeypatch.setattr(httpx, "post", fake_post)
    from safetytrace.models import CaseState
    s = CaseState(case_id="T")
    assert client.generate_json("draft", "sys", [{"type": "text", "text": "hi"},
                                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}], SCHEMA, state=s) == {"a": "n"}
    assert ":generateContent" in sent["url"] and sent["headers"]["x-goog-api-key"] == "gem-key-1"
    assert sent["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert sent["body"]["contents"][0]["parts"][1]["inline_data"]["data"] == "QUJD"
    assert s.usage["completion_tokens"] == 5


def test_timeouts_fail_fast(live, monkeypatch):
    class Timeout(Exception):
        pass
    fake = _install(live, [Timeout("Request timed out."), Timeout("Request timed out."), '{"a": "late"}'])
    with pytest.raises(client.LLMError):
        client.generate_json("verify", "sys", "user", SCHEMA)
    assert len(fake.calls) == 2
    assert client.is_exhausted("groq", config.GROQ_MODEL)  # skipped for 30 min instead of 2 more timeouts per call


class HTTP429(Exception):
    status_code = 429


GEMINI_DAILY = ("HTTP 429: You exceeded your current quota. Quota exceeded for metric: "
                "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20. "
                "Generate content requests per day limit exceeded on Free Tier. Please retry in 15h37m47.6s.")


@pytest.fixture
def quota_file(live, tmp_path):
    live.setattr(config, "QUOTA_STATE_FILE", tmp_path / "quota_state.json")
    client.reset_quota_state()
    yield tmp_path / "quota_state.json"
    client.reset_quota_state()


def test_retry_after_parsing():
    assert client._retry_after("Please retry in 15h37m47.6s.") == pytest.approx(15 * 3600 + 37 * 60 + 47.6)
    assert client._retry_after("Please try again in 7.5s") == pytest.approx(7.5)
    assert client._retry_after("try again in 1m2.5s") == pytest.approx(62.5)
    assert client._is_daily_quota(GEMINI_DAILY) and not client._is_daily_quota("limit per minute, retry in 30s")


def test_daily_quota_skips_to_next_model_without_waiting(live, quota_file):
    """Gemini daily quota hit -> immediately answered by the next model in the chain; remembered for the next call."""
    live.setattr(config, "ROLES", {**config.ROLES, "draft": ("gemini", "g-main")})
    live.setattr(config, "GEMINI_FALLBACK_MODELS", ["g-lite"])
    slept = []
    live.setattr(client.time, "sleep", lambda s: slept.append(s))
    fake = _install(live, [HTTP429(GEMINI_DAILY), '{"a": "lite"}', '{"a": "lite2"}'])
    from safetytrace.models import CaseState
    s = CaseState(case_id="T")
    assert client.generate_json("draft", "sys", "user", SCHEMA, state=s) == {"a": "lite"}
    assert [c["model"] for c in fake.calls] == ["g-main", "g-lite"] and not [x for x in slept if x > 1]
    assert s.usage["models"]["draft"] == "gemini:g-lite" and s.usage["fallbacks"]
    assert client.is_exhausted("gemini", "g-main") and quota_file.exists()
    # second call: the exhausted model is not even tried
    assert client.generate_json("draft", "sys", "user2", SCHEMA) == {"a": "lite2"}
    assert [c["model"] for c in fake.calls] == ["g-main", "g-lite", "g-lite"]


def test_draft_never_falls_back_to_verifier_model():
    for role in ("draft",):
        assert (("groq", config.GROQ_MODEL)) not in config.role_chain(role)
    assert config.role_chain("verify") == [("groq", config.GROQ_MODEL)]
    assert config.ROLES["planner"][0] == "groq"


def test_all_models_exhausted_raises(live, quota_file):
    live.setattr(config, "ROLES", {**config.ROLES, "transcribe": ("gemini", "g-main")})
    live.setattr(config, "GEMINI_FALLBACK_MODELS", [])
    _install(live, [HTTP429(GEMINI_DAILY)])
    with pytest.raises(client.LLMError, match="no model in the chain"):
        client.generate_json("transcribe", "sys", "user", SCHEMA)


def test_per_minute_limit_waits_then_retries_same_model(live, quota_file):
    slept = []
    live.setattr(client.time, "sleep", lambda s: slept.append(s))
    fake = _install(live, [HTTP429("Rate limit reached: tokens per minute (TPM). Please try again in 4.2s."), '{"a": "ok"}'])
    assert client.generate_json("verify", "sys", "user", SCHEMA) == {"a": "ok"}
    assert len(fake.calls) == 2 and fake.calls[0]["model"] == fake.calls[1]["model"]
    assert slept and 5 <= slept[0] < 6 and not client.is_exhausted("groq", config.GROQ_MODEL)


def test_gemma_request_has_no_system_or_json_mode_and_prose_json_is_parsed(live, monkeypatch):
    import httpx
    monkeypatch.setattr(config, "GEMINI_API_STYLE", "native")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(config, "ROLES", {**config.ROLES, "extract": ("gemini", "gemma-4-26b-a4b-it")})
    sent = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        sent.update(body=json)
        return SimpleNamespace(status_code=200, text="", json=lambda: {
            "candidates": [{"content": {"parts": [{"text": 'Here is the JSON:\n```json\n{"a": "g"}\n```'}]}}], "usageMetadata": {}})
    monkeypatch.setattr(httpx, "post", fake_post)
    assert client.generate_json("extract", "sys", "user", SCHEMA) == {"a": "g"}
    b = sent["body"]
    assert "systemInstruction" not in b and "responseMimeType" not in b["generationConfig"]
    assert b["contents"][0]["parts"][0]["text"].startswith("sys") and b["contents"][0]["parts"][1]["text"] == "user"


def test_per_minute_limit_moves_to_next_model_instead_of_waiting(live, monkeypatch):
    slept = []
    monkeypatch.setattr(client.time, "sleep", lambda s: slept.append(s))
    fake = _install(monkeypatch, [HTTP429("Rate limit reached: tokens per minute (TPM). Please try again in 9s."), '{"a": "ok"}'])
    assert client.generate_json("planner", "sys", "user", SCHEMA) == {"a": "ok"}
    assert fake.calls[0]["model"] != fake.calls[1]["model"]
    assert not [x for x in slept if x > 1] and not client.is_exhausted("groq", fake.calls[0]["model"])



def test_keys_are_rotated_before_models(live, quota_file):
    """Key 1 out of daily quota -> key 2 on the SAME model answers; only then would the next model be tried."""
    live.setattr(config, "GEMINI_API_KEYS", ["key-A", "key-B"])
    live.setattr(config, "ROLES", {**config.ROLES, "draft": ("gemini", "g-lite")})
    live.setattr(config, "GEMINI_FALLBACK_MODELS", ["g-flash"])
    used = []

    class Fake:
        def create(self, **kw):
            used.append((kw["model"], cur["key"]))
            if cur["key"] == "key-A":
                raise HTTP429(GEMINI_DAILY)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"a": "ok"}'))],
                                   usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
    cur = {}

    def fake_client(provider, key=None):
        cur["key"] = key
        return SimpleNamespace(chat=SimpleNamespace(completions=Fake()))
    live.setattr(client, "_client", fake_client)
    assert client.generate_json("draft", "sys", "u", SCHEMA) == {"a": "ok"}
    assert used == [("g-lite", "key-A"), ("g-lite", "key-B")]
    assert client.is_exhausted("gemini", "g-lite", client.key_id("key-A"))
    assert not client.is_exhausted("gemini", "g-lite")  # key-B still has quota for this model
    state = json.loads(quota_file.read_text())
    assert not any("key-A" in k for k in state)  # keys are never written to disk, only fingerprints


def test_per_minute_limit_on_one_key_moves_to_next_key_without_sleeping(live, quota_file):
    live.setattr(config, "GROQ_API_KEYS", ["q1", "q2"])
    slept = []
    live.setattr(client.time, "sleep", lambda s: slept.append(s))
    calls = []

    def fake_client(provider, key=None):
        class F:
            def create(self, **kw):
                calls.append(key)
                if key == "q1":
                    raise HTTP429("Rate limit reached: tokens per minute (TPM). Please try again in 7s.")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"a": "q2"}'))],
                                       usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
        return SimpleNamespace(chat=SimpleNamespace(completions=F()))
    live.setattr(client, "_client", fake_client)
    assert client.generate_json("verify", "sys", "u", SCHEMA) == {"a": "q2"}
    assert calls == ["q1", "q2"] and not [s for s in slept if s > 1]


def test_key_lists_alone_are_enough(monkeypatch):
    """Only GEMINI_API_KEYS / GROQ_API_KEYS set in .env (no single-key variables): everything must still see a key."""
    import importlib
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEYS", "g1, g2")
    monkeypatch.setenv("GROQ_API_KEYS", "q1")
    import safetytrace.config as cfg
    try:
        importlib.reload(cfg)
        assert cfg.GEMINI_API_KEYS == ["g1", "g2"] and cfg.GEMINI_API_KEY == "g1"
        assert cfg.GROQ_API_KEYS == ["q1"] and cfg.GROQ_API_KEY == "q1"
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)
