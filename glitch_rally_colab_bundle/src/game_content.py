"""Offline content integrity and review tools for Mathbreakers: Glitch Rally."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from difflib import SequenceMatcher
from fractions import Fraction
from functools import reduce
import hashlib
import html
import json
from math import gcd
from pathlib import Path
import re
import unicodedata

from .config import NUMBER_SUBJECTS
from .consistency import computation_consistent, eval_computation, to_value
from .prompts import SYSTEM_PROMPT, build_user


class GameContentError(ValueError):
    """Raised when content cannot cross the game approval boundary."""


CANDIDATE_SCHEMA_VERSION = "glitch-rally-candidate-v1"
VALIDATOR_VERSION = "glitch-rally-validator-v1"
FINAL_BASE_MODEL = "unsloth/Qwen3-4B-bnb-4bit"
FINAL_ADAPTER = "j2ampn/qwen3-4b-distractor-lora-v7"
FINAL_GENERATOR_VERSION = "glitch-rally-generator-v1"
FROZEN_HOLDOUT_RECORD_COUNT = 140
FROZEN_HOLDOUT_SHA256 = (
    "47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693"
)
DETERMINISTIC_GENERATION_PARAMETERS = {
    "do_sample": False,
    "max_new_tokens": 512,
    "enable_thinking": False,
}

GLITCH_FAMILIES = {
    "decimal_drifter": {
        "name": "Decimal Drifter",
        "personality": "slides digits into the wrong place-value lane",
    },
    "factor_faker": {
        "name": "Factor Faker",
        "personality": "mixes up factors, multiples, GCF, and LCM",
    },
    "fraction_forger": {
        "name": "Fraction Forger",
        "personality": "counterfeits fraction pieces and denominators",
    },
    "operation_swapper": {
        "name": "Operation Swapper",
        "personality": "secretly replaces the operation the problem asks for",
    },
    "order_hacker": {
        "name": "Order Hacker",
        "personality": "scrambles parentheses, powers, and operation order",
    },
    "place_value_phantom": {
        "name": "Place-Value Phantom",
        "personality": "haunts digit positions, conversions, and regrouping",
    },
    "reciprocal_rogue": {
        "name": "Reciprocal Rogue",
        "personality": "flips the wrong fraction during division",
    },
    "rounding_rascal": {
        "name": "Rounding Rascal",
        "personality": "checks the wrong digit before rounding",
    },
    "sign_flipper": {
        "name": "Sign Flipper",
        "personality": "reverses positive and negative rules",
    },
}


def sha256_text(value):
    """Return a lowercase SHA-256 hex digest for exact UTF-8 text."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def stable_json_sha256(value):
    """Hash JSON with the canonical settings shared by the offline forge."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reject_duplicate_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise GameContentError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def strict_json_loads(text, *, source="JSON"):
    """Decode JSON while rejecting ambiguous duplicate object keys."""
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise GameContentError(f"{source} is not valid JSON: {exc.msg}") from exc


def assert_frozen_holdout(holdout_questions):
    """Require the exact committed 140-row evaluation holdout receipt."""
    records = list(holdout_questions)
    digest = stable_json_sha256(records)
    if len(records) != FROZEN_HOLDOUT_RECORD_COUNT or digest != FROZEN_HOLDOUT_SHA256:
        raise GameContentError(
            "frozen holdout receipt mismatch: expected "
            f"{FROZEN_HOLDOUT_RECORD_COUNT} records with hash {FROZEN_HOLDOUT_SHA256}, "
            f"got {len(records)} records with hash {digest}"
        )
    return {
        "record_count": len(records),
        "sha256": digest,
    }


def current_generator_source_sha256():
    source = Path(__file__).with_name("game_candidate_generation.py")
    if not source.is_file():
        raise GameContentError("candidate generator source file is unavailable")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def current_backend_source_sha256():
    source = Path(__file__).with_name("game_colab_backend.py")
    if not source.is_file():
        raise GameContentError("Colab generation backend source file is unavailable")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def canonicalize_question(text):
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


def question_fingerprint(text):
    canonical = canonicalize_question(text)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"question:v1:{digest}"


def question_similarity(left, right):
    canonical_left = canonicalize_question(left)
    canonical_right = canonicalize_question(right)
    return SequenceMatcher(
        None,
        canonical_left,
        canonical_right,
        autojunk=False,
    ).ratio()


def _safe_display_text(value, *, minimum=1, maximum, canonical=True):
    if not isinstance(value, str):
        return False
    if canonical and value != value.strip():
        return False
    if not minimum <= len(value) <= maximum:
        return False
    if "<" in value or ">" in value:
        return False
    return not any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    )


def solve_question(solver):
    if not isinstance(solver, dict):
        raise GameContentError("solver must be an object")
    kind = solver.get("kind")

    if kind == "arithmetic":
        expression = solver.get("expression")
        if (
            set(solver) != {"kind", "expression"}
            or not _safe_display_text(expression, maximum=200)
            or "=" in expression
        ):
            raise GameContentError("arithmetic solver has invalid fields or expression")
        result = eval_computation(expression)
        if result is None:
            raise GameContentError("arithmetic solver expression is not safely parseable")
        return result

    if kind == "round_decimal":
        value_text = solver.get("value")
        places = solver.get("places")
        if (
            set(solver) != {"kind", "value", "places"}
            or not isinstance(value_text, str)
            or len(value_text) > 64
            or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value_text)
            is None
            or not isinstance(places, int)
            or isinstance(places, bool)
            or not 0 <= places <= 6
        ):
            raise GameContentError("round_decimal solver has invalid fields or values")
        value = Decimal(value_text)
        quantum = Decimal(1).scaleb(-places)
        rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
        return Fraction(rounded)

    if kind in {"gcd", "lcm"}:
        raw_values = solver.get("values")
        if (
            set(solver) != {"kind", "values"}
            or not isinstance(raw_values, list)
            or not 2 <= len(raw_values) <= 8
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value == 0
                or abs(value) > 1_000_000
                for value in (raw_values or [])
            )
        ):
            raise GameContentError(
                f"{kind} solver needs 2-8 bounded nonzero exact integers"
            )
        values = [abs(value) for value in raw_values]
        if kind == "gcd":
            return Fraction(reduce(gcd, values))

        def pair_lcm(left, right):
            return left * right // gcd(left, right)

        return Fraction(reduce(pair_lcm, values))

    if kind == "decimal_to_fraction":
        value_text = solver.get("value")
        if (
            set(solver) != {"kind", "value"}
            or not isinstance(value_text, str)
            or len(value_text) > 64
            or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value_text)
            is None
        ):
            raise GameContentError(
                "decimal_to_fraction solver has invalid fields or value"
            )
        return Fraction(Decimal(value_text))

    raise GameContentError(f"unsupported solver kind: {kind}")


def validate_question_bank(items, holdout_questions):
    errors = []
    validated = []
    seen_ids = set()
    seen_canonical_questions = {}
    holdout = [
        {
            "id": str(record.get("id", "unknown")),
            "question": str(record.get("question", "")),
            "canonical": canonicalize_question(record.get("question", "")),
        }
        for record in holdout_questions
    ]

    for index, original in enumerate(items):
        if not isinstance(original, dict):
            errors.append(f"item {index + 1}: question record must be an object")
            continue
        item = deepcopy(original)
        required_fields = {
            "id",
            "question",
            "correct",
            "topic",
            "difficulty",
            "visual_tool",
            "trusted_steps",
            "solver",
        }
        enrichment_fields = {"canonical_question", "question_hash", "source"}
        missing_fields = required_fields - set(item)
        extra_fields = set(item) - required_fields - enrichment_fields
        if missing_fields:
            errors.append(
                f"item {index + 1}: missing fields {', '.join(sorted(missing_fields))}"
            )
        if extra_fields:
            errors.append(
                f"item {index + 1}: unexpected fields {', '.join(sorted(extra_fields))}"
            )

        raw_question_id = item.get("id")
        question_id = raw_question_id if isinstance(raw_question_id, str) else ""
        prefix = question_id or f"item {index + 1}"

        if not re.fullmatch(r"GR-NUM-\d{3}", question_id):
            errors.append(f"{prefix}: ID must match GR-NUM-###")
        if question_id in seen_ids:
            errors.append(f"duplicate question ID: {question_id}")
        seen_ids.add(question_id)

        question_text = item.get("question")
        if not _safe_display_text(question_text, maximum=420):
            errors.append(
                f"{prefix}: question must be canonical plain text containing 1-420 characters"
            )
            question_text = question_text if isinstance(question_text, str) else ""

        topic = item.get("topic")
        if topic not in NUMBER_SUBJECTS:
            errors.append(f"{prefix}: topic is not in the trained Number taxonomy")
        if item.get("difficulty") not in {"easy", "medium", "hard"}:
            errors.append(f"{prefix}: difficulty must be easy, medium, or hard")
        visual_tool = item.get("visual_tool")
        if not isinstance(visual_tool, str) or re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}", visual_tool
        ) is None:
            errors.append(
                f"{prefix}: visual_tool must be a lowercase authored tool identifier"
            )
        trusted_steps = item.get("trusted_steps")
        if not isinstance(trusted_steps, list) or not 1 <= len(trusted_steps) <= 6:
            errors.append(f"{prefix}: trusted_steps must contain 1-6 steps")
        elif any(
            not _safe_display_text(step, maximum=240)
            for step in trusted_steps
        ):
            errors.append(
                f"{prefix}: trusted_steps must be canonical plain text of 1-240 characters"
            )

        correct = _simple_answer_value(item.get("correct"))
        if correct is None:
            errors.append(f"{prefix}: correct answer must be a simple integer, decimal, or fraction")
        else:
            try:
                solved = solve_question(item.get("solver"))
            except (GameContentError, ArithmeticError, TypeError, ValueError) as exc:
                errors.append(f"{prefix}: trusted solver failed: {exc}")
            else:
                if solved != correct:
                    errors.append(
                        f"{prefix}: trusted solver produces {solved}, not {item.get('correct')}"
                    )

        canonical = canonicalize_question(question_text)
        if canonical in seen_canonical_questions:
            errors.append(
                f"{prefix}: duplicate canonical question with "
                f"{seen_canonical_questions[canonical]}"
            )
        else:
            seen_canonical_questions[canonical] = prefix
        for heldout in holdout:
            if canonical == heldout["canonical"]:
                errors.append(
                    f"{prefix}: frozen holdout exact match with {heldout['id']}"
                )
                continue
            similarity = question_similarity(question_text, heldout["question"])
            if similarity >= 0.92:
                errors.append(
                    f"{prefix}: near-duplicate frozen holdout match with "
                    f"{heldout['id']} ({similarity:.3f})"
                )

        computed_hash = question_fingerprint(question_text)
        if "canonical_question" in item and item["canonical_question"] != canonical:
            errors.append(f"{prefix}: canonical_question enrichment is stale or modified")
        if "question_hash" in item and item["question_hash"] != computed_hash:
            errors.append(f"{prefix}: question_hash enrichment is stale or modified")
        if "source" in item and item["source"] != "original-game-v1":
            errors.append(f"{prefix}: source enrichment must be original-game-v1")
        item["canonical_question"] = canonical
        item["question_hash"] = computed_hash
        item["source"] = "original-game-v1"
        validated.append(item)

    if errors:
        raise GameContentError("Question bank validation failed: " + " | ".join(errors))
    return validated


def strict_parse_distractors(raw_response):
    """Parse the model's exact JSON contract without best-effort recovery.

    The general evaluation parser intentionally salvages JSON embedded in prose. Game
    content cannot do that: a response either matches the complete three-item contract
    or it remains outside the review boundary.
    """
    if not isinstance(raw_response, str):
        raise GameContentError("raw response must be text")
    if not raw_response.strip() or len(raw_response) > 20_000:
        raise GameContentError("raw response must contain 1-20000 characters")
    try:
        payload = strict_json_loads(raw_response, source="raw response")
    except GameContentError as exc:
        if "duplicate JSON key" in str(exc):
            raise
        raise GameContentError(str(exc).replace("not valid JSON", "not exact JSON")) from exc
    if not isinstance(payload, dict) or set(payload) != {"distractors"}:
        raise GameContentError("top-level JSON must contain only 'distractors'")
    distractors = payload["distractors"]
    if not isinstance(distractors, list) or len(distractors) != 3:
        raise GameContentError("distractors must be a list of exactly three items")

    parsed = []
    required = {"misconception", "computation", "answer"}
    limits = {"misconception": 240, "computation": 320, "answer": 64}
    for index, distractor in enumerate(distractors):
        if not isinstance(distractor, dict) or set(distractor) != required:
            raise GameContentError(
                f"distractor {index + 1} must contain exactly misconception, "
                "computation, and answer"
            )
        normalized = {}
        for field in ("misconception", "computation", "answer"):
            value = distractor[field]
            if not isinstance(value, str):
                raise GameContentError(f"distractor {index + 1} {field} must be text")
            value = value.strip()
            if not value or len(value) > limits[field]:
                raise GameContentError(
                    f"distractor {index + 1} {field} must contain "
                    f"1-{limits[field]} characters"
                )
            if (
                any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
                or any(
                    character.isdigit() and character not in "0123456789"
                    for character in value
                )
                or "<" in value
                or ">" in value
            ):
                raise GameContentError(
                    f"distractor {index + 1} {field} must be plain display text"
                )
            normalized[field] = value
        parsed.append(normalized)
    return parsed


def _issue(issues, code, message, distractor_index=None):
    issue = {"code": code, "message": message}
    if distractor_index is not None:
        issue["distractor_index"] = distractor_index
    issues.append(issue)


def _is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_pinned_revision(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _is_utc_timestamp(value):
    return _parse_utc_timestamp(value) is not None


def _parse_utc_timestamp(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        return None
    return parsed


def _candidate_id(record):
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"candidate_id", "generated_at_utc"}
    }
    return f"candidate:v1:{stable_json_sha256(payload)}"


_SIMPLE_ANSWER = re.compile(
    r"^-?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]+)?|(?:0|[1-9][0-9]*)/(?:[1-9][0-9]*))$"
)


def _simple_answer_value(value):
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value) > 64
        or _SIMPLE_ANSWER.fullmatch(value) is None
    ):
        return None
    return to_value(value)


def _misconception_key(value):
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", normalized, flags=re.UNICODE))


def _validation_digest(candidate_hash, question_hash, status, issues, distractors):
    payload = {
        "validator_version": VALIDATOR_VERSION,
        "candidate_hash": candidate_hash,
        "question_hash": question_hash,
        "status": status,
        "issues": issues,
        "distractors": distractors,
    }
    return f"validation:v1:{stable_json_sha256(payload)}"


def _assert_validation_artifact(validation):
    if not isinstance(validation, dict):
        raise GameContentError("validation artifact must be an object")
    if (
        validation.get("schema_version") != "glitch-rally-validation-v1"
        or validation.get("validator_version") != VALIDATOR_VERSION
    ):
        raise GameContentError("validation artifact version is not current")
    expected = _validation_digest(
        validation.get("candidate_hash"),
        validation.get("question_hash"),
        validation.get("status"),
        validation.get("issues"),
        validation.get("distractors"),
    )
    if validation.get("validation_hash") != expected:
        raise GameContentError("validation artifact content does not match its hash")
    if validation.get("candidate_id") != validation.get("candidate_hash"):
        raise GameContentError("validation artifact candidate binding is invalid")


def _assert_validation_matches_question(validation, trusted_question):
    _assert_validation_artifact(validation)
    fresh = validate_generation_candidate(
        validation.get("raw_candidate"),
        trusted_question,
    )
    for field in (
        "candidate_id",
        "candidate_hash",
        "question_id",
        "question_hash",
        "status",
        "issues",
        "distractors",
        "validation_hash",
    ):
        if fresh.get(field) != validation.get(field):
            raise GameContentError(
                f"validation artifact does not match the trusted question ({field})"
            )


def build_review_payload(validation, trusted_question):
    """Return the exact immutable context the owner is being asked to review."""
    _assert_validation_matches_question(validation, trusted_question)
    raw = validation["raw_candidate"]
    return {
        "candidate_id": validation["candidate_id"],
        "candidate_hash": validation["candidate_hash"],
        "validation_hash": validation["validation_hash"],
        "question": {
            "id": trusted_question["id"],
            "question_hash": trusted_question["question_hash"],
            "prompt": trusted_question["question"],
            "correct": trusted_question["correct"],
            "topic": trusted_question["topic"],
            "trusted_steps": deepcopy(trusted_question["trusted_steps"]),
        },
        "distractors": deepcopy(validation["distractors"]),
        "generation": {
            "model_id": raw["model_id"],
            "model_revision": raw["model_revision"],
            "adapter_id": raw["adapter_id"],
            "adapter_revision": raw["adapter_revision"],
            "generator_version": raw["generator_version"],
            "generator_source_sha256": raw["generator_source_sha256"],
            "backend_source_sha256": raw["backend_source_sha256"],
            "prompt_sha256": raw["prompt_sha256"],
        },
        "allowed_glitch_families": deepcopy(GLITCH_FAMILIES),
    }


def review_payload_fingerprint(validation, trusted_question):
    payload = build_review_payload(validation, trusted_question)
    return f"review-payload:v1:{stable_json_sha256(payload)}"


def validate_generation_candidate(
    record,
    trusted_question,
    *,
    expected_source_batch_sha256=None,
):
    """Validate one raw model generation and fail closed to review or rejection.

    Passing here is deliberately not approval. It proves pinned provenance, exact
    structure, and checkable arithmetic; a human still has to judge whether each named
    misconception is genuinely represented and suitable for a sixth-grade learner.
    """
    raw_candidate = deepcopy(record) if isinstance(record, dict) else {}
    question = deepcopy(trusted_question) if isinstance(trusted_question, dict) else {}
    issues = []
    distractors = []

    allowed_fields = {
        "schema_version",
        "run_id",
        "candidate_id",
        "generator_source_sha256",
        "backend_source_sha256",
        "question_id",
        "question",
        "correct",
        "topic",
        "question_hash",
        "model_id",
        "model_revision",
        "adapter_id",
        "adapter_revision",
        "system_prompt_sha256",
        "user_prompt_sha256",
        "prompt_sha256",
        "generation_parameters",
        "source_batch_sha256",
        "question_record_sha256",
        "generator_version",
        "generated_at_utc",
        "raw_response",
        "raw_response_sha256",
    }
    missing = sorted(allowed_fields - set(raw_candidate))
    extra = sorted(set(raw_candidate) - allowed_fields)
    if missing:
        _issue(issues, "missing_provenance", f"missing fields: {', '.join(missing)}")
    if extra:
        _issue(issues, "unexpected_provenance", f"unexpected fields: {', '.join(extra)}")
    if raw_candidate.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        _issue(issues, "schema_version_mismatch", "candidate schema version is not supported")

    for field in ("run_id", "generator_version"):
        value = raw_candidate.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            _issue(issues, "invalid_provenance", f"{field} must be nonempty text")
    if re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{2,79}",
        raw_candidate.get("run_id", "")
        if isinstance(raw_candidate.get("run_id"), str)
        else "",
    ) is None:
        _issue(
            issues,
            "invalid_run_id",
            "run_id must be a lowercase non-PII safe identifier",
        )
    if raw_candidate.get("generator_version") != FINAL_GENERATOR_VERSION:
        _issue(
            issues,
            "generator_version_mismatch",
            "candidate did not use the locked Glitch Rally generator",
        )
    generator_source_hash = raw_candidate.get("generator_source_sha256")
    if not _is_sha256(generator_source_hash):
        _issue(
            issues,
            "invalid_generator_source_hash",
            "generator_source_sha256 must be a lowercase SHA-256",
        )
    elif generator_source_hash != current_generator_source_sha256():
        _issue(
            issues,
            "generator_source_hash_mismatch",
            "candidate generator source does not match the reviewed local generator",
        )
    backend_source_hash = raw_candidate.get("backend_source_sha256")
    if not _is_sha256(backend_source_hash):
        _issue(
            issues,
            "invalid_backend_source_hash",
            "backend_source_sha256 must be a lowercase SHA-256",
        )
    elif backend_source_hash != current_backend_source_sha256():
        _issue(
            issues,
            "backend_source_hash_mismatch",
            "candidate backend source does not match the reviewed Colab backend",
        )
    if not _is_utc_timestamp(raw_candidate.get("generated_at_utc")):
        _issue(issues, "invalid_timestamp", "generated_at_utc must be an ISO-8601 UTC timestamp")

    expected_candidate_id = _candidate_id(raw_candidate)
    if raw_candidate.get("candidate_id") != expected_candidate_id:
        _issue(issues, "candidate_id_mismatch", "candidate_id does not bind the raw record")

    question_fields = {
        "question_id": "id",
        "question": "question",
        "correct": "correct",
        "topic": "topic",
        "question_hash": "question_hash",
    }
    for candidate_field, question_field in question_fields.items():
        if raw_candidate.get(candidate_field) != question.get(question_field):
            _issue(
                issues,
                "question_mismatch",
                f"{candidate_field} does not match the trusted question bank",
            )

    if raw_candidate.get("model_id") != FINAL_BASE_MODEL:
        _issue(issues, "model_mismatch", "candidate did not use the final 4B base model")
    if raw_candidate.get("adapter_id") != FINAL_ADAPTER:
        _issue(issues, "adapter_mismatch", "candidate did not use the final v7.1 adapter")
    for field in ("model_revision", "adapter_revision"):
        if not _is_pinned_revision(raw_candidate.get(field)):
            _issue(
                issues,
                "immutable_revision_required",
                f"{field} must be a resolved 40-character commit SHA",
            )
    if raw_candidate.get("generation_parameters") != DETERMINISTIC_GENERATION_PARAMETERS:
        _issue(
            issues,
            "generation_parameters_mismatch",
            "generation must use the locked deterministic parameters",
        )

    user_prompt = build_user(
        str(question.get("question", "")),
        str(question.get("correct", "")),
        str(question.get("topic", "")),
    )
    prompt_hashes = {
        "system_prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "user_prompt_sha256": sha256_text(user_prompt),
        "prompt_sha256": stable_json_sha256(
            {"system": SYSTEM_PROMPT, "user": user_prompt}
        ),
    }
    for field, expected in prompt_hashes.items():
        if raw_candidate.get(field) != expected:
            _issue(issues, "prompt_hash_mismatch", f"{field} does not match current prompts")

    expected_question_record_hash = stable_json_sha256(question)
    if raw_candidate.get("question_record_sha256") != expected_question_record_hash:
        _issue(
            issues,
            "question_record_hash_mismatch",
            "question record hash does not match the validated question",
        )
    source_batch_hash = raw_candidate.get("source_batch_sha256")
    if not _is_sha256(source_batch_hash):
        _issue(issues, "invalid_source_batch_hash", "source batch hash must be SHA-256")
    if (
        expected_source_batch_sha256 is not None
        and source_batch_hash != expected_source_batch_sha256
    ):
        _issue(
            issues,
            "source_batch_hash_mismatch",
            "source batch hash does not match the validated generation batch",
        )

    raw_response = raw_candidate.get("raw_response")
    claimed_raw_hash = raw_candidate.get("raw_response_sha256")
    if not isinstance(raw_response, str) or claimed_raw_hash != sha256_text(raw_response or ""):
        _issue(
            issues,
            "raw_response_hash_mismatch",
            "raw response does not match its SHA-256",
        )

    try:
        distractors = strict_parse_distractors(raw_response)
    except GameContentError as exc:
        _issue(issues, "strict_parse_failed", str(exc))
    else:
        correct = _simple_answer_value(question.get("correct"))
        if correct is None:
            _issue(
                issues,
                "unsupported_correct",
                "trusted correct answer is not a simple integer, decimal, or fraction",
            )
        answer_values = []
        misconception_keys = []
        for index, distractor in enumerate(distractors):
            answer = distractor["answer"]
            answer_value = _simple_answer_value(answer)
            answer_values.append(answer_value)
            if answer_value is None:
                _issue(
                    issues,
                    "unsupported_answer",
                    "answer must be a simple integer, decimal, or fraction",
                    index,
                )
            elif answer_value == correct:
                _issue(
                    issues,
                    "answer_equals_correct",
                    "distractor answer equals the trusted correct answer",
                    index,
                )

            misconception_key = _misconception_key(distractor["misconception"])
            misconception_keys.append(misconception_key)
            if sum(character.isalnum() for character in misconception_key) < 3:
                _issue(
                    issues,
                    "invalid_misconception",
                    "misconception must contain at least three letters or numbers",
                    index,
                )

            computation = distractor["computation"]
            if computation.count("=") != 1:
                _issue(
                    issues,
                    "equals_count",
                    "computation must contain exactly one equals sign",
                    index,
                )
                continue
            displayed_rhs = computation.split("=", 1)[1].strip()
            rhs_value = _simple_answer_value(displayed_rhs)
            if rhs_value is None:
                _issue(
                    issues,
                    "unsupported_rhs",
                    "displayed computation result is not a supported numeric value",
                    index,
                )
            elif answer_value is not None and rhs_value != answer_value:
                _issue(
                    issues,
                    "rhs_answer_mismatch",
                    "displayed computation result does not equal the answer",
                    index,
                )

            consistency = computation_consistent(
                computation,
                answer,
                question=question.get("question", ""),
            )
            if consistency is None:
                _issue(
                    issues,
                    "computation_uncheckable",
                    "computation is not safely evaluable operator-bearing arithmetic",
                    index,
                )
            elif consistency is False:
                _issue(
                    issues,
                    "computation_inconsistent",
                    "computation does not evaluate to the answer or is ungrounded",
                    index,
                )

        seen_answers = {}
        for index, value in enumerate(answer_values):
            if value is None:
                continue
            if value in seen_answers:
                _issue(
                    issues,
                    "duplicate_answer",
                    f"answer duplicates distractor {seen_answers[value] + 1}",
                    index,
                )
            else:
                seen_answers[value] = index
        seen_labels = {}
        for index, key in enumerate(misconception_keys):
            if key in seen_labels:
                _issue(
                    issues,
                    "duplicate_misconception",
                    f"misconception duplicates distractor {seen_labels[key] + 1}",
                    index,
                )
            else:
                seen_labels[key] = index

    status = "needs_review" if not issues else "rejected"
    candidate_hash = expected_candidate_id
    return {
        "schema_version": "glitch-rally-validation-v1",
        "validator_version": VALIDATOR_VERSION,
        "candidate_id": raw_candidate.get("candidate_id", ""),
        "candidate_hash": candidate_hash,
        "question_id": question.get("id", ""),
        "question_hash": question.get("question_hash", ""),
        "status": status,
        "issues": issues,
        "distractors": distractors,
        "validation_hash": _validation_digest(
            candidate_hash,
            question.get("question_hash", ""),
            status,
            issues,
            distractors,
        ),
        "raw_candidate": raw_candidate,
    }


def create_review_queue(validations, trusted_questions):
    """Create an owner-editable queue from automatically valid candidates only."""
    questions = {item.get("id"): item for item in trusted_questions}
    queue = []
    for validation in validations:
        _assert_validation_artifact(validation)
        if validation.get("status") != "needs_review":
            continue
        question_id = validation.get("question_id")
        question = questions.get(question_id)
        if question is None:
            raise GameContentError(f"review queue has no trusted question for {question_id}")
        review_payload = build_review_payload(validation, question)
        review_payload_hash = f"review-payload:v1:{stable_json_sha256(review_payload)}"
        distractor_reviews = [
            {
                "index": index,
                "semantic_valid": None,
                "age_appropriate": None,
                "glitch_family_id": "",
                "repair_prompt": "",
                "repair_explanation": "",
            }
            for index in range(3)
        ]
        queue.append(
            {
                "schema_version": "glitch-rally-review-queue-v1",
                "review_status": "pending",
                "review_payload": review_payload,
                "review_payload_hash": review_payload_hash,
                "decision": {
                    "schema_version": "glitch-rally-review-decision-v1",
                    "candidate_id": validation.get("candidate_id", ""),
                    "candidate_hash": validation.get("candidate_hash", ""),
                    "validation_hash": validation.get("validation_hash", ""),
                    "review_payload_hash": review_payload_hash,
                    "decision": "pending",
                    "reviewer": "",
                    "reviewed_at_utc": "",
                    "notes": "",
                    "trusted_question_verified": None,
                    "trusted_answer_verified": None,
                    "trusted_steps_verified": None,
                    "holdout_origin_verified": None,
                    "distractor_reviews": deepcopy(distractor_reviews),
                },
            }
        )
    return queue


def _review_text(value, field, *, required=True, maximum=500):
    if not _safe_display_text(
        value,
        minimum=1 if required else 0,
        maximum=maximum,
    ):
        raise GameContentError(
            f"{field} must be canonical plain display text up to {maximum} characters"
        )
    return value


def _review_digest(validation, decision):
    payload = {
        "candidate_id": validation.get("candidate_id"),
        "candidate_hash": validation.get("candidate_hash"),
        "validation_hash": validation.get("validation_hash"),
        "decision": decision,
    }
    return f"review:v1:{stable_json_sha256(payload)}"


def apply_review_decision(validation, decision, *, trusted_question):
    """Bind an explicit owner decision to the exact candidate and validation run."""
    _assert_validation_matches_question(validation, trusted_question)
    if validation.get("status") != "needs_review":
        raise GameContentError("only a needs_review candidate can receive a decision")
    if not isinstance(decision, dict):
        raise GameContentError("review decision must be an object")
    required_fields = {
        "schema_version",
        "candidate_id",
        "candidate_hash",
        "validation_hash",
        "review_payload_hash",
        "decision",
        "reviewer",
        "reviewed_at_utc",
        "notes",
        "trusted_question_verified",
        "trusted_answer_verified",
        "trusted_steps_verified",
        "holdout_origin_verified",
        "distractor_reviews",
    }
    if set(decision) != required_fields:
        raise GameContentError("review decision fields do not match the v1 contract")
    if decision.get("schema_version") != "glitch-rally-review-decision-v1":
        raise GameContentError("review decision schema version is not supported")
    for field in ("candidate_id", "candidate_hash", "validation_hash"):
        if decision.get(field) != validation.get(field):
            raise GameContentError(f"review {field} does not match current validation")
    expected_payload_hash = review_payload_fingerprint(validation, trusted_question)
    if decision.get("review_payload_hash") != expected_payload_hash:
        raise GameContentError(
            "review review_payload_hash does not match the exact question and distractors shown"
        )

    normalized = deepcopy(decision)
    normalized["reviewer"] = _review_text(
        decision.get("reviewer"), "reviewer", maximum=100
    )
    if not _is_utc_timestamp(decision.get("reviewed_at_utc")):
        raise GameContentError("reviewed_at_utc must be an ISO-8601 UTC timestamp")
    generated_at = _parse_utc_timestamp(
        validation.get("raw_candidate", {}).get("generated_at_utc")
    )
    reviewed_at = _parse_utc_timestamp(decision.get("reviewed_at_utc"))
    if generated_at is None or reviewed_at < generated_at:
        raise GameContentError("reviewed_at_utc cannot predate generation")
    normalized["notes"] = _review_text(
        decision.get("notes"),
        "notes",
        required=decision.get("decision") == "rejected",
        maximum=1000,
    )

    review_status = decision.get("decision")
    if review_status not in {"approved", "rejected"}:
        raise GameContentError("decision must be approved or rejected")
    reviews = decision.get("distractor_reviews")
    verification_fields = (
        "trusted_question_verified",
        "trusted_answer_verified",
        "trusted_steps_verified",
        "holdout_origin_verified",
    )
    for field in verification_fields:
        if not isinstance(decision.get(field), bool):
            raise GameContentError(f"{field} must be explicitly true or false")
    if review_status == "rejected":
        if reviews != []:
            raise GameContentError("a rejected decision must use an empty distractor_reviews list")
    else:
        for field in verification_fields:
            if decision.get(field) is not True:
                raise GameContentError(f"approval requires {field}=true")
        if not isinstance(reviews, list) or len(reviews) != 3:
            raise GameContentError("approval requires exactly three distractor reviews")
        normalized_reviews = []
        seen_indexes = set()
        repair_prompts = set()
        for review in reviews:
            if not isinstance(review, dict) or set(review) != {
                "index",
                "semantic_valid",
                "age_appropriate",
                "glitch_family_id",
                "repair_prompt",
                "repair_explanation",
            }:
                raise GameContentError("distractor review fields do not match the v1 contract")
            index = review.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or index not in {0, 1, 2}:
                raise GameContentError("distractor review index must be 0, 1, or 2")
            if index in seen_indexes:
                raise GameContentError("distractor review indexes must be unique")
            seen_indexes.add(index)
            if review.get("semantic_valid") is not True:
                raise GameContentError(
                    f"distractor {index + 1} must be explicitly marked semantically valid"
                )
            if review.get("age_appropriate") is not True:
                raise GameContentError(
                    f"distractor {index + 1} must be explicitly marked age appropriate"
                )
            family_id = review.get("glitch_family_id")
            if family_id not in GLITCH_FAMILIES:
                raise GameContentError(f"distractor {index + 1} has an unknown Glitch family")
            prompt = _review_text(
                review.get("repair_prompt"),
                f"distractor {index + 1} repair_prompt",
                maximum=180,
            )
            explanation = _review_text(
                review.get("repair_explanation"),
                f"distractor {index + 1} repair_explanation",
                maximum=500,
            )
            prompt_key = prompt.casefold()
            if prompt_key in repair_prompts:
                raise GameContentError("repair prompts must be distinct")
            repair_prompts.add(prompt_key)
            normalized_reviews.append(
                {
                    "index": index,
                    "semantic_valid": True,
                    "age_appropriate": True,
                    "glitch_family_id": family_id,
                    "repair_prompt": prompt,
                    "repair_explanation": explanation,
                }
            )
        normalized["distractor_reviews"] = sorted(
            normalized_reviews, key=lambda review: review["index"]
        )

    review_hash = _review_digest(validation, normalized)
    return {
        "schema_version": "glitch-rally-reviewed-candidate-v1",
        "candidate_id": validation.get("candidate_id", ""),
        "candidate_hash": validation.get("candidate_hash", ""),
        "validation_hash": validation.get("validation_hash", ""),
        "review_payload_hash": expected_payload_hash,
        "question_id": validation.get("question_id", ""),
        "review_status": review_status,
        "review_hash": review_hash,
        "decision": normalized,
        "validation": deepcopy(validation),
    }


def _district_for_topic(topic):
    if "Fraction" in topic:
        return "Fraction Foundry"
    if "Decimal" in topic or "Rounding" in topic or topic == "Place Value":
        return "Decimal Docks"
    if "Negative" in topic:
        return "Integer Iceway"
    if "Factor" in topic or "Multiple" in topic:
        return "Factor Forest"
    if topic in {"BIDMAS", "Squares, Cubes, etc", "Laws of Indices"}:
        return "Operation Overpass"
    return "Number Nexus"


def _road_equation(question):
    solver = question["solver"]
    kind = solver["kind"]
    if kind == "arithmetic":
        return (
            solver["expression"]
            .replace("*", "×")
            .replace("/", "÷")
            .replace("^", "^")
            + " = ?"
        )
    if kind == "round_decimal":
        return f"Round {solver['value']} to {solver['places']} decimal place(s)"
    if kind == "decimal_to_fraction":
        return f"{solver['value']} = ? as a fraction"
    if kind == "gcd":
        return "GCF(" + ", ".join(str(value) for value in solver["values"]) + ") = ?"
    if kind == "lcm":
        return "LCM(" + ", ".join(str(value) for value in solver["values"]) + ") = ?"
    raise GameContentError(f"cannot build road equation for {kind}")


def _build_encounter(question, validation, reviewed, *, position, total):
    raw = validation["raw_candidate"]
    decision = reviewed["decision"]
    review_by_index = {
        review["index"]: review for review in decision["distractor_reviews"]
    }
    counterfeits = []
    repairs = []
    for index, distractor in enumerate(validation["distractors"]):
        review = review_by_index[index]
        family_id = review["glitch_family_id"]
        counterfeit_id = f"{question['id']}-counterfeit-{index + 1}"
        repair_id = f"{question['id']}-repair-{index + 1}"
        counterfeits.append(
            {
                "id": counterfeit_id,
                "answerId": f"{counterfeit_id}-answer",
                "answer": distractor["answer"],
                "misconception": distractor["misconception"],
                "computation": distractor["computation"],
                "glitchFamilyId": family_id,
                "glitchName": GLITCH_FAMILIES[family_id]["name"],
                "repairId": repair_id,
                "repairExplanation": review["repair_explanation"],
            }
        )
        repairs.append(
            {
                "id": repair_id,
                "label": review["repair_prompt"],
                "detail": review["repair_explanation"],
            }
        )

    return {
        "id": question["id"],
        "contentStatus": "approved",
        "reviewStatus": "approved",
        "sourceSplit": "original-game-v1",
        "questionHash": question["question_hash"],
        "district": _district_for_topic(question["topic"]),
        "roomLabel": f"Checkpoint {position} of {total}",
        "grade": 6,
        "difficulty": question["difficulty"],
        "visualTool": question["visual_tool"],
        "question": {
            "prompt": question["question"],
            "topic": question["topic"],
            "correctAnswer": question["correct"],
            "roadEquation": _road_equation(question),
            "trustedSteps": deepcopy(question["trusted_steps"]),
        },
        "correctAnswerId": f"{question['id']}-answer-correct",
        "featuredCounterfeitId": counterfeits[
            int(sha256_text(f"{question['question_hash']}|featured")[:8], 16)
            % len(counterfeits)
        ]["id"],
        "counterfeits": counterfeits,
        "repairChoices": repairs,
        "provenance": {
            "sourceType": "slm-generated",
            "sourceQuestionId": question["id"],
            "sourceCollection": question["source"],
            "excludedFromEvaluationHoldout": True,
            "questionFingerprint": question["question_hash"],
            "modelId": raw["model_id"],
            "modelRevision": raw["model_revision"],
            "adapterId": raw["adapter_id"],
            "adapterRevision": raw["adapter_revision"],
            "generationRunId": raw["run_id"],
            "generatedAt": raw["generated_at_utc"],
            "generatorVersion": raw["generator_version"],
            "generatorSourceSha256": raw["generator_source_sha256"],
            "backendSourceSha256": raw["backend_source_sha256"],
            "generationParameters": deepcopy(raw["generation_parameters"]),
            "systemPromptSha256": raw["system_prompt_sha256"],
            "userPromptSha256": raw["user_prompt_sha256"],
            "promptSha256": raw["prompt_sha256"],
            "rawResponseSha256": raw["raw_response_sha256"],
            "candidateHash": reviewed["candidate_hash"],
            "validatorVersion": VALIDATOR_VERSION,
            "validationReportHash": reviewed["validation_hash"],
            "reviewPayloadHash": reviewed["review_payload_hash"],
            "reviewRevision": reviewed["review_hash"],
            "approvedAt": decision["reviewed_at_utc"],
            "contentLicense": "original-game-content",
        },
    }


def export_approved_pack(
    question_items,
    *,
    holdout_questions,
    reviewed_records,
    pack_id,
    released_at_utc,
):
    """Revalidate every trust boundary and emit only sanitized approved encounters."""
    if not isinstance(pack_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,80}", pack_id) is None:
        raise GameContentError("pack_id must be a lowercase stable identifier")
    if not _is_utc_timestamp(released_at_utc):
        raise GameContentError("released_at_utc must be an ISO-8601 UTC timestamp")
    released_at = _parse_utc_timestamp(released_at_utc)

    holdout = list(holdout_questions)
    holdout_receipt = assert_frozen_holdout(holdout)
    questions = validate_question_bank(question_items, holdout)
    questions_by_id = {question["id"]: question for question in questions}
    source_batch_hash = stable_json_sha256(questions)
    approved_reviews = []
    seen_candidate_ids = set()
    for stored_review in reviewed_records:
        if not isinstance(stored_review, dict) or set(stored_review) != {
            "schema_version",
            "candidate_id",
            "candidate_hash",
            "validation_hash",
            "review_payload_hash",
            "question_id",
            "review_status",
            "review_hash",
            "decision",
            "validation",
        }:
            raise GameContentError("reviewed candidate fields do not match the v1 contract")
        if stored_review.get("schema_version") != "glitch-rally-reviewed-candidate-v1":
            raise GameContentError("reviewed candidate schema version is not supported")
        if stored_review.get("review_status") not in {"approved", "rejected"}:
            raise GameContentError("reviewed candidate status must be approved or rejected")
        candidate_id = stored_review.get("candidate_id")
        if candidate_id in seen_candidate_ids:
            raise GameContentError(f"duplicate reviewed candidate: {candidate_id}")
        seen_candidate_ids.add(candidate_id)
        stored_validation = stored_review.get("validation")
        if not isinstance(stored_validation, dict):
            raise GameContentError("reviewed candidate is missing its validation artifact")
        question_id = stored_review.get("question_id")
        question = questions_by_id.get(question_id)
        if question is None:
            raise GameContentError(f"reviewed candidate has unknown question {question_id}")

        fresh_validation = validate_generation_candidate(
            stored_validation.get("raw_candidate"),
            question,
            expected_source_batch_sha256=source_batch_hash,
        )
        if fresh_validation["status"] != "needs_review":
            raise GameContentError(
                f"reviewed candidate {question_id} no longer passes automatic validation"
            )
        for field in (
            "candidate_id",
            "candidate_hash",
            "validation_hash",
        ):
            if fresh_validation.get(field) != stored_review.get(field):
                raise GameContentError(
                    f"reviewed candidate {question_id} has a stale {field}"
                )

        fresh_review = apply_review_decision(
            fresh_validation,
            stored_review.get("decision"),
            trusted_question=question,
        )
        for field in (
            "candidate_id",
            "candidate_hash",
            "validation_hash",
            "review_payload_hash",
            "question_id",
            "review_status",
            "review_hash",
        ):
            if fresh_review.get(field) != stored_review.get(field):
                raise GameContentError(
                    f"reviewed candidate {question_id} has a stale or modified {field}"
                )
        if fresh_review["review_status"] == "approved":
            approved_at = _parse_utc_timestamp(
                fresh_review["decision"]["reviewed_at_utc"]
            )
            if approved_at is None or released_at < approved_at:
                raise GameContentError("pack createdAt cannot predate approval")
            approved_reviews.append((question, fresh_validation, fresh_review))

    if not approved_reviews:
        raise GameContentError("no approved current candidates are available for export")
    approved_reviews.sort(key=lambda values: values[0]["id"])
    approved_question_ids = [values[0]["id"] for values in approved_reviews]
    if len(set(approved_question_ids)) != len(approved_question_ids):
        raise GameContentError("multiple approved candidates exist for one question")
    encounters = [
        _build_encounter(
            question,
            validation,
            review,
            position=index,
            total=len(approved_reviews),
        )
        for index, (question, validation, review) in enumerate(
            approved_reviews,
            start=1,
        )
    ]

    pack = {
        "schemaVersion": "glitch-rally-pack-v1",
        "packVersion": pack_id,
        "createdAt": released_at_utc,
        "validatorVersion": VALIDATOR_VERSION,
        "contentOrigin": "offline-slm-generated-owner-reviewed",
        "questionBankSha256": source_batch_hash,
        "holdoutAssertion": {
            "excluded": True,
            "recordCount": holdout_receipt["record_count"],
            "sha256": holdout_receipt["sha256"],
        },
        "encounterIds": [encounter["id"] for encounter in encounters],
        "encounterCount": len(encounters),
        "glitchFamilies": deepcopy(GLITCH_FAMILIES),
        "encounters": encounters,
    }
    pack["contentHash"] = f"pack:v1:{stable_json_sha256(pack)}"
    return pack
