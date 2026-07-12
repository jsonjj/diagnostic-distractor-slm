"""Audit and explicitly apply owner-approved Glitch Rally cache migrations.

The default operation is a read-only audit.  It never treats a legacy approval
as approval of different Wayline content: a prompt that the current frozen
compiler cannot represent is rejected, and absent current model/bundle inputs
are reported rather than synthesized.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import string
import tempfile
from typing import Any, Iterable

from services.wayline_forge.app.curriculum import CURRICULUM_V1_SHA256
from services.wayline_forge.app.model_manifest import parse_model_manifest
from services.wayline_forge.app.procedure_registry import (
    PROCEDURE_REGISTRY_V1_SHA256,
)
from services.wayline_forge.app.providers.distractor import PinnedSlmManifest
from services.wayline_forge.app.question_kernel import QuestionCompiler
from services.wayline_forge.app.reviewed_cache import (
    ReviewReceipt,
    ReviewedCache,
    ReviewedCacheError,
)
from services.wayline_forge.app.safe_numeric import parse_exact_value
from services.wayline_forge.app.verified_question import (
    VerifiedQuestionBundle,
    VerifiedQuestionError,
)
from services.wayline_forge.scripts.legacy_review_audit import (
    LegacyReviewError,
    solve_legacy_question,
    verify_legacy_owner_approval,
)


REPORT_SCHEMA_VERSION = "wayline.legacy-migration-report.v1"
EXPECTED_REVIEWER_ALIAS = "owner-01"
EXPECTED_APPROVED_RECORDS = 6
_MAX_JSONL_BYTES = 8 * 1024 * 1024
_MAX_JSONL_LINE_BYTES = 2 * 1024 * 1024
_MAX_JSON_OBJECT_BYTES = 2 * 1024 * 1024
_INTEGER_PATTERN = r"-?(?:0|[1-9][0-9]*)"
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_QUESTION_ID = re.compile(r"GR-NUM-[0-9]{3}", re.ASCII)


@dataclass(frozen=True, slots=True)
class _BundleInput:
    legacy_question_id: str
    bundle_file_sha256: str
    raw: bytes


@dataclass(frozen=True, slots=True)
class _ApprovalArtifact:
    approval_artifact_sha256: str
    reviewed_at_utc: str


@dataclass(frozen=True, slots=True)
class _AuditOutcome:
    report: dict[str, Any]
    bundles: dict[str, VerifiedQuestionBundle]
    compiler: QuestionCompiler
    manifest: PinnedSlmManifest | None


class MigrationError(RuntimeError):
    """Fail-closed migration error with a stable, non-sensitive code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_nonstandard_number(_value: str) -> None:
    raise ValueError("nonstandard JSON number")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_sha256(value: Any) -> str:
    """Return the canonical UTF-8 JSON receipt used by reports/artifacts."""

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_jsonl(path: Path, *, source: str) -> tuple[list[dict[str, Any]], str]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise MigrationError(f"{source}_unreadable") from None
    if not raw or len(raw) > _MAX_JSONL_BYTES:
        raise MigrationError(f"{source}_size_invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise MigrationError(f"{source}_encoding_invalid") from None
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or len(line.encode("utf-8")) > _MAX_JSONL_LINE_BYTES:
            raise MigrationError(f"{source}_row_invalid")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_nonstandard_number,
            )
        except _DuplicateJsonKey:
            raise MigrationError("duplicate_json_key") from None
        except (ValueError, json.JSONDecodeError, RecursionError):
            raise MigrationError(f"{source}_row_invalid") from None
        if not isinstance(value, dict):
            raise MigrationError(f"{source}_row_invalid")
        records.append(value)
    if not records:
        raise MigrationError(f"{source}_empty")
    return records, _file_sha256(raw)


