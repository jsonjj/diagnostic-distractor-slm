"""Deterministic boss-access and world-clear progression gates."""

from __future__ import annotations

from dataclasses import dataclass

from services.wayline_forge.app.contracts import (
    BossGateResult,
    GateRequirement,
)
from services.wayline_forge.app.evidence_reducer import LearnerState
from services.wayline_forge.app.events import (
    AssistedRouteCompletionEvent,
    BossOutcomeEvent,
    ObservationEvent,
    SealTrialOutcomeEvent,
)


REQUIRED_LEAD_IN_WINS = 4
REQUIRED_VALID_WORLD_ITEMS = 16
REQUIRED_LATEST_TEN_CORRECT = 7
WORLD_BOSS_REQUIRED_FINAL_CORRECT = 6
CAMPAIGN_FINALE_REQUIRED_FINAL_CORRECT = 8
SEAL_TRIAL_ITEM_COUNT = 3


@dataclass(frozen=True, slots=True)
class AssistedRouteSlot:
    slot_index: int
    kind: str
    skill_id: str
    support_provided: bool
    difficulty_delta: int
    operand_complexity: str


@dataclass(frozen=True, slots=True)
class AssistedRoutePlan:
    item_count: int
    slots: tuple[AssistedRouteSlot, ...]


@dataclass(frozen=True, slots=True)
class WorldClearResult:
    world_id: str
    cleared: bool
    combat_victory_preserved: bool
    boss_replay_required: bool
    final_correct: int
    item_count: int
    required_final_correct: int
    seal_trial_required: bool
    seal_trial_item_count: int
    missed_seal_trials: int
    assisted_route_plan: AssistedRoutePlan | None

    @property
    def assisted_route_unlocked(self) -> bool:
        return self.assisted_route_plan is not None


def _assisted_route_plan(state: LearnerState, world_id: str) -> AssistedRoutePlan:
    core_skills = state.world(world_id).core_subskill_ids
    if not core_skills:
        raise ValueError("assisted route requires an authoritative curriculum activation")
    ordered = sorted(
        core_skills,
        key=lambda skill_id: (
            state.skill(skill_id, world_id).first_pass_correct_count,
            state.skill(skill_id, world_id).exposure_count,
            skill_id,
        ),
    )
    slots = (
        AssistedRouteSlot(0, "worked_example", ordered[0], True, 0, "worked"),
        AssistedRouteSlot(1, "supported_mcq", ordered[0], True, -1, "reduced"),
        AssistedRouteSlot(
            2,
            "supported_mcq",
            ordered[1 % len(ordered)],
            True,
            -1,
            "reduced",
        ),
    )
    return AssistedRoutePlan(item_count=3, slots=slots)


