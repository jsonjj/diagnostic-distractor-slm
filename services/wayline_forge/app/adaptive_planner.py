"""Deterministic fixed-length slot planning from reduced learner evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import cycle

from services.wayline_forge.app.boss_gate import AssistedRoutePlan
from services.wayline_forge.app.evidence_reducer import LearnerState, SkillEvidence
from services.wayline_forge.app.events import ObservationEvent


QUIZ_LENGTH_BY_TIER = {
    "route_1": 3,
    "route_2": 4,
    "route_3": 4,
    "elite": 5,
    "world_boss": 8,
    "campaign_finale": 10,
    "seal_trial": 3,
    "assisted_route": 3,
}


@dataclass(frozen=True, slots=True)
class SlotIntent:
    kind: str
    campaign_world_id: str
    content_world_id: str
    skill_id: str
    procedure_ids: tuple[str, ...] = ()
    excluded_item_ids: tuple[str, ...] = ()
    excluded_question_ids: tuple[str, ...] = ()
    excluded_template_ids: tuple[str, ...] = ()
    excluded_operand_signatures: tuple[str, ...] = ()
    excluded_context_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.excluded_context_ids, tuple):
            raise ValueError("excluded_context_ids must be an immutable tuple")
        if (
            any(
                not isinstance(context_id, str) or not context_id
                for context_id in self.excluded_context_ids
            )
            or len(set(self.excluded_context_ids)) != len(self.excluded_context_ids)
        ):
            raise ValueError("excluded_context_ids must contain unique context IDs")

    @property
    def world_id(self) -> str:
        """Compatibility alias for the mathematical content world."""

        return self.content_world_id


def _last_batch_exclusions(
    state: LearnerState,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return exact prior-batch IDs plus its final adjacency boundary."""

    observations = [item for item in state.events if isinstance(item, ObservationEvent)]
    if not observations:
        return (), (), (), ()
    latest = max(observations, key=lambda item: (item.ordinal, item.event_id))
    latest_batch = [item for item in observations if item.batch_id == latest.batch_id]
    latest_batch.sort(key=lambda item: (item.ordinal, item.event_id))
    return (
        tuple(dict.fromkeys(item.item_id for item in latest_batch)),
        tuple(dict.fromkeys(item.question_id for item in latest_batch)),
        (latest.template_id,),
        (latest.operand_signature,),
    )


def _prior_skill_context_ids(
    state: LearnerState,
    *,
    world_id: str,
    skill_id: str,
) -> tuple[str, ...]:
    """Return the persisted contexts that authoritative skill evidence used."""

    return tuple(dict.fromkeys(
        item.context_id
        for item in state.events
        if isinstance(item, ObservationEvent)
        and item.world_id == world_id
        and item.skill_id == skill_id
    ))


def _tier_value(battle_tier: object) -> str:
    value = getattr(battle_tier, "value", battle_tier)
    if not isinstance(value, str) or value not in QUIZ_LENGTH_BY_TIER:
        raise ValueError(f"unknown battle tier: {value!r}")
    return value