def _decode_json_object(
    raw: bytes,
    *,
    source: str,
    maximum_bytes: int = _MAX_JSON_OBJECT_BYTES,
) -> dict[str, Any]:
    if not raw or len(raw) > maximum_bytes:
        raise MigrationError(f"{source}_size_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonstandard_number,
        )
    except _DuplicateJsonKey:
        raise MigrationError("duplicate_json_key") from None
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise MigrationError(f"{source}_invalid") from None
    if not isinstance(value, dict):
        raise MigrationError(f"{source}_invalid")
    return value


def _read_json_object(path: Path, *, source: str) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise MigrationError(f"{source}_unreadable") from None
    return _decode_json_object(raw, source=source), _file_sha256(raw)


def _safe_relative_path(value: object) -> Path:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > 512
        or "\\" in value
    ):
        raise MigrationError("bundle_path_invalid")
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise MigrationError("bundle_path_invalid")
    return candidate


def _load_bundle_inputs(path: Path) -> tuple[dict[str, _BundleInput], str]:
    value, index_sha256 = _read_json_object(path, source="bundle_index")
    if set(value) != {"items", "schemaVersion"} or value.get(
        "schemaVersion"
    ) != "wayline.legacy-bundle-index.v1":
        raise MigrationError("bundle_index_schema_invalid")
    items = value.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 256:
        raise MigrationError("bundle_index_items_invalid")
    index_root = path.parent.resolve()
    loaded: dict[str, _BundleInput] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "bundleFileSha256",
            "bundlePath",
            "legacyQuestionId",
        }:
            raise MigrationError("bundle_index_item_invalid")
        question_id = item.get("legacyQuestionId")
        claimed_sha256 = item.get("bundleFileSha256")
        if not isinstance(question_id, str) or not _QUESTION_ID.fullmatch(question_id):
            raise MigrationError("bundle_index_question_id_invalid")
        if question_id in loaded:
            raise MigrationError("bundle_index_duplicate_question_id")
        if not isinstance(claimed_sha256, str) or not _SHA256.fullmatch(
            claimed_sha256
        ):
            raise MigrationError("bundle_file_receipt_invalid")
        relative = _safe_relative_path(item.get("bundlePath"))
        resolved = (index_root / relative).resolve()
        try:
            resolved.relative_to(index_root)
        except ValueError:
            raise MigrationError("bundle_path_escape") from None
        try:
            raw = resolved.read_bytes()
        except OSError:
            raise MigrationError("bundle_file_unreadable") from None
        if not raw or len(raw) > 512 * 1024:
            raise MigrationError("bundle_file_size_invalid")
        if _file_sha256(raw) != claimed_sha256:
            raise MigrationError("bundle_file_receipt_mismatch")
        loaded[question_id] = _BundleInput(
            legacy_question_id=question_id,
            bundle_file_sha256=claimed_sha256,
            raw=raw,
        )
    return loaded, index_sha256


def _unique_by(
    records: Iterable[dict[str, Any]],
    *,
    field: str,
    source: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        value = record.get(field)
        if not isinstance(value, str) or not value:
            raise MigrationError(f"{source}_identifier_invalid")
        if value in result:
            raise MigrationError(f"{source}_duplicate_identifier")
        result[value] = record
    return result


def _template_pattern(template: str) -> str:
    pattern: list[str] = []
    seen: set[str] = set()
    try:
        parsed = string.Formatter().parse(template)
        for literal, field_name, format_spec, conversion in parsed:
            pattern.append(re.escape(literal))
            if field_name is None:
                continue
            if (
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field_name)
                or format_spec
                or conversion
            ):
                raise ValueError("unsupported template field")
            if field_name in seen:
                pattern.append(f"(?P={field_name})")
            else:
                seen.add(field_name)
                pattern.append(f"(?P<{field_name}>{_INTEGER_PATTERN})")
    except ValueError:
        raise MigrationError("current_curriculum_template_invalid") from None
    return "".join(pattern)


