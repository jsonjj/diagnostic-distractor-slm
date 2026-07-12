"""Canonical prompt compilation and receipt validation for the local SLM."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .providers.distractor import SlmRequest
from .question_kernel import QuestionBlueprint


PROMPT_VERSION = "wayline-distractor-prompt-v1"
SYSTEM_PROMPT = """You create diagnostic wrong answer choices for one trusted middle-school Number question.
Return exactly one JSON object with exactly this shape:
{"distractors":[{"misconception":"...","computation":"...","answer":"..."},{"misconception":"...","computation":"...","answer":"..."},{"misconception":"...","computation":"...","answer":"..."}]}
Each answer must be wrong, distinct, and produced by the named misconception. Return JSON only."""
USER_PROMPT_TEMPLATE = (
    "Question: {question}\n"
    "Correct answer: {correct_answer}\n"
    "Topic: {topic}"
)
INFERENCE_PARAMETERS = {
    "temperature": 0,
    "seed": 0,
    "stream": False,
    "max_tokens": 768,
    "chat_template_kwargs": {"enable_thinking": False},
}
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)


def _inference_payload() -> dict[str, Any]:
    return {
        "temperature": INFERENCE_PARAMETERS["temperature"],
        "seed": INFERENCE_PARAMETERS["seed"],
        "stream": INFERENCE_PARAMETERS["stream"],
        "max_tokens": INFERENCE_PARAMETERS["max_tokens"],
        "chat_template_kwargs": dict(INFERENCE_PARAMETERS["chat_template_kwargs"]),
    }


def _template_receipt_payload() -> dict[str, Any]:
    return {
        "prompt_version": PROMPT_VERSION,
        "message_templates": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE},
        ],
        "inference": _inference_payload(),
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


PROMPT_TEMPLATE_SHA256 = hashlib.sha256(
    _canonical_json(_template_receipt_payload())
).hexdigest()


def _render_messages(request: SlmRequest) -> list[dict[str, str]]:
    user = USER_PROMPT_TEMPLATE.format(
        question=request.question,
        correct_answer=request.correct_answer,
        topic=request.topic,
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def prompt_payload(request: SlmRequest) -> dict[str, Any]:
    return {
        "prompt_version": PROMPT_VERSION,
        "template_sha256": PROMPT_TEMPLATE_SHA256,
        "messages": _render_messages(request),
        "inference": _inference_payload(),
    }


def prompt_sha256(request: SlmRequest) -> str:
    return hashlib.sha256(_canonical_json(prompt_payload(request))).hexdigest()


def validate_prompt_receipt(request: SlmRequest) -> bool:
    return bool(_SHA256.fullmatch(request.prompt_sha256)) and (
        request.prompt_sha256 == prompt_sha256(request)
    )


def build_slm_request(blueprint: QuestionBlueprint) -> SlmRequest:
    unsigned = SlmRequest(
        question_id=blueprint.question_id,
        question=blueprint.prompt,
        correct_answer=blueprint.canonical_answer.display,
        topic=blueprint.topic,
        prompt_sha256="",
    )
    return SlmRequest(
        question_id=unsigned.question_id,
        question=unsigned.question,
        correct_answer=unsigned.correct_answer,
        topic=unsigned.topic,
        prompt_sha256=prompt_sha256(unsigned),
    )


def openai_messages(request: SlmRequest) -> list[dict[str, str]]:
    if not validate_prompt_receipt(request):
        raise ValueError("prompt receipt mismatch")
    return _render_messages(request)
