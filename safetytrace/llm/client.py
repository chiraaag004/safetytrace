"""Provider adapter. Every LLM call in the codebase goes through `generate_json`.

* Callers name a ROLE (planner, extract, draft, verify, ...), never a model.
  config.ROLES maps roles to (provider, model).
* Two providers: gemini (Google AI Studio free tier, native generateContent API) and groq (free tier,
  OpenAI-compatible API). Each role has a model CHAIN (config.role_chain): when a model is out of its daily
  free quota it is skipped at once (remembered in logs/quota_state.json) and the next model answers.
* LLM_MODE=mock returns the caller-supplied `mock_response` (a deterministic test double),
  so the whole pipeline, the UI and the tests run with no keys and no network.
* Live mode adds: per-provider throttle, disk cache, retries with backoff, JSON-schema output with a
  json_object fallback, and a JSONL log line for every call (logs/llm_calls.jsonl).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Optional

from .. import config
from ..utils.logging_setup import get_logger, log_llm_call

log = get_logger("llm")

_clients: dict[str, Any] = {}
_last_call: dict[str, float] = {}
_reasoning_rejected: set[str] = set()  # providers/models that refused the reasoning_effort parameter
_cache = None


class LLMError(RuntimeError):
    pass


def key_id(key: str) -> str:
    """Short fingerprint used in logs and quota_state.json (the key itself is never written anywhere)."""
    return "k" + hashlib.sha256(key.encode()).hexdigest()[:6] if key else "k-none"


def _client(provider: str, key: str | None = None):
    key = config.api_keys(provider)[0] if key is None else key
    cid = f"{provider}:{key_id(key)}"
    if cid not in _clients:
        from openai import OpenAI

        if not key:
            raise LLMError(f"Missing API key for provider '{provider}'. Set it in .env or switch LLM_MODE=mock.")
        _clients[cid] = OpenAI(base_url=config.PROVIDER_BASE_URLS[provider], api_key=key, max_retries=0, timeout=config.LLM_TIMEOUT)
    return _clients[cid]


def _get_cache():
    global _cache
    if _cache is None and config.USE_CACHE:
        import diskcache

        _cache = diskcache.Cache(str(config.CACHE_DIR))
    return _cache


def _throttle(provider: str, kid: str = "") -> None:
    """Requests-per-minute pacing, per provider AND key (each key's project has its own limit)."""
    slot = f"{provider}:{kid}"
    gap = 60.0 / max(1, config.RPM.get(provider, 10))
    wait = gap - (time.time() - _last_call.get(slot, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_call[slot] = time.time()


def _record_usage(state, role: str, prompt_tokens: int, completion_tokens: int) -> None:
    if state is None:
        return
    u = state.usage
    u["requests"] = u.get("requests", 0) + 1
    u["prompt_tokens"] = u.get("prompt_tokens", 0) + prompt_tokens
    u["completion_tokens"] = u.get("completion_tokens", 0) + completion_tokens
    br = u.setdefault("by_role", {}).setdefault(role, {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0})
    br["requests"] += 1
    br["prompt_tokens"] += prompt_tokens
    br["completion_tokens"] += completion_tokens


def _strictify(schema: dict) -> dict:
    """Make a JSON schema strict-mode friendly: every object lists all properties as required
    and forbids additional properties (required by Groq strict mode)."""
    schema = json.loads(json.dumps(schema))

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


class ProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


_thinking_rejected: set[str] = set()


def _to_gemini_parts(user) -> list[dict]:
    """Convert OpenAI-style user content (str or [{type:text|image_url}]) to Gemini parts."""
    if isinstance(user, str):
        return [{"text": user}]
    parts = []
    for item in user:
        if item.get("type") == "text":
            parts.append({"text": item["text"]})
        elif item.get("type") == "image_url":
            url = item["image_url"]["url"]
            mime, b64 = url.split(";base64,", 1)
            parts.append({"inline_data": {"mime_type": mime.replace("data:", ""), "data": b64}})
    return parts


def _gemini_native(model: str, system: str, user, schema: dict, temperature: float, api_key: str = "") -> tuple[str, int, int]:
    """Google's native generateContent API. JSON mode + schema in the system prompt (portable across models)."""
    import httpx

    instructions = system + "\n\nReturn ONLY a JSON object that matches this JSON schema:\n" + json.dumps(schema)
    if model.startswith("gemma"):
        # Open-weight Gemma on the Gemini API: no system instruction, JSON mode or thinking config -> instructions go
        # into the user turn and the JSON object is parsed out of the text reply.
        parts = _to_gemini_parts(user)
        parts.insert(0, {"text": instructions + "\n\n---\n"})
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": temperature}}
    else:
        gen = {"temperature": temperature, "responseMimeType": "application/json"}
        effort = config.REASONING_EFFORT.get("gemini")
        if effort and model not in _thinking_rejected:
            gen["thinkingConfig"] = ({"thinkingBudget": 0} if model.startswith("gemini-2.5-flash") else {"thinkingLevel": effort})
        body = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": _to_gemini_parts(user)}],
            "generationConfig": gen,
        }
    r = httpx.post(config.GEMINI_NATIVE_URL.format(model=model), json=body, timeout=config.LLM_TIMEOUT,
                   headers={"x-goog-api-key": api_key or config.GEMINI_API_KEY, "Content-Type": "application/json"})
    if r.status_code != 200:
        txt = r.text[:600]
        if r.status_code == 400 and "thinking" in txt.lower() and model not in _thinking_rejected:
            _thinking_rejected.add(model)
        raise ProviderHTTPError(r.status_code, txt)
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise ProviderHTTPError(500, f"no candidates: {json.dumps(data)[:400]}")
    text = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if not p.get("thought"))
    um = data.get("usageMetadata", {})
    return text, int(um.get("promptTokenCount", 0)), int(um.get("candidatesTokenCount", 0)) + int(um.get("thoughtsTokenCount", 0))


# ------------------------------------------------------------------ quota bookkeeping
# "provider:model" -> unix time until which the model is skipped (daily quota hit / model missing). Persisted so the
# next run skips an exhausted model immediately instead of waiting through retries.
_exhausted: dict[str, float] | None = None


def _load_exhausted() -> dict[str, float]:
    global _exhausted
    if _exhausted is None:
        try:
            _exhausted = {k: float(v) for k, v in json.loads(config.QUOTA_STATE_FILE.read_text(encoding="utf-8")).items()}
        except Exception:
            _exhausted = {}
    return _exhausted


def _slot(provider: str, model: str, kid: str | None) -> str:
    return f"{provider}:{model}" + (f"@{kid}" if kid else "")


def _mark_exhausted(provider: str, model: str, seconds: float, why: str, kid: str | None = None) -> None:
    ex = _load_exhausted()
    ex[_slot(provider, model, kid)] = time.time() + seconds
    log.warning("%s unavailable for ~%.1f h (%s); moving on to the next key/model", _slot(provider, model, kid), seconds / 3600, why)
    try:
        config.QUOTA_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.QUOTA_STATE_FILE.write_text(json.dumps({k: v for k, v in ex.items() if v > time.time()}, indent=1), encoding="utf-8")
    except Exception:  # pragma: no cover
        pass


def is_exhausted(provider: str, model: str, kid: str | None = None) -> bool:
    """kid=None: True if the model is out for EVERY configured key."""
    ex, now = _load_exhausted(), time.time()
    if kid is not None:
        return ex.get(_slot(provider, model, kid), 0) > now or ex.get(_slot(provider, model, None), 0) > now
    return all(is_exhausted(provider, model, key_id(k)) for k in config.api_keys(provider))


_cooling: dict[str, float] = {}  # per-minute limits: slot -> time when it can be tried again (in memory only)


def _cooldown_left(provider: str, model: str, kid: str) -> float:
    return max(0.0, _cooling.get(_slot(provider, model, kid), 0) - time.time())


def quota_status() -> dict[str, str]:
    """Models currently skipped and when they come back (for the UI / check script)."""
    now = time.time()
    return {k: f"back in {(v - now) / 3600:.1f} h" for k, v in _load_exhausted().items() if v > now}


def reset_quota_state() -> None:
    global _exhausted
    _exhausted = {}
    try:
        config.QUOTA_STATE_FILE.unlink()
    except Exception:
        pass


_RETRY_RE = re.compile(r"(?:retry|try again) in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?", re.I)
_RETRY_DELAY_RE = re.compile(r'"retryDelay"\s*:\s*"([\d.]+)s"')


def _retry_after(msg: str) -> float | None:
    m = _RETRY_RE.search(msg)
    if m and any(m.groups()):
        h, mi, s = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    m = _RETRY_DELAY_RE.search(msg)
    return float(m.group(1)) if m else None


def _is_daily_quota(msg: str) -> bool:
    return bool(re.search(r"per ?day|perday|per_day|\(RPD\)|\(TPD\)|daily", msg, re.I))


def _is_model_missing(status, msg: str) -> bool:
    low = msg.lower()
    return status == 404 or "model_not_found" in low or "does not exist" in low or "decommissioned" in low or (
        "not found" in low and "model" in low)


class _NextModel(Exception):
    """This model cannot answer now; try the next one in the role's chain."""


class _CoolDown(_NextModel):
    """Per-minute limit on this key+model: try another key/model now, come back after `seconds`."""

    def __init__(self, msg: str, seconds: float):
        super().__init__(msg)
        self.seconds = seconds


def _call_model(provider: str, model: str, role: str, system: str, user, schema: dict, name: str, temperature: float,
                can_skip: bool = False, api_key: str | None = None):
    api_key = config.api_keys(provider)[0] if api_key is None else api_key
    kid = key_id(api_key)
    if not api_key:
        raise _NextModel(f"no API key configured for {provider}")
    native = provider == "gemini" and config.GEMINI_API_STYLE == "native"
    client = None if native else _client(provider, api_key)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    response_format = {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}
    last_err: Exception | None = None
    timeouts = bad_json = minute_limits = 0
    for attempt in range(5):
        _throttle(provider, kid)
        t0 = time.time()
        try:
            if native:
                text, pt, ct = _gemini_native(model, system, user, schema, temperature, api_key)
            else:
                kwargs = dict(model=model, messages=messages, temperature=temperature, response_format=response_format)
                effort = config.REASONING_EFFORT.get(provider)
                if effort and f"{provider}:{model}" not in _reasoning_rejected:
                    kwargs["reasoning_effort"] = effort
                r = client.chat.completions.create(**kwargs)
                text = r.choices[0].message.content or ""
                pt = getattr(r.usage, "prompt_tokens", 0) or 0
                ct = getattr(r.usage, "completion_tokens", 0) or 0
            text = text.strip()
            if text.startswith("```"):
                text = text.strip("`")
                text = text[text.find("{"):]
            try:
                out = json.loads(text)
            except json.JSONDecodeError:
                if "{" not in text or "}" not in text:
                    raise
                out = json.loads(text[text.find("{"):text.rfind("}") + 1])  # JSON object wrapped in prose
            if not isinstance(out, dict):
                raise json.JSONDecodeError("top-level JSON is not an object", text, 0)
            log_llm_call({"mode": "live", "role": role, "provider": provider, "model": model, "key": kid, "latency_s": round(time.time() - t0, 2),
                          "prompt_tokens": pt, "completion_tokens": ct, "attempt": attempt,
                          "prompt": json.dumps(messages, default=str)[-3000:], "response": text})
            return out, pt, ct
        except json.JSONDecodeError as e:
            last_err, bad_json = e, bad_json + 1
            log.warning("role=%s %s attempt=%d invalid JSON", role, model, attempt)
            if bad_json >= 3:
                break
        except Exception as e:  # provider errors
            last_err = e
            msg = str(e)
            log_llm_call({"mode": "live", "role": role, "provider": provider, "model": model, "key": kid, "error": msg[:1000], "attempt": attempt})
            status = getattr(e, "status_code", None)
            if "timed out" in msg.lower() or "timeout" in type(e).__name__.lower():
                timeouts += 1
                log.warning("role=%s %s timed out after %ss (%d)", role, model, config.LLM_TIMEOUT, timeouts)
                if timeouts >= 2:  # a model that times out twice will not recover soon: skip it for 30 min
                    _mark_exhausted(provider, model, 1800, "timed out twice", kid)
                    raise _NextModel(f"timed out twice ({config.LLM_TIMEOUT}s each)") from e
            elif status == 429 or "429" in msg or "rate limit" in msg.lower() or "quota" in msg.lower():
                wait = _retry_after(msg)
                if _is_daily_quota(msg) or (wait is not None and wait > 120):
                    _mark_exhausted(provider, model, wait if wait else 6 * 3600, "daily quota", kid)
                    raise _NextModel(f"daily quota exhausted: {msg[:200]}") from e
                if can_skip:  # another key/model can answer now instead of waiting for this one's minute window
                    raise _CoolDown(f"per-minute limit: {msg[:120]}", (wait or 10) + 1) from e
                minute_limits += 1
                if minute_limits > 3:
                    break
                sleep = min(60.0, (wait or 8 * minute_limits) + 1)
                log.warning("role=%s %s per-minute limit; sleeping %.0fs", role, model, sleep)
                time.sleep(sleep)
            elif _is_model_missing(status, msg):
                _mark_exhausted(provider, model, 6 * 3600, "model not available to this key", kid)
                raise _NextModel(f"model not available: {msg[:200]}") from e
            elif status == 400 and "reasoning" in msg.lower() and f"{provider}:{model}" not in _reasoning_rejected:
                log.warning("role=%s %s rejected reasoning_effort; retrying without it", role, model)
                _reasoning_rejected.add(f"{provider}:{model}")
            elif status == 400 and native:
                if "thinking" not in msg.lower():
                    break  # a native 400 that is not about thinking config will not fix itself
            elif status == 400 and response_format["type"] == "json_schema":
                # Some endpoints reject strict json_schema: fall back to json_object + schema in the prompt.
                log.warning("role=%s json_schema rejected (%s); falling back to json_object", role, msg[:200])
                response_format = {"type": "json_object"}
                messages = [
                    {"role": "system", "content": system + "\n\nReturn ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema)},
                    {"role": "user", "content": user},
                ]
            elif status == 400:
                break
            else:
                time.sleep(2 * (attempt + 1))
    raise _NextModel(str(last_err)[:300] if last_err else "no valid response")


def _user_text(user) -> str:
    if isinstance(user, str):
        return user
    return "\n".join(p.get("text", "[image]") if p.get("type") == "text" else "[page image]" for p in user)


def _file_call(state, role: str, model: str, system: str, user, out, secs: float = 0.0) -> None:
    """Keep a readable copy of the call on the case (shown in the agent flow diagram). Truncated to stay small."""
    if state is None:
        return
    state.llm_calls.append({"step": state.current_step, "role": role, "model": model, "secs": round(secs, 2),
                            "instructions": system[:2500], "input": _user_text(user)[:6000],
                            "output": json.dumps(out, ensure_ascii=False)[:6000]})


def generate_json(
    role: str,
    system: str,
    user: str | list,
    schema: dict,
    *,
    name: str = "output",
    mock_response: Optional[dict] = None,
    state=None,
    temperature: float = 0.0,
) -> dict:
    """Ask the model for a JSON object matching `schema`. Returns the parsed dict.

    Tries the role's model chain (config.role_chain) in order: a model that is out of daily quota, missing, or keeps
    failing is skipped and the next one answers. Raises LLMError only when every model in the chain failed."""
    chain = config.role_chain(role)
    provider, model = chain[0]
    schema = _strictify(schema)

    if config.LLM_MODE == "mock":
        if mock_response is None:
            raise LLMError(f"No mock_response supplied for role '{role}' in mock mode.")
        _record_usage(state, role, 0, 0)
        log_llm_call({"mode": "mock", "role": role, "provider": "mock", "model": "mock", "response": json.dumps(mock_response)[:2000]})
        _file_call(state, role, "offline test double", system, user, mock_response)
        return json.loads(json.dumps(mock_response))

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    cache = _get_cache()
    # keyed on the PRIMARY model so an answer from a fallback model is reused next time too
    key = hashlib.sha256(json.dumps([provider, model, messages, schema], sort_keys=True, default=str).encode()).hexdigest()
    if cache is not None and key in cache:
        log_llm_call({"mode": "live-cache", "role": role, "provider": provider, "model": model})
        _record_usage(state, role, 0, 0)
        _file_call(state, role, f"{provider}:{model} (cached answer)", system, user, cache[key])
        return cache[key]

    # candidates: every key on the first model, then every key on the next model, ... (keys first, then models)
    cands = [(prov, mod, k) for prov, mod in chain for k in config.api_keys(prov)]
    errors = []
    for _round in range(3):  # extra rounds only if everything left was cooling down from a per-minute limit
        waiting = []
        for i, (prov, mod, k) in enumerate(cands):
            kid = key_id(k)
            if is_exhausted(prov, mod, kid):
                continue
            left = _cooldown_left(prov, mod, kid)
            if left > 0:
                waiting.append(left)
                continue
            later = [c for c in cands[i + 1:] if not is_exhausted(c[0], c[1], key_id(c[2])) and not _cooldown_left(c[0], c[1], key_id(c[2]))]
            try:
                t_call = time.time()
                out, pt, ct = _call_model(prov, mod, role, system, user, schema, name, temperature, can_skip=bool(later), api_key=k)
                _file_call(state, role, f"{prov}:{mod}", system, user, out, time.time() - t_call)
            except _CoolDown as e:
                _cooling[_slot(prov, mod, kid)] = time.time() + e.seconds
                waiting.append(e.seconds)
                log.warning("role=%s %s@%s per-minute limit; trying next key/model", role, mod, kid)
                continue
            except _NextModel as e:
                errors.append(f"{prov}:{mod}@{kid}: {e}")
                log.warning("role=%s %s:%s@%s failed (%s); trying next key/model", role, prov, mod, kid, str(e)[:160])
                continue
            _record_usage(state, role, pt, ct)
            if state is not None:
                state.usage.setdefault("models", {})[role] = f"{prov}:{mod}"
                if (prov, mod) != (provider, model):
                    state.usage.setdefault("fallbacks", []).append(f"{role}: {provider}:{model} -> {prov}:{mod}")
            if cache is not None:
                cache[key] = out
            return out
        if not waiting:
            break
        pause = min(60.0, min(waiting))
        log.warning("role=%s every key/model is cooling down; waiting %.0fs", role, pause)
        time.sleep(pause)
    errors += [f"{p}:{m} skipped (out of quota until reset)" for p, m in chain if is_exhausted(p, m)]
    raise LLMError(f"{role}: no model in the chain could answer: " + " | ".join(errors))


def image_message(prompt: str, png_bytes: bytes) -> list:
    """OpenAI-style multimodal user content (used for transcribing scanned pages)."""
    import base64

    b64 = base64.b64encode(png_bytes).decode()
    return [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]
