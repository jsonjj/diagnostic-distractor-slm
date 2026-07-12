"""Strict public contracts shared by Unity and the Wayline Forge sidecar."""

from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Annotated, Literal, Self, TypeVar
import unicodedata

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


Identifier = Annotated[
    str,
    Field(
        min_length=3,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$",
    ),
]
DisplayText = Annotated[str, Field(min_length=1, max_length=256)]
FeedbackText = Annotated[str, Field(min_length=1, max_length=512)]
TrustedStep = Annotated[str, Field(min_length=1, max_length=512)]
CampaignCatalogSha256 = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{64}$"),
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

PROFILE_EXPORT_CAMPAIGN_CATALOG_SHA256 = (
    "5509097676eccc6c3848bfb64295ac931c73621a1120b9431af0ccc8e793d513"
)
PROFILE_EXPORT_GENESIS_EVENT_HASH = "0" * 64
PROFILE_EXPORT_MAX_CANONICAL_EVENT_BYTES = 32_768
_PROFILE_EXPORT_WORLD_ORDINALS = {
    world_id: ordinal
    for ordinal, world_id in enumerate(
        (
            "valuehold",
            "decimara",
            "fracture_isles",
            "roundglass",
            "reciprocal_deep",
            "hundredfold",
            "minus_meridian",
            "factor_forge",
            "order_spire",
        ),
        start=1,
    )
}


def _parse_canonical_utc_timestamp(value: str) -> datetime:
    timestamp_format = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
    try:
        return datetime.strptime(value, timestamp_format)
    except ValueError as error:
        raise ValueError("timestamp must be a real canonical UTC value") from error


def _validate_canonical_utc_timestamp(value: str) -> str:
    _parse_canonical_utc_timestamp(value)
    return value


CanonicalUtcTimestamp = Annotated[
    str,
    Field(
        pattern=(
            r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
            r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{6})?Z$"
        ),
    ),
    AfterValidator(_validate_canonical_utc_timestamp),
]


def normalize_public_display(value: str) -> str:
    """Apply the frozen cross-runtime display normalization contract."""

    normalized = unicodedata.normalize("NFKC", value)
    whitespace_collapsed_and_trimmed = " ".join(normalized.split())
    return whitespace_collapsed_and_trimmed.casefold()


class StrictModel(BaseModel):
    """Base for closed, immutable, strict public payloads."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        frozen=True,
    )


class DuplicateJsonKeyError(ValueError):
    """Raised before validation when public JSON repeats an object key."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"duplicate JSON key: {key}")


PublicContract = TypeVar("PublicContract", bound=StrictModel)


def parse_public_json(
    model_type: type[PublicContract],
    payload: str | bytes | bytearray,
) -> PublicContract:
    """Decode duplicate-free standard JSON, then validate a public contract."""

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        decoded: dict[str, object] = {}
        for key, value in pairs:
            if key in decoded:
                raise DuplicateJsonKeyError(key)
            decoded[key] = value
        return decoded

    def reject_nonstandard_number(value: str) -> object:
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    decoded = json.loads(
        payload,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_nonstandard_number,
    )
    return model_type.model_validate(decoded)


class PublicErrorCode(str, Enum):
    AUTHORIZATION_REQUIRED = "authorization_required"
    BATCH_UNAVAILABLE = "batch_unavailable"
    BODY_TOO_LARGE = "body_too_large"
    BOSS_GATE_LOCKED = "boss_gate_locked"
    CATALOG_CONFLICT = "catalog_conflict"
    CONTENT_TYPE_UNSUPPORTED = "content_type_unsupported"
    CONTRACT_INVALID = "contract_invalid"
    EVIDENCE_SYNC_UNAVAILABLE = "evidence_sync_unavailable"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INTEGRITY_FAILURE = "integrity_failure"
    INVALID_SUBMISSION = "invalid_submission"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    ORIGIN_FORBIDDEN = "origin_forbidden"
    PROFILE_NOT_FOUND = "profile_not_found"
    QUIZ_IN_PROGRESS = "quiz_in_progress"
    QUIZ_STATE_CONFLICT = "quiz_state_conflict"
    REQUEST_MALFORMED = "request_malformed"
    ROUTE_NOT_FOUND = "route_not_found"
    RUNTIME_STATE_UNAVAILABLE = "runtime_state_unavailable"
    SAFE_CONTENT_UNAVAILABLE = "safe_content_unavailable"
    SESSION_NOT_CURRENT = "session_not_current"
    SNAPSHOT_NOT_READY = "snapshot_not_ready"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"
    STORAGE_BUSY = "storage_busy"


class PublicError(StrictModel):
    schema_version: Literal["wayline.error.v1"] = Field(alias="schemaVersion")
    code: PublicErrorCode = Field(strict=False)

    @field_validator("code", mode="before")
    @classmethod
    def code_is_a_public_string(cls, value: object) -> object:
        if isinstance(value, PublicErrorCode) or type(value) is str:
            return value
        raise ValueError("code must be a string")


class Confidence(str, Enum):
    CERTAIN = "certain"
    LEANING = "leaning"
    GUESSING = "guessing"


