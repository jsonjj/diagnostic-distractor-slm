"""Private, fail-closed assembly of verified quiz-batch material.

Planner blueprints are candidates, not delivered truth.  This seam binds each
actual live/cache :class:`VerifiedQuestionBundle` to its planned slot, rekeys it
for one batch, and commits the public layout and server-only reveal material.
Raw SLM output never enters the resulting object or its private serializer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Any
import unicodedata

from .adaptive_planner import QUIZ_LENGTH_BY_TIER
from .contracts import PublicOption, PublicQuizBatch, PublicQuizItem
from .events import ObservationEvent, ProvenanceReceipts
from .providers.distractor import PinnedSlmManifest
from .question_kernel import CompileRequest, QuestionBlueprint, QuestionCompiler
from .quiz_machine import SealedQuiz, SealedQuizItem
from .reviewed_cache import CacheKey, ReviewReceipt, ReviewedCacheHit
from .slot_materializer import (
    SUPPORTED_SLOT_KINDS,
    MaterializedSlot,
    question_semantic_sha256,
)
from .verified_question import (
    PlacedVerifiedQuestion,
    VerifiedQuestionBundle,
    VerifiedQuestionError,
    mint_item_instance_id,
)


BATCH_MATERIAL_SCHEMA_VERSION = "wayline.batch-material.v4"
_BATCH_PLAN_SCHEMA_VERSION = "wayline.batch-plan-contract.v1"
_PLANNED_SLOT_SCHEMA_VERSION = "wayline.planned-slot-contract.v1"
_PLANNED_BLUEPRINT_RECEIPT_SCHEMA_VERSION = (
    "wayline.planned-blueprint-receipt.v1"
)
_PUBLIC_SCHEMA_VERSION = "wayline.v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_MAX_PRIVATE_BYTES = 4 * 1024 * 1024
_SIGNED_63_LIMIT = 2**63
_TRANSFER_SLOT_KINDS = frozenset(
    {
        "active_misconception_probe",
        "misconception_discrimination",
        "fragile_skill_transfer",
        "spaced_prior_world_transfer",
    }
)


class BatchMaterialError(ValueError):
    """Stable, non-sensitive failure from batch assembly."""

    retryable = False

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RetryableBatchMaterialRejection(BatchMaterialError):
    """The current actual source is unsuitable; the slot may be retried."""

    retryable = True


class BatchMaterialValidationError(BatchMaterialError):
    """Persisted or downstream material failed a trust-boundary check."""


class _DuplicateJsonKey(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number: {value}")


def _canonical_json(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise BatchMaterialValidationError("invalid_private_payload") from exc
    return encoded


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_text(value: object, *, maximum: int = 256) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= maximum
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _require_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} is not a valid identifier")
    return value


def _tier_value(value: object) -> str:
    normalized = getattr(value, "value", value)
    if not isinstance(normalized, str) or normalized not in QUIZ_LENGTH_BY_TIER:
        raise ValueError("battle_tier is not supported")
    return normalized


def _ordered_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            if value not in seen:
                seen.add(value)
                result.append(value)
    return tuple(result)


def _without(values: tuple[str, ...], removed: set[str]) -> tuple[str, ...]:
    return tuple(value for value in values if value not in removed)


@dataclass(frozen=True, slots=True)
class BatchContext:
    """Server-owned identity and curriculum context for one quiz batch."""

    profile_id: str
    session_id: str
    world_id: str
    battle_id: str
    core_subskill_ids: tuple[str, ...]
    content_version_id: str
    battle_tier: str

    def __post_init__(self) -> None:
        for name in (
            "profile_id",
            "session_id",
            "world_id",
            "battle_id",
            "content_version_id",
        ):
            _require_identifier(name, getattr(self, name))
        if (
            not isinstance(self.core_subskill_ids, tuple)
            or not self.core_subskill_ids
            or len(set(self.core_subskill_ids)) != len(self.core_subskill_ids)
        ):
            raise ValueError("core_subskill_ids must be a non-empty unique tuple")
        for value in self.core_subskill_ids:
            _require_identifier("core_subskill_id", value)
        object.__setattr__(self, "battle_tier", _tier_value(self.battle_tier))

    @property
    def authoritative_core_subskill_ids(self) -> tuple[str, ...]:
        return self.core_subskill_ids


@dataclass(frozen=True, slots=True)
class SelectionExclusions:
    """Exclusions for selecting the next *actual* live/cache bundle."""

    item_ids: tuple[str, ...]
    question_ids: tuple[str, ...]
    question_semantic_sha256s: tuple[str, ...]
    adjacent_template_ids: tuple[str, ...]
    adjacent_operand_signatures: tuple[str, ...]
    content_ids: tuple[str, ...]
    context_ids: tuple[str, ...]


def _require_unique_text_tuple(
    name: str,
    value: object,
    *,
    maximum: int = 256,
    sha256: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an immutable tuple")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must be unique")
    for item in value:
        if not _safe_text(item, maximum=maximum):
            raise ValueError(f"{name} contains invalid text")
        if sha256 and not _SHA256.fullmatch(item):
            raise ValueError(f"{name} contains an invalid SHA-256")
    return value


def _operand_signature_for_blueprint(blueprint: QuestionBlueprint) -> str:
    return _canonical_sha256(
        {
            "familyId": blueprint.family_id,
            "operandNames": list(blueprint.operand_names),
            "operands": list(blueprint.operands),
            "schemaVersion": "wayline.operand-signature.v1",
        }
    )


def _planned_blueprint_receipt_payload(
    blueprint: QuestionBlueprint,
) -> dict[str, object]:
    if not isinstance(blueprint, QuestionBlueprint):
        raise TypeError("blueprint must be a QuestionBlueprint")
    holdout = blueprint.holdout_receipt
    return {
        "schemaVersion": _PLANNED_BLUEPRINT_RECEIPT_SCHEMA_VERSION,
        "blueprintSchemaVersion": blueprint.schema_version,
        "questionId": blueprint.question_id,
        "worldId": blueprint.world_id,
        "skillId": blueprint.skill_id,
        "familyId": blueprint.family_id,
        "topic": blueprint.topic,
        "templateId": blueprint.template_id,
        "templateRevision": blueprint.template_revision,
        "operandNames": list(blueprint.operand_names),
        "operands": list(blueprint.operands),
        "solverSpec": blueprint.solver_spec,
        "prompt": blueprint.prompt,
        "canonicalAnswer": {
            "numerator": blueprint.canonical_answer.value.numerator,
            "denominator": blueprint.canonical_answer.value.denominator,
            "display": blueprint.canonical_answer.display,
        },
        "trustedSteps": list(blueprint.trusted_steps),
        "allowedProcedureIds": list(blueprint.allowed_procedure_ids),
        "difficulty": blueprint.difficulty,
        "seed": blueprint.seed,
        "contentSha256": blueprint.content_sha256,
        "holdoutReceipt": {
            "boundaryVersion": holdout.boundary_version,
            "recordCount": holdout.record_count,
            "sourceSha256": holdout.source_sha256,
            "canonicalSha256": holdout.canonical_sha256,
            "questionFingerprint": holdout.question_fingerprint,
            "maximumSimilarityBits": holdout.maximum_similarity_bits,
            "similarityThresholdBits": holdout.similarity_threshold_bits,
            "excluded": holdout.excluded,
        },
    }


def _planned_blueprint_sha256(blueprint: QuestionBlueprint) -> str:
    return _canonical_sha256(_planned_blueprint_receipt_payload(blueprint))


def _planned_slot_unsigned_values(
    values: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schemaVersion": _PLANNED_SLOT_SCHEMA_VERSION,
        "slotIndex": values["slot_index"],
        "kind": values["kind"],
        "campaignWorldId": values["campaign_world_id"],
        "contentWorldId": values["content_world_id"],
        "skillId": values["skill_id"],
        "familyId": values["family_id"],
        "difficulty": values["difficulty"],
        "compileSeed": values["compile_seed"],
        "plannedQuestionId": values["planned_question_id"],
        "plannedTemplateId": values["planned_template_id"],
        "plannedContentSha256": values["planned_content_sha256"],
        "plannedQuestionSemanticSha256": values[
            "planned_question_semantic_sha256"
        ],
        "plannedOperandSignature": values["planned_operand_signature"],
        "plannedBlueprintSha256": values["planned_blueprint_sha256"],
        "requiredProcedureIds": list(values["required_procedure_ids"]),
        "selectionSeed": values["selection_seed"],
        "registryId": values["registry_id"],
        "curriculumId": values["curriculum_id"],
        "excludedItemIds": list(values["excluded_item_ids"]),
        "excludedQuestionIds": list(values["excluded_question_ids"]),
        "excludedQuestionSemanticSha256s": list(
            values["excluded_question_semantic_sha256s"]
        ),
        "excludedTemplateIds": list(values["excluded_template_ids"]),
        "excludedOperandSignatures": list(
            values["excluded_operand_signatures"]
        ),
        "excludedContextIds": list(values["excluded_context_ids"]),
        "excludedContentIds": list(values["excluded_content_ids"]),
    }


@dataclass(frozen=True, slots=True)
class PlannedSlotContract:
    """Persisted authorization for one exact planner/materializer slot."""

    schema_version: str
    slot_index: int
    kind: str
    campaign_world_id: str
    content_world_id: str
    skill_id: str
    family_id: str
    difficulty: int
    compile_seed: int
    planned_question_id: str
    planned_template_id: str
    planned_content_sha256: str
    planned_question_semantic_sha256: str
    planned_operand_signature: str
    planned_blueprint_sha256: str
    required_procedure_ids: tuple[str, ...]
    selection_seed: int
    registry_id: str
    curriculum_id: str
    excluded_item_ids: tuple[str, ...]
    excluded_question_ids: tuple[str, ...]
    excluded_question_semantic_sha256s: tuple[str, ...]
    excluded_template_ids: tuple[str, ...]
    excluded_operand_signatures: tuple[str, ...]
    excluded_context_ids: tuple[str, ...]
    excluded_content_ids: tuple[str, ...]
    slot_contract_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != _PLANNED_SLOT_SCHEMA_VERSION:
            raise ValueError("planned slot schema is not supported")
        if (
            not isinstance(self.slot_index, int)
            or isinstance(self.slot_index, bool)
            or self.slot_index < 0
        ):
            raise ValueError("slot_index must be nonnegative")
        if self.kind not in SUPPORTED_SLOT_KINDS:
            raise ValueError("kind is not supported")
        for name in (
            "campaign_world_id",
            "content_world_id",
            "skill_id",
            "family_id",
            "planned_question_id",
            "planned_template_id",
            "registry_id",
            "curriculum_id",
        ):
            _require_identifier(name, getattr(self, name))
        if (
            not isinstance(self.difficulty, int)
            or isinstance(self.difficulty, bool)
            or self.difficulty not in (1, 2, 3)
        ):
            raise ValueError("difficulty is not supported")
        for name in ("compile_seed", "selection_seed"):
            value = getattr(self, name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value < _SIGNED_63_LIMIT
            ):
                raise ValueError(f"{name} is invalid")
        for name in (
            "planned_content_sha256",
            "planned_question_semantic_sha256",
            "planned_operand_signature",
            "planned_blueprint_sha256",
            "slot_contract_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{name} is not a canonical SHA-256")
        for name in (
            "required_procedure_ids",
            "excluded_item_ids",
            "excluded_question_ids",
            "excluded_template_ids",
            "excluded_context_ids",
        ):
            _require_unique_text_tuple(name, getattr(self, name))
        for name in (
            "excluded_question_semantic_sha256s",
            "excluded_operand_signatures",
            "excluded_content_ids",
        ):
            _require_unique_text_tuple(
                name,
                getattr(self, name),
                sha256=True,
            )
        expected = _canonical_sha256(
            _planned_slot_unsigned_values(self._unsigned_values())
        )
        if self.slot_contract_sha256 != expected:
            raise ValueError("planned slot receipt is invalid")

    def _unsigned_values(self) -> dict[str, object]:
        return {
            name: getattr(self, name)
            for name in (
                "slot_index",
                "kind",
                "campaign_world_id",
                "content_world_id",
                "skill_id",
                "family_id",
                "difficulty",
                "compile_seed",
                "planned_question_id",
                "planned_template_id",
                "planned_content_sha256",
                "planned_question_semantic_sha256",
                "planned_operand_signature",
                "planned_blueprint_sha256",
                "required_procedure_ids",
                "selection_seed",
                "registry_id",
                "curriculum_id",
                "excluded_item_ids",
                "excluded_question_ids",
                "excluded_question_semantic_sha256s",
                "excluded_template_ids",
                "excluded_operand_signatures",
                "excluded_context_ids",
                "excluded_content_ids",
            )
        }

    @classmethod
    def from_materialized_slot(
        cls,
        slot: MaterializedSlot,
    ) -> "PlannedSlotContract":
        if not isinstance(slot, MaterializedSlot):
            raise TypeError("slot must be a MaterializedSlot")
        # Re-run its invariant checks in case a caller bypassed frozen fields.
        slot.__post_init__()
        values: dict[str, object] = {
            "slot_index": slot.slot_index,
            "kind": slot.kind,
            "campaign_world_id": slot.campaign_world_id,
            "content_world_id": slot.request.world_id,
            "skill_id": slot.request.skill_id,
            "family_id": slot.request.family_id,
            "difficulty": slot.difficulty,
            "compile_seed": slot.request.seed,
            "planned_question_id": slot.blueprint.question_id,
            "planned_template_id": slot.blueprint.template_id,
            "planned_content_sha256": slot.blueprint.content_sha256,
            "planned_question_semantic_sha256": (
                slot.question_semantic_sha256
            ),
            "planned_operand_signature": slot.operand_signature,
            "planned_blueprint_sha256": _planned_blueprint_sha256(
                slot.blueprint
            ),
            "required_procedure_ids": slot.required_procedure_ids,
            "selection_seed": slot.selection_seed,
            "registry_id": slot.cache_key.registry_id,
            "curriculum_id": slot.cache_key.curriculum_id,
            "excluded_item_ids": slot.excluded_item_ids,
            "excluded_question_ids": slot.excluded_question_ids,
            "excluded_question_semantic_sha256s": (
                slot.excluded_question_semantic_sha256s
            ),
            "excluded_template_ids": slot.excluded_template_ids,
            "excluded_operand_signatures": slot.excluded_operand_signatures,
            "excluded_context_ids": slot.excluded_context_ids,
            "excluded_content_ids": slot.excluded_content_ids,
        }
        return cls(
            schema_version=_PLANNED_SLOT_SCHEMA_VERSION,
            **values,
            slot_contract_sha256=_canonical_sha256(
                _planned_slot_unsigned_values(values)
            ),
        )

    def reconstruct_slot(self, compiler: QuestionCompiler) -> MaterializedSlot:
        """Recompile and revalidate this exact slot against live resources."""

        try:
            curriculum_id = compiler.curriculum.curriculum_id
            registry_id = compiler.registry.registry_id
            compile_blueprint = compiler.compile
        except AttributeError:
            raise TypeError("compiler does not expose authoritative resources") from None
        if not callable(compile_blueprint):
            raise TypeError("compiler does not expose an authoritative compiler")
        if (
            curriculum_id != self.curriculum_id
            or registry_id != self.registry_id
        ):
            raise ValueError("planned slot resources do not match compiler")
        request = CompileRequest(
            world_id=self.content_world_id,
            skill_id=self.skill_id,
            family_id=self.family_id,
            difficulty=self.difficulty,
            seed=self.compile_seed,
        )
        blueprint = compile_blueprint(request)
        if not isinstance(blueprint, QuestionBlueprint):
            raise ValueError("compiler did not return a QuestionBlueprint")
        receipts = (
            blueprint.question_id,
            blueprint.template_id,
            blueprint.content_sha256,
            question_semantic_sha256(blueprint),
            _operand_signature_for_blueprint(blueprint),
            _planned_blueprint_sha256(blueprint),
        )
        expected_receipts = (
            self.planned_question_id,
            self.planned_template_id,
            self.planned_content_sha256,
            self.planned_question_semantic_sha256,
            self.planned_operand_signature,
            self.planned_blueprint_sha256,
        )
        if receipts != expected_receipts:
            raise ValueError("planned blueprint receipts do not match compiler")
        cache_key = CacheKey(
            world_id=self.content_world_id,
            skill_id=self.skill_id,
            family_id=self.family_id,
            difficulty=self.difficulty,
            required_procedure_ids=self.required_procedure_ids,
            registry_id=self.registry_id,
            curriculum_id=self.curriculum_id,
            selection_seed=self.selection_seed,
            excluded_question_ids=self.excluded_question_ids,
            excluded_template_ids=self.excluded_template_ids,
            excluded_operand_signatures=self.excluded_operand_signatures,
            excluded_content_ids=self.excluded_content_ids,
            excluded_question_semantic_sha256s=(
                self.excluded_question_semantic_sha256s
            ),
            excluded_context_ids=self.excluded_context_ids,
        )
        return MaterializedSlot(
            slot_index=self.slot_index,
            kind=self.kind,
            campaign_world_id=self.campaign_world_id,
            request=request,
            blueprint=blueprint,
            difficulty=self.difficulty,
            required_procedure_ids=self.required_procedure_ids,
            selection_seed=self.selection_seed,
            excluded_item_ids=self.excluded_item_ids,
            excluded_question_ids=self.excluded_question_ids,
            excluded_template_ids=self.excluded_template_ids,
            excluded_operand_signatures=self.excluded_operand_signatures,
            excluded_content_ids=self.excluded_content_ids,
            excluded_question_semantic_sha256s=(
                self.excluded_question_semantic_sha256s
            ),
            excluded_context_ids=self.excluded_context_ids,
            operand_signature=self.planned_operand_signature,
            question_semantic_sha256=(
                self.planned_question_semantic_sha256
            ),
            cache_key=cache_key,
        )


def _planned_slot_dict(contract: PlannedSlotContract) -> dict[str, object]:
    unsigned = _planned_slot_unsigned_values(contract._unsigned_values())
    unsigned["slotContractSha256"] = contract.slot_contract_sha256
    return unsigned


def _batch_plan_unsigned(
    slots: tuple[PlannedSlotContract, ...],
) -> dict[str, object]:
    return {
        "schemaVersion": _BATCH_PLAN_SCHEMA_VERSION,
        "slots": [_planned_slot_dict(slot) for slot in slots],
    }


@dataclass(frozen=True, slots=True)
class BatchPlanContract:
    """Canonical immutable receipt for every authorized batch slot."""

    schema_version: str
    slots: tuple[PlannedSlotContract, ...]
    plan_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != _BATCH_PLAN_SCHEMA_VERSION:
            raise ValueError("batch plan schema is not supported")
        if (
            not isinstance(self.slots, tuple)
            or not self.slots
            or any(
                not isinstance(slot, PlannedSlotContract)
                for slot in self.slots
            )
        ):
            raise ValueError("batch plan slots are invalid")
        if tuple(slot.slot_index for slot in self.slots) != tuple(
            range(len(self.slots))
        ):
            raise ValueError("batch plan slots are not contiguous")
        if len({slot.registry_id for slot in self.slots}) != 1:
            raise ValueError("batch plan registry receipts differ")
        if len({slot.curriculum_id for slot in self.slots}) != 1:
            raise ValueError("batch plan curriculum receipts differ")
        if not isinstance(self.plan_sha256, str) or not _SHA256.fullmatch(
            self.plan_sha256
        ):
            raise ValueError("plan_sha256 is not a canonical SHA-256")
        if self.plan_sha256 != _canonical_sha256(
            _batch_plan_unsigned(self.slots)
        ):
            raise ValueError("batch plan receipt is invalid")

    @classmethod
    def from_materialized_slots(
        cls,
        slots: Iterable[MaterializedSlot],
    ) -> "BatchPlanContract":
        try:
            contracts = tuple(
                PlannedSlotContract.from_materialized_slot(slot)
                for slot in slots
            )
        except TypeError:
            raise TypeError("slots must be iterable MaterializedSlot values") from None
        return cls(
            schema_version=_BATCH_PLAN_SCHEMA_VERSION,
            slots=contracts,
            plan_sha256=_canonical_sha256(_batch_plan_unsigned(contracts)),
        )

    def reconstruct_slots(
        self,
        compiler: QuestionCompiler,
    ) -> tuple[MaterializedSlot, ...]:
        return tuple(slot.reconstruct_slot(compiler) for slot in self.slots)

    @property
    def receipt_sha256(self) -> str:
        return self.plan_sha256


def _batch_plan_dict(contract: BatchPlanContract) -> dict[str, object]:
    unsigned = _batch_plan_unsigned(contract.slots)
    unsigned["planSha256"] = contract.plan_sha256
    return unsigned


def _source_proof_unsigned(
    *,
    source_kind: str,
    source_bundle_sha256: str,
    cache_content_sha256: str,
    semantic_content_sha256: str,
    cache_row_sha256: str | None,
    review_decision_receipt_sha256: str | None,
    approval_record_sha256: str | None,
    approved_semantic_content_sha256: str | None,
    reviewer_alias: str | None,
    reviewed_at_utc: str | None,
    reviewed_cache_hit_sha256: str | None,
) -> dict[str, object]:
    return {
        "schemaVersion": "wayline.batch-item-source-proof.v1",
        "sourceKind": source_kind,
        "sourceBundleSha256": source_bundle_sha256,
        "cacheContentSha256": cache_content_sha256,
        "semanticContentSha256": semantic_content_sha256,
        "cacheRowSha256": cache_row_sha256,
        "reviewDecisionReceiptSha256": review_decision_receipt_sha256,
        "approvalRecordSha256": approval_record_sha256,
        "approvedSemanticContentSha256": approved_semantic_content_sha256,
        "reviewerAlias": reviewer_alias,
        "reviewedAtUtc": reviewed_at_utc,
        "reviewedCacheHitSha256": reviewed_cache_hit_sha256,
    }


@dataclass(frozen=True, slots=True)
class BatchItemSourceProof:
    """Closed proof that distinguishes live verification from reviewed fallback."""

    source_kind: str
    source_bundle_sha256: str
    cache_content_sha256: str
    semantic_content_sha256: str
    cache_row_sha256: str | None
    review_decision_receipt_sha256: str | None
    approval_record_sha256: str | None
    approved_semantic_content_sha256: str | None
    reviewer_alias: str | None
    reviewed_at_utc: str | None
    reviewed_cache_hit_sha256: str | None
    source_proof_sha256: str

    def __post_init__(self) -> None:
        if self.source_kind not in {"live_verified", "reviewed_cache"}:
            raise ValueError("source_kind is not supported")
        for name in (
            "source_bundle_sha256",
            "cache_content_sha256",
            "semantic_content_sha256",
            "source_proof_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{name} is not a canonical SHA-256")
        review_values = (
            self.cache_row_sha256,
            self.review_decision_receipt_sha256,
            self.approval_record_sha256,
            self.approved_semantic_content_sha256,
            self.reviewer_alias,
            self.reviewed_at_utc,
            self.reviewed_cache_hit_sha256,
        )
        if self.source_kind == "live_verified":
            if any(value is not None for value in review_values):
                raise ValueError("live source cannot carry review proof")
        else:
            if any(value is None for value in review_values):
                raise ValueError("reviewed source requires complete review proof")
            for name in (
                "cache_row_sha256",
                "review_decision_receipt_sha256",
                "approval_record_sha256",
                "approved_semantic_content_sha256",
                "reviewed_cache_hit_sha256",
            ):
                value = getattr(self, name)
                if not isinstance(value, str) or not _SHA256.fullmatch(value):
                    raise ValueError(f"{name} is not a canonical SHA-256")
            if self.approved_semantic_content_sha256 != self.semantic_content_sha256:
                raise ValueError("review approval does not match source semantics")
            ReviewReceipt(
                owner_alias=self.reviewer_alias,
                decision="approved",
                reviewed_at_utc=self.reviewed_at_utc,
                approved_semantic_content_sha256=(
                    self.approved_semantic_content_sha256
                ),
                approval_record_sha256=self.approval_record_sha256,
                decision_receipt_sha256=self.review_decision_receipt_sha256,
            )
        expected = _canonical_sha256(_source_proof_unsigned(
            source_kind=self.source_kind,
            source_bundle_sha256=self.source_bundle_sha256,
            cache_content_sha256=self.cache_content_sha256,
            semantic_content_sha256=self.semantic_content_sha256,
            cache_row_sha256=self.cache_row_sha256,
            review_decision_receipt_sha256=(
                self.review_decision_receipt_sha256
            ),
            approval_record_sha256=self.approval_record_sha256,
            approved_semantic_content_sha256=(
                self.approved_semantic_content_sha256
            ),
            reviewer_alias=self.reviewer_alias,
            reviewed_at_utc=self.reviewed_at_utc,
            reviewed_cache_hit_sha256=self.reviewed_cache_hit_sha256,
        ))
        if self.source_proof_sha256 != expected:
            raise ValueError("source proof receipt is invalid")

    @classmethod
    def live(cls, bundle: VerifiedQuestionBundle) -> "BatchItemSourceProof":
        if not isinstance(bundle, VerifiedQuestionBundle):
            raise TypeError("bundle must be a VerifiedQuestionBundle")
        values = {
            "source_kind": "live_verified",
            "source_bundle_sha256": bundle.source_bundle_sha256,
            "cache_content_sha256": bundle.cache_content_sha256,
            "semantic_content_sha256": bundle.semantic_content_sha256,
            "cache_row_sha256": None,
            "review_decision_receipt_sha256": None,
            "approval_record_sha256": None,
            "approved_semantic_content_sha256": None,
            "reviewer_alias": None,
            "reviewed_at_utc": None,
            "reviewed_cache_hit_sha256": None,
        }
        return cls(
            **values,
            source_proof_sha256=_canonical_sha256(
                _source_proof_unsigned(**values)
            ),
        )

    @classmethod
    def reviewed(cls, hit: ReviewedCacheHit) -> "BatchItemSourceProof":
        trusted = _revalidate_reviewed_hit(hit)
        values = {
            "source_kind": "reviewed_cache",
            "source_bundle_sha256": trusted.bundle.source_bundle_sha256,
            "cache_content_sha256": trusted.cache_content_sha256,
            "semantic_content_sha256": trusted.bundle.semantic_content_sha256,
            "cache_row_sha256": trusted.cache_row_sha256,
            "review_decision_receipt_sha256": (
                trusted.review_decision_receipt_sha256
            ),
            "approval_record_sha256": trusted.approval_record_sha256,
            "approved_semantic_content_sha256": (
                trusted.approved_semantic_content_sha256
            ),
            "reviewer_alias": trusted.reviewer_alias,
            "reviewed_at_utc": trusted.reviewed_at_utc,
            "reviewed_cache_hit_sha256": trusted.hit_receipt_sha256,
        }
        return cls(
            **values,
            source_proof_sha256=_canonical_sha256(
                _source_proof_unsigned(**values)
            ),
        )

    def reviewed_hit_for(
        self,
        bundle: VerifiedQuestionBundle,
    ) -> ReviewedCacheHit:
        if self.source_kind != "reviewed_cache":
            raise ValueError("live source has no reviewed cache hit")
        return ReviewedCacheHit(
            bundle=bundle,
            cache_row_sha256=self.cache_row_sha256,
            cache_content_sha256=self.cache_content_sha256,
            approved_semantic_content_sha256=(
                self.approved_semantic_content_sha256
            ),
            review_decision_receipt_sha256=(
                self.review_decision_receipt_sha256
            ),
            approval_record_sha256=self.approval_record_sha256,
            reviewer_alias=self.reviewer_alias,
            reviewed_at_utc=self.reviewed_at_utc,
            hit_receipt_sha256=self.reviewed_cache_hit_sha256,
        )

    def validate_bundle(self, bundle: VerifiedQuestionBundle) -> None:
        if not isinstance(bundle, VerifiedQuestionBundle):
            raise TypeError("bundle must be a VerifiedQuestionBundle")
        if (
            self.source_bundle_sha256 != bundle.source_bundle_sha256
            or self.cache_content_sha256 != bundle.cache_content_sha256
            or self.semantic_content_sha256 != bundle.semantic_content_sha256
        ):
            raise ValueError("source proof does not match bundle")
        if self.source_kind == "reviewed_cache":
            self.reviewed_hit_for(bundle)


def _revalidate_reviewed_hit(hit: ReviewedCacheHit) -> ReviewedCacheHit:
    if not isinstance(hit, ReviewedCacheHit):
        raise TypeError("hit must be a ReviewedCacheHit")
    return ReviewedCacheHit(
        bundle=hit.bundle,
        cache_row_sha256=hit.cache_row_sha256,
        cache_content_sha256=hit.cache_content_sha256,
        approved_semantic_content_sha256=(
            hit.approved_semantic_content_sha256
        ),
        review_decision_receipt_sha256=(
            hit.review_decision_receipt_sha256
        ),
        approval_record_sha256=hit.approval_record_sha256,
        reviewer_alias=hit.reviewer_alias,
        reviewed_at_utc=hit.reviewed_at_utc,
        hit_receipt_sha256=hit.hit_receipt_sha256,
    )


@dataclass(frozen=True, slots=True)
class ItemSourceReceipts:
    """All source receipts retained after raw generation text is discarded."""

    source_bundle_sha256: str
    cache_content_sha256: str
    semantic_content_sha256: str
    placement_sha256: str
    model_id: str
    model_sha256: str
    adapter_identity_receipt_sha256: str
    gguf_sha256: str
    generator_identity_receipt_sha256: str
    prompt_sha256: str
    prompt_template_sha256: str
    generation_sha256: str
    generation_receipt_sha256: str
    verifier_version: str
    verifier_receipt_sha256: str
    registry_id: str
    source_proof_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "source_bundle_sha256",
            "cache_content_sha256",
            "semantic_content_sha256",
            "placement_sha256",
            "model_sha256",
            "adapter_identity_receipt_sha256",
            "gguf_sha256",
            "generator_identity_receipt_sha256",
            "prompt_sha256",
            "prompt_template_sha256",
            "generation_sha256",
            "generation_receipt_sha256",
            "verifier_receipt_sha256",
            "source_proof_sha256",
        ):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} is not a canonical SHA-256")
        for name in ("model_id", "verifier_version", "registry_id"):
            if not _safe_text(getattr(self, name), maximum=256):
                raise ValueError(f"{name} is invalid")

    def as_event_receipts(self) -> ProvenanceReceipts:
        return ProvenanceReceipts(
            generator=self.generator_identity_receipt_sha256,
            model=self.model_sha256,
            adapter=self.adapter_identity_receipt_sha256,
            gguf=self.gguf_sha256,
            verifier=self.verifier_receipt_sha256,
            registry=self.registry_id,
            cache=self.source_proof_sha256,
        )


@dataclass(frozen=True, slots=True)
class PlacedRouteMaterial:
    """Exact server-only meaning of one per-batch opaque option."""

    option_id: str
    procedure_id: str | None
    feedback: str | None
    reliable_method: str | None

    def __post_init__(self) -> None:
        _require_identifier("option_id", self.option_id)
        if self.procedure_id is None:
            if self.feedback is not None or self.reliable_method is not None:
                raise ValueError("the correct route cannot carry error feedback")
            return
        if not _safe_text(self.procedure_id, maximum=128):
            raise ValueError("procedure_id is invalid")
        if not _safe_text(self.feedback, maximum=512):
            raise ValueError("feedback is invalid")
        if not _safe_text(self.reliable_method, maximum=512):
            raise ValueError("reliable_method is invalid")


@dataclass(frozen=True, slots=True)
class VerifiedBatchItem:
    """One actual verified source, its placement, and its sealed teaching data."""

    slot_index: int
    kind: str
    campaign_world_id: str
    required_procedure_ids: tuple[str, ...]
    excluded_question_semantic_sha256s: tuple[str, ...]
    excluded_context_ids: tuple[str, ...]
    planned_slot_contract: PlannedSlotContract
    bundle: VerifiedQuestionBundle
    placement: PlacedVerifiedQuestion
    question_semantic_sha256: str
    routes: tuple[PlacedRouteMaterial, ...]
    trusted_steps: tuple[str, ...]
    source_proof: BatchItemSourceProof
    receipts: ItemSourceReceipts

    def __post_init__(self) -> None:
        if (
            not isinstance(self.slot_index, int)
            or isinstance(self.slot_index, bool)
            or self.slot_index < 0
        ):
            raise ValueError("slot_index must be nonnegative")
        if not _safe_text(self.kind, maximum=128):
            raise ValueError("kind is invalid")
        _require_identifier("campaign_world_id", self.campaign_world_id)
        if (
            not isinstance(self.required_procedure_ids, tuple)
            or len(set(self.required_procedure_ids))
            != len(self.required_procedure_ids)
        ):
            raise ValueError("required_procedure_ids must be a unique tuple")
        if (
            not isinstance(self.excluded_question_semantic_sha256s, tuple)
            or len(set(self.excluded_question_semantic_sha256s))
            != len(self.excluded_question_semantic_sha256s)
            or any(
                not _SHA256.fullmatch(value)
                for value in self.excluded_question_semantic_sha256s
            )
        ):
            raise ValueError(
                "excluded_question_semantic_sha256s must be a unique SHA-256 tuple"
            )
        if (
            not isinstance(self.excluded_context_ids, tuple)
            or len(set(self.excluded_context_ids)) != len(self.excluded_context_ids)
            or any(
                not _safe_text(value, maximum=128)
                for value in self.excluded_context_ids
            )
        ):
            raise ValueError("excluded_context_ids must be a unique context tuple")
        if self.kind == "fragile_skill_transfer" and not self.excluded_context_ids:
            raise ValueError("fragile transfer requires a prior context baseline")
        if not isinstance(self.planned_slot_contract, PlannedSlotContract):
            raise TypeError(
                "planned_slot_contract must be a PlannedSlotContract"
            )
        if (
            self.slot_index,
            self.kind,
            self.campaign_world_id,
            self.required_procedure_ids,
        ) != (
            self.planned_slot_contract.slot_index,
            self.planned_slot_contract.kind,
            self.planned_slot_contract.campaign_world_id,
            self.planned_slot_contract.required_procedure_ids,
        ):
            raise ValueError("item does not match its planned slot contract")
        if not isinstance(self.bundle, VerifiedQuestionBundle):
            raise TypeError("bundle must be a VerifiedQuestionBundle")
        if (
            self.bundle.blueprint.world_id,
            self.bundle.blueprint.skill_id,
            self.bundle.blueprint.family_id,
            self.bundle.blueprint.difficulty,
        ) != (
            self.planned_slot_contract.content_world_id,
            self.planned_slot_contract.skill_id,
            self.planned_slot_contract.family_id,
            self.planned_slot_contract.difficulty,
        ):
            raise ValueError("item is incompatible with its planned slot")
        if self.bundle.context_id in self.excluded_context_ids:
            raise ValueError("bundle context is inside the prior context baseline")
        if not isinstance(self.placement, PlacedVerifiedQuestion):
            raise TypeError("placement must be a PlacedVerifiedQuestion")
        if self.question_semantic_sha256 != question_semantic_sha256(
            self.bundle.blueprint
        ):
            raise ValueError("question semantic receipt is invalid")
        if (
            self.question_semantic_sha256
            in self.excluded_question_semantic_sha256s
        ):
            raise ValueError("question semantic is inside its selection baseline")
        if self.placement.source_bundle_sha256 != self.bundle.source_bundle_sha256:
            raise ValueError("placement source bundle does not match")
        if (
            self.placement.source_cache_content_sha256
            != self.bundle.cache_content_sha256
            or self.placement.source_semantic_content_sha256
            != self.bundle.semantic_content_sha256
        ):
            raise ValueError("placement source content does not match")
        if not isinstance(self.routes, tuple) or len(self.routes) != 4:
            raise ValueError("routes must map exactly four options")
        if tuple(route.option_id for route in self.routes) != tuple(
            option.option_id for option in self.placement.options
        ):
            raise ValueError("routes must follow exact placed option order")
        if len({route.option_id for route in self.routes}) != 4:
            raise ValueError("route option IDs must be unique")
        correct = tuple(route for route in self.routes if route.procedure_id is None)
        if len(correct) != 1 or correct[0].option_id != self.placement.correct_option_id:
            raise ValueError("route map does not contain the exact answer key")
        actual_procedures = {
            route.procedure_id
            for route in self.routes
            if route.procedure_id is not None
        }
        if not set(self.required_procedure_ids).issubset(actual_procedures):
            raise ValueError("route map omits a targeted procedure")
        if self.trusted_steps != self.bundle.blueprint.trusted_steps:
            raise ValueError("trusted steps do not match the blueprint")
        if not isinstance(self.source_proof, BatchItemSourceProof):
            raise TypeError("source_proof must be a BatchItemSourceProof")
        self.source_proof.validate_bundle(self.bundle)
        if self.receipts != _source_receipts(
            self.bundle,
            self.placement,
            self.source_proof,
        ):
            raise ValueError("source receipts do not match the exact item")

    @property
    def item_id(self) -> str:
        return self.placement.item_instance_id

    @property
    def option_procedure_map(self) -> tuple[tuple[str, str | None], ...]:
        return tuple((route.option_id, route.procedure_id) for route in self.routes)

    @property
    def event_receipts(self) -> ProvenanceReceipts:
        return self.receipts.as_event_receipts()

    @property
    def is_transfer(self) -> bool:
        """Whether the planner intentionally selected a transfer/probe slot."""

        return self.kind in _TRANSFER_SLOT_KINDS

    @property
    def is_changed_context_transfer(self) -> bool:
        """Report a changed context only when the persisted evidence proves it."""

        return (
            self.kind == "fragile_skill_transfer"
            and bool(self.excluded_context_ids)
            and self.bundle.context_id not in self.excluded_context_ids
        )

    @property
    def valid_for_progression(self) -> bool:
        """Every finalized item crossed compiler, verifier, and batch gates."""

        return self.kind not in {
            "assisted_worked_example",
            "assisted_supported_mcq",
        }

    def route_for_option(self, option_id: str) -> PlacedRouteMaterial:
        for route in self.routes:
            if route.option_id == option_id:
                return route
        raise BatchMaterialValidationError("unknown_placed_option")


def _source_receipts(
    bundle: VerifiedQuestionBundle,
    placement: PlacedVerifiedQuestion,
    source_proof: BatchItemSourceProof,
) -> ItemSourceReceipts:
    provenance = bundle.provenance
    return ItemSourceReceipts(
        source_bundle_sha256=bundle.source_bundle_sha256,
        cache_content_sha256=bundle.cache_content_sha256,
        semantic_content_sha256=bundle.semantic_content_sha256,
        placement_sha256=placement.placement_sha256,
        model_id=provenance.model_id,
        model_sha256=provenance.model_sha256,
        adapter_identity_receipt_sha256=(
            provenance.adapter_identity_receipt_sha256
        ),
        gguf_sha256=provenance.gguf_sha256,
        generator_identity_receipt_sha256=(
            provenance.generator_identity_receipt_sha256
        ),
        prompt_sha256=provenance.prompt_sha256,
        prompt_template_sha256=provenance.prompt_template_sha256,
        generation_sha256=provenance.generation_sha256,
        generation_receipt_sha256=provenance.generation_receipt_sha256,
        verifier_version=provenance.verifier_version,
        verifier_receipt_sha256=provenance.verifier_receipt_sha256,
        registry_id=provenance.registry_id,
        source_proof_sha256=source_proof.source_proof_sha256,
    )


def _make_item(
    *,
    slot_index: int,
    kind: str,
    campaign_world_id: str,
    required_procedure_ids: tuple[str, ...],
    excluded_question_semantic_sha256s: tuple[str, ...],
    excluded_context_ids: tuple[str, ...],
    planned_slot_contract: PlannedSlotContract,
    bundle: VerifiedQuestionBundle,
    source_proof: BatchItemSourceProof,
    item_instance_id: str,
    declared_question_semantic_sha256: str | None = None,
) -> VerifiedBatchItem:
    try:
        placement = bundle.place(item_instance_id)
    except VerifiedQuestionError as exc:
        raise RetryableBatchMaterialRejection(exc.code) from None
    route_by_source_id = {
        route.option_id: route for route in bundle.verified_distractors
    }
    routes: list[PlacedRouteMaterial] = []
    for binding in placement.bindings:
        if binding.procedure_id is None:
            routes.append(
                PlacedRouteMaterial(
                    option_id=binding.instance_option_id,
                    procedure_id=None,
                    feedback=None,
                    reliable_method=None,
                )
            )
            continue
        source = route_by_source_id.get(binding.source_option_id)
        if source is None or source.procedure_id != binding.procedure_id:
            raise BatchMaterialValidationError("placed_route_mismatch")
        routes.append(
            PlacedRouteMaterial(
                option_id=binding.instance_option_id,
                procedure_id=source.procedure_id,
                feedback=source.feedback,
                reliable_method=source.reliable_method,
            )
        )
    semantic = question_semantic_sha256(bundle.blueprint)
    if (
        declared_question_semantic_sha256 is not None
        and declared_question_semantic_sha256 != semantic
    ):
        raise BatchMaterialValidationError("question_semantic_mismatch")
    try:
        return VerifiedBatchItem(
            slot_index=slot_index,
            kind=kind,
            campaign_world_id=campaign_world_id,
            required_procedure_ids=required_procedure_ids,
            excluded_question_semantic_sha256s=(
                excluded_question_semantic_sha256s
            ),
            excluded_context_ids=excluded_context_ids,
            planned_slot_contract=planned_slot_contract,
            bundle=bundle,
            placement=placement,
            question_semantic_sha256=semantic,
            routes=tuple(routes),
            trusted_steps=bundle.blueprint.trusted_steps,
            source_proof=source_proof,
            receipts=_source_receipts(bundle, placement, source_proof),
        )
    except (TypeError, ValueError) as exc:
        raise BatchMaterialValidationError("invalid_item_material") from exc


def _reliable_method(item: VerifiedBatchItem) -> str:
    methods = {
        route.reliable_method
        for route in item.routes
        if route.reliable_method is not None
    }
    if len(methods) != 1:
        raise BatchMaterialValidationError("inconsistent_reliable_method")
    return next(iter(methods))


def _public_batch(
    batch_id: str,
    items: tuple[VerifiedBatchItem, ...],
) -> PublicQuizBatch:
    public_items = tuple(
        PublicQuizItem(
            itemId=item.placement.item_instance_id,
            prompt=item.placement.prompt,
            options=tuple(
                PublicOption(
                    optionId=option.option_id,
                    displayText=option.display_text,
                )
                for option in item.placement.options
            ),
        )
        for item in items
    )
    return PublicQuizBatch(
        schemaVersion=_PUBLIC_SCHEMA_VERSION,
        batchId=batch_id,
        itemCount=len(public_items),
        items=public_items,
    )


def _sealed_quiz(
    batch_id: str,
    items: tuple[VerifiedBatchItem, ...],
) -> SealedQuiz:
    sealed_items: list[SealedQuizItem] = []
    for item in items:
        sealed_items.append(
            SealedQuizItem(
                item_id=item.placement.item_instance_id,
                correct_option_id=item.placement.correct_option_id,
                correct_answer=item.bundle.blueprint.canonical_answer.display,
                trusted_steps=item.trusted_steps,
                possible_errors=tuple(
                    (route.option_id, route.feedback)
                    for route in item.routes
                    if route.procedure_id is not None
                    and route.feedback is not None
                ),
                reliable_method=_reliable_method(item),
            )
        )
    return SealedQuiz(batch_id=batch_id, items=tuple(sealed_items))


def _context_dict(context: BatchContext) -> dict[str, object]:
    return {
        "profileId": context.profile_id,
        "sessionId": context.session_id,
        "worldId": context.world_id,
        "battleId": context.battle_id,
        "coreSubskillIds": list(context.core_subskill_ids),
        "contentVersionId": context.content_version_id,
        "battleTier": context.battle_tier,
    }


def _private_item_dict(item: VerifiedBatchItem) -> dict[str, object]:
    bundle = json.loads(
        item.bundle.to_private_json(),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_nonstandard_number,
    )
    source_proof = _source_proof_unsigned(
        source_kind=item.source_proof.source_kind,
        source_bundle_sha256=item.source_proof.source_bundle_sha256,
        cache_content_sha256=item.source_proof.cache_content_sha256,
        semantic_content_sha256=item.source_proof.semantic_content_sha256,
        cache_row_sha256=item.source_proof.cache_row_sha256,
        review_decision_receipt_sha256=(
            item.source_proof.review_decision_receipt_sha256
        ),
        approval_record_sha256=item.source_proof.approval_record_sha256,
        approved_semantic_content_sha256=(
            item.source_proof.approved_semantic_content_sha256
        ),
        reviewer_alias=item.source_proof.reviewer_alias,
        reviewed_at_utc=item.source_proof.reviewed_at_utc,
        reviewed_cache_hit_sha256=(
            item.source_proof.reviewed_cache_hit_sha256
        ),
    )
    source_proof["sourceProofSha256"] = (
        item.source_proof.source_proof_sha256
    )
    return {
        "slotIndex": item.slot_index,
        "kind": item.kind,
        "campaignWorldId": item.campaign_world_id,
        "requiredProcedureIds": list(item.required_procedure_ids),
        "excludedQuestionSemanticSha256s": list(
            item.excluded_question_semantic_sha256s
        ),
        "excludedContextIds": list(item.excluded_context_ids),
        "plannedSlotContractSha256": (
            item.planned_slot_contract.slot_contract_sha256
        ),
        "itemInstanceId": item.placement.item_instance_id,
        "questionSemanticSha256": item.question_semantic_sha256,
        "sourceProof": source_proof,
        "bundle": bundle,
    }


def _private_unsigned(
    batch_id: str,
    context: BatchContext,
    plan_contract: BatchPlanContract,
    items: tuple[VerifiedBatchItem, ...],
) -> dict[str, object]:
    return {
        "schemaVersion": BATCH_MATERIAL_SCHEMA_VERSION,
        "batchId": batch_id,
        "context": _context_dict(context),
        "planContract": _batch_plan_dict(plan_contract),
        "items": [_private_item_dict(item) for item in items],
    }


def _compatibility_plan_from_items(
    context: BatchContext,
    items: tuple[VerifiedBatchItem, ...],
) -> BatchPlanContract:
    """Retain the exact embedded plan for legacy internal ``_create`` callers."""

    if not isinstance(context, BatchContext):
        raise TypeError("context must be a BatchContext")
    if not isinstance(items, tuple):
        raise TypeError("items must be an immutable tuple")
    slots = tuple(item.planned_slot_contract for item in items)
    return BatchPlanContract(
        schema_version=_BATCH_PLAN_SCHEMA_VERSION,
        slots=slots,
        plan_sha256=_canonical_sha256(_batch_plan_unsigned(slots)),
    )


@dataclass(frozen=True, slots=True)
class VerifiedBatchMaterial:
    """Immutable public layout plus server-only truth for one complete batch."""

    schema_version: str
    batch_id: str
    context: BatchContext
    plan_contract: BatchPlanContract
    items: tuple[VerifiedBatchItem, ...]
    public_batch: PublicQuizBatch
    sealed_quiz: SealedQuiz
    batch_material_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != BATCH_MATERIAL_SCHEMA_VERSION:
            raise ValueError("batch material schema is not supported")
        _require_identifier("batch_id", self.batch_id)
        if not isinstance(self.context, BatchContext):
            raise TypeError("context must be BatchContext")
        if not isinstance(self.plan_contract, BatchPlanContract):
            raise TypeError("plan_contract must be a BatchPlanContract")
        expected_count = QUIZ_LENGTH_BY_TIER[self.context.battle_tier]
        if not isinstance(self.items, tuple) or len(self.items) != expected_count:
            raise ValueError("batch item count does not match the battle tier")
        if len(self.plan_contract.slots) != expected_count:
            raise ValueError("batch plan count does not match the battle tier")
        if tuple(item.slot_index for item in self.items) != tuple(
            range(expected_count)
        ):
            raise ValueError("batch slots are not canonical and contiguous")
        if any(
            item.campaign_world_id != self.context.world_id for item in self.items
        ):
            raise ValueError("batch item campaign world does not match context")
        if any(
            slot.campaign_world_id != self.context.world_id
            for slot in self.plan_contract.slots
        ):
            raise ValueError("batch plan campaign world does not match context")
        if tuple(item.planned_slot_contract for item in self.items) != (
            self.plan_contract.slots
        ):
            raise ValueError("batch items do not retain the exact batch plan")
        for item, slot in zip(
            self.items,
            self.plan_contract.slots,
            strict=True,
        ):
            if (
                item.slot_index,
                item.kind,
                item.campaign_world_id,
                item.required_procedure_ids,
            ) != (
                slot.slot_index,
                slot.kind,
                slot.campaign_world_id,
                slot.required_procedure_ids,
            ):
                raise ValueError("batch item does not match its planned slot")
            if (
                item.bundle.blueprint.world_id,
                item.bundle.blueprint.skill_id,
                item.bundle.blueprint.family_id,
                item.bundle.blueprint.difficulty,
            ) != (
                slot.content_world_id,
                slot.skill_id,
                slot.family_id,
                slot.difficulty,
            ):
                raise ValueError("batch item is incompatible with its planned slot")
        item_ids = tuple(item.placement.item_instance_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("batch item instance IDs must be unique")
        semantic_hashes = tuple(
            item.question_semantic_sha256 for item in self.items
        )
        if len(set(semantic_hashes)) != len(semantic_hashes):
            raise ValueError("batch question semantics must be unique")
        for previous, current in zip(self.items, self.items[1:], strict=False):
            if previous.bundle.template_id == current.bundle.template_id:
                raise ValueError("adjacent batch templates must differ")
            if previous.bundle.operand_signature == current.bundle.operand_signature:
                raise ValueError("adjacent batch operands must differ")
        if self.public_batch != _public_batch(self.batch_id, self.items):
            raise ValueError("public batch does not match placed material")
        if self.sealed_quiz != _sealed_quiz(self.batch_id, self.items):
            raise ValueError("sealed quiz does not match placed material")
        expected_hash = _canonical_sha256(
            _private_unsigned(
                self.batch_id,
                self.context,
                self.plan_contract,
                self.items,
            )
        )
        if self.batch_material_sha256 != expected_hash:
            raise ValueError("batch material receipt does not match")

    @classmethod
    def _create(
        cls,
        *,
        batch_id: str,
        context: BatchContext,
        items: tuple[VerifiedBatchItem, ...],
        plan_contract: BatchPlanContract | None = None,
    ) -> "VerifiedBatchMaterial":
        if plan_contract is None:
            plan_contract = _compatibility_plan_from_items(context, items)
        public = _public_batch(batch_id, items)
        sealed = _sealed_quiz(batch_id, items)
        digest = _canonical_sha256(
            _private_unsigned(batch_id, context, plan_contract, items)
        )
        return cls(
            schema_version=BATCH_MATERIAL_SCHEMA_VERSION,
            batch_id=batch_id,
            context=context,
            plan_contract=plan_contract,
            items=items,
            public_batch=public,
            sealed_quiz=sealed,
            batch_material_sha256=digest,
        )

    def public_payload(self) -> dict[str, Any]:
        """Return only the frozen learner-facing PublicQuizBatch contract."""

        return self.public_batch.model_dump(mode="json", by_alias=True)

    def to_private_json(self) -> str:
        payload = _private_unsigned(
            self.batch_id,
            self.context,
            self.plan_contract,
            self.items,
        )
        payload["batchMaterialSha256"] = self.batch_material_sha256
        return _canonical_json(payload)

    @property
    def plan_sha256(self) -> str:
        """Receipt exposed to batch persistence and preparation orchestration."""

        return self.plan_contract.plan_sha256

    @classmethod
    def from_private_json(
        cls,
        payload: str | bytes | bytearray,
        *,
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
        expected_context: BatchContext | None = None,
        planned_slots: Iterable[MaterializedSlot] | None = None,
    ) -> "VerifiedBatchMaterial":
        """Strictly decode and replay every bundle's compiler/manifest checks."""

        try:
            if isinstance(payload, str):
                text = payload
                encoded = text.encode("utf-8")
            elif isinstance(payload, (bytes, bytearray)):
                encoded = bytes(payload)
                text = encoded.decode("utf-8")
            else:
                raise TypeError("private payload must be text or bytes")
            if len(encoded) > _MAX_PRIVATE_BYTES:
                raise ValueError("private payload exceeds size bound")
            decoded = json.loads(
                text,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_nonstandard_number,
            )
            if _canonical_json(decoded) != text:
                raise ValueError("private payload is not canonical JSON")
            top = _expect_object(
                decoded,
                {
                    "schemaVersion",
                    "batchId",
                    "context",
                    "planContract",
                    "items",
                    "batchMaterialSha256",
                },
            )
            if top["schemaVersion"] != BATCH_MATERIAL_SCHEMA_VERSION:
                raise ValueError("batch material schema is not supported")
            declared_hash = _expect_sha256(top["batchMaterialSha256"])
            unsigned = dict(top)
            unsigned.pop("batchMaterialSha256")
            if _canonical_sha256(unsigned) != declared_hash:
                raise BatchMaterialValidationError("canonical_hash_mismatch")
            batch_id = _expect_identifier(top["batchId"])
            context = _context_from_dict(top["context"])
            plan_contract = _batch_plan_from_dict(top["planContract"])
            raw_items = top["items"]
            if not isinstance(raw_items, list):
                raise ValueError("items must be a list")
        except BatchMaterialValidationError:
            raise
        except (
            _DuplicateJsonKey,
            TypeError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            RecursionError,
        ):
            raise BatchMaterialValidationError("invalid_private_payload") from None

        if expected_context is not None and context != expected_context:
            raise BatchMaterialValidationError("context_mismatch")

        try:
            reconstructed_slots = plan_contract.reconstruct_slots(compiler)
        except (TypeError, ValueError):
            raise BatchMaterialValidationError(
                "planned_material_mismatch"
            ) from None
        if planned_slots is not None:
            try:
                external_slots = tuple(planned_slots)
            except TypeError:
                raise BatchMaterialValidationError(
                    "planned_material_mismatch"
                ) from None
            if external_slots != reconstructed_slots:
                raise BatchMaterialValidationError(
                    "planned_material_mismatch"
                )

        items: list[VerifiedBatchItem] = []
        try:
            for raw_item in raw_items:
                item_raw = _expect_object(
                    raw_item,
                    {
                        "slotIndex",
                        "kind",
                        "campaignWorldId",
                        "requiredProcedureIds",
                        "excludedQuestionSemanticSha256s",
                        "excludedContextIds",
                        "plannedSlotContractSha256",
                        "itemInstanceId",
                        "questionSemanticSha256",
                        "sourceProof",
                        "bundle",
                    },
                )
                slot_index = _expect_nonnegative_int(item_raw["slotIndex"])
                try:
                    planned_slot_contract = plan_contract.slots[slot_index]
                except IndexError:
                    raise ValueError("item slot is outside the plan") from None
                if _expect_sha256(
                    item_raw["plannedSlotContractSha256"]
                ) != planned_slot_contract.slot_contract_sha256:
                    raise BatchMaterialValidationError(
                        "item_plan_receipt_mismatch"
                    )
                bundle_json = _canonical_json(item_raw["bundle"])
                try:
                    bundle = VerifiedQuestionBundle.from_private_json(
                        bundle_json,
                        compiler=compiler,
                        manifest=manifest,
                    )
                except VerifiedQuestionError:
                    raise BatchMaterialValidationError(
                        "bundle_revalidation_failed"
                    ) from None
                items.append(
                    _make_item(
                        slot_index=slot_index,
                        kind=_expect_text(item_raw["kind"], maximum=128),
                        campaign_world_id=_expect_identifier(
                            item_raw["campaignWorldId"]
                        ),
                        required_procedure_ids=_expect_text_tuple(
                            item_raw["requiredProcedureIds"],
                            maximum=128,
                        ),
                        excluded_question_semantic_sha256s=_expect_sha256_tuple(
                            item_raw["excludedQuestionSemanticSha256s"]
                        ),
                        excluded_context_ids=_expect_text_tuple(
                            item_raw["excludedContextIds"],
                            maximum=128,
                        ),
                        planned_slot_contract=planned_slot_contract,
                        bundle=bundle,
                        source_proof=_source_proof_from_dict(
                            item_raw["sourceProof"]
                        ),
                        item_instance_id=_expect_identifier(
                            item_raw["itemInstanceId"]
                        ),
                        declared_question_semantic_sha256=_expect_sha256(
                            item_raw["questionSemanticSha256"]
                        ),
                    )
                )
        except BatchMaterialValidationError:
            raise
        except (TypeError, ValueError, KeyError):
            raise BatchMaterialValidationError("invalid_private_payload") from None

        try:
            builder = BatchMaterialBuilder(
                batch_id=batch_id,
                context=context,
                planned_slots=reconstructed_slots,
            )
            for item in items:
                if item.source_proof.source_kind == "live_verified":
                    builder.accept_live(
                        item.bundle,
                        item_instance_id=item.placement.item_instance_id,
                    )
                else:
                    builder.accept_reviewed_hit(
                        item.source_proof.reviewed_hit_for(item.bundle),
                        item_instance_id=item.placement.item_instance_id,
                    )
                if builder.items[-1] != item:
                    raise ValueError("persisted item does not match plan replay")
            restored = builder.finalize()
        except (BatchMaterialError, TypeError, ValueError):
            raise BatchMaterialValidationError(
                "planned_material_mismatch"
            ) from None
        if restored.batch_material_sha256 != declared_hash:
            raise BatchMaterialValidationError("canonical_hash_mismatch")
        return restored

    def validate_observation(
        self,
        event: ObservationEvent,
        final_item_result: object,
        *,
        observation_session_id: str,
    ) -> None:
        """Bind an observation's server metadata to its exact placed material.

        Submission membership, confidence, and correctness are intentionally
        validated by the quiz/outbox transition.  This method owns stable
        metadata, route meaning, reveal material, and provenance only.  The
        observation session is explicit because a batch may be prepared in an
        earlier session and revealed after that same profile resumes it.
        """

        if not isinstance(event, ObservationEvent):
            raise BatchMaterialValidationError("invalid_observation")
        try:
            _require_identifier(
                "observation_session_id",
                observation_session_id,
            )
        except ValueError:
            raise BatchMaterialValidationError(
                "invalid_observation_session"
            ) from None
        item = next(
            (
                candidate
                for candidate in self.items
                if candidate.placement.item_instance_id == event.item_id
            ),
            None,
        )
        if item is None:
            raise BatchMaterialValidationError("observation_item_mismatch")
        expected_metadata = (
            self.context.profile_id,
            observation_session_id,
            item.bundle.blueprint.world_id,
            self.context.battle_id,
            self.batch_id,
            item.bundle.blueprint.question_id,
            item.bundle.template_id,
            self.context.content_version_id,
            item.bundle.blueprint.skill_id,
            self.context.core_subskill_ids,
            item.bundle.operand_signature,
            item.bundle.context_id,
            item.required_procedure_ids,
            item.event_receipts,
            item.is_transfer,
            item.is_changed_context_transfer,
            item.valid_for_progression,
        )
        actual_metadata = (
            event.profile_id,
            event.session_id,
            event.world_id,
            event.battle_id,
            event.batch_id,
            event.question_id,
            event.template_id,
            event.content_version_id,
            event.skill_id,
            event.world_core_subskill_ids,
            event.operand_signature,
            event.context_id,
            event.targeted_procedure_ids,
            event.receipts,
            event.is_transfer,
            event.is_changed_context_transfer,
            event.valid_for_progression,
        )
        if actual_metadata != expected_metadata:
            raise BatchMaterialValidationError("observation_metadata_mismatch")

        if (
            item.route_for_option(event.first_option_id).procedure_id
            != event.first_procedure_id
            or item.route_for_option(event.final_option_id).procedure_id
            != event.final_procedure_id
        ):
            raise BatchMaterialValidationError("observation_procedure_mismatch")

        sealed = next(
            sealed_item
            for sealed_item in self.sealed_quiz.items
            if sealed_item.item_id == item.placement.item_instance_id
        )
        try:
            result_shape = (
                final_item_result.item_id,
                final_item_result.first_selection.option_id,
                final_item_result.first_selection.confidence,
                final_item_result.first_selection.is_correct,
                final_item_result.final_selection.option_id,
                final_item_result.final_selection.confidence,
                final_item_result.final_selection.is_correct,
                final_item_result.correct_option_id,
                final_item_result.correct_answer,
                tuple(final_item_result.trusted_steps),
                final_item_result.reliable_method,
                final_item_result.self_corrected,
            )
            possible_error = final_item_result.possible_error
        except (AttributeError, TypeError):
            raise BatchMaterialValidationError("invalid_final_item_result") from None
        expected_shape = (
            sealed.item_id,
            event.first_option_id,
            event.first_confidence,
            event.first_correct,
            event.final_option_id,
            event.final_confidence,
            event.final_correct,
            sealed.correct_option_id,
            sealed.correct_answer,
            sealed.trusted_steps,
            sealed.reliable_method,
            event.self_corrected,
        )
        if result_shape != expected_shape:
            raise BatchMaterialValidationError("final_reveal_material_mismatch")

        first_route = item.route_for_option(event.first_option_id)
        final_route = item.route_for_option(event.final_option_id)
        if event.final_correct and not event.first_correct:
            expected_possible_error = first_route.feedback
        elif event.final_correct:
            expected_possible_error = None
        else:
            expected_possible_error = final_route.feedback
        if possible_error != expected_possible_error:
            raise BatchMaterialValidationError("final_feedback_mismatch")
        expected_feedback = tuple(
            value
            for value in (possible_error, sealed.reliable_method)
            if value is not None
        )
        if event.canonical_feedback != expected_feedback:
            raise BatchMaterialValidationError("observation_feedback_mismatch")


