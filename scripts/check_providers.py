"""Day-0 compatibility check for the FREE providers. Run this first with your real keys:

    python scripts/check_providers.py

Checks: (1) Gemini returns JSON for our schema, (2) Groq verifier strict JSON, (3) the planner action schema on its
role's model, (4) Gemini reads an image. Uses 2 Gemini requests (+1 per fallback model) (free daily quota can be ~20/model).
Also prints every role's model chain and any model currently skipped for quota.

    python scripts/check_providers.py --reset-quota    # forget remembered quota exhaustion (e.g. after the daily reset)
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["LLM_MODE"] = "live"
os.environ["LLM_CACHE"] = "0"

from safetytrace import config  # noqa: E402
from safetytrace.agent.planner import ACTION_SCHEMA  # noqa: E402
from safetytrace.llm.client import _call_model, _strictify, generate_json, image_message  # noqa: E402

config.LLM_MODE = "live"
config.USE_CACHE = False


def check(name, fn, fix):
    import time

    print(f"...   {name}: calling (can take up to {config.LLM_TIMEOUT}s on free tiers)", flush=True)
    t0 = time.time()
    try:
        out = fn()
        print(f"PASS  {name} in {time.time() - t0:.1f}s: {str(out)[:160]}", flush=True)
        return True
    except Exception as e:
        print(f"FAIL  {name} after {time.time() - t0:.1f}s: {type(e).__name__}: {str(e)[:300]}\n      fix: {fix}", flush=True)
        return False


def main():
    from safetytrace.llm.client import quota_status, reset_quota_state
    if "--reset-quota" in sys.argv:
        reset_quota_state()
        print("Quota memory cleared.")
    print("Role -> model chain (first = primary; later ones answer when earlier ones are out of quota):")
    for role in config.ROLES:
        print(f"   {role:<14} " + "  ->  ".join(f"{p}:{m}" for p, m in config.role_chain(role)))
    qs = quota_status()
    print(f"Models skipped for quota right now: {qs or 'none'}")
    print(f"Gemini model: {config.GEMINI_MODEL} | Groq model: {config.GROQ_MODEL} | reasoning effort: {config.REASONING_EFFORT} "
          f"| timeout {config.LLM_TIMEOUT}s", flush=True)
    if not config.GEMINI_API_KEY or not config.GROQ_API_KEY:
        print("Set GEMINI_API_KEY (or GEMINI_API_KEYS) and GROQ_API_KEY (or GROQ_API_KEYS) in .env first (see README).")
        return
    # 0) list the model IDs each key can use (fixes 'model not found' immediately)
    from safetytrace.llm.client import _client
    for prov in ("gemini", "groq"):
        try:
            ids = sorted(m.id for m in _client(prov).models.list())
            pick = [i.replace("models/", "") for i in ids if ("flash" in i or "gemma" in i if prov == "gemini" else ("gpt-oss" in i or "qwen" in i or "llama" in i))]
            print(f"PASS  {prov} key works. Relevant models ({len(pick)}): {pick or ids[:20]}")
            if prov == "gemini" and (config.GEMINI_MODEL in ids or f"models/{config.GEMINI_MODEL}" in ids):
                print(f"PASS  GEMINI_MODEL '{config.GEMINI_MODEL}' is available to this key")
            if prov == "gemini" and config.GEMINI_MODEL not in ids and f"models/{config.GEMINI_MODEL}" not in ids:
                print(f"WARN  GEMINI_MODEL='{config.GEMINI_MODEL}' not in the list above -> set GEMINI_MODEL in .env to one of them "
                      "(drop any 'models/' prefix), preferably the newest '-flash' one marked free in AI Studio.")
        except Exception as e:
            print(f"FAIL  {prov} key/model listing: {type(e).__name__}: {str(e)[:250]}")
    schema = {"type": "object", "properties": {"facts": {"type": "array", "items": {"type": "object", "properties": {
        "field": {"type": "string", "enum": ["dose", "event_onset_date"]}, "value": {"type": "string"}, "quote": {"type": "string"}}}}}}
    text = "Velorantib 50 mg orally once daily. Rash first noted on 14-Mar-2026."

    def direct(provider, model, role, system, user, sch):
        """Call ONE model, bypassing the fallback chain, so a PASS really means that model answered."""
        return _call_model(provider, model, role, system, user, _strictify(sch), "output", 0.0)[0]

    from safetytrace.llm.client import key_id
    print(f"Keys: gemini {len(config.GEMINI_API_KEYS)} {[key_id(k) for k in config.GEMINI_API_KEYS]} | "
          f"groq {len(config.GROQ_API_KEYS)} {[key_id(k) for k in config.GROQ_API_KEYS]}  (fingerprints, not the keys)")
    results = []
    for k in config.GEMINI_API_KEYS[1:]:  # each extra key once, on the primary model
        results.append(check(f"Gemini key {key_id(k)} on {config.GEMINI_MODEL}", lambda k=k: _call_model(
            "gemini", config.GEMINI_MODEL, "extract", "Extract facts with verbatim quotes.", text, _strictify(schema), "output", 0.0, api_key=k)[0],
            "key invalid, or out of quota; keys from the SAME Google project share one quota"))
    for k in config.GROQ_API_KEYS[1:]:
        results.append(check(f"Groq key {key_id(k)} on {config.GROQ_FAST_MODEL}", lambda k=k: _call_model(
            "groq", config.GROQ_FAST_MODEL, "planner", "Choose the next tool.", "Case has 3 documents, none ingested.",
            _strictify(ACTION_SCHEMA), "output", 0.0, api_key=k)[0], "check the key in the Groq console"))
    for gm in [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]:
        results.append(check(f"Gemini {gm} JSON output", lambda gm=gm: direct("gemini", gm, "extract", "Extract facts with verbatim quotes.", text, schema),
                             "out of daily quota -> wait for the reset or use another model in GEMINI_FALLBACK_MODELS; "
                             "model not found -> use an ID from the list above"))
    results += [
        check(f"Groq {config.GROQ_MODEL} strict JSON (verifier)", lambda: direct("groq", config.GROQ_MODEL, "verify", "Extract facts with verbatim quotes.", text, schema),
              "check GROQ_API_KEY and that GROQ_MODEL=openai/gpt-oss-120b is listed in your Groq console"),
        check(f"Planner action schema on {config.ROLES['planner'][0]}:{config.ROLES['planner'][1]}",
              lambda: direct(*config.ROLES["planner"], "planner", "Choose the next tool.", "Case has 3 documents, none ingested.", ACTION_SCHEMA),
              "set GROQ_FAST_MODEL to a model listed for your Groq key (or openai/gpt-oss-120b), or PLANNER=rules for the demo"),
    ]
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (900, 120), "white")
        ImageDraw.Draw(img).text((10, 40), "Rash first noted on 14-Mar-2026. Velorantib 50 mg.", fill="black")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        results.append(check(f"Gemini {config.GEMINI_MODEL} image transcription", lambda: direct(
            "gemini", config.GEMINI_MODEL, "transcribe", "Transcribe verbatim.", image_message("Transcribe this image verbatim.", buf.getvalue()),
            {"type": "object", "properties": {"text": {"type": "string"}}}),
            "if images fail, install Tesseract and use OCR for scanned pages, or drop the scanned-page case"))
    except ImportError:
        print("SKIP  image check (Pillow not installed)")
    print(f"\n{sum(results)}/{len(results)} checks passed. Usage is logged in logs/llm_calls.jsonl")
    print(f"Models skipped for quota now: {quota_status() or 'none'}")
    if not config.GEMINI_FALLBACK_MODELS:
        print("TIP: set GEMINI_FALLBACK_MODELS in .env to 1-2 other Flash models from the list above (each has its own free daily quota).")


if __name__ == "__main__":
    main()
