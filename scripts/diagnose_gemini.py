"""Find a Gemini model + API style that actually answers, fast (25 s cap per try, no retries).

    python scripts/diagnose_gemini.py

Tries the NATIVE Gemini API and the OpenAI-compatible endpoint across several free Flash models and
prints which combinations work and how fast. Put the winner in .env (GEMINI_MODEL / GEMINI_API_STYLE).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from safetytrace import config  # noqa: E402

TIMEOUT = 25
PROMPT = 'Return a JSON object {"dose": "...", "onset": "..."} for: Velorantib 50 mg daily. Rash first noted on 14-Mar-2026.'
MODELS = list(dict.fromkeys([config.GEMINI_MODEL, "gemini-flash-latest", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash",
                             "gemini-flash-lite-latest"]))


def native(model: str, thinking: bool):
    import httpx

    gen = {"temperature": 0, "responseMimeType": "application/json"}
    if thinking:
        gen["thinkingConfig"] = {"thinkingBudget": 0} if model.startswith("gemini-2.5-flash") else {"thinkingLevel": "low"}
    body = {"contents": [{"role": "user", "parts": [{"text": PROMPT}]}], "generationConfig": gen}
    r = httpx.post(config.GEMINI_NATIVE_URL.format(model=model), json=body, timeout=TIMEOUT,
                   headers={"x-goog-api-key": config.GEMINI_API_KEY})
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:160]}"
    d = r.json()
    t = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"] if not p.get("thought"))
    json.loads(t)
    return True, t[:80]


def compat(model: str, mode: str):
    from openai import OpenAI

    c = OpenAI(base_url=config.PROVIDER_BASE_URLS["gemini"], api_key=config.GEMINI_API_KEY, timeout=TIMEOUT, max_retries=0)
    kw = {}
    if mode == "json_object":
        kw["response_format"] = {"type": "json_object"}
    if mode == "reasoning_low":
        kw["reasoning_effort"] = "low"
    r = c.chat.completions.create(model=model, messages=[{"role": "user", "content": PROMPT}], **kw)
    return True, (r.choices[0].message.content or "")[:80]


def run(label, fn):
    t0 = time.time()
    try:
        ok, info = fn()
    except Exception as e:
        ok, info = False, f"{type(e).__name__}: {str(e)[:150]}"
    dt = time.time() - t0
    print(f"{'OK  ' if ok else 'FAIL'} {dt:5.1f}s  {label:55} {info}", flush=True)
    return ok, dt


def main():
    if not config.GEMINI_API_KEY:
        print("GEMINI_API_KEY missing in .env"); return
    print(f"Each try is capped at {TIMEOUT}s. This takes 1-4 minutes.\n", flush=True)
    winners = []
    for m in MODELS:
        ok, dt = run(f"native  {m}  (thinking low)", lambda: native(m, True))
        if ok:
            winners.append((dt, "native", m))
        else:
            ok2, dt2 = run(f"native  {m}  (default thinking)", lambda: native(m, False))
            if ok2:
                winners.append((dt2, "native", m))
    m = config.GEMINI_MODEL
    for mode in ("plain", "json_object", "reasoning_low"):
        ok, dt = run(f"openai-compat  {m}  ({mode})", lambda: compat(m, mode))
        if ok and mode == "json_object":
            winners.append((dt, "openai", m))
    print()
    if winners:
        dt, style, model = sorted(winners)[0]
        print(f"RECOMMENDED (.env):  GEMINI_API_STYLE={style}   GEMINI_MODEL={model}   ({dt:.1f}s)")
        print("Then re-run: python scripts/check_providers.py")
    else:
        print("Nothing answered. Check: AI Studio > your key's project has the Gemini API enabled; try creating a NEW key "
              "(standard 'AIza...' format) in AI Studio; check firewall/VPN; meanwhile run with PLANNER=rules and "
              "move the Gemini roles to Groq in config.ROLES (see README).")


if __name__ == "__main__":
    main()
