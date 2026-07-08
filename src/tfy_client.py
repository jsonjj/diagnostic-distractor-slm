"""Thin TrueFoundry AI Gateway (OpenAI-compatible) client for teacher + judge calls.

No network call happens at import or unless chat() is invoked. Requires TFY_API_KEY.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .config import TFY_API_KEY, TFY_BASE_URL, TFY_EXTRA_HEADERS, TFY_MODEL

_client = None
_MAX_ATTEMPTS = 6  # long judge/teacher runs (~400 calls) must survive transient 529 "Overloaded"


def get_client():
    global _client
    if _client is None:
        from openai import OpenAI  # lazy: module imports fine without the dep installed

        if not TFY_API_KEY:
            raise RuntimeError("TFY_API_KEY is not set (add it to .env or your environment).")
        _client = OpenAI(api_key=TFY_API_KEY, base_url=TFY_BASE_URL, timeout=45, max_retries=2)
    return _client


def chat(messages: List[Dict], model: Optional[str] = None, temperature: Optional[float] = None, max_tokens: int = 1024) -> str:
    kwargs = dict(
        model=model or TFY_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        extra_headers=TFY_EXTRA_HEADERS,
    )
    if temperature is not None:  # some gateway models (e.g. claude-sonnet-5) reject `temperature`
        kwargs["temperature"] = temperature
    last_err = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = get_client().chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001 — retry transient gateway/overload errors (e.g. 529)
            msg = str(e).lower()
            transient = "529" in msg or "overload" in msg or "timeout" in msg or "503" in msg or "502" in msg
            if not transient or attempt == _MAX_ATTEMPTS - 1:
                raise
            last_err = e
            time.sleep(2 ** attempt)  # 1,2,4,8,16s backoff
    raise last_err  # unreachable, but keeps type-checkers happy
