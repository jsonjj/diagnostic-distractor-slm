"""Deterministic raw-candidate generation for Mathbreakers content forging.

This module deliberately has no model-runtime dependencies.  A caller supplies the
generation backend, which keeps the content/provenance boundary testable on CPU while
the Colab entrypoint owns Unsloth, Torch, PEFT, and Hugging Face integration.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterable, Mapping

from .game_colab_backend import BACKEND_SOURCE_SHA256, LoadedColabBackend
from .game_content import (
    canonicalize_question,
    question_fingerprint,
    validate_question_bank,
)
from .prompts import SYSTEM_PROMPT, build_user


SCHEMA_VERSION = "glitch-rally-candidate-v1"
GENERATOR_VERSION = "glitch-rally-generator-v1"
DEFAULT_MODEL_ID = "unsloth/Qwen3-4B-bnb-4bit"
DEFAULT_ADAPTER_ID = "j2ampn/qwen3-4b-distractor-lora-v7"
GENERATION_PARAMETERS = {
    "do_sample": False,
    "max_new_tokens": 512,
    "enable_thinking": False,
}
FROZEN_HOLDOUT_RECORD_COUNT = 140
FROZEN_HOLDOUT_SHA256 = (
    "47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693"
)
GENERATOR_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

_IMMUTABLE_REVISION = re.compile(r"[0-9a-f]{40}")
_SAFE_RUN_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,79}")
_VALIDATED_QUESTION_FIELDS = {
    "id",
    "question",
    "correct",
    "topic",
    "canonical_question",
    "question_hash",
    "source",
    "difficulty",
    "visual_tool",
    "trusted_steps",
    "solver",
}
_CANDIDATE_FIELDS = {
    "schema_version",
    "generator_version",
    "generator_source_sha256",
    "backend_source_sha256",
    "run_id",
    "candidate_id",
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
    "generated_at_utc",
    "raw_response",
    "raw_response_sha256",
}


class CandidateGenerationError(ValueError):
    """Raised when raw candidate generation cannot preserve provenance."""


@dataclass(frozen=True)
class GenerationProvenance:
    """Immutable identifiers shared by every candidate in one generation run."""

    run_id: str
    model_revision: str
    adapter_revision: str
    backend_source_sha256: str
    model_id: str = DEFAULT_MODEL_ID
    adapter_id: str = DEFAULT_ADAPTER_ID


_BATCH_RECEIPT_SEAL = object()


class ValidatedQuestionBatch(Sequence[dict[str, Any]]):
    """Read-only receipt proving which frozen holdout gated a question batch."""

    __slots__ = (
        "_records",
        "holdout_count",
        "holdout_sha256",
        "source_batch_sha256",
    )

    def __init__(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        holdout_count: int,
        holdout_sha256: str,
        _seal: object,
    ) -> None:
        if _seal is not _BATCH_RECEIPT_SEAL:
            raise CandidateGenerationError(
                "ValidatedQuestionBatch receipts must come from "
                "load_validated_question_batch"
            )
        materialized = tuple(deepcopy(dict(record)) for record in records)
        self._records = materialized
        self.holdout_count = holdout_count
        self.holdout_sha256 = holdout_sha256
        self.source_batch_sha256 = stable_json_sha256(materialized)

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index):
        return deepcopy(self._records[index])

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for record in self._records:
            yield deepcopy(record)


def sha256_text(text: str) -> str:
    """Hash the exact UTF-8 bytes of a text value."""

    if not isinstance(text, str):
        raise TypeError("sha256_text requires a string")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_json_sha256(value: Any) -> str:
    """Hash canonical compact JSON with sorted keys and Unicode preserved."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(payload)


def candidate_identity_sha256(record: Mapping[str, Any]) -> str:
    """Return the response-bound candidate ID for a raw record.

    Generation time is audit metadata rather than identity.  All other fields,
    including the exact raw response and every provenance hash, are bound here.
    """

    identity = {
        key: value
        for key, value in record.items()
        if key not in {"candidate_id", "generated_at_utc"}
    }
    return f"candidate:v1:{stable_json_sha256(identity)}"


