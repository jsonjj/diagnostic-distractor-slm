"""Thin TrueFoundry AI Gateway (OpenAI-compatible) client for teacher + judge calls.

No network call happens at import or unless chat() is invoked. Requires TFY_API_KEY.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .config import TFY_API_KEY, TFY_BASE_URL, TFY_EXTRA_HEADERS, TFY_MODEL

_client = None


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
    resp = get_client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""