def plan_slots(state: LearnerState, battle_tier: object) -> tuple[SlotIntent, ...]:
    """Plan content intents in fixed priority order without changing quiz length."""

    target_length = QUIZ_LENGTH_BY_TIER[_tier_value(battle_tier)]
    current_world_id = state.active_world_id
    if current_world_id is None:
        raise ValueError("cannot plan a quiz before a current world is established")

    current_world = state.world(current_world_id)
    core_skills = list(current_world.core_subskill_ids)
    if not core_skills:
        raise ValueError("active world has no authoritative curriculum activation")

    (
        excluded_items,
        excluded_questions,
        excluded_templates,
        excluded_operands,
    ) = _last_batch_exclusions(state)

    def intent(
        kind: str,
        world_id: str,
        skill_id: str,
        procedure_ids: tuple[str, ...] = (),
    ) -> SlotIntent:
        return SlotIntent(
            kind=kind,
            campaign_world_id=current_world_id,
            content_world_id=world_id,
            skill_id=skill_id,
            procedure_ids=procedure_ids,
            excluded_item_ids=excluded_items,
            excluded_question_ids=excluded_questions,
            excluded_template_ids=excluded_templates,
            excluded_operand_signatures=excluded_operands,
            excluded_context_ids=(
                _prior_skill_context_ids(
                    state,
                    world_id=world_id,
                    skill_id=skill_id,
                )
                if kind == "fragile_skill_transfer"
                else ()
            ),
        )

    candidates: list[SlotIntent] = []

    active = sorted(
        (procedure for procedure in state.procedures if procedure.status == "active"),
        key=lambda procedure: (-procedure.priority, procedure.procedure_id),
    )
    for procedure in active:
        candidates.append(intent(
            "active_misconception_probe",
            procedure.world_id or current_world_id,
            procedure.skill_id or core_skills[0],
            (procedure.procedure_id,),
        ))

    for pair in state.ambiguous_procedure_pairs:
        if all(state.procedure(procedure_id).status == "resolved" for procedure_id in pair):
            continue
        evidence = state.procedure(pair[0])
        candidates.append(intent(
            "misconception_discrimination",
            evidence.world_id or current_world_id,
            evidence.skill_id or core_skills[0],
            pair,
        ))

    current_skills = [
        skill for skill in state.skills if skill.world_id == current_world_id
    ]
    fragile = sorted(
        (skill for skill in current_skills if skill.status == "fragile"),
        key=lambda skill: (skill.last_ordinal, skill.skill_id),
    )
    candidates.extend(
        intent("fragile_skill_transfer", current_world_id, skill.skill_id)
        for skill in fragile
    )

    by_skill: dict[str, SkillEvidence] = {
        skill.skill_id: skill for skill in current_skills
    }
    under_sampled = sorted(
        (
            by_skill[skill_id]
            for skill_id in core_skills
            if skill_id in by_skill and by_skill[skill_id].exposure_count > 0
            and by_skill[skill_id].status != "fragile"
        ),
        key=lambda skill: (skill.exposure_count, skill.last_ordinal, skill.skill_id),
    )
    candidates.extend(
        intent("under_sampled_core_skill", current_world_id, skill.skill_id)
        for skill in under_sampled
    )

    prior_skills = sorted(
        (skill for skill in state.skills if skill.world_id != current_world_id),
        key=lambda skill: (skill.last_ordinal, skill.world_id or "", skill.skill_id),
    )
    candidates.extend(
        intent(
            "spaced_prior_world_transfer",
            skill.world_id or current_world_id,
            skill.skill_id,
        )
        for skill in prior_skills
    )

    novel = [skill_id for skill_id in core_skills if skill_id not in by_skill]
    candidates.extend(
        intent("novel_current_skill", current_world_id, skill_id)
        for skill_id in novel
    )

    planned = candidates[:target_length]
    if len(planned) < target_length:
        fallback_order = core_skills or [current_skills[0].skill_id]
        for skill_id in cycle(fallback_order):
            kind = (
                "novel_current_skill"
                if skill_id not in by_skill
                else "under_sampled_core_skill"
            )
            planned.append(intent(kind, current_world_id, skill_id))
            if len(planned) == target_length:
                break

    # Only slot zero is adjacent to the prior quiz. Later slots receive their
    # template/operand boundary from the preceding slot during materialization.
    return tuple(
        replace(
            slot,
            excluded_template_ids=(excluded_templates if index == 0 else ()),
            excluded_operand_signatures=(excluded_operands if index == 0 else ()),
        )
        for index, slot in enumerate(planned)
    )


def plan_assisted_slots(
    state: LearnerState,
    route_plan: AssistedRoutePlan,
) -> tuple[SlotIntent, SlotIntent, SlotIntent]:
    """Bind the deterministic support plan to fresh practice-only intents."""

    if (
        not isinstance(route_plan, AssistedRoutePlan)
        or route_plan.item_count != 3
        or len(route_plan.slots) != 3
    ):
        raise ValueError("assisted route plan must contain exactly three slots")
    if state.active_world_id is None:
        raise ValueError("assisted route requires an active world")

    observations = tuple(
        item for item in state.events if isinstance(item, ObservationEvent)
    )
    excluded_items = tuple(dict.fromkeys(item.item_id for item in observations))
    excluded_questions = tuple(
        dict.fromkeys(item.question_id for item in observations)
    )
    excluded_operands = tuple(
        dict.fromkeys(item.operand_signature for item in observations)
    )
    latest_templates = _last_batch_exclusions(state)[2]

    intents: list[SlotIntent] = []
    for index, planned in enumerate(route_plan.slots):
        kind = {
            "worked_example": "assisted_worked_example",
            "supported_mcq": "assisted_supported_mcq",
        }.get(planned.kind)
        if (
            kind is None
            or not planned.support_provided
            or planned.slot_index != index
        ):
            raise ValueError("assisted route slot is not supported")
        intents.append(
            SlotIntent(
                kind=kind,
                campaign_world_id=state.active_world_id,
                content_world_id=state.active_world_id,
                skill_id=planned.skill_id,
                excluded_item_ids=excluded_items,
                excluded_question_ids=excluded_questions,
                excluded_template_ids=(latest_templates if index == 0 else ()),
                excluded_operand_signatures=excluded_operands,
            )
        )
    return tuple(intents)  # type: ignore[return-value]