def _reject_duplicate_object_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CandidateGenerationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CandidateGenerationError(f"{label} JSONL does not exist: {path}")

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(
                    line,
                    object_pairs_hook=_reject_duplicate_object_keys,
                )
            except json.JSONDecodeError as exc:
                raise CandidateGenerationError(
                    f"{label} JSONL line {line_number} is invalid JSON: {exc.msg}"
                ) from exc
            except CandidateGenerationError as exc:
                raise CandidateGenerationError(
                    f"{label} JSONL line {line_number} is invalid JSON: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise CandidateGenerationError(
                    f"{label} JSONL line {line_number} must be an object"
                )
            records.append(record)
    return records


def load_validated_question_batch(
    questions_path: str | Path,
    holdout_path: str | Path,
) -> ValidatedQuestionBatch:
    """Load original questions and pass all of them through the holdout gate."""

    questions = _read_jsonl(Path(questions_path), "question batch")
    holdout = _read_jsonl(Path(holdout_path), "frozen holdout")
    holdout_hash = stable_json_sha256(holdout)
    if (
        len(holdout) != FROZEN_HOLDOUT_RECORD_COUNT
        or holdout_hash != FROZEN_HOLDOUT_SHA256
    ):
        raise CandidateGenerationError(
            "frozen holdout receipt mismatch: expected "
            f"{FROZEN_HOLDOUT_RECORD_COUNT} records with hash "
            f"{FROZEN_HOLDOUT_SHA256}, got {len(holdout)} records with hash "
            f"{holdout_hash}"
        )
    validated = validate_question_bank(questions, holdout_questions=holdout)
    return ValidatedQuestionBatch(
        validated,
        holdout_count=len(holdout),
        holdout_sha256=holdout_hash,
        _seal=_BATCH_RECEIPT_SEAL,
    )


def _validate_provenance(provenance: GenerationProvenance) -> None:
    if not isinstance(provenance, GenerationProvenance):
        raise CandidateGenerationError("provenance must be GenerationProvenance")
    if (
        not isinstance(provenance.run_id, str)
        or _SAFE_RUN_ID.fullmatch(provenance.run_id) is None
    ):
        raise CandidateGenerationError(
            "run_id must match lowercase [a-z0-9][a-z0-9._-]{2,79}"
        )
    if not provenance.model_id.strip() or not provenance.adapter_id.strip():
        raise CandidateGenerationError("model_id and adapter_id must be nonempty")
    for name, revision in (
        ("model_revision", provenance.model_revision),
        ("adapter_revision", provenance.adapter_revision),
    ):
        if not _IMMUTABLE_REVISION.fullmatch(revision):
            raise CandidateGenerationError(
                f"{name} must be an immutable 40-character lowercase hexadecimal commit SHA"
            )
    if re.fullmatch(r"[0-9a-f]{64}", provenance.backend_source_sha256) is None:
        raise CandidateGenerationError(
            "backend_source_sha256 must be an immutable 64-character "
            "lowercase hexadecimal SHA-256"
        )


def _validate_backend_receipt(
    receipt: LoadedColabBackend,
    provenance: GenerationProvenance,
) -> None:
    if not isinstance(receipt, LoadedColabBackend):
        raise CandidateGenerationError(
            "backend must be a LoadedColabBackend receipt from the pinned loader "
            "or explicit test factory"
        )
    expected = {
        "model_id": provenance.model_id,
        "model_revision": provenance.model_revision,
        "adapter_id": provenance.adapter_id,
        "adapter_revision": provenance.adapter_revision,
        "backend_source_sha256": provenance.backend_source_sha256,
    }
    for field, expected_value in expected.items():
        if getattr(receipt, field) != expected_value:
            raise CandidateGenerationError(
                f"backend receipt {field} does not match generation provenance"
            )
    if not receipt.is_test and receipt.backend_source_sha256 != BACKEND_SOURCE_SHA256:
        raise CandidateGenerationError(
            "production backend receipt does not match the current backend source"
        )