def _current_template_matches(
    compiler: QuestionCompiler,
    prompt: str,
) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for family in compiler.curriculum.families.values():
        for template in family.templates:
            if re.fullmatch(_template_pattern(template.prompt_template), prompt):
                matches.append({
                    "familyId": family.family_id,
                    "skillId": family.skill_id,
                    "templateId": template.template_id,
                    "worldId": family.world_id,
                })
    return sorted(
        matches,
        key=lambda item: (
            item["worldId"],
            item["skillId"],
            item["familyId"],
            item["templateId"],
        ),
    )


def _load_manifest(
    path: Path | None,
    *,
    compiler: QuestionCompiler,
    injected: PinnedSlmManifest | None,
) -> tuple[PinnedSlmManifest | None, str | None, str | None]:
    if injected is not None:
        if injected.registry_id != compiler.registry.registry_id:
            return None, None, "model_manifest_registry_mismatch"
        receipt = canonical_sha256(asdict(injected))
        return injected, receipt, None
    if path is None or not path.is_file():
        return None, None, "model_manifest_missing"
    try:
        raw = path.read_bytes()
        parsed = parse_model_manifest(raw)
        manifest = PinnedSlmManifest.from_model_manifest(
            parsed,
            registry_id=compiler.registry.registry_id,
            max_response_bytes=16_384,
            max_tokens=768,
        )
    except Exception:
        return None, None, "model_manifest_invalid"
    return manifest, _file_sha256(raw), None


def _holdout_payload(receipt: Any) -> dict[str, Any]:
    return {
        "boundaryVersion": receipt.boundary_version,
        "canonicalSha256": receipt.canonical_sha256,
        "excluded": receipt.excluded,
        "maximumSimilarityBits": receipt.maximum_similarity_bits,
        "questionFingerprint": receipt.question_fingerprint,
        "recordCount": receipt.record_count,
        "similarityThresholdBits": receipt.similarity_threshold_bits,
        "sourceSha256": receipt.source_sha256,
    }