class BatchMaterialBuilder:
    """Incrementally bind sequential planned slots to actual verified sources."""

    def __init__(
        self,
        *,
        batch_id: str,
        context: BatchContext,
        planned_slots: Iterable[MaterializedSlot],
        item_id_factory: Callable[[], str] = mint_item_instance_id,
    ) -> None:
        _require_identifier("batch_id", batch_id)
        if not isinstance(context, BatchContext):
            raise TypeError("context must be BatchContext")
        try:
            slots = tuple(planned_slots)
        except TypeError:
            raise TypeError("planned_slots must be iterable") from None
        if any(not isinstance(slot, MaterializedSlot) for slot in slots):
            raise TypeError("every planned slot must be MaterializedSlot")
        expected = QUIZ_LENGTH_BY_TIER[context.battle_tier]
        if len(slots) != expected:
            raise BatchMaterialError("planned_slot_count_mismatch")
        if tuple(slot.slot_index for slot in slots) != tuple(range(expected)):
            raise BatchMaterialError("planned_slot_order_mismatch")
        if any(slot.campaign_world_id != context.world_id for slot in slots):
            raise BatchMaterialError("planned_campaign_world_mismatch")
        if not callable(item_id_factory):
            raise TypeError("item_id_factory must be callable")
        self.batch_id = batch_id
        self.context = context
        self.planned_slots = slots
        self.plan_contract = BatchPlanContract.from_materialized_slots(slots)
        self._item_id_factory = item_id_factory
        self._items: list[VerifiedBatchItem] = []
        self._finalized: VerifiedBatchMaterial | None = None

    @property
    def accepted_count(self) -> int:
        return len(self._items)

    @property
    def items(self) -> tuple[VerifiedBatchItem, ...]:
        return tuple(self._items)

    @property
    def next_slot(self) -> MaterializedSlot | None:
        if len(self._items) == len(self.planned_slots):
            return None
        return self.planned_slots[len(self._items)]

    @property
    def selection_exclusions(self) -> SelectionExclusions:
        """Return next-slot constraints rewritten from actual accepted sources."""

        index = len(self._items)
        if index < len(self.planned_slots):
            slot = self.planned_slots[index]
            planned_prior = self.planned_slots[:index]
            base_questions = _without(
                slot.excluded_question_ids,
                {item.blueprint.question_id for item in planned_prior},
            )
            base_content = _without(
                slot.excluded_content_ids,
                {item.blueprint.content_sha256 for item in planned_prior},
            )
            base_semantics = _without(
                slot.excluded_question_semantic_sha256s,
                {item.question_semantic_sha256 for item in planned_prior},
            )
            prior_planned_template = (
                set() if not planned_prior else {planned_prior[-1].blueprint.template_id}
            )
            prior_planned_operand = (
                set() if not planned_prior else {planned_prior[-1].operand_signature}
            )
            base_templates = _without(
                slot.excluded_template_ids,
                prior_planned_template,
            )
            base_operands = _without(
                slot.excluded_operand_signatures,
                prior_planned_operand,
            )
            base_items = slot.excluded_item_ids
            base_contexts = slot.excluded_context_ids
        else:
            base_questions = ()
            base_content = ()
            base_semantics = ()
            base_templates = ()
            base_operands = ()
            base_items = ()
            base_contexts = ()

        actual_question_ids = tuple(
            item.bundle.blueprint.question_id for item in self._items
        )
        actual_semantics = tuple(
            item.question_semantic_sha256 for item in self._items
        )
        actual_content = tuple(
            value
            for item in self._items
            for value in (
                item.bundle.blueprint.content_sha256,
                item.bundle.cache_content_sha256,
                item.bundle.semantic_content_sha256,
            )
        )
        adjacent_template = (
            () if not self._items else (self._items[-1].bundle.template_id,)
        )
        adjacent_operand = (
            () if not self._items else (self._items[-1].bundle.operand_signature,)
        )
        return SelectionExclusions(
            item_ids=_ordered_union(
                base_items,
                tuple(item.placement.item_instance_id for item in self._items),
            ),
            question_ids=_ordered_union(base_questions, actual_question_ids),
            question_semantic_sha256s=_ordered_union(
                base_semantics,
                actual_semantics,
            ),
            adjacent_template_ids=_ordered_union(
                base_templates,
                adjacent_template,
            ),
            adjacent_operand_signatures=_ordered_union(
                base_operands,
                adjacent_operand,
            ),
            content_ids=_ordered_union(base_content, actual_content),
            context_ids=base_contexts,
        )

    def next_fallback_cache_key(self) -> CacheKey:
        """Return the next slot's key rewritten from actual accepted sources."""

        slot = self.next_slot
        if self._finalized is not None or slot is None:
            raise BatchMaterialError("batch_complete")
        exclusions = self.selection_exclusions
        return replace(
            slot.cache_key,
            excluded_question_ids=exclusions.question_ids,
            excluded_question_semantic_sha256s=(
                exclusions.question_semantic_sha256s
            ),
            excluded_template_ids=exclusions.adjacent_template_ids,
            excluded_operand_signatures=(
                exclusions.adjacent_operand_signatures
            ),
            excluded_content_ids=exclusions.content_ids,
            excluded_context_ids=exclusions.context_ids,
        )

    def accept_live(
        self,
        bundle: VerifiedQuestionBundle,
        *,
        item_instance_id: str | None = None,
    ) -> PlacedVerifiedQuestion:
        """Explicitly accept a bundle produced by the live verified path."""

        source_proof = BatchItemSourceProof.live(bundle)
        return self._accept_with_source(
            bundle,
            source_proof=source_proof,
            item_instance_id=item_instance_id,
        )

    def accept_reviewed_hit(
        self,
        hit: ReviewedCacheHit,
        *,
        item_instance_id: str | None = None,
    ) -> PlacedVerifiedQuestion:
        """Accept only a revalidated reviewed-cache hit with approval proof."""

        trusted = _revalidate_reviewed_hit(hit)
        source_proof = BatchItemSourceProof.reviewed(trusted)
        return self._accept_with_source(
            trusted.bundle,
            source_proof=source_proof,
            item_instance_id=item_instance_id,
        )

    def _accept_with_source(
        self,
        bundle: VerifiedQuestionBundle,
        *,
        source_proof: BatchItemSourceProof,
        item_instance_id: str | None,
    ) -> PlacedVerifiedQuestion:
        """Validate and bind one already classified source without relabeling it."""

        if self._finalized is not None or self.next_slot is None:
            raise BatchMaterialError("batch_complete")
        if not isinstance(bundle, VerifiedQuestionBundle):
            raise TypeError("bundle must be a VerifiedQuestionBundle")
        if not isinstance(source_proof, BatchItemSourceProof):
            raise TypeError("source_proof must be a BatchItemSourceProof")
        source_proof.validate_bundle(bundle)
        slot = self.next_slot
        assert slot is not None
        actual = bundle.blueprint
        expected = slot.request
        if (
            actual.world_id,
            actual.skill_id,
            actual.family_id,
            actual.difficulty,
        ) != (
            expected.world_id,
            expected.skill_id,
            expected.family_id,
            expected.difficulty,
        ):
            raise RetryableBatchMaterialRejection("bundle_slot_mismatch")

        actual_procedure_ids = {
            route.procedure_id for route in bundle.verified_distractors
        }
        if not set(slot.required_procedure_ids).issubset(actual_procedure_ids):
            raise RetryableBatchMaterialRejection("missing_required_procedure")

        if item_instance_id is None:
            try:
                item_instance_id = self._item_id_factory()
            except Exception:
                raise BatchMaterialError("item_id_mint_failed") from None
        exclusions = self.selection_exclusions
        if item_instance_id in exclusions.item_ids:
            raise RetryableBatchMaterialRejection("duplicate_item_instance_id")

        semantic = question_semantic_sha256(actual)
        if semantic in exclusions.question_semantic_sha256s:
            raise RetryableBatchMaterialRejection("duplicate_question_semantic")
        if bundle.context_id in exclusions.context_ids:
            raise RetryableBatchMaterialRejection("excluded_context")
        if actual.question_id in exclusions.question_ids:
            raise RetryableBatchMaterialRejection("excluded_question")
        if bundle.template_id in exclusions.adjacent_template_ids:
            raise RetryableBatchMaterialRejection("adjacent_template_repeat")
        if bundle.operand_signature in exclusions.adjacent_operand_signatures:
            raise RetryableBatchMaterialRejection("adjacent_operand_repeat")
        if any(
            value in exclusions.content_ids
            for value in (
                actual.content_sha256,
                bundle.cache_content_sha256,
                bundle.semantic_content_sha256,
            )
        ):
            raise RetryableBatchMaterialRejection("excluded_content")

        item = _make_item(
            slot_index=slot.slot_index,
            kind=slot.kind,
            campaign_world_id=slot.campaign_world_id,
            required_procedure_ids=slot.required_procedure_ids,
            excluded_question_semantic_sha256s=(
                exclusions.question_semantic_sha256s
            ),
            excluded_context_ids=exclusions.context_ids,
            planned_slot_contract=self.plan_contract.slots[slot.slot_index],
            bundle=bundle,
            source_proof=source_proof,
            item_instance_id=item_instance_id,
        )
        self._items.append(item)
        return item.placement

    def finalize(self) -> VerifiedBatchMaterial:
        if self._finalized is not None:
            return self._finalized
        expected = QUIZ_LENGTH_BY_TIER[self.context.battle_tier]
        if len(self._items) != expected:
            raise BatchMaterialError("incomplete_batch")
        try:
            material = VerifiedBatchMaterial._create(
                batch_id=self.batch_id,
                context=self.context,
                items=tuple(self._items),
                plan_contract=self.plan_contract,
            )
        except (TypeError, ValueError) as exc:
            raise BatchMaterialValidationError("invalid_batch_material") from exc
        self._finalized = material
        return material