def _validate_generation_questions(
    questions: ValidatedQuestionBatch,
) -> list[dict[str, Any]]:
    if not isinstance(questions, ValidatedQuestionBatch):
        raise CandidateGenerationError(
            "questions must be a ValidatedQuestionBatch receipt from "
            "load_validated_question_batch"
        )
    if (
        questions.holdout_count != FROZEN_HOLDOUT_RECORD_COUNT
        or questions.holdout_sha256 != FROZEN_HOLDOUT_SHA256
    ):
        raise CandidateGenerationError(
            "ValidatedQuestionBatch receipt does not match the frozen holdout"
        )
    materialized = [dict(question) for question in questions]
    if not materialized:
        raise CandidateGenerationError("validated question batch cannot be empty")
    if stable_json_sha256(materialized) != questions.source_batch_sha256:
        raise CandidateGenerationError(
            "ValidatedQuestionBatch receipt does not match its source records"
        )

    seen_ids: set[str] = set()
    for index, question in enumerate(materialized, start=1):
        missing = _VALIDATED_QUESTION_FIELDS.difference(question)
        if missing:
            raise CandidateGenerationError(
                f"question {index} is not validated; missing {', '.join(sorted(missing))}"
            )
        question_id = str(question["id"])
        if question_id in seen_ids:
            raise CandidateGenerationError(
                f"validated question batch has duplicate ID: {question_id}"
            )
        seen_ids.add(question_id)
        if question["canonical_question"] != canonicalize_question(question["question"]):
            raise CandidateGenerationError(
                f"{question_id}: canonical_question does not match question"
            )
        if question["question_hash"] != question_fingerprint(question["question"]):
            raise CandidateGenerationError(
                f"{question_id}: question_hash does not match question"
            )
        if question["source"] != "original-game-v1":
            raise CandidateGenerationError(
                f"{question_id}: source is not an approved original game question bank"
            )
    return materialized


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_candidate_record(
    *,
    question: Mapping[str, Any],
    source_batch_sha256: str,
    provenance: GenerationProvenance,
    raw_response: str,
    generated_at_utc: str,
) -> dict[str, Any]:
    user_prompt = build_user(
        str(question["question"]),
        str(question["correct"]),
        str(question["topic"]),
    )
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generator_source_sha256": GENERATOR_SOURCE_SHA256,
        "backend_source_sha256": provenance.backend_source_sha256,
        "run_id": provenance.run_id,
        "question_id": str(question["id"]),
        "question": str(question["question"]),
        "correct": str(question["correct"]),
        "topic": str(question["topic"]),
        "question_hash": str(question["question_hash"]),
        "model_id": provenance.model_id,
        "model_revision": provenance.model_revision,
        "adapter_id": provenance.adapter_id,
        "adapter_revision": provenance.adapter_revision,
        "system_prompt_sha256": sha256_text(SYSTEM_PROMPT),
        "user_prompt_sha256": sha256_text(user_prompt),
        "prompt_sha256": stable_json_sha256(
            {"system": SYSTEM_PROMPT, "user": user_prompt}
        ),
        "generation_parameters": dict(GENERATION_PARAMETERS),
        "source_batch_sha256": source_batch_sha256,
        "question_record_sha256": stable_json_sha256(question),
        "generated_at_utc": generated_at_utc,
        "raw_response": raw_response,
        "raw_response_sha256": sha256_text(raw_response),
    }
    record["candidate_id"] = candidate_identity_sha256(record)
    return record


def _invalid_existing_record(
    index: int,
    question_id: object,
    field: str,
    detail: str = "does not match the requested run",
) -> CandidateGenerationError:
    return CandidateGenerationError(
        f"invalid existing record {index} ({question_id}): {field} {detail}"
    )


