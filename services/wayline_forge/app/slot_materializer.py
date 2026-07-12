"""Deterministically turn adaptive slot intents into exact safe blueprints.

This module is deliberately a narrow seam.  The planner owns *what* should be
tested; the question compiler owns mathematical truth and frozen-holdout
exclusion; this materializer binds the two without widening either authority.

This module materializes compiler candidates, not delivered live/cache bundles.
The orchestrator must apply :func:`question_semantic_sha256` to every delivered
bundle, enforce delivered-batch exclusions, and require targeted routes in the
bundle's verified distractors before presentation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Literal

from .adaptive_planner import QUIZ_LENGTH_BY_TIER, SlotIntent
from .procedure_registry import RegistryError
from .question_kernel import (
    CompilationError,
    CompileRequest,
    QuestionBlueprint,
    QuestionCompiler,
)
from .reviewed_cache import CacheKey, question_semantic_sha256

if TYPE_CHECKING:
    from .batch_material import SelectionExclusions


MAX_SEED_ATTEMPTS = 32

DIFFICULTY_SCHEDULE_BY_TIER = {
    "route_1": (1,),
    "route_2": (1, 2),
    "route_3": (2,),
    "elite": (2, 3),
    "world_boss": (2, 3),
    "campaign_finale": (3,),
    "seal_trial": (1,),
    "assisted_route": (2, 1, 1),
}

_DIAGNOSTIC_KINDS = frozenset(
    {"active_misconception_probe", "misconception_discrimination"}
)
SUPPORTED_SLOT_KINDS = frozenset(
    {
        "active_misconception_probe",
        "assisted_supported_mcq",
        "assisted_worked_example",
        "misconception_discrimination",
        "fragile_skill_transfer",
        "under_sampled_core_skill",
        "spaced_prior_world_transfer",
        "novel_current_skill",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SIGNED_63_LIMIT = 2**63


class SlotMaterializationError(ValueError):
    """Raised when an intent cannot be bound to a safe exact blueprint."""


# Short compatibility name for callers that describe the operation rather than
# the domain object.  Both names denote the same fail-closed error contract.
MaterializationError = SlotMaterializationError


@dataclass(frozen=True, slots=True)
class MaterializedSlot:
    """Immutable binding of one planner intent to compiler and cache inputs."""

    slot_index: int
    kind: str
    campaign_world_id: str
    request: CompileRequest
    blueprint: QuestionBlueprint
    difficulty: int
    required_procedure_ids: tuple[str, ...]
    selection_seed: int
    excluded_item_ids: tuple[str, ...]
    excluded_question_ids: tuple[str, ...]
    excluded_template_ids: tuple[str, ...]
    excluded_operand_signatures: tuple[str, ...]
    excluded_content_ids: tuple[str, ...]
    excluded_question_semantic_sha256s: tuple[str, ...]
    excluded_context_ids: tuple[str, ...]
    operand_signature: str
    question_semantic_sha256: str
    cache_key: CacheKey

    def __post_init__(self) -> None:
        if (
            not isinstance(self.slot_index, int)
            or isinstance(self.slot_index, bool)
            or self.slot_index < 0
        ):
            raise ValueError("slot_index must be a nonnegative integer")
        if self.kind not in SUPPORTED_SLOT_KINDS:
            raise ValueError("kind is not a supported slot kind")
        if not isinstance(self.campaign_world_id, str) or not self.campaign_world_id:
            raise ValueError("campaign_world_id must be non-empty text")
        if not isinstance(self.request, CompileRequest):
            raise TypeError("request must be a CompileRequest")
        if not isinstance(self.blueprint, QuestionBlueprint):
            raise TypeError("blueprint must be a QuestionBlueprint")
        if not isinstance(self.cache_key, CacheKey):
            raise TypeError("cache_key must be a CacheKey")
        if self.difficulty != self.request.difficulty:
            raise ValueError("difficulty must match the compile request")
        if not _blueprint_matches_request(self.blueprint, self.request):
            raise ValueError("blueprint must exactly match the compile request")
        if self.blueprint.holdout_receipt.excluded:
            raise ValueError("blueprint crosses the frozen holdout boundary")
        if len(set(self.required_procedure_ids)) != len(
            self.required_procedure_ids
        ):
            raise ValueError("required_procedure_ids must be unique")
        if not set(self.required_procedure_ids).issubset(
            self.blueprint.allowed_procedure_ids
        ):
            raise ValueError("blueprint does not allow every required procedure")
        if (
            not isinstance(self.selection_seed, int)
            or isinstance(self.selection_seed, bool)
            or not 0 <= self.selection_seed < _SIGNED_63_LIMIT
        ):
            raise ValueError("selection_seed must be a nonnegative signed 64-bit integer")
        if self.operand_signature != _operand_signature(self.blueprint):
            raise ValueError("operand_signature does not match the blueprint")
        if self.question_semantic_sha256 != question_semantic_sha256(
            self.blueprint
        ):
            raise ValueError("question_semantic_sha256 does not match the blueprint")
        if self.cache_key != CacheKey(
            world_id=self.request.world_id,
            skill_id=self.request.skill_id,
            family_id=self.request.family_id,
            difficulty=self.difficulty,
            required_procedure_ids=self.required_procedure_ids,
            registry_id=self.cache_key.registry_id,
            curriculum_id=self.cache_key.curriculum_id,
            selection_seed=self.selection_seed,
            excluded_question_ids=self.excluded_question_ids,
            excluded_template_ids=self.excluded_template_ids,
            excluded_operand_signatures=self.excluded_operand_signatures,
            excluded_content_ids=self.excluded_content_ids,
            excluded_question_semantic_sha256s=(
                self.excluded_question_semantic_sha256s
            ),
            excluded_context_ids=self.excluded_context_ids,
        ):
            raise ValueError("cache_key does not match the materialized slot")

    @property
    def index(self) -> int:
        """Concise compatibility alias for callers that already name slots."""

        return self.slot_index

    @property
    def compile_request(self) -> CompileRequest:
        return self.request

    @property
    def content_world_id(self) -> str:
        return self.request.world_id

    @property
    def world_id(self) -> str:
        return self.request.world_id

    @property
    def skill_id(self) -> str:
        return self.request.skill_id


@dataclass(frozen=True, slots=True)
class LiveSlotCandidate:
    """One exact compiler candidate for one planned live-generation attempt."""

    slot_index: int
    request: CompileRequest
    blueprint: QuestionBlueprint
    operand_signature: str
    question_semantic_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.slot_index, int)
            or isinstance(self.slot_index, bool)
            or self.slot_index < 0
        ):
            raise ValueError("slot_index must be a nonnegative integer")
        if not isinstance(self.request, CompileRequest):
            raise TypeError("request must be a CompileRequest")
        if not isinstance(self.blueprint, QuestionBlueprint):
            raise TypeError("blueprint must be a QuestionBlueprint")
        if not _blueprint_matches_request(self.blueprint, self.request):
            raise ValueError("blueprint must exactly match the compile request")
        if self.blueprint.holdout_receipt.excluded:
            raise ValueError("blueprint crosses the frozen holdout boundary")
        if self.operand_signature != _operand_signature(self.blueprint):
            raise ValueError("operand_signature does not match the blueprint")
        if self.question_semantic_sha256 != question_semantic_sha256(
            self.blueprint
        ):
            raise ValueError(
                "question_semantic_sha256 does not match the blueprint"
            )


def _tier_value(battle_tier: object) -> str:
    value = getattr(battle_tier, "value", battle_tier)
    if not isinstance(value, str) or value not in DIFFICULTY_SCHEDULE_BY_TIER:
        raise ValueError(f"unknown battle tier: {value!r}")
    return value


def _validate_seed(name: str, value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value < _SIGNED_63_LIMIT
    ):
        raise ValueError(f"{name} must be a nonnegative signed 64-bit integer")
    return value


def _validate_max_attempts(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= MAX_SEED_ATTEMPTS
    ):
        raise ValueError(
            f"max_attempts must be an integer from 1 to {MAX_SEED_ATTEMPTS}"
        )
    return value


def _validate_compiler(compiler: object) -> None:
    curriculum = getattr(compiler, "curriculum", None)
    registry = getattr(compiler, "registry", None)
    if (
        not callable(getattr(compiler, "compile", None))
        or curriculum is None
        or not isinstance(getattr(curriculum, "families", None), Mapping)
        or not isinstance(getattr(curriculum, "curriculum_id", None), str)
        or registry is None
        or not callable(getattr(registry, "entry", None))
        or not isinstance(getattr(registry, "registry_id", None), str)
    ):
        raise TypeError("compiler must expose validated curriculum and registry contracts")


def _derived_seed(domain: str, *parts: object) -> int:
    encoded = json.dumps(
        [domain, *parts],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big") & (
        _SIGNED_63_LIMIT - 1
    )


def _ordered_union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, tuple):
            raise SlotMaterializationError("exclusions must be immutable tuples")
        for value in group:
            if value not in seen:
                result.append(value)
                seen.add(value)
    return tuple(result)


def _declared_procedures(family: object) -> frozenset[str]:
    try:
        templates = family.templates
        return frozenset(
            procedure_id
            for template in templates
            for procedure_id in template.procedure_ids
        )
    except (AttributeError, TypeError):
        raise SlotMaterializationError("curriculum family is malformed") from None


def _compatible_families(compiler: Any, source: SlotIntent) -> tuple[object, ...]:
    if not isinstance(source.procedure_ids, tuple):
        raise SlotMaterializationError("procedure_ids must be an immutable tuple")
    if len(set(source.procedure_ids)) != len(source.procedure_ids):
        raise SlotMaterializationError("procedure_ids must be unique")

    route_families: dict[str, str] = {}
    for procedure_id in source.procedure_ids:
        try:
            route_families[procedure_id] = compiler.registry.entry(
                procedure_id
            ).family_id
        except RegistryError:
            raise SlotMaterializationError(
                f"unknown required procedure: {procedure_id}"
            ) from None

    matches: list[object] = []
    for mapping_id, family in compiler.curriculum.families.items():
        try:
            exact_mapping = mapping_id == family.family_id
            exact_content = (
                family.world_id == source.content_world_id
                and family.skill_id == source.skill_id
            )
        except AttributeError:
            raise SlotMaterializationError("curriculum family is malformed") from None
        if not exact_mapping or not exact_content:
            continue
        if any(
            route_family_id != family.family_id
            for route_family_id in route_families.values()
        ):
            continue
        if not set(source.procedure_ids).issubset(_declared_procedures(family)):
            continue
        matches.append(family)

    matches.sort(key=lambda family: family.family_id)
    if not matches:
        raise SlotMaterializationError(
            "no exact compatible family for intent world, skill, and required procedures"
        )
    if len({family.family_id for family in matches}) != len(matches):
        raise SlotMaterializationError("curriculum contains duplicate compatible families")
    return tuple(matches)


def _difficulty(tier: str, slot_index: int, kind: str) -> int:
    if kind in _DIAGNOSTIC_KINDS:
        return 1
    schedule = DIFFICULTY_SCHEDULE_BY_TIER[tier]
    return schedule[slot_index % len(schedule)]


def _operand_signature(blueprint: QuestionBlueprint) -> str:
    encoded = json.dumps(
        {
            "familyId": blueprint.family_id,
            "operandNames": list(blueprint.operand_names),
            "operands": list(blueprint.operands),
            "schemaVersion": "wayline.operand-signature.v1",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _blueprint_matches_request(
    blueprint: QuestionBlueprint,
    request: CompileRequest,
) -> bool:
    return (
        blueprint.world_id == request.world_id
        and blueprint.skill_id == request.skill_id
        and blueprint.family_id == request.family_id
        and blueprint.difficulty == request.difficulty
        and blueprint.seed == request.seed
        and bool(_SHA256.fullmatch(blueprint.content_sha256))
    )


def _blueprint_context_id(
    compiler: object,
    blueprint: QuestionBlueprint,
) -> str | None:
    """Resolve context from the compiler's authoritative authored template."""

    try:
        family = compiler.curriculum.families[blueprint.family_id]
        matches = tuple(
            template.context_id
            for template in family.templates
            if template.template_id == blueprint.template_id
        )
    except (AttributeError, KeyError, TypeError):
        return None
    if (
        len(matches) != 1
        or not isinstance(matches[0], str)
        or not matches[0]
    ):
        return None
    return matches[0]