def _expect_object(value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("object fields do not match schema")
    return value


def _expect_identifier(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("identifier is invalid")
    return value


def _expect_text(value: object, *, maximum: int) -> str:
    if not _safe_text(value, maximum=maximum):
        raise ValueError("text is invalid")
    return value


def _expect_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("SHA-256 is invalid")
    return value


def _expect_optional_sha256(value: object) -> str | None:
    return None if value is None else _expect_sha256(value)


def _expect_optional_text(value: object, *, maximum: int) -> str | None:
    return None if value is None else _expect_text(value, maximum=maximum)


def _expect_nonnegative_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("integer is invalid")
    return value


def _expect_text_tuple(value: object, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("text tuple is invalid")
    result = tuple(_expect_text(item, maximum=maximum) for item in value)
    if len(set(result)) != len(result):
        raise ValueError("text tuple must be unique")
    return result


def _expect_sha256_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("SHA-256 tuple is invalid")
    result = tuple(_expect_sha256(item) for item in value)
    if len(set(result)) != len(result):
        raise ValueError("SHA-256 tuple must be unique")
    return result


def _source_proof_from_dict(value: object) -> BatchItemSourceProof:
    raw = _expect_object(
        value,
        {
            "schemaVersion",
            "sourceKind",
            "sourceBundleSha256",
            "cacheContentSha256",
            "semanticContentSha256",
            "cacheRowSha256",
            "reviewDecisionReceiptSha256",
            "approvalRecordSha256",
            "approvedSemanticContentSha256",
            "reviewerAlias",
            "reviewedAtUtc",
            "reviewedCacheHitSha256",
            "sourceProofSha256",
        },
    )
    if raw["schemaVersion"] != "wayline.batch-item-source-proof.v1":
        raise ValueError("source proof schema is not supported")
    return BatchItemSourceProof(
        source_kind=_expect_text(raw["sourceKind"], maximum=32),
        source_bundle_sha256=_expect_sha256(raw["sourceBundleSha256"]),
        cache_content_sha256=_expect_sha256(raw["cacheContentSha256"]),
        semantic_content_sha256=_expect_sha256(raw["semanticContentSha256"]),
        cache_row_sha256=_expect_optional_sha256(raw["cacheRowSha256"]),
        review_decision_receipt_sha256=_expect_optional_sha256(
            raw["reviewDecisionReceiptSha256"]
        ),
        approval_record_sha256=_expect_optional_sha256(
            raw["approvalRecordSha256"]
        ),
        approved_semantic_content_sha256=_expect_optional_sha256(
            raw["approvedSemanticContentSha256"]
        ),
        reviewer_alias=_expect_optional_text(
            raw["reviewerAlias"],
            maximum=128,
        ),
        reviewed_at_utc=_expect_optional_text(
            raw["reviewedAtUtc"],
            maximum=64,
        ),
        reviewed_cache_hit_sha256=_expect_optional_sha256(
            raw["reviewedCacheHitSha256"]
        ),
        source_proof_sha256=_expect_sha256(raw["sourceProofSha256"]),
    )


def _planned_slot_from_dict(value: object) -> PlannedSlotContract:
    raw = _expect_object(
        value,
        {
            "schemaVersion",
            "slotIndex",
            "kind",
            "campaignWorldId",
            "contentWorldId",
            "skillId",
            "familyId",
            "difficulty",
            "compileSeed",
            "plannedQuestionId",
            "plannedTemplateId",
            "plannedContentSha256",
            "plannedQuestionSemanticSha256",
            "plannedOperandSignature",
            "plannedBlueprintSha256",
            "requiredProcedureIds",
            "selectionSeed",
            "registryId",
            "curriculumId",
            "excludedItemIds",
            "excludedQuestionIds",
            "excludedQuestionSemanticSha256s",
            "excludedTemplateIds",
            "excludedOperandSignatures",
            "excludedContextIds",
            "excludedContentIds",
            "slotContractSha256",
        },
    )
    if raw["schemaVersion"] != _PLANNED_SLOT_SCHEMA_VERSION:
        raise ValueError("planned slot schema is not supported")
    return PlannedSlotContract(
        schema_version=_PLANNED_SLOT_SCHEMA_VERSION,
        slot_index=_expect_nonnegative_int(raw["slotIndex"]),
        kind=_expect_text(raw["kind"], maximum=128),
        campaign_world_id=_expect_identifier(raw["campaignWorldId"]),
        content_world_id=_expect_identifier(raw["contentWorldId"]),
        skill_id=_expect_identifier(raw["skillId"]),
        family_id=_expect_identifier(raw["familyId"]),
        difficulty=_expect_nonnegative_int(raw["difficulty"]),
        compile_seed=_expect_nonnegative_int(raw["compileSeed"]),
        planned_question_id=_expect_identifier(raw["plannedQuestionId"]),
        planned_template_id=_expect_identifier(raw["plannedTemplateId"]),
        planned_content_sha256=_expect_sha256(raw["plannedContentSha256"]),
        planned_question_semantic_sha256=_expect_sha256(
            raw["plannedQuestionSemanticSha256"]
        ),
        planned_operand_signature=_expect_sha256(
            raw["plannedOperandSignature"]
        ),
        planned_blueprint_sha256=_expect_sha256(
            raw["plannedBlueprintSha256"]
        ),
        required_procedure_ids=_expect_text_tuple(
            raw["requiredProcedureIds"],
            maximum=128,
        ),
        selection_seed=_expect_nonnegative_int(raw["selectionSeed"]),
        registry_id=_expect_identifier(raw["registryId"]),
        curriculum_id=_expect_identifier(raw["curriculumId"]),
        excluded_item_ids=_expect_text_tuple(
            raw["excludedItemIds"],
            maximum=256,
        ),
        excluded_question_ids=_expect_text_tuple(
            raw["excludedQuestionIds"],
            maximum=256,
        ),
        excluded_question_semantic_sha256s=_expect_sha256_tuple(
            raw["excludedQuestionSemanticSha256s"]
        ),
        excluded_template_ids=_expect_text_tuple(
            raw["excludedTemplateIds"],
            maximum=256,
        ),
        excluded_operand_signatures=_expect_sha256_tuple(
            raw["excludedOperandSignatures"]
        ),
        excluded_context_ids=_expect_text_tuple(
            raw["excludedContextIds"],
            maximum=256,
        ),
        excluded_content_ids=_expect_sha256_tuple(
            raw["excludedContentIds"]
        ),
        slot_contract_sha256=_expect_sha256(raw["slotContractSha256"]),
    )


def _batch_plan_from_dict(value: object) -> BatchPlanContract:
    raw = _expect_object(
        value,
        {"schemaVersion", "slots", "planSha256"},
    )
    if raw["schemaVersion"] != _BATCH_PLAN_SCHEMA_VERSION:
        raise ValueError("batch plan schema is not supported")
    raw_slots = raw["slots"]
    if not isinstance(raw_slots, list) or not raw_slots or len(raw_slots) > 10:
        raise ValueError("batch plan slots are invalid")
    return BatchPlanContract(
        schema_version=_BATCH_PLAN_SCHEMA_VERSION,
        slots=tuple(_planned_slot_from_dict(slot) for slot in raw_slots),
        plan_sha256=_expect_sha256(raw["planSha256"]),
    )


def _context_from_dict(value: object) -> BatchContext:
    raw = _expect_object(
        value,
        {
            "profileId",
            "sessionId",
            "worldId",
            "battleId",
            "coreSubskillIds",
            "contentVersionId",
            "battleTier",
        },
    )
    return BatchContext(
        profile_id=_expect_identifier(raw["profileId"]),
        session_id=_expect_identifier(raw["sessionId"]),
        world_id=_expect_identifier(raw["worldId"]),
        battle_id=_expect_identifier(raw["battleId"]),
        core_subskill_ids=_expect_text_tuple(
            raw["coreSubskillIds"],
            maximum=96,
        ),
        content_version_id=_expect_identifier(raw["contentVersionId"]),
        battle_tier=_expect_text(raw["battleTier"], maximum=32),
    )


BatchMaterialAssembler = BatchMaterialBuilder


__all__ = [
    "BATCH_MATERIAL_SCHEMA_VERSION",
    "BatchItemSourceProof",
    "BatchContext",
    "BatchMaterialAssembler",
    "BatchMaterialBuilder",
    "BatchMaterialError",
    "BatchMaterialValidationError",
    "BatchPlanContract",
    "ItemSourceReceipts",
    "PlannedSlotContract",
    "PlacedRouteMaterial",
    "RetryableBatchMaterialRejection",
    "SelectionExclusions",
    "VerifiedBatchItem",
    "VerifiedBatchMaterial",
]