class BattleTier(str, Enum):
    ROUTE_1 = "route_1"
    ROUTE_2 = "route_2"
    ROUTE_3 = "route_3"
    ELITE = "elite"
    WORLD_BOSS = "world_boss"
    CAMPAIGN_FINALE = "campaign_finale"
    SEAL_TRIAL = "seal_trial"


class GateRequirement(str, Enum):
    LEAD_IN_WINS = "lead_in_wins"
    VALID_WORLD_ITEMS = "valid_world_items"
    LATEST_TEN_ACCURACY = "latest_ten_accuracy"
    CORE_SUBSKILL_COVERAGE = "core_subskill_coverage"


class SessionCreate(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    profile_id: Identifier = Field(alias="profileId")
    client_build: Identifier = Field(alias="clientBuild")


class ProfileCreate(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")


class ProfileCreated(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    profile_id: Identifier = Field(alias="profileId")
    created_at_utc: CanonicalUtcTimestamp = Field(alias="createdAtUtc")


class SessionCreated(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    profile_id: Identifier = Field(alias="profileId")
    session_id: Identifier = Field(alias="sessionId")
    created_at_utc: CanonicalUtcTimestamp = Field(alias="createdAtUtc")
    active_world_id: Identifier = Field(alias="activeWorldId")
    campaign_catalog_sha256: CampaignCatalogSha256 = Field(
        alias="campaignCatalogSha256"
    )


class RuntimeState(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    profile_id: Identifier = Field(alias="profileId")
    session_id: Identifier = Field(alias="sessionId")
    active_world_id: Identifier = Field(alias="activeWorldId")
    campaign_ordinal: int = Field(alias="campaignOrdinal", ge=1)
    resumable_batch_id: Identifier | None = Field(alias="resumableBatchId")
    campaign_catalog_sha256: CampaignCatalogSha256 = Field(
        alias="campaignCatalogSha256"
    )


class BattleQuizRequest(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    session_id: Identifier = Field(alias="sessionId")
    battle_id: Identifier = Field(alias="battleId")
    world_id: Identifier = Field(alias="worldId")
    battle_tier: BattleTier = Field(alias="battleTier", strict=False)


class SealTrialPrepare(StrictModel):
    """Request a server-authorized Seal Trial for the path-owned world."""

    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    session_id: Identifier = Field(alias="sessionId")


class PublicOption(StrictModel):
    option_id: Identifier = Field(alias="optionId")
    display_text: DisplayText = Field(alias="displayText")


class PublicQuizItem(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    prompt: Annotated[str, Field(min_length=1, max_length=1_000)]
    options: Annotated[
        tuple[PublicOption, ...],
        Field(strict=False),
    ]

    @model_validator(mode="after")
    def options_are_unique(self) -> Self:
        if len(self.options) != 4:
            raise ValueError("options must contain exactly 4 options")
        option_ids = tuple(option.option_id for option in self.options)
        if len(set(option_ids)) != len(option_ids):
            raise ValueError("optionId must be unique within an item")
        display_values = tuple(
            normalize_public_display(option.display_text)
            for option in self.options
        )
        if len(set(display_values)) != len(display_values):
            raise ValueError("displayText must be unique within an item")
        return self


class PublicQuizBatch(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    batch_id: Identifier = Field(alias="batchId")
    item_count: int = Field(alias="itemCount", ge=3, le=10)
    items: Annotated[
        tuple[PublicQuizItem, ...],
        Field(strict=False),
    ]

    @model_validator(mode="after")
    def item_count_and_ids_are_consistent(self) -> Self:
        if not 3 <= len(self.items) <= 10:
            raise ValueError("items must contain between 3 and 10 items")
        if self.item_count != len(self.items):
            raise ValueError("itemCount must equal the number of items")
        item_ids = tuple(item.item_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("itemId must be unique within a batch")
        return self


class SealTrialPrepared(StrictModel):
    """One authoritative three-item Seal Trial and its attempt identity."""

    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    world_id: Identifier = Field(alias="worldId")
    attempt_number: int = Field(alias="attemptNumber", ge=1)
    battle_id: Identifier = Field(alias="battleId")
    batch: PublicQuizBatch

    @model_validator(mode="after")
    def attempt_and_batch_are_consistent(self) -> Self:
        expected_battle_id = f"{self.world_id}_seal_trial_{self.attempt_number}"
        if self.battle_id != expected_battle_id:
            raise ValueError("battleId must match the Seal Trial attempt")
        if self.batch.item_count != 3:
            raise ValueError("Seal Trials must contain exactly three items")
        return self


class AssistedRoutePrepare(StrictModel):
    """Request a fresh assisted route for the path-owned world."""

    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    session_id: Identifier = Field(alias="sessionId")


class AssistedWorkedExample(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    prompt: Annotated[str, Field(min_length=1, max_length=1_000)]
    correct_answer: DisplayText = Field(alias="correctAnswer")
    trusted_steps: Annotated[
        tuple[TrustedStep, ...],
        Field(alias="trustedSteps", min_length=1, max_length=8, strict=False),
    ]
    reliable_method: FeedbackText = Field(alias="reliableMethod")


class AssistedSupportedItem(StrictModel):
    """One keyless supported MCQ exposed before assisted completion."""

    item_id: Identifier = Field(alias="itemId")
    prompt: Annotated[str, Field(min_length=1, max_length=1_000)]
    options: Annotated[
        tuple[PublicOption, PublicOption, PublicOption, PublicOption],
        Field(strict=False),
    ]

    @model_validator(mode="after")
    def options_are_distinct(self) -> Self:
        option_ids = tuple(option.option_id for option in self.options)
        displays = tuple(
            normalize_public_display(option.display_text)
            for option in self.options
        )
        if len(set(option_ids)) != 4 or len(set(displays)) != 4:
            raise ValueError("supported options must have distinct IDs and displays")
        return self


class AssistedRouteBatch(StrictModel):
    route_id: Identifier = Field(alias="routeId")
    world_id: Identifier = Field(alias="worldId")
    worked_example: AssistedWorkedExample = Field(alias="workedExample")
    items: Annotated[
        tuple[AssistedSupportedItem, AssistedSupportedItem],
        Field(strict=False),
    ]

    @model_validator(mode="after")
    def item_ids_are_distinct(self) -> Self:
        item_ids = (
            self.worked_example.item_id,
            *(item.item_id for item in self.items),
        )
        if len(set(item_ids)) != 3:
            raise ValueError("all assisted item IDs must be distinct")
        return self


class AssistedRoutePrepared(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    world_id: Identifier = Field(alias="worldId")
    batch: AssistedRouteBatch

    @model_validator(mode="after")
    def world_matches(self) -> Self:
        if self.batch.world_id != self.world_id:
            raise ValueError("batch.worldId must match worldId")
        return self


class AssistedSelection(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    option_id: Identifier = Field(alias="optionId")
    confidence: Confidence = Field(strict=False)


class AssistedRouteComplete(StrictModel):
    """One-shot completion command for the path-owned assisted route."""

    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    session_id: Identifier = Field(alias="sessionId")
    selections: Annotated[
        tuple[AssistedSelection, AssistedSelection],
        Field(strict=False),
    ]

    @model_validator(mode="after")
    def item_ids_are_distinct(self) -> Self:
        if self.selections[0].item_id == self.selections[1].item_id:
            raise ValueError("supported selections must target distinct items")
        return self


class AssistedItemResult(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    selected_option_id: Identifier = Field(alias="selectedOptionId")
    selected_answer: DisplayText = Field(alias="selectedAnswer")
    confidence: Confidence = Field(strict=False)
    correct_option_id: Identifier = Field(alias="correctOptionId")
    correct_answer: DisplayText = Field(alias="correctAnswer")
    is_correct: bool = Field(alias="isCorrect")
    possible_error: FeedbackText | None = Field(alias="possibleError")
    reliable_method: FeedbackText = Field(alias="reliableMethod")
    trusted_steps: Annotated[
        tuple[TrustedStep, ...],
        Field(alias="trustedSteps", min_length=1, max_length=8, strict=False),
    ]
    canonical_feedback: Annotated[
        tuple[FeedbackText, ...],
        Field(alias="canonicalFeedback", min_length=2, max_length=10, strict=False),
    ]

    @model_validator(mode="after")
    def reveal_is_consistent(self) -> Self:
        expected_correct = self.selected_option_id == self.correct_option_id
        if self.is_correct != expected_correct:
            raise ValueError("isCorrect must match correctOptionId")
        if expected_correct and self.possible_error is not None:
            raise ValueError("a correct assisted answer cannot have possibleError")
        if not expected_correct and self.possible_error is None:
            raise ValueError("an incorrect assisted answer requires possibleError")
        expected_feedback = (
            *((self.possible_error,) if self.possible_error is not None else ()),
            self.reliable_method,
            *self.trusted_steps,
        )
        if self.canonical_feedback != expected_feedback:
            raise ValueError("canonicalFeedback must match canonical explanation order")
        return self


class AssistedRouteCompleted(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    world_id: Identifier = Field(alias="worldId")
    route_id: Identifier = Field(alias="routeId")
    worked_example_count: Literal[1] = Field(alias="workedExampleCount")
    supported_mcq_count: Literal[2] = Field(alias="supportedMcqCount")
    final_correct: int = Field(alias="finalCorrect", ge=0, le=2)
    world_cleared: Literal[True] = Field(alias="worldCleared")
    items: Annotated[
        tuple[AssistedItemResult, AssistedItemResult],
        Field(strict=False),
    ]

    @model_validator(mode="after")
    def aggregate_is_truthful(self) -> Self:
        item_ids = tuple(item.item_id for item in self.items)
        if len(set(item_ids)) != 2:
            raise ValueError("assisted result item IDs must be distinct")
        if self.final_correct != sum(item.is_correct for item in self.items):
            raise ValueError("finalCorrect must match item results")
        return self


class _ProgressionCommand(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    session_id: Identifier = Field(alias="sessionId")


class BattleComplete(_ProgressionCommand):
    combat_won: bool = Field(alias="combatWon")


class BattleCompleted(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    world_id: Identifier = Field(alias="worldId")
    battle_id: Identifier = Field(alias="battleId")
    batch_id: Identifier = Field(alias="batchId")
    final_correct: int = Field(alias="finalCorrect", ge=0, le=10)
    item_count: int = Field(alias="itemCount", ge=3, le=10)
    boss_battle: bool = Field(alias="bossBattle")
    world_cleared: bool = Field(alias="worldCleared")
    seal_trial_required: bool = Field(alias="sealTrialRequired")

    @model_validator(mode="after")
    def aggregate_and_outcome_are_consistent(self) -> Self:
        if self.final_correct > self.item_count:
            raise ValueError("finalCorrect cannot exceed itemCount")
        if (self.world_cleared or self.seal_trial_required) and not self.boss_battle:
            raise ValueError("only a boss battle can resolve world clearance")
        if self.world_cleared and self.seal_trial_required:
            raise ValueError("a cleared world cannot require a Seal Trial")
        return self


class SealTrialComplete(_ProgressionCommand):
    """Complete the path-owned Seal Trial batch after its final reveal."""


class SealTrialCompleted(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    world_id: Identifier = Field(alias="worldId")
    attempt_number: int = Field(alias="attemptNumber", ge=1)
    batch_id: Identifier = Field(alias="batchId")
    final_correct: int = Field(alias="finalCorrect", ge=0, le=3)
    item_count: Literal[3] = Field(alias="itemCount")
    passed: bool
    world_cleared: bool = Field(alias="worldCleared")
    assisted_route_unlocked: bool = Field(alias="assistedRouteUnlocked")

    @model_validator(mode="after")
    def outcome_is_truthful(self) -> Self:
        if self.passed != (self.final_correct >= 2):
            raise ValueError("passed must equal finalCorrect >= 2")
        if self.world_cleared != self.passed:
            raise ValueError("worldCleared must equal passed")
        if self.assisted_route_unlocked and self.passed:
            raise ValueError("a passed Seal Trial cannot unlock the assisted route")
        return self


class SecondWindStart(_ProgressionCommand):
    preparation_request_id: Identifier = Field(alias="preparationRequestId")


class SecondWindStarted(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    second_wind_id: Identifier = Field(alias="secondWindId")
    world_id: Identifier = Field(alias="worldId")
    battle_id: Identifier = Field(alias="battleId")
    combat_attempt_id: Identifier = Field(alias="combatAttemptId")
    quiz_battle_id: Identifier = Field(alias="quizBattleId")
    batch: PublicQuizBatch

    @model_validator(mode="after")
    def identities_and_batch_are_consistent(self) -> Self:
        if (
            self.second_wind_id != f"second-wind-{self.combat_attempt_id}"
            or self.quiz_battle_id != f"{self.battle_id}_second_wind"
        ):
            raise ValueError("Second Wind identities must be derived from combat")
        if self.batch.item_count != 3:
            raise ValueError("Second Wind quizzes must contain exactly three items")
        return self


class SecondWindComplete(_ProgressionCommand):
    """Complete the path-owned Second Wind quiz batch."""


class SecondWindCompleted(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    second_wind_id: Identifier = Field(alias="secondWindId")
    batch_id: Identifier = Field(alias="batchId")
    final_correct: int = Field(alias="finalCorrect", ge=0, le=3)
    item_count: Literal[3] = Field(alias="itemCount")
    revive_health_percent: Literal[35] = Field(alias="reviveHealthPercent")
    shield_percent: int = Field(alias="shieldPercent", ge=0, le=15)
    revived_combat_pending: Literal[True] = Field(alias="revivedCombatPending")

    @model_validator(mode="after")
    def shield_matches_quiz_result(self) -> Self:
        if self.shield_percent != min(self.final_correct * 5, 15):
            raise ValueError("shieldPercent must match finalCorrect")
        return self


class RevivedCombatComplete(_ProgressionCommand):
    combat_won: bool = Field(alias="combatWon")


class RevivedCombatCompleted(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    second_wind_id: Identifier = Field(alias="secondWindId")
    combat_attempt_id: Identifier = Field(alias="combatAttemptId")
    combat_won: bool = Field(alias="combatWon")
    battle_completed: bool = Field(alias="battleCompleted")
    second_wind_closed: Literal[True] = Field(alias="secondWindClosed")

    @model_validator(mode="after")
    def completion_matches_combat(self) -> Self:
        if self.battle_completed != self.combat_won:
            raise ValueError("battleCompleted must equal combatWon")
        return self


class WorldActivate(_ProgressionCommand):
    """Activate the successor worlds named and bound by the request path."""


class WorldActivated(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    completed_world_id: Identifier = Field(alias="completedWorldId")
    active_world_id: Identifier = Field(alias="activeWorldId")
    campaign_sequence: int = Field(alias="campaignSequence", ge=2, le=9)

    @model_validator(mode="after")
    def successor_differs_from_completed_world(self) -> Self:
        if self.active_world_id == self.completed_world_id:
            raise ValueError("activeWorldId must differ from completedWorldId")
        return self


class AnswerSelection(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    option_id: Identifier = Field(alias="optionId")
    confidence: Confidence = Field(strict=False)


class _Submission(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    batch_id: Identifier = Field(alias="batchId")
    item_count: int = Field(alias="itemCount", ge=3, le=10)
    selections: Annotated[
        tuple[AnswerSelection, ...],
        Field(strict=False),
    ]

    @model_validator(mode="after")
    def selections_are_complete_and_unique(self) -> Self:
        if not 3 <= len(self.selections) <= 10:
            raise ValueError("selections must contain between 3 and 10 answers")
        if self.item_count != len(self.selections):
            raise ValueError("itemCount must equal the number of selections")
        item_ids = tuple(selection.item_id for selection in self.selections)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("each itemId must be selected exactly once")
        return self


class InitialSubmission(_Submission):
    """One complete, immutable first-pass submission."""


class RevisionSubmission(_Submission):
    """The only complete review-pass submission accepted for a batch."""


class WrongCountResult(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    batch_id: Identifier = Field(alias="batchId")
    item_count: int = Field(alias="itemCount", ge=3, le=10)
    wrong_count: int = Field(alias="wrongCount", ge=0, le=10)
    revision_required: bool = Field(alias="revisionRequired")

    @model_validator(mode="after")
    def count_and_revision_are_truthful(self) -> Self:
        if self.wrong_count > self.item_count:
            raise ValueError("wrongCount cannot exceed itemCount")
        if self.revision_required != (self.wrong_count > 0):
            raise ValueError("revisionRequired must equal wrongCount > 0")
        return self


class RevealedSelection(StrictModel):
    option_id: Identifier = Field(alias="optionId")
    confidence: Confidence = Field(strict=False)
    is_correct: bool = Field(alias="isCorrect")


class FinalItemResult(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    first_selection: RevealedSelection = Field(alias="firstSelection")
    final_selection: RevealedSelection = Field(alias="finalSelection")
    correct_option_id: Identifier = Field(alias="correctOptionId")
    correct_answer: DisplayText = Field(alias="correctAnswer")
    trusted_steps: Annotated[
        tuple[TrustedStep, ...],
        Field(alias="trustedSteps", min_length=1, max_length=8, strict=False),
    ]
    possible_error: FeedbackText | None = Field(alias="possibleError")
    reliable_method: FeedbackText = Field(alias="reliableMethod")
    self_corrected: bool = Field(alias="selfCorrected")

    @model_validator(mode="after")
    def correctness_flags_match_the_revealed_key(self) -> Self:
        first_is_correct = self.first_selection.option_id == self.correct_option_id
        final_is_correct = self.final_selection.option_id == self.correct_option_id
        if self.first_selection.is_correct != first_is_correct:
            raise ValueError("firstSelection.isCorrect must match correctOptionId")
        if self.final_selection.is_correct != final_is_correct:
            raise ValueError("finalSelection.isCorrect must match correctOptionId")
        if self.self_corrected != (not first_is_correct and final_is_correct):
            raise ValueError("selfCorrected must represent wrong-to-correct revision")
        return self


class FinalQuizResult(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    batch_id: Identifier = Field(alias="batchId")
    item_count: int = Field(alias="itemCount", ge=3, le=10)
    first_pass_wrong_count: int = Field(alias="firstPassWrongCount", ge=0, le=10)
    final_correct_count: int = Field(alias="finalCorrectCount", ge=0, le=10)
    revision_used: bool = Field(alias="revisionUsed")
    items: Annotated[
        tuple[FinalItemResult, ...],
        Field(strict=False),
    ]

    @model_validator(mode="after")
    def aggregate_counts_match_items(self) -> Self:
        if not 3 <= len(self.items) <= 10:
            raise ValueError("items must contain between 3 and 10 final results")
        if self.item_count != len(self.items):
            raise ValueError("itemCount must equal the number of item results")
        item_ids = tuple(item.item_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("itemId must be unique within final results")
        wrong_count = sum(not item.first_selection.is_correct for item in self.items)
        final_correct = sum(item.final_selection.is_correct for item in self.items)
        if self.first_pass_wrong_count != wrong_count:
            raise ValueError("firstPassWrongCount must match item results")
        if self.final_correct_count != final_correct:
            raise ValueError("finalCorrectCount must match item results")
        if self.revision_used != (self.first_pass_wrong_count > 0):
            raise ValueError("revisionUsed must equal firstPassWrongCount > 0")
        if not self.revision_used:
            for item in self.items:
                if item.first_selection != item.final_selection:
                    raise ValueError("finalSelection must equal firstSelection when revision is skipped")
        return self


class InitialSubmissionResult(WrongCountResult):
    """First-pass response with an immediate reveal only when nothing is wrong."""

    final_result: FinalQuizResult | None = Field(alias="finalResult")

    @model_validator(mode="after")
    def reveal_is_present_exactly_for_zero_wrong(self) -> Self:
        if (self.final_result is not None) != (self.wrong_count == 0):
            raise ValueError(
                "finalResult must be present exactly when wrongCount is zero"
            )
        if self.final_result is not None and (
            self.final_result.batch_id != self.batch_id
            or self.final_result.item_count != self.item_count
            or self.final_result.first_pass_wrong_count != 0
            or self.final_result.revision_used
        ):
            raise ValueError("finalResult must match the zero-wrong first pass")
        return self


class QuizSnapshotState(str, Enum):
    READY = "ready"
    INITIAL_LOCKED = "initial_locked"
    REVISION_OPEN = "revision_open"
    REVEALED = "revealed"
    CLOSED = "closed"


class QuizSnapshot(StrictModel):
    """Immutable learner-facing reload state for one persisted quiz."""

    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    batch_id: Identifier = Field(alias="batchId")
    quiz_state: QuizSnapshotState = Field(alias="quizState", strict=False)
    state_version: int = Field(alias="stateVersion", ge=1)
    public_batch: PublicQuizBatch = Field(alias="publicBatch")
    initial_submission: InitialSubmission | None = Field(
        alias="initialSubmission"
    )
    initial_result: InitialSubmissionResult | None = Field(alias="initialResult")
    revision_submission: RevisionSubmission | None = Field(
        alias="revisionSubmission"
    )
    final_result: FinalQuizResult | None = Field(alias="finalResult")

    @model_validator(mode="after")
    def state_and_public_records_are_consistent(self) -> Self:
        if self.public_batch.batch_id != self.batch_id:
            raise ValueError("publicBatch.batchId must match batchId")

        layouts = {
            item.item_id: {option.option_id for option in item.options}
            for item in self.public_batch.items
        }

        def validate_submission(
            name: str,
            submission: InitialSubmission | RevisionSubmission,
        ) -> dict[str, AnswerSelection]:
            if (
                submission.batch_id != self.batch_id
                or submission.item_count != self.public_batch.item_count
            ):
                raise ValueError(f"{name} must match the public batch identity")
            selections = {
                selection.item_id: selection
                for selection in submission.selections
            }
            if set(selections) != set(layouts):
                raise ValueError(f"{name} must select every public item")
            for item_id, selection in selections.items():
                if selection.option_id not in layouts[item_id]:
                    raise ValueError(f"{name} selects an unknown public option")
            return selections

        initial_by_item: dict[str, AnswerSelection] = {}
        if self.initial_submission is not None:
            initial_by_item = validate_submission(
                "initialSubmission",
                self.initial_submission,
            )

        revision_by_item: dict[str, AnswerSelection] = {}
        if self.revision_submission is not None:
            revision_by_item = validate_submission(
                "revisionSubmission",
                self.revision_submission,
            )

        if self.initial_result is not None and (
            self.initial_result.batch_id != self.batch_id
            or self.initial_result.item_count != self.public_batch.item_count
        ):
            raise ValueError("initialResult must match the public batch identity")

        if self.final_result is not None:
            if (
                self.final_result.batch_id != self.batch_id
                or self.final_result.item_count != self.public_batch.item_count
            ):
                raise ValueError("finalResult must match the public batch identity")
            final_by_item = {item.item_id: item for item in self.final_result.items}
            if set(final_by_item) != set(layouts):
                raise ValueError("finalResult must reveal every public item")
            final_submission = (
                revision_by_item
                if self.revision_submission is not None
                else initial_by_item
            )
            for item_id, item_result in final_by_item.items():
                if (
                    item_result.correct_option_id not in layouts[item_id]
                    or item_result.first_selection.option_id not in layouts[item_id]
                    or item_result.final_selection.option_id not in layouts[item_id]
                ):
                    raise ValueError("finalResult contains an unknown public option")
                initial = initial_by_item.get(item_id)
                final = final_submission.get(item_id)
                if initial is None or (
                    item_result.first_selection.option_id != initial.option_id
                    or item_result.first_selection.confidence != initial.confidence
                ):
                    raise ValueError(
                        "finalResult firstSelection must match initialSubmission"
                    )
                if final is None or (
                    item_result.final_selection.option_id != final.option_id
                    or item_result.final_selection.confidence != final.confidence
                ):
                    raise ValueError(
                        "finalResult finalSelection must match the final submission"
                    )

        presence = (
            self.initial_submission is not None,
            self.initial_result is not None,
            self.revision_submission is not None,
            self.final_result is not None,
        )
        if self.quiz_state is QuizSnapshotState.READY:
            expected_presence = (False, False, False, False)
            expected_version = 1
        elif self.quiz_state is QuizSnapshotState.INITIAL_LOCKED:
            expected_presence = (True, False, False, False)
            expected_version = 2
        elif self.quiz_state is QuizSnapshotState.REVISION_OPEN:
            expected_presence = (True, True, False, False)
            expected_version = 3
            if (
                self.initial_result is None
                or not self.initial_result.revision_required
            ):
                raise ValueError(
                    "revision_open requires a nonzero initialResult"
                )
        else:
            if self.initial_result is None:
                raise ValueError("revealed and closed states require initialResult")
            revision_used = self.initial_result.revision_required
            expected_presence = (True, True, revision_used, True)
            expected_version = (
                4 if self.quiz_state is QuizSnapshotState.REVEALED else 5
            ) if revision_used else (
                3 if self.quiz_state is QuizSnapshotState.REVEALED else 4
            )
            if self.final_result is not None:
                if self.final_result.revision_used != revision_used:
                    raise ValueError(
                        "finalResult.revisionUsed must match initialResult"
                    )
                if (
                    self.initial_result.wrong_count
                    != self.final_result.first_pass_wrong_count
                ):
                    raise ValueError(
                        "initialResult.wrongCount must match finalResult"
                    )
                if self.initial_result.final_result is not None and (
                    self.initial_result.final_result != self.final_result
                ):
                    raise ValueError(
                        "initialResult.finalResult must match finalResult"
                    )

        if presence != expected_presence:
            raise ValueError(
                f"{self.quiz_state.value} has an invalid public state shape"
            )
        if self.state_version != expected_version:
            raise ValueError(
                f"stateVersion must be {expected_version} for this public state shape"
            )
        return self


class BossGateResult(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    world_id: Identifier = Field(alias="worldId")
    unlocked: bool
    lead_in_wins: int = Field(alias="leadInWins", ge=0, le=4)
    required_lead_in_wins: int = Field(alias="requiredLeadInWins", ge=4, le=4)
    valid_world_items: int = Field(alias="validWorldItems", ge=0, le=10_000)
    required_valid_world_items: int = Field(alias="requiredValidWorldItems", ge=16, le=16)
    latest_ten_item_count: int = Field(alias="latestTenItemCount", ge=0, le=10)
    latest_ten_correct_count: int = Field(alias="latestTenCorrectCount", ge=0, le=10)
    required_latest_ten_correct_count: int = Field(
        alias="requiredLatestTenCorrectCount",
        ge=7,
        le=7,
    )
    core_subskill_count: int = Field(alias="coreSubskillCount", ge=1, le=32)
    ready_core_subskill_count: int = Field(alias="readyCoreSubskillCount", ge=0, le=32)
    unmet_requirements: Annotated[
        tuple[Annotated[GateRequirement, Field(strict=False)], ...],
        Field(alias="unmetRequirements", max_length=4, strict=False),
    ]

    @model_validator(mode="after")
    def gate_status_matches_evidence(self) -> Self:
        if self.latest_ten_correct_count > self.latest_ten_item_count:
            raise ValueError("latestTenCorrectCount cannot exceed latestTenItemCount")
        if self.ready_core_subskill_count > self.core_subskill_count:
            raise ValueError("readyCoreSubskillCount cannot exceed coreSubskillCount")

        unmet: list[GateRequirement] = []
        if self.lead_in_wins < self.required_lead_in_wins:
            unmet.append(GateRequirement.LEAD_IN_WINS)
        if self.valid_world_items < self.required_valid_world_items:
            unmet.append(GateRequirement.VALID_WORLD_ITEMS)
        if (
            self.latest_ten_item_count < 10
            or self.latest_ten_correct_count < self.required_latest_ten_correct_count
        ):
            unmet.append(GateRequirement.LATEST_TEN_ACCURACY)
        if self.ready_core_subskill_count < self.core_subskill_count:
            unmet.append(GateRequirement.CORE_SUBSKILL_COVERAGE)

        if self.unlocked != (not unmet):
            raise ValueError("unlocked must match the deterministic boss gate")
        if tuple(self.unmet_requirements) != tuple(unmet):
            raise ValueError("unmetRequirements must list each failed gate in canonical order")
        return self


class ProfileExportSessionV1(StrictModel):
    """One closed session record in a local profile export."""

    session_id: Identifier = Field(alias="sessionId")
    client_build: Identifier = Field(alias="clientBuild")
    opened_at_utc: CanonicalUtcTimestamp = Field(alias="openedAtUtc")
    closed_at_utc: CanonicalUtcTimestamp | None = Field(alias="closedAtUtc")

    @model_validator(mode="after")
    def closure_does_not_precede_opening(self) -> Self:
        if self.closed_at_utc is not None and _parse_canonical_utc_timestamp(
            self.closed_at_utc
        ) < _parse_canonical_utc_timestamp(self.opened_at_utc):
            raise ValueError("closedAtUtc cannot precede openedAtUtc")
        return self


class ProfileExportEventV1(StrictModel):
    """One canonical learning-event record in a local profile export."""

    ordinal: int = Field(ge=1)
    canonical_event_json: Annotated[
        str,
        Field(
            alias="canonicalEventJson",
            min_length=2,
            max_length=PROFILE_EXPORT_MAX_CANONICAL_EVENT_BYTES,
        ),
    ]
    event_sha256: Sha256 = Field(alias="eventSha256")

    @model_validator(mode="after")
    def canonical_payload_and_digest_verify(self) -> Self:
        encoded = self.canonical_event_json.encode("utf-8")
        if len(encoded) > PROFILE_EXPORT_MAX_CANONICAL_EVENT_BYTES:
            raise ValueError("canonicalEventJson exceeds the UTF-8 byte limit")

        def reject_duplicate_keys(
            pairs: list[tuple[str, object]],
        ) -> dict[str, object]:
            decoded: dict[str, object] = {}
            for key, value in pairs:
                if key in decoded:
                    raise ValueError(f"duplicate canonical event key: {key}")
                decoded[key] = value
            return decoded

        def reject_nonstandard_number(value: str) -> object:
            raise ValueError(f"non-standard JSON numeric constant: {value}")

        try:
            payload = json.loads(
                self.canonical_event_json,
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=reject_nonstandard_number,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("canonicalEventJson must be duplicate-free JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("canonicalEventJson must contain one object")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if canonical != self.canonical_event_json:
            raise ValueError("canonicalEventJson is not canonical JSON")
        try:
            from services.wayline_forge.app.events import (
                canonical_event_json,
                event_from_json,
            )

            canonical_event = event_from_json(self.canonical_event_json)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                "canonicalEventJson does not contain a closed learning event"
            ) from error
        if canonical_event_json(canonical_event) != self.canonical_event_json:
            raise ValueError("canonicalEventJson does not round-trip exactly")
        try:
            _parse_canonical_utc_timestamp(canonical_event.occurred_at)
        except ValueError as error:
            raise ValueError(
                "canonicalEventJson event timestamp is not canonical UTC"
            ) from error
        payload_ordinal = payload.get("ordinal")
        if (
            not isinstance(payload_ordinal, int)
            or isinstance(payload_ordinal, bool)
            or payload_ordinal != self.ordinal
        ):
            raise ValueError("ordinal must match canonicalEventJson")
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != self.event_sha256:
            raise ValueError("eventSha256 does not verify canonicalEventJson")
        return self


class ProfileExportV1(StrictModel):
    """Portable, learner-owned local profile export."""

    schema_version: Literal["wayline.profile-export.v1"] = Field(
        alias="schemaVersion"
    )
    profile_id: Identifier = Field(alias="profileId")
    created_at_utc: CanonicalUtcTimestamp = Field(alias="createdAtUtc")
    campaign_catalog_sha256: Literal[
        "5509097676eccc6c3848bfb64295ac931c73621a1120b9431af0ccc8e793d513"
    ] = Field(alias="campaignCatalogSha256")
    active_world_id: Identifier | None = Field(alias="activeWorldId")
    campaign_ordinal: int | None = Field(alias="campaignOrdinal", ge=1, le=9)
    sessions: Annotated[
        tuple[ProfileExportSessionV1, ...],
        Field(max_length=10_000, strict=False),
    ]
    events: Annotated[
        tuple[ProfileExportEventV1, ...],
        Field(max_length=100_000, strict=False),
    ]
    terminal_event_chain_sha256: Sha256 = Field(
        alias="terminalEventChainSha256"
    )

    @model_validator(mode="after")
    def relationships_and_chain_verify(self) -> Self:
        if (self.active_world_id is None) != (self.campaign_ordinal is None):
            raise ValueError(
                "activeWorldId and campaignOrdinal must both be null or both be present"
            )
        if self.active_world_id is not None and self.campaign_ordinal != (
            _PROFILE_EXPORT_WORLD_ORDINALS.get(self.active_world_id)
        ):
            raise ValueError("campaignOrdinal must match the pinned campaign world")

        session_ids = tuple(session.session_id for session in self.sessions)
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("sessionId must be unique within an export")
        if sum(session.closed_at_utc is None for session in self.sessions) > 1:
            raise ValueError("an export cannot contain multiple open sessions")
        profile_created_at = _parse_canonical_utc_timestamp(self.created_at_utc)
        if any(
            _parse_canonical_utc_timestamp(session.opened_at_utc)
            < profile_created_at
            for session in self.sessions
        ):
            raise ValueError("a session cannot precede profile creation")

        expected_ordinals = tuple(range(1, len(self.events) + 1))
        if tuple(event.ordinal for event in self.events) != expected_ordinals:
            raise ValueError("event ordinals must be contiguous and ordered")

        known_sessions = set(session_ids)
        activation_worlds: list[str] = []
        chain_hash = PROFILE_EXPORT_GENESIS_EVENT_HASH
        for event in self.events:
            payload = json.loads(event.canonical_event_json)
            if payload.get("profile_id") != self.profile_id:
                raise ValueError("canonical event profile differs from the export")
            if payload.get("session_id") not in known_sessions:
                raise ValueError("canonical event session is absent from the export")
            if payload.get("event_type") == "world_activated":
                world_id = payload.get("world_id")
                if not isinstance(world_id, str):
                    raise ValueError("world activation lacks a world identifier")
                activation_worlds.append(world_id)
            chain_hash = hashlib.sha256(
                (chain_hash + event.canonical_event_json).encode("utf-8")
            ).hexdigest()
        if chain_hash != self.terminal_event_chain_sha256:
            raise ValueError("terminalEventChainSha256 does not verify the event chain")

        derived_active = activation_worlds[-1] if activation_worlds else None
        if derived_active != self.active_world_id:
            raise ValueError("activeWorldId does not match canonical events")
        return self