def _candidate_is_allowed(
    compiler: object,
    blueprint: object,
    request: CompileRequest,
    required_procedure_ids: tuple[str, ...],
    excluded_question_ids: tuple[str, ...],
    excluded_template_ids: tuple[str, ...],
    excluded_operand_signatures: tuple[str, ...],
    excluded_content_ids: tuple[str, ...],
    excluded_semantic_hashes: tuple[str, ...],
    excluded_context_ids: tuple[str, ...],
) -> tuple[bool, str | None, str | None]:
    if not isinstance(blueprint, QuestionBlueprint):
        return False, None, None
    if not _blueprint_matches_request(blueprint, request):
        return False, None, None
    if blueprint.holdout_receipt.excluded:
        return False, None, None
    if not set(required_procedure_ids).issubset(blueprint.allowed_procedure_ids):
        return False, None, None
    signature = _operand_signature(blueprint)
    semantic_hash = question_semantic_sha256(blueprint)
    context_id = _blueprint_context_id(compiler, blueprint)
    if context_id is None or context_id in excluded_context_ids:
        return False, None, None
    if blueprint.question_id in excluded_question_ids:
        return False, None, None
    if blueprint.template_id in excluded_template_ids:
        return False, None, None
    if signature in excluded_operand_signatures:
        return False, None, None
    if blueprint.content_sha256 in excluded_content_ids:
        return False, None, None
    if semantic_hash in excluded_semantic_hashes:
        return False, None, None
    return True, signature, semantic_hash