def evaluate_boss_gate(state: LearnerState, world_id: str) -> BossGateResult:
    world = state.world(world_id)
    observations = sorted(
        (
            item
            for item in state.events
            if isinstance(item, ObservationEvent)
            and item.world_id == world_id
            and item.valid_for_progression
        ),
        key=lambda item: (item.ordinal, item.event_id),
    )
    latest_ten = observations[-10:]
    latest_ten_correct = sum(item.first_correct for item in latest_ten)

    core_subskills = world.core_subskill_ids
    # A missing curriculum roster is itself not ready; the public contract requires
    # a positive denominator so it cannot accidentally become vacuously true.
    if not core_subskills:
        core_subskills = ("unconfigured_core",)

    ready_core = 0
    for skill_id in core_subskills:
        exposures = [item for item in observations if item.skill_id == skill_id]
        if len(exposures) >= 2 and any(item.first_correct for item in exposures):
            ready_core += 1

    lead_in_wins = min(len(world.lead_in_battle_wins), REQUIRED_LEAD_IN_WINS)
    valid_world_items = len(observations)
    unmet: list[GateRequirement] = []
    if lead_in_wins < REQUIRED_LEAD_IN_WINS:
        unmet.append(GateRequirement.LEAD_IN_WINS)
    if valid_world_items < REQUIRED_VALID_WORLD_ITEMS:
        unmet.append(GateRequirement.VALID_WORLD_ITEMS)
    if len(latest_ten) < 10 or latest_ten_correct < REQUIRED_LATEST_TEN_CORRECT:
        unmet.append(GateRequirement.LATEST_TEN_ACCURACY)
    if ready_core < len(core_subskills):
        unmet.append(GateRequirement.CORE_SUBSKILL_COVERAGE)

    return BossGateResult(
        schemaVersion="wayline.v1",
        worldId=world_id,
        unlocked=not unmet,
        leadInWins=lead_in_wins,
        requiredLeadInWins=REQUIRED_LEAD_IN_WINS,
        validWorldItems=valid_world_items,
        requiredValidWorldItems=REQUIRED_VALID_WORLD_ITEMS,
        latestTenItemCount=len(latest_ten),
        latestTenCorrectCount=latest_ten_correct,
        requiredLatestTenCorrectCount=REQUIRED_LATEST_TEN_CORRECT,
        coreSubskillCount=len(core_subskills),
        readyCoreSubskillCount=ready_core,
        unmetRequirements=tuple(unmet),
    )


def evaluate_gate(state: LearnerState, world_id: str) -> BossGateResult:
    """Plan-named alias retained for callers consuming the implementation spec."""

    return evaluate_boss_gate(state, world_id)


def evaluate_world_clear(state: LearnerState, world_id: str) -> WorldClearResult:
    boss_attempts = sorted(
        (
            item
            for item in state.events
            if isinstance(item, BossOutcomeEvent) and item.world_id == world_id
        ),
        key=lambda item: (item.ordinal, item.event_id),
    )
    if not boss_attempts:
        return WorldClearResult(
            world_id=world_id,
            cleared=False,
            combat_victory_preserved=False,
            boss_replay_required=True,
            final_correct=0,
            item_count=8,
            required_final_correct=WORLD_BOSS_REQUIRED_FINAL_CORRECT,
            seal_trial_required=False,
            seal_trial_item_count=SEAL_TRIAL_ITEM_COUNT,
            missed_seal_trials=0,
            assisted_route_plan=None,
        )

    boss = boss_attempts[-1]
    required = (
        CAMPAIGN_FINALE_REQUIRED_FINAL_CORRECT
        if boss.is_campaign_finale
        else WORLD_BOSS_REQUIRED_FINAL_CORRECT
    )
    trials = sorted(
        (
            item
            for item in state.events
            if isinstance(item, SealTrialOutcomeEvent)
            and item.world_id == world_id
            and item.ordinal > boss.ordinal
        ),
        key=lambda item: (item.ordinal, item.event_id),
    )
    passed_trial = any(item.passed for item in trials)
    missed_trials = sum(not item.passed for item in trials)
    assisted_completed = any(
        isinstance(item, AssistedRouteCompletionEvent)
        and item.world_id == world_id
        and item.ordinal > boss.ordinal
        for item in state.events
    )
    cleared = boss.combat_won and (
        boss.final_correct >= required or passed_trial or assisted_completed
    )
    victory_preserved = boss.combat_won

    return WorldClearResult(
        world_id=world_id,
        cleared=cleared,
        combat_victory_preserved=victory_preserved,
        boss_replay_required=not victory_preserved,
        final_correct=boss.final_correct,
        item_count=boss.item_count,
        required_final_correct=required,
        seal_trial_required=(
            victory_preserved
            and not cleared
            and boss.final_correct < required
        ),
        seal_trial_item_count=SEAL_TRIAL_ITEM_COUNT,
        missed_seal_trials=missed_trials,
        assisted_route_plan=(
            _assisted_route_plan(state, world_id)
            if victory_preserved and not cleared and missed_trials >= 2
            else None
        ),
    )