def _verify_existing_records(
    records: Iterable[Mapping[str, Any]],
    *,
    questions: list[dict[str, Any]],
    source_batch_sha256: str,
    provenance: GenerationProvenance,
) -> dict[str, dict[str, Any]]:
    questions_by_id = {str(question["id"]): question for question in questions}
    verified: dict[str, dict[str, Any]] = {}

    for index, original in enumerate(records, start=1):
        record = dict(original)
        question_id = record.get("question_id", "unknown")
        fields = set(record)
        if fields != _CANDIDATE_FIELDS:
            missing = sorted(_CANDIDATE_FIELDS - fields)
            unexpected = sorted(fields - _CANDIDATE_FIELDS)
            detail_parts = []
            if missing:
                detail_parts.append(f"missing {', '.join(missing)}")
            if unexpected:
                detail_parts.append(f"has unexpected {', '.join(unexpected)}")
            raise _invalid_existing_record(
                index,
                question_id,
                "fields",
                "; ".join(detail_parts),
            )

        if question_id not in questions_by_id:
            raise _invalid_existing_record(
                index,
                question_id,
                "question_id",
                "is not in the requested source batch",
            )
        if question_id in verified:
            raise _invalid_existing_record(
                index,
                question_id,
                "question_id",
                "is duplicated",
            )

        question = questions_by_id[str(question_id)]
        user_prompt = build_user(
            str(question["question"]),
            str(question["correct"]),
            str(question["topic"]),
        )
        expected = {
            "schema_version": SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "generator_source_sha256": GENERATOR_SOURCE_SHA256,
            "backend_source_sha256": provenance.backend_source_sha256,
            "run_id": provenance.run_id,
            "question_id": str(question["id"]),
            "question": str(question["question"]),
            "correct": str(question["correct"]),
            "topic": str(question["topic"]),
            "question_hash": str(question["question_hash"]),
            "model_id": provenance.model_id,
            "model_revision": provenance.model_revision,
            "adapter_id": provenance.adapter_id,
            "adapter_revision": provenance.adapter_revision,
            "system_prompt_sha256": sha256_text(SYSTEM_PROMPT),
            "user_prompt_sha256": sha256_text(user_prompt),
            "prompt_sha256": stable_json_sha256(
                {"system": SYSTEM_PROMPT, "user": user_prompt}
            ),
            "generation_parameters": dict(GENERATION_PARAMETERS),
            "source_batch_sha256": source_batch_sha256,
            "question_record_sha256": stable_json_sha256(question),
        }
        for field, expected_value in expected.items():
            if record[field] != expected_value:
                raise _invalid_existing_record(index, question_id, field)

        raw_response = record["raw_response"]
        if not isinstance(raw_response, str):
            raise _invalid_existing_record(
                index,
                question_id,
                "raw_response",
                "must be text",
            )
        if record["raw_response_sha256"] != sha256_text(raw_response):
            raise _invalid_existing_record(
                index,
                question_id,
                "raw_response_sha256",
                "does not match raw_response",
            )
        if (
            not isinstance(record["generated_at_utc"], str)
            or not record["generated_at_utc"].strip()
        ):
            raise _invalid_existing_record(
                index,
                question_id,
                "generated_at_utc",
                "must be a nonempty string",
            )
        if record["candidate_id"] != candidate_identity_sha256(record):
            raise _invalid_existing_record(
                index,
                question_id,
                "candidate_id",
                "does not match the candidate identity payload",
            )
        verified[str(question_id)] = record

    return verified


def _atomic_write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def generate_candidate_batch(
    *,
    questions: ValidatedQuestionBatch,
    output_path: str | Path,
    backend: LoadedColabBackend,
    provenance: GenerationProvenance,
    resume: bool = False,
    clock: Callable[[], str] = _utc_now,
) -> list[dict[str, Any]]:
    """Generate one raw response per validated question and persist JSONL.

    A new run refuses an existing destination.  Resume verifies every existing byte-
    bound candidate before skipping it, and checkpoints atomically after each newly
    generated response so an interrupted Colab session loses at most one generation.
    """

    _validate_provenance(provenance)
    validated_questions = _validate_generation_questions(questions)
    _validate_backend_receipt(backend, provenance)
    output = Path(output_path)
    batch_hash = questions.source_batch_sha256

    if output.exists() and not resume:
        raise CandidateGenerationError(
            f"output already exists; pass resume=True to verify and continue: {output}"
        )

    existing_records = (
        _read_jsonl(output, "existing candidate output")
        if output.exists()
        else []
    )
    records_by_question = _verify_existing_records(
        existing_records,
        questions=validated_questions,
        source_batch_sha256=batch_hash,
        provenance=provenance,
    )

    for question in validated_questions:
        question_id = str(question["id"])
        if question_id in records_by_question:
            continue
        user_prompt = build_user(
            str(question["question"]),
            str(question["correct"]),
            str(question["topic"]),
        )
        raw_response = backend.backend(
            SYSTEM_PROMPT,
            user_prompt,
            dict(GENERATION_PARAMETERS),
        )
        if not isinstance(raw_response, str):
            raise CandidateGenerationError(
                f"backend returned non-text response for {question['id']}"
            )
        records_by_question[question_id] = _build_candidate_record(
            question=question,
            source_batch_sha256=batch_hash,
            provenance=provenance,
            raw_response=raw_response,
            generated_at_utc=clock(),
        )
        checkpoint = [
            records_by_question[str(item["id"])]
            for item in validated_questions
            if str(item["id"]) in records_by_question
        ]
        _atomic_write_jsonl(output, checkpoint)

    return [records_by_question[str(question["id"])] for question in validated_questions]