def _bundle_compatibility_reasons(
    bundle: VerifiedQuestionBundle,
    *,
    review_payload: dict[str, Any],
    trusted_question: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    blueprint = bundle.blueprint
    if blueprint.prompt != trusted_question.get("question"):
        reasons.append("current_bundle_prompt_mismatch")
    if blueprint.canonical_answer.display != trusted_question.get("correct"):
        reasons.append("current_bundle_answer_mismatch")
    if blueprint.topic != trusted_question.get("topic"):
        reasons.append("current_bundle_topic_mismatch")
    if tuple(blueprint.trusted_steps) != tuple(
        trusted_question.get("trusted_steps", ())
    ):
        reasons.append("current_bundle_trusted_steps_mismatch")

    option_display = {
        option.option_id: option.display_text for option in bundle.options
    }
    current_distractors = sorted(
        (
            option_display[route.option_id],
            route.canonical_label,
            route.computation,
        )
        for route in bundle.verified_distractors
    )
    raw_legacy = review_payload.get("distractors")
    if not isinstance(raw_legacy, list) or len(raw_legacy) != 3:
        reasons.append("legacy_distractor_contract_invalid")
    else:
        try:
            legacy_distractors = sorted(
                (
                    item["answer"],
                    item["misconception"],
                    item["computation"],
                )
                for item in raw_legacy
                if isinstance(item, dict)
            )
        except (KeyError, TypeError):
            legacy_distractors = []
        if current_distractors != legacy_distractors:
            reasons.append("current_bundle_distractors_mismatch")
    return reasons


def _is_exact_utc(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            value,
            re.ASCII,
        )
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _load_approval_artifact(
    path: Path,
    *,
    report: dict[str, Any],
) -> _ApprovalArtifact:
    value, _file_receipt = _read_json_object(path, source="approval_artifact")
    expected_fields = {
        "approvalArtifactSha256",
        "approvedItems",
        "auditSha256",
        "reviewedAtUtc",
        "reviewerAlias",
        "schemaVersion",
    }
    if set(value) != expected_fields or value.get(
        "schemaVersion"
    ) != "wayline.legacy-migration-approval.v1":
        raise MigrationError("approval_artifact_schema_invalid")
    claimed = value.get("approvalArtifactSha256")
    if not isinstance(claimed, str) or not _SHA256.fullmatch(claimed):
        raise MigrationError("approval_artifact_hash_invalid")
    unsigned = dict(value)
    unsigned.pop("approvalArtifactSha256")
    if canonical_sha256(unsigned) != claimed:
        raise MigrationError("approval_artifact_hash_mismatch")
    if value.get("reviewerAlias") != EXPECTED_REVIEWER_ALIAS:
        raise MigrationError("approval_reviewer_mismatch")
    reviewed_at = value.get("reviewedAtUtc")
    if not _is_exact_utc(reviewed_at):
        raise MigrationError("approval_timestamp_invalid")
    if value.get("auditSha256") != report.get("auditSha256"):
        raise MigrationError("approval_audit_mismatch")

    approved_items = value.get("approvedItems")
    if not isinstance(approved_items, list):
        raise MigrationError("approval_items_invalid")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in approved_items:
        if not isinstance(item, dict) or set(item) != {
            "legacyQuestionId",
            "legacyReviewHash",
            "semanticContentSha256",
        }:
            raise MigrationError("approval_item_invalid")
        question_id = item.get("legacyQuestionId")
        review_hash = item.get("legacyReviewHash")
        semantic_hash = item.get("semanticContentSha256")
        if (
            not isinstance(question_id, str)
            or not _QUESTION_ID.fullmatch(question_id)
            or question_id in seen
            or not isinstance(review_hash, str)
            or not re.fullmatch(r"review:v1:[0-9a-f]{64}", review_hash, re.ASCII)
            or not isinstance(semantic_hash, str)
            or not _SHA256.fullmatch(semantic_hash)
        ):
            raise MigrationError("approval_item_invalid")
        seen.add(question_id)
        normalized.append({
            "legacyQuestionId": question_id,
            "legacyReviewHash": review_hash,
            "semanticContentSha256": semantic_hash,
        })
    if normalized != sorted(normalized, key=lambda item: item["legacyQuestionId"]):
        raise MigrationError("approval_items_not_canonical")
    expected = [
        {
            "legacyQuestionId": item["legacyQuestionId"],
            "legacyReviewHash": item["legacyReviewHash"],
            "semanticContentSha256": item["checks"][
                "currentBundleSemanticContentSha256"
            ],
        }
        for item in report["items"]
        if item["status"] == "accepted"
    ]
    if normalized != expected:
        raise MigrationError("approval_items_mismatch")
    return _ApprovalArtifact(
        approval_artifact_sha256=claimed,
        reviewed_at_utc=reviewed_at,
    )


def _apply_bundles_atomically(
    *,
    cache_path: Path,
    bundles: dict[str, VerifiedQuestionBundle],
    compiler: QuestionCompiler,
    manifest: PinnedSlmManifest,
    approval: _ApprovalArtifact,
) -> None:
    if cache_path.is_symlink():
        raise MigrationError("cache_path_symlink_forbidden")
    parent = cache_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise MigrationError("cache_parent_unwritable") from None
    if not parent.is_dir():
        raise MigrationError("cache_parent_unwritable")
    for suffix in ("-journal", "-shm", "-wal"):
        if Path(str(cache_path) + suffix).exists():
            raise MigrationError("cache_has_live_sidecar")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{cache_path.name}.migration-",
        suffix=".tmp",
        dir=parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if cache_path.exists():
            if not cache_path.is_file():
                raise MigrationError("cache_path_invalid")
            shutil.copy2(cache_path, temporary)
        with ReviewedCache.open_build(
            temporary,
            compiler=compiler,
            manifest=manifest,
        ) as cache:
            for question_id in sorted(bundles):
                bundle = bundles[question_id]
                cache.insert(
                    bundle,
                    ReviewReceipt.approved(
                        owner_alias=EXPECTED_REVIEWER_ALIAS,
                        reviewed_at_utc=approval.reviewed_at_utc,
                        approved_semantic_content_sha256=(
                            bundle.semantic_content_sha256
                        ),
                        approval_record_sha256=(
                            approval.approval_artifact_sha256
                        ),
                    ),
                )
        with ReviewedCache.open_learner(
            temporary,
            compiler=compiler,
            manifest=manifest,
        ):
            pass
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, cache_path)
    except MigrationError:
        raise
    except (OSError, ReviewedCacheError, TypeError, ValueError):
        raise MigrationError("cache_apply_failed") from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _audit_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "globalReasons": report["globalReasons"],
        "items": report["items"],
        "reviewerAlias": report["reviewerAlias"],
        "runtimeReceipts": report["runtimeReceipts"],
        "schemaVersion": "wayline.legacy-migration-audit.v1",
        "sourceReceipts": report["sourceReceipts"],
    }


