"""Build deterministic v8 data without contaminating the final benchmark.

The legacy 140-item Eedi split has informed seven model iterations, so v8 treats it as a
frozen development set rather than an unbiased final test. Previously unused Number
questions are deterministically split into an Opus teacher pool and a new benchmark
before any v8 training or inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

from .config import DATA_PROCESSED, NUMBER_SUBJECTS, OPUS_MODEL_ID
from .consistency import computation_consistent
from .data_prep import labeled_all3, load_rows
from .format_augment import stylize
from .generate import (
    V5_FAMILIES,
    generate_balanced,
    load_jsonl,
    synth_to_sft,
    three_distinct_ok,
)
from .text_utils import normalize_answer
from .v8_targeted import generate_targeted_sft


V8_SEED = 808
V8_SYNTH_SEED = 83
V8_STYLE_SEED = 89
V8_PER_FAMILY_CAP = 180
V8_TARGETED_PER_FAMILY = 100
V8_REAL_REPEAT = 4
V8_TEACHER_COUNT = 130
V8_BENCHMARK_COUNT = 140
DETERMINISTIC_TEACHER_ROUTE = "deterministic_teacher_filter"
DETERMINISTIC_TEACHER_FILTER_VERSION = "v1"
MIN_DETERMINISTIC_TEACHER_SURVIVORS = 20

TEACHER_POOL_PATH = DATA_PROCESSED / "v8_teacher_pool.jsonl"
FROZEN_BENCHMARK_PATH = DATA_PROCESSED / "eval_v8_frozen.jsonl"
SYNTH_PATH = DATA_PROCESSED / "synth_train_v8.jsonl"
TRAIN_PATH = DATA_PROCESSED / "train_v8.jsonl"
MANIFEST_PATH = DATA_PROCESSED / "v8_manifest.json"
OPUS_REAL_PATH = DATA_PROCESSED / "real_train_seed_v8_opus.jsonl"
TEACHER_FILTER_REPORT_PATH = (
    DATA_PROCESSED / "v8_teacher_filter_report.json"
)
OPUS_PREFLIGHT_PATH = (
    DATA_PROCESSED.parent / "eval_out" / "opus_access_preflight_v8.json"
)
OPUS_NUMERIC_CALIBRATION_PATH = (
    DATA_PROCESSED.parent / "eval_out" / "opus_binding_calibration_v8.json"
)
OPUS_NONNUMERIC_CALIBRATION_PATH = (
    DATA_PROCESSED.parent
    / "eval_out"
    / "opus_nonnumeric_binding_calibration_v8.json"
)


def opus_access_preflight_ok(preflight: dict) -> bool:
    """True only for the exact successful owner-approved Opus probe."""
    return (
        preflight.get("model") == OPUS_MODEL_ID
        and preflight.get("stream_ok") is True
        and preflight.get("nonstream_ok") is True
        and preflight.get("repository_client_ok") is True
    )


def calibration_artifact_ok(artifact: dict, scope: str) -> bool:
    """Validate the exact-model calibration gate for one answer scope."""
    return (
        artifact.get("accepted") is True
        and artifact.get("model") == OPUS_MODEL_ID
        and str(artifact.get("scope", "")).casefold().startswith(
            f"{scope} "
        )
    )


def deterministic_teacher_records_ok(
    records: Sequence[dict],
    report: dict,
    *,
    minimum_survivors: int = MIN_DETERMINISTIC_TEACHER_SURVIVORS,
) -> bool:
    """Validate provenance for the judge-free deterministic teacher route."""
    if len(records) < minimum_survivors:
        return False
    if (
        report.get("route") != DETERMINISTIC_TEACHER_ROUTE
        or report.get("filter_version")
        != DETERMINISTIC_TEACHER_FILTER_VERSION
        or report.get("minimum_survivors") != minimum_survivors
        or report.get("survivors") != len(records)
        or report.get("ready") is not True
        or report.get("opus_judge_used") is not False
        or report.get("task_quality_proxy") != "not_available"
    ):
        return False
    registry_id = str(report.get("procedure_registry_id", "")).strip()
    if not registry_id:
        return False
    ids = set()
    users = set()
    for record in records:
        meta = record.get("meta", {})
        item_id = str(meta.get("id", "")).strip()
        user = str(record.get("user", "")).strip()
        procedure_ids = meta.get("procedure_ids")
        if (
            not item_id
            or item_id in ids
            or not user
            or user in users
            or meta.get("source") != "opus_distilled_real_question"
            or meta.get("teacher_model") != OPUS_MODEL_ID
            or meta.get("teacher_filter_route")
            != DETERMINISTIC_TEACHER_ROUTE
            or meta.get("teacher_filter_version")
            != DETERMINISTIC_TEACHER_FILTER_VERSION
            or meta.get("procedure_registry_id") != registry_id
            or not isinstance(procedure_ids, list)
            or len(procedure_ids) != 3
            or len(set(procedure_ids)) != 3
            or not all(str(value).strip() for value in procedure_ids)
            or meta.get("opus_judge_used") is not False
            or meta.get("task_quality_proxy") != "not_available"
        ):
            return False
        ids.add(item_id)
        users.add(user)
    return True


def _canonical_question(question: str) -> str:
    text = str(question or "").casefold().strip()
    for old, new in (
        ("\\(", ""),
        ("\\)", ""),
        ("\\[", ""),
        ("\\]", ""),
        ("\\times", "*"),
        ("\\div", "/"),
        ("×", "*"),
        ("÷", "/"),
        ("−", "-"),
    ):
        text = text.replace(old, new)
    # Formatting wrappers should not make duplicate questions look distinct.
    text = re.sub(r"\\(?:mathbf|textbf|mathrm)\{([^{}]*)\}", r"\1", text)
    return re.sub(r"[^a-z0-9+\-*/.^%=]", "", text)


def question_fingerprint(question: str) -> str:
    """Stable content fingerprint robust to whitespace and common LaTeX wrappers."""
    canonical = _canonical_question(question)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_partition_unused(
    rows: Sequence[dict],
    used_ids: Iterable[str],
    *,
    teacher_n: int,
    benchmark_n: int,
    seed: int,
) -> tuple[list[dict], list[dict]]:
    """Return deterministic disjoint teacher and benchmark partitions."""
    used = {str(value) for value in used_ids}
    unique: dict[str, dict] = {}
    seen_questions = set()
    ranked = sorted(
        (dict(row) for row in rows if str(row.get("id", "")) not in used),
        key=lambda row: hashlib.sha256(
            f"{seed}:{row.get('id', '')}:{question_fingerprint(row.get('question', ''))}".encode(
                "utf-8"
            )
        ).hexdigest(),
    )
    for row in ranked:
        row_id = str(row.get("id", ""))
        fingerprint = question_fingerprint(row.get("question", ""))
        if not row_id or not row.get("question") or fingerprint in seen_questions:
            continue
        seen_questions.add(fingerprint)
        unique[row_id] = row
    ordered = list(unique.values())
    needed = teacher_n + benchmark_n
    if len(ordered) < needed:
        raise ValueError(
            f"need {needed} unused unique questions, found {len(ordered)}"
        )
    teacher = ordered[:teacher_n]
    benchmark = ordered[teacher_n : teacher_n + benchmark_n]
    return teacher, benchmark


def _question_from_sft(record: dict) -> str:
    user = str(record.get("user", ""))
    if "Question: " not in user or "\nCorrect answer: " not in user:
        return ""
    return user.split("Question: ", 1)[1].rsplit("\nCorrect answer: ", 1)[0].strip()


def _correct_from_sft(record: dict) -> str:
    user = str(record.get("user", ""))
    if "\nCorrect answer: " not in user or "\nTopic: " not in user:
        return ""
    return (
        user.rsplit("\nCorrect answer: ", 1)[1]
        .rsplit("\nTopic: ", 1)[0]
        .strip()
    )


def assert_no_leakage(train_records: Sequence[dict], benchmark_records: Sequence[dict]) -> None:
    """Reject exact ID or normalized-question overlap."""
    train_ids = {
        str(record.get("meta", {}).get("id"))
        for record in train_records
        if record.get("meta", {}).get("id") not in (None, "")
    }
    benchmark_ids = {
        str(record.get("id"))
        for record in benchmark_records
        if record.get("id") not in (None, "")
    }
    id_overlap = sorted(train_ids & benchmark_ids)
    if id_overlap:
        raise ValueError(f"id leakage detected: {id_overlap[:5]}")

    train_questions = {
        question_fingerprint(_question_from_sft(record))
        for record in train_records
        if _question_from_sft(record)
    }
    benchmark_questions = {
        question_fingerprint(record.get("question", ""))
        for record in benchmark_records
        if record.get("question")
    }
    if train_questions & benchmark_questions:
        raise ValueError("question leakage detected after normalization")


def _parse_assistant(record: dict) -> list[dict]:
    try:
        value = json.loads(record["assistant"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("assistant must be valid JSON") from exc
    distractors = value.get("distractors")
    if not isinstance(distractors, list):
        raise ValueError("assistant.distractors must be a list")
    return distractors


def validate_training_records(records: Sequence[dict]) -> dict:
    """Fail closed unless every target passes all v8 structural/verifier gates."""
    verified_pairs = 0
    for index, record in enumerate(records):
        question = _question_from_sft(record)
        correct = normalize_answer(_correct_from_sft(record))
        distractors = _parse_assistant(record)
        if len(distractors) != 3:
            raise ValueError(f"record {index}: expected exactly 3 distractors")
        misconceptions = [
            str(item.get("misconception", "")).strip().casefold()
            for item in distractors
        ]
        answers = [
            normalize_answer(item.get("answer", ""))
            for item in distractors
        ]
        if not all(misconceptions) or len(set(misconceptions)) != 3:
            raise ValueError(f"record {index}: misconceptions must be non-empty/distinct")
        if not all(answers) or len(set(answers)) != 3:
            raise ValueError(f"record {index}: answers must be non-empty/distinct")
        if any(answer == correct for answer in answers):
            raise ValueError(f"record {index}: distractor collides with correct answer")
        for item in distractors:
            if "confidence" in item:
                raise ValueError(
                    f"record {index}: confidence is post-hoc, not an SFT target"
                )
            if computation_consistent(
                item.get("computation", ""),
                item.get("answer", ""),
                question,
                display_units=True,
            ) is not True:
                raise ValueError(
                    f"record {index}: computation failed hardened verification"
                )
            verified_pairs += 1
    total_pairs = 3 * len(records)
    return {
        "records": len(records),
        "verified_pairs": verified_pairs,
        "pairs": total_pairs,
        "pair_consistency": verified_pairs / total_pairs if total_pairs else 0.0,
    }


def _canonical_json(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def jsonl_sha256(rows: Sequence[dict]) -> str:
    payload = "".join(_canonical_json(row) + "\n" for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_jsonl(path: Path, rows: Sequence[dict]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(_canonical_json(row) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")
    return {
        "path": str(path.relative_to(DATA_PROCESSED.parent.parent)),
        "rows": len(rows),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_evidence(path: Path) -> dict:
    return {
        "path": str(path.relative_to(DATA_PROCESSED.parent.parent)),
        "sha256": _file_sha256(path),
    }


def verify_manifest(path: Path | str = MANIFEST_PATH) -> dict:
    """Verify every generated v8 artifact against its recorded row count/hash."""
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("opus_access_ready") is True:
        if not OPUS_PREFLIGHT_PATH.exists() or not opus_access_preflight_ok(
            json.loads(OPUS_PREFLIGHT_PATH.read_text(encoding="utf-8"))
        ):
            raise ValueError("manifest Opus access status lacks valid preflight evidence")
    root = DATA_PROCESSED.parent.parent
    for artifact in manifest.get("artifacts", {}).values():
        artifact_path = root / artifact["path"]
        if _file_sha256(artifact_path) != artifact["sha256"]:
            raise ValueError(f"artifact hash mismatch: {artifact['path']}")
        with artifact_path.open(encoding="utf-8") as handle:
            rows = sum(1 for line in handle if line.strip())
        if rows != artifact["rows"]:
            raise ValueError(f"artifact row-count mismatch: {artifact['path']}")
    for evidence in manifest.get("evidence", {}).values():
        evidence_path = root / evidence["path"]
        if _file_sha256(evidence_path) != evidence["sha256"]:
            raise ValueError(f"evidence hash mismatch: {evidence['path']}")
    artifacts = manifest.get("artifacts", {})
    if "train" in artifacts and "frozen_benchmark" in artifacts:
        train_records = load_jsonl(root / artifacts["train"]["path"])
        frozen_records = load_jsonl(
            root / artifacts["frozen_benchmark"]["path"]
        )
        validation = validate_training_records(train_records)
        if validation != manifest.get("validation"):
            raise ValueError("manifest training validation summary mismatch")
        assert_no_leakage(train_records, frozen_records)
        legacy_path = DATA_PROCESSED / "eval_heldout.jsonl"
        if legacy_path.exists():
            assert_no_leakage(train_records, load_jsonl(legacy_path))
    teacher_records = (
        load_jsonl(root / artifacts["opus_teacher"]["path"])
        if "opus_teacher" in artifacts
        else []
    )
    teacher_report = (
        json.loads(TEACHER_FILTER_REPORT_PATH.read_text(encoding="utf-8"))
        if TEACHER_FILTER_REPORT_PATH.exists()
        else {}
    )
    deterministic_ready = deterministic_teacher_records_ok(
        teacher_records,
        teacher_report,
    )
    if manifest.get("deterministic_teacher_filter_ready") is not deterministic_ready:
        raise ValueError("manifest deterministic teacher readiness mismatch")
    expected_training_ready = (
        manifest.get("opus_access_ready") is True
        and deterministic_ready
    )
    if manifest.get("training_ready") is not expected_training_ready:
        raise ValueError("manifest training readiness mismatch")
    return manifest


def _minimal_number_record(raw: dict) -> dict:
    correct_letter = str(raw.get("CorrectAnswer", "")).strip()
    correct_text = normalize_answer(
        str(raw.get(f"Answer{correct_letter}Text", "")).strip()
    )
    return {
        "id": str(raw.get("QuestionId", "")),
        "question": str(raw.get("QuestionText", "")).strip(),
        "correct": correct_text,
        "topic": str(raw.get("SubjectName", "")).strip(),
        "construct": str(raw.get("ConstructName", "")).strip(),
        "subject_id": str(raw.get("SubjectId", "")),
        "construct_id": str(raw.get("ConstructId", "")),
        "source": "eedi_2024_unused_number",
        "student_option_counts_available": False,
    }


def _legacy_used_ids(raw_rows: Sequence[dict]) -> set[str]:
    return {
        str(row.get("QuestionId", ""))
        for row in raw_rows
        if str(row.get("SubjectName", "")).strip() in NUMBER_SUBJECTS
        and labeled_all3(row)
    }


def _load_verified_real() -> list[dict]:
    paths = [
        DATA_PROCESSED / "real_train_seed_v5.jsonl",
        DATA_PROCESSED / "real_train_seed_v7.jsonl",
        OPUS_REAL_PATH,
    ]
    merged = {}
    for path in paths:
        if not path.exists():
            continue
        for record in load_jsonl(path):
            if three_distinct_ok(record):
                merged[record["user"]] = record
    return list(merged.values())


def _build_synthetic(per_family_cap: int) -> tuple[list[dict], dict]:
    examples, family_counts = generate_balanced(
        per_family_cap=per_family_cap,
        seed=V8_SYNTH_SEED,
        families=V5_FAMILIES,
        harden=True,
    )
    style_rng = random.Random(V8_STYLE_SEED)
    styled_count = 0
    for example in examples:
        plain = example["question"]
        styled = stylize(plain, style_rng)
        if styled != plain and all(
            computation_consistent(
                distractor.get("computation", ""),
                distractor.get("answer", ""),
                styled,
                display_units=True,
            )
            is True
            for distractor in example["distractors"]
        ):
            example["question"] = styled
            styled_count += 1
    records = []
    for example in examples:
        record = synth_to_sft(example, with_computation=True)
        record["meta"] = {
            **record.get("meta", {}),
            "dataset_version": "v8",
            "procedure_ids": [
                distractor["misconception_id"]
                for distractor in example["distractors"]
            ],
        }
        records.append(record)
    targeted = generate_targeted_sft(
        per_family=V8_TARGETED_PER_FAMILY,
        seed=V8_STYLE_SEED + 2,
    )
    seen_questions = {
        question_fingerprint(_question_from_sft(record))
        for record in records
    }
    targeted_unique = [
        record
        for record in targeted
        if question_fingerprint(_question_from_sft(record)) not in seen_questions
    ]
    records.extend(targeted_unique)
    return records, {
        "family_counts": family_counts,
        "styled_records": styled_count,
        "targeted_records": len(targeted_unique),
        "targeted_per_family": V8_TARGETED_PER_FAMILY,
        "targeted_family_counts": dict(
            sorted(
                Counter(
                    record.get("meta", {}).get("family", "unknown")
                    for record in targeted_unique
                ).items()
            )
        ),
    }


def build_v8(
    *,
    per_family_cap: int = V8_PER_FAMILY_CAP,
    require_opus_teacher: bool = False,
    require_deterministic_teacher: bool = False,
) -> dict:
    """Build all non-API v8 data artifacts and a deterministic manifest."""
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    raw_rows, _mapping = load_rows()
    legacy_used = _legacy_used_ids(raw_rows)
    unused = []
    for row in raw_rows:
        if str(row.get("SubjectName", "")).strip() not in NUMBER_SUBJECTS:
            continue
        if str(row.get("QuestionId", "")) in legacy_used:
            continue
        minimal = _minimal_number_record(row)
        if minimal["id"] and minimal["question"] and minimal["correct"]:
            unused.append(minimal)

    teacher, benchmark = stable_partition_unused(
        unused,
        legacy_used,
        teacher_n=V8_TEACHER_COUNT,
        benchmark_n=V8_BENCHMARK_COUNT,
        seed=V8_SEED,
    )

    synthetic, synth_report = _build_synthetic(per_family_cap)
    opus_teacher = (
        load_jsonl(OPUS_REAL_PATH)
        if OPUS_REAL_PATH.exists()
        else []
    )
    opus_preflight = (
        json.loads(OPUS_PREFLIGHT_PATH.read_text(encoding="utf-8"))
        if OPUS_PREFLIGHT_PATH.exists()
        else {}
    )
    opus_access_ready = opus_access_preflight_ok(opus_preflight)
    numeric_calibration = (
        json.loads(
            OPUS_NUMERIC_CALIBRATION_PATH.read_text(encoding="utf-8")
        )
        if OPUS_NUMERIC_CALIBRATION_PATH.exists()
        else {}
    )
    nonnumeric_calibration = (
        json.loads(
            OPUS_NONNUMERIC_CALIBRATION_PATH.read_text(encoding="utf-8")
        )
        if OPUS_NONNUMERIC_CALIBRATION_PATH.exists()
        else {}
    )
    numeric_calibration_ready = calibration_artifact_ok(
        numeric_calibration,
        "numeric",
    )
    nonnumeric_calibration_ready = calibration_artifact_ok(
        nonnumeric_calibration,
        "nonnumeric",
    )
    teacher_filter_report = (
        json.loads(
            TEACHER_FILTER_REPORT_PATH.read_text(encoding="utf-8")
        )
        if TEACHER_FILTER_REPORT_PATH.exists()
        else {}
    )
    deterministic_teacher_ready = deterministic_teacher_records_ok(
        opus_teacher,
        teacher_filter_report,
    )
    require_teacher = (
        require_opus_teacher or require_deterministic_teacher
    )
    if require_teacher and not opus_access_ready:
        raise ValueError(
            "successful exact-model Opus access preflight is required"
        )
    if require_teacher and not deterministic_teacher_ready:
        raise ValueError(
            "at least 20 deterministic-filter Opus teacher rows are required"
        )
    real = _load_verified_real()
    real_upsampled = real * V8_REAL_REPEAT
    combined = real_upsampled + synthetic
    random.Random(V8_SEED).shuffle(combined)

    # The legacy holdout remains frozen for continuity but is a development set;
    # both it and the new benchmark are forbidden from v8 training.
    legacy_dev = load_jsonl(DATA_PROCESSED / "eval_heldout.jsonl")
    assert_no_leakage(combined, legacy_dev)
    assert_no_leakage(combined, benchmark)
    validation = validate_training_records(combined)
    synthetic_family_counts = Counter(
        record.get("meta", {}).get("family", "unknown")
        for record in synthetic
    )
    source_counts = Counter(
        record.get("meta", {}).get("source", "unknown")
        for record in combined
    )

    artifacts = {
        "teacher_pool": _write_jsonl(TEACHER_POOL_PATH, teacher),
        "frozen_benchmark": _write_jsonl(FROZEN_BENCHMARK_PATH, benchmark),
        "synthetic_train": _write_jsonl(SYNTH_PATH, synthetic),
        "train": _write_jsonl(TRAIN_PATH, combined),
    }
    evidence = {}
    if opus_teacher:
        artifacts["opus_teacher"] = _write_jsonl(
            OPUS_REAL_PATH,
            opus_teacher,
        )
    if TEACHER_FILTER_REPORT_PATH.exists():
        evidence["teacher_filter_report"] = _file_evidence(
            TEACHER_FILTER_REPORT_PATH
        )
    opus_judge_ready = (
        numeric_calibration_ready
        and nonnumeric_calibration_ready
    )
    manifest = {
        "schema_version": "diagnostic-distractor-v8-data-v1",
        "partition_seed": V8_SEED,
        "synthetic_seed": V8_SYNTH_SEED,
        "style_seed": V8_STYLE_SEED,
        "legacy_fully_labeled_number_ids": len(legacy_used),
        "unused_number_candidates": len(unused),
        "student_option_counts_available": False,
        "confidence_is_training_target": False,
        "real_unique_verified_records": len(real),
        "opus_teacher_records": len(opus_teacher),
        "opus_access_ready": opus_access_ready,
        "opus_numeric_calibration_ready": numeric_calibration_ready,
        "opus_nonnumeric_calibration_ready": nonnumeric_calibration_ready,
        "opus_judge_ready": opus_judge_ready,
        "deterministic_teacher_filter_ready": (
            deterministic_teacher_ready
        ),
        "teacher_acceptance_route": (
            DETERMINISTIC_TEACHER_ROUTE
            if deterministic_teacher_ready
            else None
        ),
        "minimum_deterministic_teacher_survivors": (
            MIN_DETERMINISTIC_TEACHER_SURVIVORS
        ),
        "task_quality_proxy_ready": False,
        "binding_confidence_scope": "numeric_programmatic_only",
        "numeric_binding_confidence_ready": False,
        "nonnumeric_binding_confidence_ready": False,
        "training_ready": (
            opus_access_ready and deterministic_teacher_ready
        ),
        "real_repeat": V8_REAL_REPEAT,
        "synthetic": synth_report,
        "synthetic_family_balance": {
            "max": max(synthetic_family_counts.values()),
            "min": min(synthetic_family_counts.values()),
            "ratio": (
                max(synthetic_family_counts.values())
                / min(synthetic_family_counts.values())
            ),
        },
        "training_source_counts": dict(sorted(source_counts.items())),
        "teacher_pool_topic_counts": dict(
            sorted(Counter(row["topic"] for row in teacher).items())
        ),
        "frozen_benchmark_topic_counts": dict(
            sorted(Counter(row["topic"] for row in benchmark).items())
        ),
        "validation": validation,
        "artifacts": artifacts,
        "evidence": evidence,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_manifest(MANIFEST_PATH)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--per-family-cap",
        type=int,
        default=V8_PER_FAMILY_CAP,
    )
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--require-opus-teacher",
        action="store_true",
        help=(
            "legacy alias: fail unless deterministic-filter Opus teacher rows "
            "meet the survivor floor"
        ),
    )
    parser.add_argument(
        "--require-deterministic-teacher",
        action="store_true",
        help=(
            "fail unless deterministic-filter Opus teacher rows meet the "
            "predeclared survivor floor"
        ),
    )
    args = parser.parse_args()
    if args.verify_only:
        manifest = verify_manifest()
        print(
            f"verified {len(manifest['artifacts'])} v8 artifacts from "
            f"{MANIFEST_PATH.relative_to(DATA_PROCESSED.parent.parent)}"
        )
        return
    manifest = build_v8(
        per_family_cap=args.per_family_cap,
        require_opus_teacher=args.require_opus_teacher,
        require_deterministic_teacher=(
            args.require_deterministic_teacher
        ),
    )
    print(
        json.dumps(
            {
                "train_rows": manifest["artifacts"]["train"]["rows"],
                "synthetic_rows": manifest["artifacts"]["synthetic_train"]["rows"],
                "teacher_pool_rows": manifest["artifacts"]["teacher_pool"]["rows"],
                "frozen_benchmark_rows": manifest["artifacts"]["frozen_benchmark"]["rows"],
                "pair_consistency": manifest["validation"]["pair_consistency"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
