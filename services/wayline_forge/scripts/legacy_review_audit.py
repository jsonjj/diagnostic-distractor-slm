"""Data-only verification for the retired Glitch Rally approval format.

This module deliberately does not import ``src``.  The retired consistency
module imports ``src.buggy_procedures`` at import time, while Wayline runtime
and build tooling must keep that executable legacy registry outside their
process.  These functions only replay canonical receipts and exact arithmetic
needed to authenticate the six owner-approved records.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from functools import reduce
import hashlib
import html
import json
from math import gcd
from pathlib import Path
import re
from typing import Any
import unicodedata


LEGACY_BASE_MODEL = "unsloth/Qwen3-4B-bnb-4bit"
LEGACY_ADAPTER = "j2ampn/qwen3-4b-distractor-lora-v7"
LEGACY_GENERATOR_VERSION = "glitch-rally-generator-v1"
LEGACY_GENERATION_PARAMETERS = {
    "do_sample": False,
    "enable_thinking": False,
    "max_new_tokens": 512,
}
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_REVISION = re.compile(r"[0-9a-f]{40}", re.ASCII)
_QUESTION_ID = re.compile(r"GR-NUM-[0-9]{3}", re.ASCII)
_NUMBER = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", re.ASCII)
_MAX_EXPRESSION_CHARS = 256
_MAX_VALUE_BITS = 4096
_GLITCH_FAMILY_IDS = {
    "decimal_drifter",
    "factor_faker",
    "fraction_forger",
    "operation_swapper",
    "order_hacker",
    "place_value_phantom",
    "reciprocal_rogue",
    "rounding_rascal",
    "sign_flipper",
}


class LegacyReviewError(ValueError):
    """The legacy artifact does not replay to its claimed immutable receipt."""


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonicalize_legacy_question(text: object) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(str(text))).casefold()
    replacements = (
        ("\\(", " "),
        ("\\)", " "),
        ("\\[", " "),
        ("\\]", " "),
        ("\\div", "/"),
        ("÷", "/"),
        ("\\times", "*"),
        ("\\cdot", "*"),
        ("×", "*"),
        ("·", "*"),
        ("−", "-"),
        ("–", "-"),
        ("—", "-"),
    )
    for old, new in replacements:
        value = value.replace(old, new)
    value = re.sub(
        r"\\(?:d|t)?frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}",
        r"\1/\2",
        value,
    )
    value = re.sub(r"[^\w\d./+*%^=-]+", " ", value, flags=re.UNICODE)
    value = " ".join(value.split())
    return re.sub(r"\s*([/+*%^=-])\s*", r"\1", value)


def legacy_question_fingerprint(text: object) -> str:
    digest = hashlib.sha256(
        canonicalize_legacy_question(text).encode("utf-8")
    ).hexdigest()
    return f"question:v1:{digest}"


def _exact_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed


def _bounded(value: Fraction) -> Fraction:
    if (
        value.numerator.bit_length() > _MAX_VALUE_BITS
        or value.denominator.bit_length() > _MAX_VALUE_BITS
    ):
        raise LegacyReviewError("legacy arithmetic exceeds exact bounds")
    return value


class _ExpressionParser:
    def __init__(self, expression: str):
        compact = re.sub(r"\s+", "", expression)
        if not compact or len(compact) > _MAX_EXPRESSION_CHARS:
            raise LegacyReviewError("legacy arithmetic expression is invalid")
        self.tokens = re.findall(r"\d+(?:\.\d+)?|[()+\-*/]", compact)
        if "".join(self.tokens) != compact or len(self.tokens) > 128:
            raise LegacyReviewError("legacy arithmetic expression is invalid")
        self.index = 0

    def parse(self) -> Fraction:
        value = self._expression()
        if self.index != len(self.tokens):
            raise LegacyReviewError("legacy arithmetic expression is invalid")
        return _bounded(value)

    def _peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self) -> str:
        token = self._peek()
        if token is None:
            raise LegacyReviewError("legacy arithmetic expression is incomplete")
        self.index += 1
        return token

    def _expression(self) -> Fraction:
        value = self._term()
        while self._peek() in {"+", "-"}:
            operator = self._take()
            right = self._term()
            value = value + right if operator == "+" else value - right
            _bounded(value)
        return value

    def _term(self) -> Fraction:
        value = self._factor()
        while self._peek() in {"*", "/"}:
            operator = self._take()
            right = self._factor()
            if operator == "/" and right == 0:
                raise LegacyReviewError("legacy arithmetic divides by zero")
            value = value * right if operator == "*" else value / right
            _bounded(value)
        return value

    def _factor(self) -> Fraction:
        token = self._take()
        if token in {"+", "-"}:
            value = self._factor()
            return value if token == "+" else -value
        if token == "(":
            value = self._expression()
            if self._take() != ")":
                raise LegacyReviewError("legacy arithmetic parentheses are invalid")
            return value
        if not _NUMBER.fullmatch(token):
            raise LegacyReviewError("legacy arithmetic token is invalid")
        return _bounded(Fraction(Decimal(token)))


def solve_legacy_question(question: dict[str, Any]) -> Fraction:
    solver = question.get("solver")
    if not isinstance(solver, dict):
        raise LegacyReviewError("legacy solver is invalid")
    kind = solver.get("kind")
    if kind == "arithmetic" and set(solver) == {"expression", "kind"}:
        expression = solver.get("expression")
        if not isinstance(expression, str):
            raise LegacyReviewError("legacy arithmetic solver is invalid")
        return _ExpressionParser(expression).parse()
    if kind == "round_decimal" and set(solver) == {"kind", "places", "value"}:
        value = solver.get("value")
        places = solver.get("places")
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value, re.ASCII)
            or not isinstance(places, int)
            or isinstance(places, bool)
            or not 0 <= places <= 6
        ):
            raise LegacyReviewError("legacy rounding solver is invalid")
        quantum = Decimal(1).scaleb(-places)
        return _bounded(Fraction(
            Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)
        ))
    if kind in {"gcd", "lcm"} and set(solver) == {"kind", "values"}:
        values = solver.get("values")
        if (
            not isinstance(values, list)
            or not 2 <= len(values) <= 8
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value == 0
                or abs(value) > 1_000_000
                for value in values
            )
        ):
            raise LegacyReviewError("legacy factor solver is invalid")
        positive = [abs(value) for value in values]
        if kind == "gcd":
            return Fraction(reduce(gcd, positive))
        return Fraction(reduce(
            lambda left, right: left * right // gcd(left, right),
            positive,
        ))
    if kind == "decimal_to_fraction" and set(solver) == {"kind", "value"}:
        value = solver.get("value")
        if not isinstance(value, str) or not re.fullmatch(
            r"[+-]?\d+(?:\.\d+)?", value, re.ASCII
        ):
            raise LegacyReviewError("legacy decimal solver is invalid")
        return _bounded(Fraction(Decimal(value)))
    raise LegacyReviewError("legacy solver kind is unsupported")


def _strict_raw_distractors(raw_response: object) -> list[dict[str, str]]:
    if not isinstance(raw_response, str) or not 1 <= len(raw_response) <= 20_000:
        raise LegacyReviewError("legacy raw response is invalid")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LegacyReviewError("legacy raw response repeats a key")
            result[key] = value
        return result

    try:
        value = json.loads(raw_response, object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        raise LegacyReviewError("legacy raw response is invalid") from None
    if not isinstance(value, dict) or set(value) != {"distractors"}:
        raise LegacyReviewError("legacy raw response schema is invalid")
    distractors = value["distractors"]
    if not isinstance(distractors, list) or len(distractors) != 3:
        raise LegacyReviewError("legacy distractor count is invalid")
    for item in distractors:
        if not isinstance(item, dict) or set(item) != {
            "answer",
            "computation",
            "misconception",
        }:
            raise LegacyReviewError("legacy distractor schema is invalid")
        if any(
            not isinstance(item[field], str)
            or not item[field]
            or item[field] != item[field].strip()
            for field in item
        ):
            raise LegacyReviewError("legacy distractor text is invalid")
    return distractors


def _legacy_source_sha256(file_name: str) -> str:
    path = Path(__file__).resolve().parents[3] / "src" / file_name
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise LegacyReviewError("legacy source receipt cannot be verified") from None


def _validate_question_record(question: dict[str, Any]) -> None:
    required = {
        "canonical_question",
        "correct",
        "difficulty",
        "id",
        "question",
        "question_hash",
        "solver",
        "source",
        "topic",
        "trusted_steps",
        "visual_tool",
    }
    if set(question) != required:
        raise LegacyReviewError("legacy question fields are invalid")
    if not isinstance(question.get("id"), str) or not _QUESTION_ID.fullmatch(
        question["id"]
    ):
        raise LegacyReviewError("legacy question ID is invalid")
    if question.get("source") != "original-game-v1":
        raise LegacyReviewError("legacy question source is invalid")
    if question.get("canonical_question") != canonicalize_legacy_question(
        question.get("question")
    ):
        raise LegacyReviewError("legacy canonical question is stale")
    if question.get("question_hash") != legacy_question_fingerprint(
        question.get("question")
    ):
        raise LegacyReviewError("legacy question hash is invalid")
    steps = question.get("trusted_steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 6 or any(
        not isinstance(step, str) or not step or step != step.strip()
        for step in steps
    ):
        raise LegacyReviewError("legacy trusted steps are invalid")


def verify_legacy_owner_approval(
    *,
    queue_record: dict[str, Any],
    approved_record: dict[str, Any],
    trusted_question: dict[str, Any],
    expected_reviewer: str,
) -> None:
    """Replay every immutable legacy receipt without executing legacy code."""

    _validate_question_record(trusted_question)
    if set(queue_record) != {
        "decision",
        "review_payload",
        "review_payload_hash",
        "review_status",
        "schema_version",
    } or queue_record.get("schema_version") != "glitch-rally-review-queue-v1":
        raise LegacyReviewError("legacy queue record schema is invalid")
    payload = queue_record.get("review_payload")
    decision = queue_record.get("decision")
    if not isinstance(payload, dict) or not isinstance(decision, dict):
        raise LegacyReviewError("legacy queue record is invalid")
    payload_hash = f"review-payload:v1:{canonical_json_sha256(payload)}"
    if (
        queue_record.get("review_payload_hash") != payload_hash
        or decision.get("review_payload_hash") != payload_hash
    ):
        raise LegacyReviewError("legacy review payload receipt is invalid")

    validation = approved_record.get("validation")
    if not isinstance(validation, dict) or set(validation) != {
        "candidate_hash",
        "candidate_id",
        "distractors",
        "issues",
        "question_hash",
        "question_id",
        "raw_candidate",
        "schema_version",
        "status",
        "validation_hash",
        "validator_version",
    }:
        raise LegacyReviewError("legacy validation schema is invalid")
    raw = validation.get("raw_candidate")
    if not isinstance(raw, dict):
        raise LegacyReviewError("legacy raw candidate is invalid")
    candidate_unsigned = {
        key: value
        for key, value in raw.items()
        if key not in {"candidate_id", "generated_at_utc"}
    }
    candidate_id = f"candidate:v1:{canonical_json_sha256(candidate_unsigned)}"
    if raw.get("candidate_id") != candidate_id:
        raise LegacyReviewError("legacy candidate receipt is invalid")
    if (
        raw.get("schema_version") != "glitch-rally-candidate-v1"
        or raw.get("model_id") != LEGACY_BASE_MODEL
        or raw.get("adapter_id") != LEGACY_ADAPTER
        or raw.get("generator_version") != LEGACY_GENERATOR_VERSION
        or raw.get("generation_parameters") != LEGACY_GENERATION_PARAMETERS
        or not isinstance(raw.get("model_revision"), str)
        or not _REVISION.fullmatch(raw["model_revision"])
        or not isinstance(raw.get("adapter_revision"), str)
        or not _REVISION.fullmatch(raw["adapter_revision"])
    ):
        raise LegacyReviewError("legacy generation identity is invalid")
    for field in (
        "backend_source_sha256",
        "generator_source_sha256",
        "prompt_sha256",
        "question_record_sha256",
        "raw_response_sha256",
        "source_batch_sha256",
        "system_prompt_sha256",
        "user_prompt_sha256",
    ):
        if not isinstance(raw.get(field), str) or not _SHA256.fullmatch(raw[field]):
            raise LegacyReviewError("legacy generation receipt is invalid")
    if raw.get("generator_source_sha256") != _legacy_source_sha256(
        "game_candidate_generation.py"
    ) or raw.get("backend_source_sha256") != _legacy_source_sha256(
        "game_colab_backend.py"
    ):
        raise LegacyReviewError("legacy source receipt is invalid")
    if _exact_utc(raw.get("generated_at_utc")) is None:
        raise LegacyReviewError("legacy generation timestamp is invalid")
    for candidate_field, question_field in {
        "correct": "correct",
        "question": "question",
        "question_hash": "question_hash",
        "question_id": "id",
        "topic": "topic",
    }.items():
        if raw.get(candidate_field) != trusted_question.get(question_field):
            raise LegacyReviewError("legacy candidate question binding is invalid")
    if raw.get("question_record_sha256") != canonical_json_sha256(
        trusted_question
    ):
        raise LegacyReviewError("legacy question-record receipt is invalid")
    raw_response = raw.get("raw_response")
    if raw.get("raw_response_sha256") != hashlib.sha256(
        str(raw_response).encode("utf-8")
    ).hexdigest():
        raise LegacyReviewError("legacy raw-response receipt is invalid")
    parsed_distractors = _strict_raw_distractors(raw_response)

    validation_hash = "validation:v1:" + canonical_json_sha256({
        "candidate_hash": candidate_id,
        "distractors": validation.get("distractors"),
        "issues": validation.get("issues"),
        "question_hash": trusted_question.get("question_hash"),
        "status": validation.get("status"),
        "validator_version": "glitch-rally-validator-v1",
    })
    if (
        validation.get("schema_version") != "glitch-rally-validation-v1"
        or validation.get("validator_version") != "glitch-rally-validator-v1"
        or validation.get("candidate_id") != candidate_id
        or validation.get("candidate_hash") != candidate_id
        or validation.get("question_id") != trusted_question.get("id")
        or validation.get("question_hash") != trusted_question.get("question_hash")
        or validation.get("status") != "needs_review"
        or validation.get("issues") != []
        or validation.get("distractors") != parsed_distractors
        or validation.get("validation_hash") != validation_hash
    ):
        raise LegacyReviewError("legacy validation receipt is invalid")

    expected_question_payload = {
        "correct": trusted_question.get("correct"),
        "id": trusted_question.get("id"),
        "prompt": trusted_question.get("question"),
        "question_hash": trusted_question.get("question_hash"),
        "topic": trusted_question.get("topic"),
        "trusted_steps": trusted_question.get("trusted_steps"),
    }
    expected_generation_payload = {
        "adapter_id": raw.get("adapter_id"),
        "adapter_revision": raw.get("adapter_revision"),
        "backend_source_sha256": raw.get("backend_source_sha256"),
        "generator_source_sha256": raw.get("generator_source_sha256"),
        "generator_version": raw.get("generator_version"),
        "model_id": raw.get("model_id"),
        "model_revision": raw.get("model_revision"),
        "prompt_sha256": raw.get("prompt_sha256"),
    }
    if (
        set(payload) != {
            "allowed_glitch_families",
            "candidate_hash",
            "candidate_id",
            "distractors",
            "generation",
            "question",
            "validation_hash",
        }
        or set(payload.get("allowed_glitch_families", {}))
        != _GLITCH_FAMILY_IDS
        or payload.get("candidate_id") != candidate_id
        or payload.get("candidate_hash") != candidate_id
        or payload.get("validation_hash") != validation_hash
        or payload.get("question") != expected_question_payload
        or payload.get("generation") != expected_generation_payload
        or payload.get("distractors") != parsed_distractors
    ):
        raise LegacyReviewError("legacy owner payload binding is invalid")

    required_decision_fields = {
        "candidate_hash",
        "candidate_id",
        "decision",
        "distractor_reviews",
        "holdout_origin_verified",
        "notes",
        "review_payload_hash",
        "reviewed_at_utc",
        "reviewer",
        "schema_version",
        "trusted_answer_verified",
        "trusted_question_verified",
        "trusted_steps_verified",
        "validation_hash",
    }
    reviews = decision.get("distractor_reviews")
    reviewed_at = _exact_utc(decision.get("reviewed_at_utc"))
    generated_at = _exact_utc(raw.get("generated_at_utc"))
    if (
        set(decision) != required_decision_fields
        or decision.get("schema_version") != "glitch-rally-review-decision-v1"
        or decision.get("decision") != "approved"
        or decision.get("reviewer") != expected_reviewer
        or decision.get("candidate_id") != candidate_id
        or decision.get("candidate_hash") != candidate_id
        or decision.get("validation_hash") != validation_hash
        or reviewed_at is None
        or generated_at is None
        or reviewed_at < generated_at
        or any(
            decision.get(field) is not True
            for field in (
                "holdout_origin_verified",
                "trusted_answer_verified",
                "trusted_question_verified",
                "trusted_steps_verified",
            )
        )
        or not isinstance(reviews, list)
        or len(reviews) != 3
    ):
        raise LegacyReviewError("legacy owner decision is invalid")
    indexes: set[int] = set()
    for review in reviews:
        if (
            not isinstance(review, dict)
            or set(review) != {
                "age_appropriate",
                "glitch_family_id",
                "index",
                "repair_explanation",
                "repair_prompt",
                "semantic_valid",
            }
            or review.get("index") not in {0, 1, 2}
            or isinstance(review.get("index"), bool)
            or review.get("index") in indexes
            or review.get("semantic_valid") is not True
            or review.get("age_appropriate") is not True
            or review.get("glitch_family_id") not in _GLITCH_FAMILY_IDS
            or not isinstance(review.get("repair_prompt"), str)
            or not review["repair_prompt"]
            or not isinstance(review.get("repair_explanation"), str)
            or not review["repair_explanation"]
        ):
            raise LegacyReviewError("legacy distractor review is invalid")
        indexes.add(review["index"])

    review_hash = "review:v1:" + canonical_json_sha256({
        "candidate_hash": candidate_id,
        "candidate_id": candidate_id,
        "decision": decision,
        "validation_hash": validation_hash,
    })
    if (
        set(approved_record) != {
            "candidate_hash",
            "candidate_id",
            "decision",
            "question_id",
            "review_hash",
            "review_payload_hash",
            "review_status",
            "schema_version",
            "validation",
            "validation_hash",
        }
        or approved_record.get("schema_version")
        != "glitch-rally-reviewed-candidate-v1"
        or approved_record.get("candidate_id") != candidate_id
        or approved_record.get("candidate_hash") != candidate_id
        or approved_record.get("validation_hash") != validation_hash
        or approved_record.get("review_payload_hash") != payload_hash
        or approved_record.get("question_id") != trusted_question.get("id")
        or approved_record.get("review_status") != "approved"
        or approved_record.get("decision") != decision
        or approved_record.get("review_hash") != review_hash
    ):
        raise LegacyReviewError("legacy approved-pack receipt is invalid")
