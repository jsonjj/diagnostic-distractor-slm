"""Immutable, canonical events for learner evidence and progression replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, ClassVar, TypeAlias


LEGACY_EVENT_SCHEMA_VERSION = "wayline.event.v1"
EVENT_SCHEMA_VERSION = LEGACY_EVENT_SCHEMA_VERSION
OUTCOME_EVENT_SCHEMA_VERSION = "wayline.event.v2"
GENESIS_EVENT_HASH = "0" * 64
VALID_CONFIDENCE = frozenset({"certain", "leaning", "guessing"})
_SHA256_HEX = frozenset("0123456789abcdef")


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")


def _require_sha256(name: str, value: str) -> None:
    _require_text(name, value)
    if len(value) != 64 or any(character not in _SHA256_HEX for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class ProvenanceReceipts:
    generator: str
    model: str
    adapter: str
    gguf: str
    verifier: str
    registry: str
    cache: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            _require_text(name, value)


@dataclass(frozen=True, slots=True)
class EventBase:
    schema_version: str
    event_id: str
    idempotency_id: str
    ordinal: int
    profile_id: str
    session_id: str
    world_id: str
    battle_id: str
    occurred_at: str

    EVENT_TYPE: ClassVar[str] = "base"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        if self.schema_version not in self.SUPPORTED_SCHEMA_VERSIONS:
            expected = ", ".join(sorted(self.SUPPORTED_SCHEMA_VERSIONS))
            raise ValueError(f"schema_version must be one of: {expected}")
        for name in (
            "event_id",
            "idempotency_id",
            "profile_id",
            "session_id",
            "world_id",
            "battle_id",
            "occurred_at",
        ):
            _require_text(name, getattr(self, name))
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) or self.ordinal < 1:
            raise ValueError("ordinal must be a positive integer")

    @property
    def event_type(self) -> str:
        return self.EVENT_TYPE

    @property
    def semantic_key(self) -> str:
        """Stable replay identity independent of storage/request identifiers."""

        return f"{self.event_type}:{self.event_id}"


@dataclass(frozen=True, slots=True)
class ObservationEvent(EventBase):
    batch_id: str
    item_id: str
    question_id: str
    template_id: str
    content_version_id: str
    skill_id: str
    # Legacy provenance only. Campaign/gate authority comes exclusively from
    # WorldActivatedEvent and reducers must never treat this as a curriculum roster.
    world_core_subskill_ids: tuple[str, ...]
    operand_signature: str
    context_id: str
    first_option_id: str
    final_option_id: str
    first_confidence: str
    final_confidence: str
    first_correct: bool
    final_correct: bool
    choice_changed: bool
    self_corrected: bool
    first_procedure_id: str | None
    final_procedure_id: str | None
    targeted_procedure_ids: tuple[str, ...]
    is_transfer: bool
    is_changed_context_transfer: bool
    valid_for_progression: bool
    batch_wrong_count: int
    canonical_feedback: tuple[str, ...]
    optional_wording_shown: str | None
    receipts: ProvenanceReceipts

    EVENT_TYPE: ClassVar[str] = "observation"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {LEGACY_EVENT_SCHEMA_VERSION, EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(ObservationEvent, self).__post_init__()
        for name in (
            "batch_id",
            "item_id",
            "question_id",
            "template_id",
            "content_version_id",
            "skill_id",
            "operand_signature",
            "context_id",
            "first_option_id",
            "final_option_id",
        ):
            _require_text(name, getattr(self, name))
        if not self.world_core_subskill_ids:
            raise ValueError("world_core_subskill_ids cannot be empty")
        if len(set(self.world_core_subskill_ids)) != len(self.world_core_subskill_ids):
            raise ValueError("world_core_subskill_ids must be unique")
        for skill_id in self.world_core_subskill_ids:
            _require_text("world_core_subskill_id", skill_id)
        if self.first_confidence not in VALID_CONFIDENCE:
            raise ValueError("first_confidence is not supported")
        if self.final_confidence not in VALID_CONFIDENCE:
            raise ValueError("final_confidence is not supported")
        if self.choice_changed != (self.first_option_id != self.final_option_id):
            raise ValueError("choice_changed must match the opaque selections")
        if self.self_corrected != (not self.first_correct and self.final_correct):
            raise ValueError("self_corrected must mean wrong-to-correct")
        if self.first_correct and self.first_procedure_id is not None:
            raise ValueError("a correct first selection cannot carry a procedure")
        if not self.first_correct and self.first_procedure_id is None:
            raise ValueError("a wrong first selection requires a verified procedure")
        if self.final_correct and self.final_procedure_id is not None:
            raise ValueError("a correct final selection cannot carry a procedure")
        if not self.final_correct and self.final_procedure_id is None:
            raise ValueError("a wrong final selection requires a verified procedure")
        if len(set(self.targeted_procedure_ids)) != len(self.targeted_procedure_ids):
            raise ValueError("targeted_procedure_ids must be unique")
        if not isinstance(self.batch_wrong_count, int) or isinstance(self.batch_wrong_count, bool):
            raise ValueError("batch_wrong_count must be an integer")
        if not 0 <= self.batch_wrong_count <= 10:
            raise ValueError("batch_wrong_count must be between zero and ten")
        if not self.canonical_feedback:
            raise ValueError("canonical_feedback cannot be empty")
        for feedback in self.canonical_feedback:
            _require_text("canonical_feedback", feedback)
        if self.optional_wording_shown is not None:
            _require_text("optional_wording_shown", self.optional_wording_shown)

    @property
    def semantic_key(self) -> str:
        return f"observation:{self.batch_id}:{self.item_id}"


@dataclass(frozen=True, slots=True)
class WorldActivatedEvent(EventBase):
    """Server-owned campaign state and authoritative curriculum roster receipt."""

    core_subskill_ids: tuple[str, ...]
    curriculum_receipt: str

    EVENT_TYPE: ClassVar[str] = "world_activated"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {LEGACY_EVENT_SCHEMA_VERSION, EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(WorldActivatedEvent, self).__post_init__()
        if not self.core_subskill_ids:
            raise ValueError("core_subskill_ids cannot be empty")
        if len(set(self.core_subskill_ids)) != len(self.core_subskill_ids):
            raise ValueError("core_subskill_ids must be unique")
        for skill_id in self.core_subskill_ids:
            _require_text("core_subskill_id", skill_id)
        _require_text("curriculum_receipt", self.curriculum_receipt)

    @property
    def semantic_key(self) -> str:
        return f"world_activation:{self.world_id}"


@dataclass(frozen=True, slots=True)
class BattleOutcomeEvent(EventBase):
    won: bool
    is_lead_in: bool

    EVENT_TYPE: ClassVar[str] = "battle_outcome"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {LEGACY_EVENT_SCHEMA_VERSION, OUTCOME_EVENT_SCHEMA_VERSION}
    )


@dataclass(frozen=True, slots=True)
class BossOutcomeEvent(EventBase):
    combat_won: bool
    final_correct: int
    item_count: int
    is_campaign_finale: bool

    EVENT_TYPE: ClassVar[str] = "boss_outcome"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {LEGACY_EVENT_SCHEMA_VERSION, OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(BossOutcomeEvent, self).__post_init__()
        expected_count = 10 if self.is_campaign_finale else 8
        if self.item_count != expected_count:
            raise ValueError(f"boss item_count must be {expected_count}")
        if not 0 <= self.final_correct <= self.item_count:
            raise ValueError("final_correct must be bounded by item_count")


@dataclass(frozen=True, slots=True)
class SealTrialOutcomeEvent(EventBase):
    attempt_number: int
    passed: bool
    final_correct: int
    item_count: int

    EVENT_TYPE: ClassVar[str] = "seal_trial_outcome"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {LEGACY_EVENT_SCHEMA_VERSION, OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(SealTrialOutcomeEvent, self).__post_init__()
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        if self.item_count != 3:
            raise ValueError("Seal Trials always contain three items")
        if not 0 <= self.final_correct <= self.item_count:
            raise ValueError("final_correct must be bounded by item_count")
        if (
            self.schema_version == OUTCOME_EVENT_SCHEMA_VERSION
            and self.passed != (self.final_correct >= 2)
        ):
            raise ValueError("v2 Seal Trial pass must equal final_correct >= 2")

    @property
    def semantic_key(self) -> str:
        return f"seal_trial:{self.world_id}:{self.attempt_number}"


@dataclass(frozen=True, slots=True)
class BattleCompletionEvent(BattleOutcomeEvent):
    """Authoritative v2 completion of one normal authored battle target."""

    batch_id: str
    final_correct: int
    item_count: int

    EVENT_TYPE: ClassVar[str] = "battle_completion"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(BattleCompletionEvent, self).__post_init__()
        _require_text("batch_id", self.batch_id)
        if not self.won or not self.is_lead_in:
            raise ValueError("normal battle completion requires a lead-in victory")
        if not isinstance(self.item_count, int) or isinstance(self.item_count, bool):
            raise ValueError("item_count must be an integer")
        if not 3 <= self.item_count <= 5:
            raise ValueError("normal battle item_count must be between three and five")
        if (
            not isinstance(self.final_correct, int)
            or isinstance(self.final_correct, bool)
            or not 0 <= self.final_correct <= self.item_count
        ):
            raise ValueError("final_correct must be bounded by item_count")

    @property
    def semantic_key(self) -> str:
        return f"battle_completion:{self.world_id}:{self.battle_id}"


@dataclass(frozen=True, slots=True)
class BossCompletionEvent(BossOutcomeEvent):
    """Authoritative v2 completion of one boss combat plus its sealed quiz."""

    batch_id: str

    EVENT_TYPE: ClassVar[str] = "boss_completion"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(BossCompletionEvent, self).__post_init__()
        _require_text("batch_id", self.batch_id)

    @property
    def semantic_key(self) -> str:
        return f"boss_completion:{self.world_id}:{self.battle_id}"


@dataclass(frozen=True, slots=True)
class SealTrialCompletionEvent(SealTrialOutcomeEvent):
    """A v2 Seal Trial whose pass is derived from a sealed two-of-three result."""

    batch_id: str
    gate_recheck_sha256: str

    EVENT_TYPE: ClassVar[str] = "seal_trial_completion"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(SealTrialCompletionEvent, self).__post_init__()
        _require_text("batch_id", self.batch_id)
        _require_sha256("gate_recheck_sha256", self.gate_recheck_sha256)


@dataclass(frozen=True, slots=True)
class AssistedRouteCompletionEvent(EventBase):
    route_revision: str
    route_id: str
    material_sha256: str
    worked_example_item_id: str
    supported_item_ids: tuple[str, str]
    supported_question_ids: tuple[str, str]
    selected_option_ids: tuple[str, str]
    selected_answers: tuple[str, str]
    correct_option_ids: tuple[str, str]
    correct_answers: tuple[str, str]
    confidences: tuple[str, str]
    correctness: tuple[bool, bool]
    selected_procedure_ids: tuple[str | None, str | None]
    possible_errors: tuple[str | None, str | None]
    reliable_methods: tuple[str, str]
    trusted_steps: tuple[tuple[str, ...], tuple[str, ...]]
    canonical_feedback: tuple[tuple[str, ...], tuple[str, ...]]
    receipts: tuple[ProvenanceReceipts, ProvenanceReceipts]
    final_correct: int
    item_count: int

    EVENT_TYPE: ClassVar[str] = "assisted_route_completion"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(AssistedRouteCompletionEvent, self).__post_init__()
        if self.route_revision != "fresh-assisted-v1":
            raise ValueError("route_revision must be fresh-assisted-v1")
        _require_text("route_id", self.route_id)
        _require_sha256("material_sha256", self.material_sha256)
        _require_text("worked_example_item_id", self.worked_example_item_id)
        for name, values, unique in (
            ("supported_item_ids", self.supported_item_ids, True),
            ("supported_question_ids", self.supported_question_ids, True),
            ("selected_option_ids", self.selected_option_ids, False),
            ("selected_answers", self.selected_answers, False),
            ("correct_option_ids", self.correct_option_ids, False),
            ("correct_answers", self.correct_answers, False),
            ("confidences", self.confidences, False),
            ("correctness", self.correctness, False),
            ("selected_procedure_ids", self.selected_procedure_ids, False),
            ("possible_errors", self.possible_errors, False),
            ("reliable_methods", self.reliable_methods, False),
            ("trusted_steps", self.trusted_steps, False),
            ("canonical_feedback", self.canonical_feedback, False),
            ("receipts", self.receipts, False),
        ):
            if not isinstance(values, tuple) or len(values) != 2:
                raise ValueError(f"{name} must contain exactly two values")
            if unique and len(set(values)) != 2:
                raise ValueError(f"{name} must contain distinct values")
        if self.worked_example_item_id in self.supported_item_ids:
            raise ValueError("worked and supported item IDs must be distinct")
        for value in (
            *self.supported_item_ids,
            *self.supported_question_ids,
            *self.selected_option_ids,
            *self.selected_answers,
            *self.correct_option_ids,
            *self.correct_answers,
        ):
            _require_text("assisted route identity", value)
        if any(value not in VALID_CONFIDENCE for value in self.confidences):
            raise ValueError("assisted route confidence is not supported")
        if any(type(value) is not bool for value in self.correctness):
            raise ValueError("assisted route correctness must contain booleans")
        for index, (correct, procedure_id, possible_error) in enumerate(
            zip(
                self.correctness,
                self.selected_procedure_ids,
                self.possible_errors,
                strict=True,
            )
        ):
            if correct:
                if procedure_id is not None or possible_error is not None:
                    raise ValueError(
                        f"correct assisted item {index} cannot carry a procedure or error"
                    )
            else:
                if procedure_id is None or possible_error is None:
                    raise ValueError(
                        f"wrong assisted item {index} requires a procedure and error"
                    )
                _require_text("selected_procedure_id", procedure_id)
                _require_text("possible_error", possible_error)
        for index, (method, steps, feedback, possible_error, receipt) in enumerate(
            zip(
                self.reliable_methods,
                self.trusted_steps,
                self.canonical_feedback,
                self.possible_errors,
                self.receipts,
                strict=True,
            )
        ):
            _require_text("reliable_method", method)
            if not isinstance(steps, tuple) or not steps:
                raise ValueError("trusted_steps must contain two nonempty tuples")
            for step in steps:
                _require_text("trusted_step", step)
            expected_feedback = (
                *((possible_error,) if possible_error is not None else ()),
                method,
                *steps,
            )
            if feedback != expected_feedback:
                raise ValueError(
                    f"canonical_feedback {index} must derive from verified material"
                )
            if not isinstance(receipt, ProvenanceReceipts):
                raise ValueError("receipts must contain provenance receipts")
        if self.correctness != tuple(
            selected == correct
            for selected, correct in zip(
                self.selected_option_ids,
                self.correct_option_ids,
                strict=True,
            )
        ):
            raise ValueError("correctness must match the sealed option IDs")
        if type(self.item_count) is not int or self.item_count != 2:
            raise ValueError("assisted route contains exactly two supported MCQs")
        if type(self.final_correct) is not int or not 0 <= self.final_correct <= 2:
            raise ValueError("final_correct must be bounded by item_count")
        if self.final_correct != sum(self.correctness):
            raise ValueError("final_correct must match supported MCQ correctness")

    @property
    def semantic_key(self) -> str:
        return f"assisted_route_completion:{self.world_id}"


@dataclass(frozen=True, slots=True)
class SecondWindStartedEvent(EventBase):
    second_wind_id: str
    combat_attempt_id: str
    preparation_request_id: str
    quiz_battle_id: str

    EVENT_TYPE: ClassVar[str] = "second_wind_started"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(SecondWindStartedEvent, self).__post_init__()
        for name in (
            "second_wind_id",
            "combat_attempt_id",
            "preparation_request_id",
            "quiz_battle_id",
        ):
            _require_text(name, getattr(self, name))

    @property
    def semantic_key(self) -> str:
        return (
            f"second_wind_started:{self.world_id}:{self.battle_id}:"
            f"{self.combat_attempt_id}"
        )


@dataclass(frozen=True, slots=True)
class SecondWindQuizCompletionEvent(EventBase):
    second_wind_id: str
    combat_attempt_id: str
    batch_id: str
    final_correct: int
    item_count: int
    revive_health_percent: int
    shield_percent: int

    EVENT_TYPE: ClassVar[str] = "second_wind_quiz_completion"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(SecondWindQuizCompletionEvent, self).__post_init__()
        for name in ("second_wind_id", "combat_attempt_id", "batch_id"):
            _require_text(name, getattr(self, name))
        if self.item_count != 3:
            raise ValueError("Second Wind always contains three MCQs")
        if not 0 <= self.final_correct <= self.item_count:
            raise ValueError("final_correct must be bounded by item_count")
        if self.revive_health_percent != 35:
            raise ValueError("Second Wind revive health must be 35 percent")
        if self.shield_percent != min(self.final_correct * 5, 15):
            raise ValueError("Second Wind shield must be 5 percent per correct, capped at 15")

    @property
    def semantic_key(self) -> str:
        return f"second_wind_quiz_completion:{self.second_wind_id}"


@dataclass(frozen=True, slots=True)
class SecondWindCombatOutcomeEvent(BattleOutcomeEvent):
    second_wind_id: str
    combat_attempt_id: str
    batch_id: str
    quiz_final_correct: int
    quiz_item_count: int

    EVENT_TYPE: ClassVar[str] = "second_wind_combat_outcome"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(SecondWindCombatOutcomeEvent, self).__post_init__()
        for name in ("second_wind_id", "combat_attempt_id", "batch_id"):
            _require_text(name, getattr(self, name))
        if self.quiz_item_count != 3:
            raise ValueError("Second Wind always contains three MCQs")
        if not 0 <= self.quiz_final_correct <= self.quiz_item_count:
            raise ValueError("quiz_final_correct must be bounded by quiz_item_count")

    @property
    def semantic_key(self) -> str:
        return f"second_wind_combat_outcome:{self.second_wind_id}"


@dataclass(frozen=True, slots=True)
class WorldProgressionActivatedEvent(WorldActivatedEvent):
    """A v2 post-clear activation linked to its completed predecessor world."""

    completed_world_id: str

    EVENT_TYPE: ClassVar[str] = "world_progression_activated"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )

    def __post_init__(self) -> None:
        super(WorldProgressionActivatedEvent, self).__post_init__()
        _require_text("completed_world_id", self.completed_world_id)
        if self.completed_world_id == self.world_id:
            raise ValueError("world activation must advance to a different world")


LearningEvent: TypeAlias = (
    ObservationEvent
    | WorldActivatedEvent
    | BattleOutcomeEvent
    | BossOutcomeEvent
    | SealTrialOutcomeEvent
    | BattleCompletionEvent
    | BossCompletionEvent
    | SealTrialCompletionEvent
    | AssistedRouteCompletionEvent
    | SecondWindStartedEvent
    | SecondWindQuizCompletionEvent
    | SecondWindCombatOutcomeEvent
    | WorldProgressionActivatedEvent
)

_EVENT_CLASSES: dict[str, type[EventBase]] = {
    cls.EVENT_TYPE: cls
    for cls in (
        ObservationEvent,
        WorldActivatedEvent,
        BattleOutcomeEvent,
        BossOutcomeEvent,
        SealTrialOutcomeEvent,
        BattleCompletionEvent,
        BossCompletionEvent,
        SealTrialCompletionEvent,
        AssistedRouteCompletionEvent,
        SecondWindStartedEvent,
        SecondWindQuizCompletionEvent,
        SecondWindCombatOutcomeEvent,
        WorldProgressionActivatedEvent,
    )
}


def canonical_event_dict(event: LearningEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload["event_type"] = event.event_type
    return payload


def canonical_event_json(event: LearningEvent) -> str:
    return json.dumps(
        canonical_event_dict(event),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_event_hash(previous_event_hash: str, event: LearningEvent) -> str:
    if len(previous_event_hash) != 64:
        raise ValueError("previous_event_hash must be a SHA-256 hex digest")
    material = (previous_event_hash + canonical_event_json(event)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def event_from_dict(payload: dict[str, Any]) -> LearningEvent:
    values = dict(payload)
    event_type = values.pop("event_type", None)
    event_class = _EVENT_CLASSES.get(event_type)
    if event_class is None:
        raise ValueError(f"unknown event_type: {event_type!r}")
    if event_class is ObservationEvent:
        values["world_core_subskill_ids"] = tuple(values["world_core_subskill_ids"])
        values["targeted_procedure_ids"] = tuple(values["targeted_procedure_ids"])
        values["canonical_feedback"] = tuple(values["canonical_feedback"])
        values["receipts"] = ProvenanceReceipts(**values["receipts"])
    elif issubclass(event_class, WorldActivatedEvent):
        values["core_subskill_ids"] = tuple(values["core_subskill_ids"])
    elif event_class is AssistedRouteCompletionEvent:
        for name in (
            "supported_item_ids",
            "supported_question_ids",
            "selected_option_ids",
            "selected_answers",
            "correct_option_ids",
            "correct_answers",
            "confidences",
            "correctness",
            "selected_procedure_ids",
            "possible_errors",
            "reliable_methods",
        ):
            if type(values[name]) is not list:
                raise ValueError(f"{name} must be a JSON array")
            values[name] = tuple(values[name])
        if type(values["trusted_steps"]) is not list or any(
            type(steps) is not list for steps in values["trusted_steps"]
        ):
            raise ValueError("trusted_steps must contain JSON arrays")
        values["trusted_steps"] = tuple(
            tuple(steps) for steps in values["trusted_steps"]
        )
        if type(values["canonical_feedback"]) is not list or any(
            type(feedback) is not list for feedback in values["canonical_feedback"]
        ):
            raise ValueError("canonical_feedback must contain JSON arrays")
        values["canonical_feedback"] = tuple(
            tuple(feedback) for feedback in values["canonical_feedback"]
        )
        if type(values["receipts"]) is not list or any(
            type(receipt) is not dict for receipt in values["receipts"]
        ):
            raise ValueError("receipts must contain JSON objects")
        values["receipts"] = tuple(
            ProvenanceReceipts(**receipt) for receipt in values["receipts"]
        )
    return event_class(**values)  # type: ignore[return-value]


def event_from_json(payload: str) -> LearningEvent:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("canonical event JSON must contain one object")
    return event_from_dict(parsed)


def is_legacy_outcome_event(event: LearningEvent) -> bool:
    """Return whether an inspectable event predates outcome schema v2."""

    return isinstance(
        event,
        (BattleOutcomeEvent, BossOutcomeEvent, SealTrialOutcomeEvent),
    ) and event.schema_version == LEGACY_EVENT_SCHEMA_VERSION