def _seal_report(report: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(report)
    sealed["auditSha256"] = canonical_sha256(_audit_payload(sealed))
    sealed["reportSha256"] = canonical_sha256(sealed)
    return sealed


def _audit_legacy_approved_outcome(
    *,
    decisions_path: str | Path,
    approved_pack_path: str | Path,
    questions_path: str | Path,
    model_manifest_path: str | Path | None,
    bundle_index_path: str | Path | None,
    compiler: QuestionCompiler | None = None,
    manifest: PinnedSlmManifest | None = None,
) -> _AuditOutcome:
    """Return a deterministic, read-only compatibility report.

    A record is accepted only after a current private bundle has been mapped and
    replay-validated.  The initial repository intentionally has no such mapping,
    so this audit identifies the exact missing inputs and compiler conflicts.
    """

    decisions_path = Path(decisions_path)
    approved_pack_path = Path(approved_pack_path)
    questions_path = Path(questions_path)
    model_path = None if model_manifest_path is None else Path(model_manifest_path)
    bundle_path = None if bundle_index_path is None else Path(bundle_index_path)

    decisions, decisions_sha = _read_jsonl(
        decisions_path, source="review_decisions"
    )
    approved_pack, approved_pack_sha = _read_jsonl(
        approved_pack_path, source="approved_pack"
    )
    questions, questions_sha = _read_jsonl(questions_path, source="questions")

    try:
        authoritative_compiler = QuestionCompiler.for_tests()
    except Exception:
        raise MigrationError("current_runtime_receipt_invalid") from None
    compiler = compiler or authoritative_compiler
    if (
        type(compiler) is not QuestionCompiler
        or compiler.curriculum != authoritative_compiler.curriculum
        or compiler.registry.registry_id
        != authoritative_compiler.registry.registry_id
        or compiler.registry.entries != authoritative_compiler.registry.entries
    ):
        raise MigrationError("current_runtime_receipt_invalid")

    questions_by_id = _unique_by(
        questions, field="id", source="questions"
    )
    pack_by_id = _unique_by(
        approved_pack, field="question_id", source="approved_pack"
    )

    owner_approved: list[dict[str, Any]] = []
    seen_approved_ids: set[str] = set()
    for record in decisions:
        decision = record.get("decision")
        if not isinstance(decision, dict) or decision.get("decision") != "approved":
            continue
        payload = record.get("review_payload")
        question = payload.get("question") if isinstance(payload, dict) else None
        question_id = question.get("id") if isinstance(question, dict) else None
        if not isinstance(question_id, str) or not question_id:
            raise MigrationError("approved_decision_identifier_invalid")
        if question_id in seen_approved_ids:
            raise MigrationError("approved_decision_duplicate_identifier")
        seen_approved_ids.add(question_id)
        owner_approved.append(record)
    if len(owner_approved) != EXPECTED_APPROVED_RECORDS:
        raise MigrationError("approved_record_count_mismatch")

    runtime_manifest, manifest_sha, manifest_reason = _load_manifest(
        model_path,
        compiler=compiler,
        injected=manifest,
    )

    global_reasons: list[str] = []
    if manifest_reason is not None:
        global_reasons.append(manifest_reason)
    bundle_inputs: dict[str, _BundleInput] = {}
    bundle_index_sha256: str | None = None
    if bundle_path is None or not bundle_path.is_file():
        global_reasons.append("bundle_index_missing")
    else:
        bundle_inputs, bundle_index_sha256 = _load_bundle_inputs(bundle_path)
        if set(bundle_inputs) != seen_approved_ids:
            raise MigrationError("bundle_index_question_set_mismatch")

    reverified_bundles: dict[str, VerifiedQuestionBundle] = {}
    bundle_reasons: dict[str, str] = {}
    semantic_owners: dict[str, list[str]] = {}
    for question_id, bundle_input in bundle_inputs.items():
        if runtime_manifest is None:
            bundle_reasons[question_id] = "current_bundle_reverification_unavailable"
            continue
        try:
            bundle = VerifiedQuestionBundle.from_private_json(
                bundle_input.raw,
                compiler=compiler,
                manifest=runtime_manifest,
            )
        except (VerifiedQuestionError, TypeError, ValueError):
            bundle_reasons[question_id] = "current_bundle_reverification_failed"
            continue
        reverified_bundles[question_id] = bundle
        semantic_owners.setdefault(bundle.semantic_content_sha256, []).append(
            question_id
        )
    for owners in semantic_owners.values():
        if len(owners) > 1:
            for question_id in owners:
                bundle_reasons[question_id] = "duplicate_semantic_content"

    items: list[dict[str, Any]] = []
    for record in sorted(
        owner_approved,
        key=lambda item: item["review_payload"]["question"]["id"],
    ):
        payload = record["review_payload"]
        decision = record["decision"]
        legacy_question = payload["question"]
        question_id = legacy_question["id"]
        reasons: list[str] = []
        checks: dict[str, Any] = {
            "currentBundleCacheContentSha256": None,
            "currentBundleFileSha256": None,
            "currentBundleReverified": False,
            "currentBundleSemanticContentSha256": None,
            "currentProcedureIds": [],
            "currentTemplateMatches": [],
            "holdoutExcluded": None,
            "holdoutReceipt": None,
            "legacyApprovalVerified": False,
            "trustedAnswerRecomputed": False,
        }

        trusted_question = questions_by_id.get(question_id)
        pack_record = pack_by_id.get(question_id)
        if trusted_question is None:
            reasons.append("trusted_question_missing")
        if pack_record is None:
            reasons.append("approved_pack_record_missing")

        if trusted_question is not None:
            try:
                solved = solve_legacy_question(trusted_question)
                correct = parse_exact_value(
                    trusted_question.get("correct"),
                    allow_percent=str(trusted_question.get("correct", "")).endswith("%"),
                ).value
                checks["trustedAnswerRecomputed"] = solved == correct
            except Exception:
                checks["trustedAnswerRecomputed"] = False
            if not checks["trustedAnswerRecomputed"]:
                reasons.append("trusted_answer_recomputation_failed")

            prompt = trusted_question.get("question")
            if isinstance(prompt, str):
                receipt = compiler.curriculum.holdout.receipt_for(prompt)
                checks["holdoutExcluded"] = receipt.excluded
                checks["holdoutReceipt"] = _holdout_payload(receipt)
                if receipt.excluded:
                    reasons.append("current_holdout_excluded")
                matches = _current_template_matches(compiler, prompt)
                checks["currentTemplateMatches"] = matches
                if not matches:
                    reasons.append("current_compiler_prompt_unrepresentable")
            else:
                reasons.append("trusted_question_prompt_invalid")

        if trusted_question is not None and pack_record is not None:
            try:
                verify_legacy_owner_approval(
                    queue_record=record,
                    approved_record=pack_record,
                    trusted_question=trusted_question,
                    expected_reviewer=EXPECTED_REVIEWER_ALIAS,
                )
                checks["legacyApprovalVerified"] = True
            except (LegacyReviewError, KeyError, TypeError, ValueError):
                reasons.append("legacy_approval_verification_failed")

        if decision.get("reviewer") != EXPECTED_REVIEWER_ALIAS:
            reasons.append("reviewer_alias_mismatch")
        if manifest_reason is not None:
            reasons.append(manifest_reason)

        bundle_input = bundle_inputs.get(question_id)
        if bundle_input is None:
            reasons.append("wayline_bundle_mapping_missing")
        else:
            checks["currentBundleFileSha256"] = (
                bundle_input.bundle_file_sha256
            )
            bundle_reason = bundle_reasons.get(question_id)
            if bundle_reason is not None:
                reasons.append(bundle_reason)
            bundle = reverified_bundles.get(question_id)
            if bundle is not None and bundle_reason is None:
                checks["currentBundleReverified"] = True
                checks["currentBundleCacheContentSha256"] = (
                    bundle.cache_content_sha256
                )
                checks["currentBundleSemanticContentSha256"] = (
                    bundle.semantic_content_sha256
                )
                checks["currentProcedureIds"] = sorted(
                    route.procedure_id
                    for route in bundle.verified_distractors
                )
                if trusted_question is None:
                    reasons.append("trusted_question_missing")
                else:
                    reasons.extend(_bundle_compatibility_reasons(
                        bundle,
                        review_payload=payload,
                        trusted_question=trusted_question,
                    ))

        status = "accepted" if not reasons else "rejected"
        items.append({
            "candidateId": decision.get("candidate_id"),
            "checks": checks,
            "legacyQuestionId": question_id,
            "legacyReviewHash": (
                None if pack_record is None else pack_record.get("review_hash")
            ),
            "reasons": sorted(set(reasons)),
            "status": status,
        })

    accepted = sum(item["status"] == "accepted" for item in items)
    report: dict[str, Any] = {
        "cacheMutation": {"performed": False, "requested": False},
        "globalReasons": sorted(set(global_reasons)),
        "items": items,
        "mode": "dry-run",
        "reviewerAlias": EXPECTED_REVIEWER_ALIAS,
        "runtimeReceipts": {
            "bundleIndexSha256": bundle_index_sha256,
            "curriculumId": compiler.curriculum.curriculum_id,
            "curriculumSha256": CURRICULUM_V1_SHA256,
            "modelManifestSha256": manifest_sha,
            "registryId": compiler.registry.registry_id,
            "registrySha256": PROCEDURE_REGISTRY_V1_SHA256,
        },
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "sourceReceipts": {
            "approvedPackSha256": approved_pack_sha,
            "approvedPackRows": len(approved_pack),
            "questionsSha256": questions_sha,
            "questionsRows": len(questions),
            "reviewDecisionsSha256": decisions_sha,
            "reviewDecisionsRows": len(decisions),
        },
        "summary": {
            "accepted": accepted,
            "approvedLegacyRecords": len(owner_approved),
            "rejected": len(items) - accepted,
        },
    }
    sealed = _seal_report(report)
    compatible = {
        item["legacyQuestionId"]: reverified_bundles[item["legacyQuestionId"]]
        for item in items
        if item["status"] == "accepted"
    }
    return _AuditOutcome(
        report=sealed,
        bundles=compatible,
        compiler=compiler,
        manifest=runtime_manifest,
    )


def audit_legacy_approved(
    *,
    decisions_path: str | Path,
    approved_pack_path: str | Path,
    questions_path: str | Path,
    model_manifest_path: str | Path | None,
    bundle_index_path: str | Path | None,
    compiler: QuestionCompiler | None = None,
    manifest: PinnedSlmManifest | None = None,
) -> dict[str, Any]:
    """Return the deterministic report from a read-only compatibility audit."""

    return _audit_legacy_approved_outcome(
        decisions_path=decisions_path,
        approved_pack_path=approved_pack_path,
        questions_path=questions_path,
        model_manifest_path=model_manifest_path,
        bundle_index_path=bundle_index_path,
        compiler=compiler,
        manifest=manifest,
    ).report


def execute_legacy_migration(
    *,
    decisions_path: str | Path,
    approved_pack_path: str | Path,
    questions_path: str | Path,
    model_manifest_path: str | Path | None,
    bundle_index_path: str | Path | None,
    apply: bool = False,
    cache_path: str | Path | None = None,
    approval_artifact_path: str | Path | None = None,
    compiler: QuestionCompiler | None = None,
    manifest: PinnedSlmManifest | None = None,
) -> dict[str, Any]:
    """Audit by default; require explicit dual authorization before any write."""

    if not apply:
        return audit_legacy_approved(
            decisions_path=decisions_path,
            approved_pack_path=approved_pack_path,
            questions_path=questions_path,
            model_manifest_path=model_manifest_path,
            bundle_index_path=bundle_index_path,
            compiler=compiler,
            manifest=manifest,
        )
    if approval_artifact_path is None:
        raise MigrationError("approval_artifact_required")
    if cache_path is None:
        raise MigrationError("cache_path_required")
    outcome = _audit_legacy_approved_outcome(
        decisions_path=decisions_path,
        approved_pack_path=approved_pack_path,
        questions_path=questions_path,
        model_manifest_path=model_manifest_path,
        bundle_index_path=bundle_index_path,
        compiler=compiler,
        manifest=manifest,
    )
    report = outcome.report
    if report["summary"]["accepted"] == 0:
        raise MigrationError("no_compatible_records")
    if outcome.manifest is None:
        raise MigrationError("model_manifest_required")
    approval = _load_approval_artifact(
        Path(approval_artifact_path),
        report=report,
    )
    _apply_bundles_atomically(
        cache_path=Path(cache_path),
        bundles=outcome.bundles,
        compiler=outcome.compiler,
        manifest=outcome.manifest,
        approval=approval,
    )
    applied = dict(report)
    applied.pop("reportSha256")
    applied["mode"] = "apply"
    applied["cacheMutation"] = {"performed": True, "requested": True}
    applied["applyReceipt"] = {
        "approvalArtifactSha256": approval.approval_artifact_sha256,
        "insertedRecords": len(outcome.bundles),
    }
    applied["reportSha256"] = canonical_sha256(applied)
    return applied


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    root = _repo_root()
    parser = argparse.ArgumentParser(
        description="Audit owner-approved Glitch Rally records for Wayline cache migration."
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=root / "data/game/work/review_decisions_owner_v1.jsonl",
    )
    parser.add_argument(
        "--approved-pack",
        type=Path,
        default=root / "data/game/work/reviewed_v1.jsonl",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=root / "data/game/work/questions_prepared_v1.jsonl",
    )
    parser.add_argument(
        "--model-manifest",
        type=Path,
        default=root / "services/wayline_forge/resources/model_manifest_v1.json",
    )
    parser.add_argument(
        "--bundle-index",
        type=Path,
        default=root / "data/wayline/runtime/legacy_bundle_index_v1.json",
    )
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--approval-artifact", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = execute_legacy_migration(
            decisions_path=args.decisions,
            approved_pack_path=args.approved_pack,
            questions_path=args.questions,
            model_manifest_path=args.model_manifest,
            bundle_index_path=args.bundle_index,
            apply=args.apply,
            cache_path=args.cache,
            approval_artifact_path=args.approval_artifact,
        )
    except MigrationError as exc:
        raise SystemExit(exc.code) from None
    payload = _canonical_json(report) + "\n"
    if args.report is not None:
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
