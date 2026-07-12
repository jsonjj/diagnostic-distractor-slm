"""Fail-closed command line pipeline for Glitch Rally content artifacts.

This module never invokes a model. Generation happens in the free-Colab/offline runner;
these commands validate questions and raw outputs, prepare owner review, and export the
sanitized static pack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .game_content import (
    GameContentError,
    apply_review_decision,
    assert_frozen_holdout,
    create_review_queue,
    export_approved_pack,
    stable_json_sha256,
    strict_json_loads,
    validate_generation_candidate,
    validate_question_bank,
)


def _read_jsonl(path):
    records = []
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise GameContentError(f"cannot read {source}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record = strict_json_loads(
            line,
            source=f"{source}:{line_number}",
        )
        if not isinstance(record, dict):
            raise GameContentError(f"{source}:{line_number} must contain a JSON object")
        records.append(record)
    return records


def _read_json(path):
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise GameContentError(f"cannot read {source}: {exc}") from exc
    value = strict_json_loads(text, source=str(source))
    if not isinstance(value, dict):
        raise GameContentError(f"{source} must contain one JSON object")
    return value


def _verify_run_manifest(path, candidate_path, candidates, source_batch_hash):
    manifest = _read_json(path)
    required = {
        "schema_version",
        "run_id",
        "bundle_id",
        "generator_source_sha256",
        "backend_source_sha256",
        "model_id",
        "model_revision",
        "adapter_id",
        "adapter_revision",
        "generation_parameters",
        "source_batch_sha256",
        "candidate_count",
        "output_sha256",
        "runtime_versions",
    }
    if set(manifest) != required:
        raise GameContentError("generation run manifest fields do not match the v1 contract")
    if manifest.get("schema_version") != "glitch-rally-generation-run-v1":
        raise GameContentError("generation run manifest schema version is not supported")
    if re.fullmatch(r"bundle:v1:[0-9a-f]{64}", manifest.get("bundle_id", "")) is None:
        raise GameContentError("generation run manifest has an invalid bundle_id")
    if (
        not isinstance(manifest.get("candidate_count"), int)
        or isinstance(manifest.get("candidate_count"), bool)
        or manifest["candidate_count"] != len(candidates)
    ):
        raise GameContentError("generation run manifest candidate_count mismatch")
    actual_output_hash = hashlib.sha256(Path(candidate_path).read_bytes()).hexdigest()
    if manifest.get("output_sha256") != actual_output_hash:
        raise GameContentError("generation run manifest output_sha256 mismatch")
    if manifest.get("source_batch_sha256") != source_batch_hash:
        raise GameContentError("generation run manifest source batch mismatch")

    runtime_versions = manifest.get("runtime_versions")
    expected_packages = {
        "unsloth",
        "torch",
        "transformers",
        "peft",
        "huggingface_hub",
    }
    if (
        not isinstance(runtime_versions, dict)
        or set(runtime_versions) != expected_packages
        or runtime_versions.get("unsloth") != "2026.7.1"
        or any(
            not isinstance(version, str)
            or not version
            or len(version) > 100
            for version in (runtime_versions or {}).values()
        )
    ):
        raise GameContentError("generation run manifest runtime versions are invalid")

    bindings = {
        "run_id": "run_id",
        "generator_source_sha256": "generator_source_sha256",
        "backend_source_sha256": "backend_source_sha256",
        "model_id": "model_id",
        "model_revision": "model_revision",
        "adapter_id": "adapter_id",
        "adapter_revision": "adapter_revision",
        "generation_parameters": "generation_parameters",
        "source_batch_sha256": "source_batch_sha256",
    }
    if not candidates:
        raise GameContentError("generation output contains no candidates")
    for index, candidate in enumerate(candidates, start=1):
        for manifest_field, candidate_field in bindings.items():
            if candidate.get(candidate_field) != manifest.get(manifest_field):
                raise GameContentError(
                    f"candidate {index} does not match run manifest {manifest_field}"
                )
    return manifest


def _atomic_write(path, text, *, force=False):
    destination = Path(path)
    if destination.exists() and not force:
        raise GameContentError(f"output already exists: {destination}; pass --force to replace it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, destination)
    except OSError as exc:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise GameContentError(f"cannot write {destination}: {exc}") from exc


def _write_jsonl(path, records, *, force=False):
    text = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    _atomic_write(path, text, force=force)


def _write_json(path, value, *, force=False):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _atomic_write(path, text, force=force)


def _validated_questions(question_path, holdout_path):
    questions = _read_jsonl(question_path)
    holdout = _read_jsonl(holdout_path)
    assert_frozen_holdout(holdout)
    return validate_question_bank(questions, holdout), holdout


def _unique_by(records, field, label):
    indexed = {}
    for record in records:
        key = record.get(field)
        if not isinstance(key, str) or not key:
            raise GameContentError(f"{label} is missing {field}")
        if key in indexed:
            raise GameContentError(f"duplicate {label} {field}: {key}")
        indexed[key] = record
    return indexed


def _fresh_validations(stored_validations, questions):
    questions_by_id = _unique_by(questions, "id", "question")
    source_batch_hash = stable_json_sha256(questions)
    fresh = []
    for stored in stored_validations:
        question_id = stored.get("question_id")
        question = questions_by_id.get(question_id)
        if question is None:
            raise GameContentError(f"validation references unknown question: {question_id}")
        validation = validate_generation_candidate(
            stored.get("raw_candidate"),
            question,
            expected_source_batch_sha256=source_batch_hash,
        )
        for field in ("candidate_id", "candidate_hash", "validation_hash", "status"):
            if validation.get(field) != stored.get(field):
                raise GameContentError(
                    f"stored validation for {question_id} has a stale or modified {field}"
                )
        fresh.append(validation)
    _unique_by(fresh, "candidate_id", "validation")
    return fresh


def _add_common_question_arguments(parser):
    parser.add_argument("--questions", required=True, help="Original game question JSONL")
    parser.add_argument("--holdout", required=True, help="Frozen eval holdout JSONL")


def _add_output_arguments(parser):
    parser.add_argument("--output", required=True)
    parser.add_argument("--force", action="store_true", help="Replace an existing output")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Validate and release Mathbreakers: Glitch Rally content"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("validate-questions", "prepare-batch"):
        child = subparsers.add_parser(command)
        _add_common_question_arguments(child)
        _add_output_arguments(child)

    validate = subparsers.add_parser("validate-candidates")
    _add_common_question_arguments(validate)
    validate.add_argument("--candidates", required=True, help="Raw generation JSONL")
    validate.add_argument("--run-manifest", required=True, help="Downloaded Colab run manifest")
    _add_output_arguments(validate)

    queue = subparsers.add_parser("create-review-queue")
    _add_common_question_arguments(queue)
    queue.add_argument("--validations", required=True)
    _add_output_arguments(queue)

    reviews = subparsers.add_parser("apply-reviews")
    _add_common_question_arguments(reviews)
    reviews.add_argument("--validations", required=True)
    reviews.add_argument(
        "--decisions",
        required=True,
        help="Decision JSONL, or an edited review-queue JSONL",
    )
    _add_output_arguments(reviews)

    export = subparsers.add_parser("export-pack")
    _add_common_question_arguments(export)
    export.add_argument("--reviewed", required=True)
    export.add_argument("--pack-id", required=True)
    export.add_argument("--released-at-utc", required=True)
    _add_output_arguments(export)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    questions, holdout = _validated_questions(args.questions, args.holdout)

    if args.command in {"validate-questions", "prepare-batch"}:
        _write_jsonl(args.output, questions, force=args.force)
        return 0

    if args.command == "validate-candidates":
        candidates = _read_jsonl(args.candidates)
        _unique_by(candidates, "candidate_id", "candidate")
        questions_by_id = _unique_by(questions, "id", "question")
        source_batch_hash = stable_json_sha256(questions)
        _verify_run_manifest(
            args.run_manifest,
            args.candidates,
            candidates,
            source_batch_hash,
        )
        validations = []
        for candidate in candidates:
            question_id = candidate.get("question_id")
            question = questions_by_id.get(question_id)
            if question is None:
                raise GameContentError(f"candidate references unknown question: {question_id}")
            validations.append(
                validate_generation_candidate(
                    candidate,
                    question,
                    expected_source_batch_sha256=source_batch_hash,
                )
            )
        _write_jsonl(args.output, validations, force=args.force)
        return 0

    if args.command == "create-review-queue":
        validations = _fresh_validations(_read_jsonl(args.validations), questions)
        queue = create_review_queue(validations, questions)
        _write_jsonl(args.output, queue, force=args.force)
        return 0

    if args.command == "apply-reviews":
        validations = _fresh_validations(_read_jsonl(args.validations), questions)
        validations_by_id = _unique_by(validations, "candidate_id", "validation")
        questions_by_id = _unique_by(questions, "id", "question")
        pending_ids = {
            candidate_id
            for candidate_id, validation in validations_by_id.items()
            if validation.get("status") == "needs_review"
        }
        decisions = []
        for record in _read_jsonl(args.decisions):
            if record.get("schema_version") == "glitch-rally-review-queue-v1":
                payload = record.get("review_payload")
                if not isinstance(payload, dict):
                    raise GameContentError("review queue payload must be an object")
                actual_payload_hash = (
                    "review-payload:v1:" + stable_json_sha256(payload)
                )
                decision = record.get("decision")
                if (
                    record.get("review_payload_hash") != actual_payload_hash
                    or not isinstance(decision, dict)
                    or decision.get("review_payload_hash") != actual_payload_hash
                ):
                    raise GameContentError(
                        "review queue payload does not match its bound decision hash"
                    )
                record = decision
            if not isinstance(record, dict):
                raise GameContentError("review queue entry is missing its decision object")
            decisions.append(record)
        decisions_by_id = _unique_by(decisions, "candidate_id", "review decision")
        decision_ids = set(decisions_by_id)
        if decision_ids != pending_ids:
            missing = sorted(pending_ids - decision_ids)
            extra = sorted(decision_ids - pending_ids)
            raise GameContentError(
                "review decisions must cover every needs_review candidate exactly once; "
                f"missing={missing}, extra={extra}"
            )
        reviewed = [
            apply_review_decision(
                validations_by_id[candidate_id],
                decisions_by_id[candidate_id],
                trusted_question=questions_by_id[
                    validations_by_id[candidate_id]["question_id"]
                ],
            )
            for candidate_id in sorted(pending_ids)
        ]
        _write_jsonl(args.output, reviewed, force=args.force)
        return 0

    if args.command == "export-pack":
        pack = export_approved_pack(
            questions,
            holdout_questions=holdout,
            reviewed_records=_read_jsonl(args.reviewed),
            pack_id=args.pack_id,
            released_at_utc=args.released_at_utc,
        )
        _write_json(args.output, pack, force=args.force)
        return 0

    raise GameContentError(f"unsupported command: {args.command}")


def _entrypoint():
    try:
        raise SystemExit(main())
    except GameContentError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    _entrypoint()