def _validate_live_attempt(value: object) -> Literal[1, 2]:
    if not isinstance(value, int) or isinstance(value, bool) or value not in (1, 2):
        raise ValueError("live_attempt must be 1 or 2")
    return value


def _validate_live_tuple(
    name: str,
    value: object,
    *,
    sha256: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an immutable tuple")
    if any(
        not isinstance(item, str)
        or not item
        or (sha256 and not _SHA256.fullmatch(item))
        for item in value
    ):
        suffix = " of canonical SHA-256 values" if sha256 else " of non-empty text"
        raise ValueError(f"{name} must be an immutable tuple{suffix}")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicates")
    return value


def _validate_selection_exclusions(
    value: object,
) -> "SelectionExclusions":
    # ``batch_material`` owns the aggregate and imports this module.  Keeping
    # this strict runtime import at the call boundary avoids a module cycle
    # while still rejecting lookalike caller-controlled objects.
    from .batch_material import SelectionExclusions

    if not isinstance(value, SelectionExclusions):
        raise TypeError("exclusions must be a SelectionExclusions")
    _validate_live_tuple("item_ids", value.item_ids)
    _validate_live_tuple("question_ids", value.question_ids)
    _validate_live_tuple(
        "question_semantic_sha256s",
        value.question_semantic_sha256s,
        sha256=True,
    )
    _validate_live_tuple("adjacent_template_ids", value.adjacent_template_ids)
    _validate_live_tuple(
        "adjacent_operand_signatures",
        value.adjacent_operand_signatures,
        sha256=True,
    )
    _validate_live_tuple("content_ids", value.content_ids, sha256=True)
    _validate_live_tuple("context_ids", value.context_ids)
    return value


def _validate_planned_slot_for_live_compiler(
    planned_slot: MaterializedSlot,
    compiler: object,
) -> None:
    if (
        compiler.curriculum.curriculum_id != planned_slot.cache_key.curriculum_id
        or compiler.registry.registry_id != planned_slot.cache_key.registry_id
    ):
        raise SlotMaterializationError(
            "planned slot resource receipts do not match the compiler"
        )
    try:
        family = compiler.curriculum.families[planned_slot.request.family_id]
        exact_family = (
            family.family_id == planned_slot.request.family_id
            and family.world_id == planned_slot.request.world_id
            and family.skill_id == planned_slot.request.skill_id
        )
    except (AttributeError, KeyError, TypeError):
        exact_family = False
        family = None
    if not exact_family or family is None:
        raise SlotMaterializationError(
            "planned slot family is incompatible with the compiler"
        )
    declared = _declared_procedures(family)
    for procedure_id in planned_slot.required_procedure_ids:
        try:
            registered_family = compiler.registry.entry(procedure_id).family_id
        except (AttributeError, RegistryError):
            raise SlotMaterializationError(
                "planned slot procedure is incompatible with the compiler"
            ) from None
        if (
            registered_family != family.family_id
            or procedure_id not in declared
        ):
            raise SlotMaterializationError(
                "planned slot procedure is incompatible with the compiler"
            )


def _live_candidate(
    planned_slot: MaterializedSlot,
    request: CompileRequest,
    blueprint: QuestionBlueprint,
    operand_signature: str,
    semantic_sha256: str,
) -> LiveSlotCandidate:
    return LiveSlotCandidate(
        slot_index=planned_slot.slot_index,
        request=request,
        blueprint=blueprint,
        operand_signature=operand_signature,
        question_semantic_sha256=semantic_sha256,
    )


def materialize_live_candidate(
    planned_slot: MaterializedSlot,
    *,
    batch_seed: int,
    live_attempt: Literal[1, 2],
    compiler: QuestionCompiler,
    exclusions: "SelectionExclusions",
    attempted_semantic_sha256s: tuple[str, ...] = (),
    max_compile_attempts: int = MAX_SEED_ATTEMPTS,
) -> LiveSlotCandidate:
    """Resolve one exact live-generation candidate without widening its plan.

    Live attempt one may reuse the planned blueprint when it remains compatible
    with exclusions derived from items actually accepted into the batch.  Every
    other path uses a deterministic live-only compiler seed stream.  Attempt two
    additionally excludes the planned semantic and SLM prompt so it can never
    spend the second provider attempt on the same mathematical input.
    """

    if not isinstance(planned_slot, MaterializedSlot):
        raise TypeError("planned_slot must be a MaterializedSlot")
    batch_seed = _validate_seed("batch_seed", batch_seed)
    live_attempt = _validate_live_attempt(live_attempt)
    max_compile_attempts = _validate_max_attempts(max_compile_attempts)
    _validate_compiler(compiler)
    exclusions = _validate_selection_exclusions(exclusions)
    attempted_semantic_sha256s = _validate_live_tuple(
        "attempted_semantic_sha256s",
        attempted_semantic_sha256s,
        sha256=True,
    )
    if live_attempt == 2 and not attempted_semantic_sha256s:
        raise ValueError(
            "attempt two requires at least one attempted semantic receipt"
        )
    _validate_planned_slot_for_live_compiler(planned_slot, compiler)

    semantic_exclusions = _ordered_union(
        exclusions.question_semantic_sha256s,
        attempted_semantic_sha256s,
        (
            (planned_slot.question_semantic_sha256,)
            if live_attempt == 2
            else ()
        ),
    )

    if live_attempt == 1:
        accepted, signature, semantic_sha256 = _candidate_is_allowed(
            compiler,
            planned_slot.blueprint,
            planned_slot.request,
            planned_slot.required_procedure_ids,
            exclusions.question_ids,
            exclusions.adjacent_template_ids,
            exclusions.adjacent_operand_signatures,
            exclusions.content_ids,
            semantic_exclusions,
            exclusions.context_ids,
        )
        if accepted and signature is not None and semantic_sha256 is not None:
            return _live_candidate(
                planned_slot,
                planned_slot.request,
                planned_slot.blueprint,
                signature,
                semantic_sha256,
            )

    planned_prompt_sha256: str | None = None
    if live_attempt == 2:
        from .slm_prompt import build_slm_request

        planned_prompt_sha256 = build_slm_request(
            planned_slot.blueprint
        ).prompt_sha256

    used_seeds = {planned_slot.request.seed}
    seed_domain = f"wayline.live-slot-attempt-{live_attempt}-compile.v1"
    for compile_index in range(max_compile_attempts):
        compile_seed = _derived_seed(
            seed_domain,
            batch_seed,
            planned_slot.slot_index,
            compile_index,
            planned_slot.kind,
            planned_slot.campaign_world_id,
            planned_slot.request.world_id,
            planned_slot.request.skill_id,
            planned_slot.request.family_id,
            planned_slot.request.difficulty,
        )
        while compile_seed in used_seeds:
            compile_seed = (compile_seed + 1) % _SIGNED_63_LIMIT
        used_seeds.add(compile_seed)
        request = CompileRequest(
            world_id=planned_slot.request.world_id,
            skill_id=planned_slot.request.skill_id,
            family_id=planned_slot.request.family_id,
            difficulty=planned_slot.request.difficulty,
            seed=compile_seed,
        )
        try:
            blueprint = compiler.compile(request)
        except CompilationError:
            continue
        accepted, signature, semantic_sha256 = _candidate_is_allowed(
            compiler,
            blueprint,
            request,
            planned_slot.required_procedure_ids,
            exclusions.question_ids,
            exclusions.adjacent_template_ids,
            exclusions.adjacent_operand_signatures,
            exclusions.content_ids,
            semantic_exclusions,
            exclusions.context_ids,
        )
        if not accepted or signature is None or semantic_sha256 is None:
            continue
        if planned_prompt_sha256 is not None:
            from .slm_prompt import build_slm_request

            if build_slm_request(blueprint).prompt_sha256 == planned_prompt_sha256:
                continue
        return _live_candidate(
            planned_slot,
            request,
            blueprint,
            signature,
            semantic_sha256,
        )

    raise SlotMaterializationError(
        "could not materialize live candidate for "
        f"slot {planned_slot.slot_index} attempt {live_attempt} "
        f"in {max_compile_attempts} compiler attempts"
    )


def materialize_slots(
    intents: Iterable[SlotIntent],
    battle_tier: object,
    batch_seed: int,
    compiler: object,
    *,
    max_attempts: int = MAX_SEED_ATTEMPTS,
) -> tuple[MaterializedSlot, ...]:
    """Bind every intent in place or fail before returning a partial batch.

    Public item IDs are retained only for orchestration; they are never treated
    as semantic content hashes.  Within the new batch, question/content reuse
    is forbidden across every prior slot while template/operand reuse is
    forbidden against the immediately prior slot.
    """

    tier = _tier_value(battle_tier)
    batch_seed = _validate_seed("batch_seed", batch_seed)
    max_attempts = _validate_max_attempts(max_attempts)
    _validate_compiler(compiler)
    try:
        sources = tuple(intents)
    except TypeError:
        raise TypeError("intents must be an iterable of SlotIntent values") from None
    if any(not isinstance(source, SlotIntent) for source in sources):
        raise TypeError("every intent must be a SlotIntent")
    expected_count = QUIZ_LENGTH_BY_TIER[tier]
    if len(sources) != expected_count:
        raise SlotMaterializationError(
            f"battle tier {tier} requires exactly {expected_count} intents; "
            f"received {len(sources)}"
        )
    unsupported = tuple(
        source.kind for source in sources if source.kind not in SUPPORTED_SLOT_KINDS
    )
    if unsupported:
        raise SlotMaterializationError(f"unsupported slot kind: {unsupported[0]!r}")
    for source in sources:
        if (
            source.kind == "fragile_skill_transfer"
            and not source.excluded_context_ids
        ):
            raise SlotMaterializationError(
                "fragile skill transfer requires a prior context baseline"
            )

    materialized: list[MaterializedSlot] = []
    previous: MaterializedSlot | None = None
    prior_question_ids: list[str] = []
    prior_content_ids: list[str] = []
    prior_semantic_hashes: list[str] = []
    for slot_index, source in enumerate(sources):
        difficulty = _difficulty(tier, slot_index, source.kind)
        families = _compatible_families(compiler, source)

        previous_templates = () if previous is None else (
            previous.blueprint.template_id,
        )
        previous_operands = () if previous is None else (
            previous.operand_signature,
        )
        excluded_questions = _ordered_union(
            source.excluded_question_ids,
            tuple(prior_question_ids),
        )
        excluded_templates = _ordered_union(
            source.excluded_template_ids,
            previous_templates,
        )
        excluded_operands = _ordered_union(
            source.excluded_operand_signatures,
            previous_operands,
        )
        excluded_content = _ordered_union(tuple(prior_content_ids))
        excluded_semantics = _ordered_union(tuple(prior_semantic_hashes))
        excluded_contexts = _ordered_union(source.excluded_context_ids)

        family_offset = _derived_seed(
            "wayline.slot-family.v1",
            batch_seed,
            source.content_world_id,
            source.skill_id,
        ) % len(families)
        selected: MaterializedSlot | None = None
        for attempt in range(max_attempts):
            family = families[(family_offset + slot_index + attempt) % len(families)]
            compile_seed = _derived_seed(
                "wayline.slot-compile.v1",
                batch_seed,
                slot_index,
                attempt,
                source.kind,
                family.family_id,
            )
            request = CompileRequest(
                world_id=source.content_world_id,
                skill_id=source.skill_id,
                family_id=family.family_id,
                difficulty=difficulty,
                seed=compile_seed,
            )
            try:
                blueprint = compiler.compile(request)
            except CompilationError:
                continue
            accepted, signature, semantic_hash = _candidate_is_allowed(
                compiler,
                blueprint,
                request,
                source.procedure_ids,
                excluded_questions,
                excluded_templates,
                excluded_operands,
                excluded_content,
                excluded_semantics,
                excluded_contexts,
            )
            if not accepted or signature is None or semantic_hash is None:
                continue

            selection_seed = _derived_seed(
                "wayline.slot-cache-selection.v1",
                batch_seed,
                slot_index,
                source.kind,
                family.family_id,
            )
            cache_key = CacheKey(
                world_id=request.world_id,
                skill_id=request.skill_id,
                family_id=request.family_id,
                difficulty=difficulty,
                required_procedure_ids=source.procedure_ids,
                registry_id=compiler.registry.registry_id,
                curriculum_id=compiler.curriculum.curriculum_id,
                selection_seed=selection_seed,
                excluded_question_ids=excluded_questions,
                excluded_template_ids=excluded_templates,
                excluded_operand_signatures=excluded_operands,
                excluded_content_ids=excluded_content,
                excluded_question_semantic_sha256s=excluded_semantics,
                excluded_context_ids=excluded_contexts,
            )
            selected = MaterializedSlot(
                slot_index=slot_index,
                kind=source.kind,
                campaign_world_id=source.campaign_world_id,
                request=request,
                blueprint=blueprint,
                difficulty=difficulty,
                required_procedure_ids=source.procedure_ids,
                selection_seed=selection_seed,
                excluded_item_ids=source.excluded_item_ids,
                excluded_question_ids=excluded_questions,
                excluded_template_ids=excluded_templates,
                excluded_operand_signatures=excluded_operands,
                excluded_content_ids=excluded_content,
                excluded_question_semantic_sha256s=excluded_semantics,
                excluded_context_ids=excluded_contexts,
                operand_signature=signature,
                question_semantic_sha256=semantic_hash,
                cache_key=cache_key,
            )
            break

        if selected is None:
            raise SlotMaterializationError(
                f"could not materialize slot {slot_index} in {max_attempts} attempts"
            )
        materialized.append(selected)
        prior_question_ids.append(selected.blueprint.question_id)
        prior_content_ids.append(selected.blueprint.content_sha256)
        prior_semantic_hashes.append(selected.question_semantic_sha256)
        previous = selected

    return tuple(materialized)


__all__ = [
    "DIFFICULTY_SCHEDULE_BY_TIER",
    "MAX_SEED_ATTEMPTS",
    "LiveSlotCandidate",
    "MaterializationError",
    "MaterializedSlot",
    "SlotMaterializationError",
    "SUPPORTED_SLOT_KINDS",
    "materialize_live_candidate",
    "materialize_slots",
    "question_semantic_sha256",
]
