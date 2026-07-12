"""Pure deterministic replay of immutable Wayline learning events."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
import json
from typing import Iterable

from services.wayline_forge.app.events import (
    AssistedRouteCompletionEvent,
    BattleOutcomeEvent,
    LearningEvent,
    ObservationEvent,
    SealTrialOutcomeEvent,
    WorldActivatedEvent,
    canonical_event_dict,
)


class EvidenceReplayError(ValueError):
    """Base error for an event stream that cannot safely produce evidence."""


class DuplicateSemanticEventError(EvidenceReplayError):
    """Two observations claim the same authoritative batch/item identity."""


class InvalidEventSequenceError(EvidenceReplayError):
    """A progression event violates its required deterministic sequence."""


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    event_id: str
    world_id: str
    battle_id: str
    batch_id: str
    item_id: str
    question_id: str
    first_option_id: str
    final_option_id: str
    first_confidence: str
    final_confidence: str
    first_correct: bool
    final_correct: bool
    explanations_shown: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcedureEvidence:
    procedure_id: str
    status: str
    world_id: str | None
    skill_id: str | None
    distinct_question_count: int
    distinct_template_count: int
    priority: int
    ambiguous: bool
    targeted_transfer_correct_count: int
    evidence_event_ids: tuple[str, ...]
    last_ordinal: int


@dataclass(frozen=True, slots=True)
class SkillEvidence:
    world_id: str | None
    skill_id: str
    status: str
    exposure_count: int
    first_pass_correct_count: int
    self_correction_count: int
    changed_context_transfer_count: int
    last_ordinal: int


@dataclass(frozen=True, slots=True)
class WorldEvidence:
    world_id: str
    core_subskill_ids: tuple[str, ...]
    valid_item_count: int
    lead_in_battle_wins: tuple[str, ...]
    curriculum_receipt: str | None
    activated_ordinal: int
    last_ordinal: int


@dataclass(frozen=True, slots=True)
class LearnerState:
    profile_id: str | None
    active_world_id: str | None
    procedures: tuple[ProcedureEvidence, ...]
    skills: tuple[SkillEvidence, ...]
    worlds: tuple[WorldEvidence, ...]
    answer_records: tuple[AnswerRecord, ...]
    ambiguous_procedure_pairs: tuple[tuple[str, str], ...]
    events: tuple[LearningEvent, ...]

    @property
    def current_world_id(self) -> str | None:
        """Compatibility alias; campaign authority is explicitly active_world_id."""

        return self.active_world_id

    def procedure(self, procedure_id: str) -> ProcedureEvidence:
        for procedure in self.procedures:
            if procedure.procedure_id == procedure_id:
                return procedure
        return ProcedureEvidence(
            procedure_id=procedure_id,
            status="unseen",
            world_id=None,
            skill_id=None,
            distinct_question_count=0,
            distinct_template_count=0,
            priority=0,
            ambiguous=False,
            targeted_transfer_correct_count=0,
            evidence_event_ids=(),
            last_ordinal=0,
        )

    def skill(self, skill_id: str, world_id: str | None = None) -> SkillEvidence:
        candidates = [skill for skill in self.skills if skill.skill_id == skill_id]
        if world_id is not None:
            candidates = [skill for skill in candidates if skill.world_id == world_id]
        elif self.active_world_id is not None:
            current = [skill for skill in candidates if skill.world_id == self.active_world_id]
            if current:
                candidates = current
        if candidates:
            return max(candidates, key=lambda skill: skill.last_ordinal)
        return SkillEvidence(
            world_id=world_id,
            skill_id=skill_id,
            status="unseen",
            exposure_count=0,
            first_pass_correct_count=0,
            self_correction_count=0,
            changed_context_transfer_count=0,
            last_ordinal=0,
        )

    def world(self, world_id: str) -> WorldEvidence:
        for world in self.worlds:
            if world.world_id == world_id:
                return world
        return WorldEvidence(world_id, (), 0, (), None, 0, 0)

    def canonical_bytes(self) -> bytes:
        payload = {
            "profile_id": self.profile_id,
            "active_world_id": self.active_world_id,
            "procedures": [asdict(item) for item in self.procedures],
            "skills": [asdict(item) for item in self.skills],
            "worlds": [asdict(item) for item in self.worlds],
            "answer_records": [asdict(item) for item in self.answer_records],
            "ambiguous_procedure_pairs": self.ambiguous_procedure_pairs,
            "events": [canonical_event_dict(item) for item in self.events],
        }
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


@dataclass(slots=True)
class _ProcedureAccumulator:
    questions: set[str]
    templates: set[str]
    confidences: list[str]
    event_ids: list[str]
    world_id: str | None
    skill_id: str | None
    last_ordinal: int
    kept_wrong_count: int


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def reduce_events(events: Iterable[LearningEvent]) -> LearnerState:
    ordered_events = tuple(sorted(events, key=lambda item: (item.ordinal, item.event_id)))
    profiles = {item.profile_id for item in ordered_events}
    if len(profiles) > 1:
        raise ValueError("one learner state cannot combine multiple profiles")

    seen_observations: dict[str, ObservationEvent] = {}
    next_seal_attempt: dict[str, int] = {}
    for item in ordered_events:
        if isinstance(item, ObservationEvent):
            if item.semantic_key in seen_observations:
                raise DuplicateSemanticEventError(
                    f"duplicate observation semantic key: {item.semantic_key}"
                )
            seen_observations[item.semantic_key] = item
        elif isinstance(item, SealTrialOutcomeEvent):
            expected = next_seal_attempt.get(item.world_id, 1)
            if item.attempt_number != expected:
                raise InvalidEventSequenceError(
                    f"Seal Trial attempt {item.attempt_number} must be {expected}"
                )
            next_seal_attempt[item.world_id] = expected + 1

    observations = tuple(
        item for item in ordered_events if isinstance(item, ObservationEvent)
    )
    observation_answers = tuple(
        AnswerRecord(
            event_id=item.event_id,
            world_id=item.world_id,
            battle_id=item.battle_id,
            batch_id=item.batch_id,
            item_id=item.item_id,
            question_id=item.question_id,
            first_option_id=item.first_option_id,
            final_option_id=item.final_option_id,
            first_confidence=item.first_confidence,
            final_confidence=item.final_confidence,
            first_correct=item.first_correct,
            final_correct=item.final_correct,
            explanations_shown=item.canonical_feedback
            + ((item.optional_wording_shown,) if item.optional_wording_shown else ()),
        )
        for item in observations
    )
    assisted_answers = tuple(
        AnswerRecord(
            event_id=item.event_id,
            world_id=item.world_id,
            battle_id=item.battle_id,
            batch_id=item.route_id,
            item_id=item.supported_item_ids[index],
            question_id=item.supported_question_ids[index],
            first_option_id=item.selected_option_ids[index],
            final_option_id=item.selected_option_ids[index],
            first_confidence=item.confidences[index],
            final_confidence=item.confidences[index],
            first_correct=item.correctness[index],
            final_correct=item.correctness[index],
            explanations_shown=item.canonical_feedback[index],
        )
        for item in ordered_events
        if isinstance(item, AssistedRouteCompletionEvent)
        for index in range(2)
    )
    answers = observation_answers + assisted_answers

    procedure_accumulators: dict[str, _ProcedureAccumulator] = {}
    ambiguous_pairs: list[tuple[str, str]] = []
    ambiguous_ids: set[str] = set()

    def record_wrong(
        item: ObservationEvent,
        procedure_id: str,
        confidence: str,
    ) -> None:
        accumulator = procedure_accumulators.setdefault(
            procedure_id,
            _ProcedureAccumulator(set(), set(), [], [], None, None, 0, 0),
        )
        accumulator.questions.add(item.question_id)
        accumulator.templates.add(item.template_id)
        accumulator.confidences.append(confidence)
        accumulator.event_ids.append(item.event_id)
        accumulator.world_id = item.world_id
        accumulator.skill_id = item.skill_id
        accumulator.last_ordinal = max(accumulator.last_ordinal, item.ordinal)

    for item in observations:
        if not item.first_correct and item.first_procedure_id:
            record_wrong(item, item.first_procedure_id, item.first_confidence)
        if (
            not item.final_correct
            and item.final_procedure_id
            and item.final_procedure_id != item.first_procedure_id
        ):
            record_wrong(item, item.final_procedure_id, item.final_confidence)
        if (
            not item.first_correct
            and not item.final_correct
            and item.first_procedure_id == item.final_procedure_id
            and item.first_procedure_id is not None
            and not item.choice_changed
        ):
            procedure_accumulators[item.first_procedure_id].kept_wrong_count += 1
        if (
            not item.first_correct
            and not item.final_correct
            and item.first_procedure_id
            and item.final_procedure_id
            and item.first_procedure_id != item.final_procedure_id
        ):
            pair = tuple(sorted((item.first_procedure_id, item.final_procedure_id)))
            if pair not in ambiguous_pairs:
                ambiguous_pairs.append(pair)
            ambiguous_ids.update(pair)

    procedures: list[ProcedureEvidence] = []
    for procedure_id, accumulator in procedure_accumulators.items():
        high_confidence = sum(
            confidence in {"certain", "leaning"}
            for confidence in accumulator.confidences
        )
        if len(accumulator.templates) >= 3 or (
            len(accumulator.templates) >= 2 and high_confidence >= 1
        ):
            status = "active"
        elif "certain" in accumulator.confidences or len(accumulator.questions) >= 2:
            status = "suspected"
        else:
            status = "candidate"

        targeted = [
            item
            for item in observations
            if item.ordinal > accumulator.last_ordinal
            and item.first_correct
            and item.is_transfer
            and procedure_id in item.targeted_procedure_ids
        ]
        targeted_questions = {item.question_id for item in targeted}
        targeted_batches = {item.batch_id for item in targeted}
        if len(targeted_questions) >= 3 and len(targeted_batches) >= 2:
            status = "resolved"

        priority = (
            len(accumulator.questions) * 10
            + high_confidence * 2
            + accumulator.kept_wrong_count * 5
            + (5 if status == "active" else 0)
        )
        procedures.append(ProcedureEvidence(
            procedure_id=procedure_id,
            status=status,
            world_id=accumulator.world_id,
            skill_id=accumulator.skill_id,
            distinct_question_count=len(accumulator.questions),
            distinct_template_count=len(accumulator.templates),
            priority=priority,
            ambiguous=procedure_id in ambiguous_ids,
            targeted_transfer_correct_count=len(targeted_questions),
            evidence_event_ids=_ordered_unique(accumulator.event_ids),
            last_ordinal=accumulator.last_ordinal,
        ))

    procedure_status = {item.procedure_id: item.status for item in procedures}
    ambiguous_pairs = [
        pair
        for pair in ambiguous_pairs
        if not all(procedure_status.get(procedure_id) == "resolved" for procedure_id in pair)
    ]
    pending_ambiguous_ids = {
        procedure_id for pair in ambiguous_pairs for procedure_id in pair
    }
    procedures = [
        replace(
            procedure,
            ambiguous=procedure.procedure_id in pending_ambiguous_ids,
        )
        for procedure in procedures
    ]

    grouped_skills: dict[tuple[str, str], list[ObservationEvent]] = defaultdict(list)
    for item in observations:
        grouped_skills[(item.world_id, item.skill_id)].append(item)

    skills: list[SkillEvidence] = []
    for (world_id, skill_id), items in grouped_skills.items():
        items.sort(key=lambda item: (item.ordinal, item.event_id))
        fragile_items = [
            item
            for item in items
            if (
            (item.first_correct and item.first_confidence == "guessing")
            or (not item.first_correct and item.final_correct)
            or (item.first_correct and not item.final_correct)
            )
        ]
        last_fragile_ordinal = max(
            (item.ordinal for item in fragile_items), default=0
        )
        qualifying_items = [
            item for item in items if item.ordinal > last_fragile_ordinal
        ] if fragile_items else items
        first_correct_by_question: dict[str, ObservationEvent] = {}
        for item in qualifying_items:
            if item.first_correct:
                first_correct_by_question[item.question_id] = item
        correct_items = tuple(first_correct_by_question.values())
        non_guessing = sum(
            item.first_confidence in {"certain", "leaning"} for item in correct_items
        )
        changed_context = sum(item.is_changed_context_transfer for item in correct_items)
        repeated_final_errors = Counter(
            item.final_procedure_id
            for item in correct_items
            if not item.final_correct and item.final_procedure_id
        )
        secure = (
            len(correct_items) >= 3
            and non_guessing >= 2
            and changed_context >= 1
            and not any(count >= 2 for count in repeated_final_errors.values())
        )

        transfer_by_question: dict[str, ObservationEvent] = {}
        for item in qualifying_items:
            if item.is_transfer:
                transfer_by_question[item.question_id] = item
        latest_six_transfers = tuple(
            sorted(
                transfer_by_question.values(),
                key=lambda item: (item.ordinal, item.event_id),
            )[-6:]
        )
        mastery = (
            len(latest_six_transfers) == 6
            and len({item.session_id for item in latest_six_transfers}) >= 2
            and sum(item.first_correct for item in latest_six_transfers) >= 5
        )
        if mastery:
            status = "mastery"
        elif secure:
            status = "secure"
        elif fragile_items:
            status = "fragile"
        else:
            status = "observed"
        skills.append(SkillEvidence(
            world_id=world_id,
            skill_id=skill_id,
            status=status,
            exposure_count=len({item.question_id for item in items}),
            first_pass_correct_count=len(correct_items),
            self_correction_count=sum(item.self_corrected for item in items),
            changed_context_transfer_count=sum(
                item.is_changed_context_transfer for item in items
            ),
            last_ordinal=max(item.ordinal for item in items),
        ))

    all_world_ids = _ordered_unique(item.world_id for item in ordered_events)
    worlds: list[WorldEvidence] = []
    for world_id in all_world_ids:
        world_observations = [item for item in observations if item.world_id == world_id]
        activations = sorted(
            (
                item
                for item in ordered_events
                if isinstance(item, WorldActivatedEvent) and item.world_id == world_id
            ),
            key=lambda item: (item.ordinal, item.event_id),
        )
        activation = activations[-1] if activations else None
        core_ids = activation.core_subskill_ids if activation else ()
        wins = _ordered_unique(
            item.battle_id
            for item in ordered_events
            if isinstance(item, BattleOutcomeEvent)
            and item.world_id == world_id
            and item.won
            and item.is_lead_in
        )
        world_events = [item for item in ordered_events if item.world_id == world_id]
        worlds.append(WorldEvidence(
            world_id=world_id,
            core_subskill_ids=core_ids,
            valid_item_count=sum(item.valid_for_progression for item in world_observations),
            lead_in_battle_wins=wins,
            curriculum_receipt=activation.curriculum_receipt if activation else None,
            activated_ordinal=activation.ordinal if activation else 0,
            last_ordinal=max((item.ordinal for item in world_events), default=0),
        ))

    activations = [
        item for item in ordered_events if isinstance(item, WorldActivatedEvent)
    ]
    active_world_id = None
    if activations:
        active_world_id = max(
            activations, key=lambda item: (item.ordinal, item.event_id)
        ).world_id

    return LearnerState(
        profile_id=next(iter(profiles), None),
        active_world_id=active_world_id,
        procedures=tuple(sorted(procedures, key=lambda item: item.procedure_id)),
        skills=tuple(sorted(skills, key=lambda item: (item.world_id or "", item.skill_id))),
        worlds=tuple(sorted(worlds, key=lambda item: item.world_id)),
        answer_records=answers,
        ambiguous_procedure_pairs=tuple(ambiguous_pairs),
        events=ordered_events,
    )
