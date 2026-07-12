"""Pure, immutable state machine for Wayline's truthful two-pass quiz.

The module deliberately uses only the Python standard library.  Public Pydantic
contracts can be passed directly because submissions are consumed structurally;
the immutable result records expose ``to_public_dict`` for contract validation at
the API boundary.  Sealed answer data is supplied only to scoring operations and
is never retained by a machine before the ``revealed`` state.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import hmac
import json
import re
from typing import Any, Iterable


SCHEMA_VERSION = "wayline.v1"
ALLOWED_CONFIDENCE = frozenset(("certain", "leaning", "guessing"))
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}")


class QuizMachineError(ValueError):
    """Base class for fail-closed quiz-machine errors."""


class InvalidQuizTransitionError(QuizMachineError):
    """Raised when an action is not legal from the current state."""


class StaleQuizStateError(QuizMachineError):
    """Raised when optimistic state-version validation fails."""


class SubmissionValidationError(QuizMachineError):
    """Raised when a submission, layout, or sealed quiz is inconsistent."""


class IdempotencyConflictError(QuizMachineError):
    """Raised when one request ID is reused with a different payload."""


class InitialAlreadySubmittedError(InvalidQuizTransitionError):
    """Raised when a distinct second initial request is attempted."""


class RevisionAlreadyUsedError(InvalidQuizTransitionError):
    """Raised when a distinct second revision request is attempted."""


class ResultNotRevealedError(InvalidQuizTransitionError):
    """Raised when item-level results are requested before reveal."""


class QuizState(str, Enum):
    PREPARING = "preparing"
    READY = "ready"
    INITIAL_LOCKED = "initial_locked"
    REVISION_OPEN = "revision_open"
    REVEALED = "revealed"
    CLOSED = "closed"


@dataclass(frozen=True)
class QuizItemLayout:
    """Public option membership for one item; it contains no answer key."""

    item_id: str
    option_ids: tuple[str, ...]


@dataclass(frozen=True)
class QuizSelection:
    item_id: str
    option_id: str
    confidence: str


@dataclass(frozen=True)
class QuizSubmission:
    """Stdlib mirror of the immutable initial/revision public contracts."""

    schema_version: str
    request_id: str
    batch_id: str
    item_count: int
    selections: tuple[QuizSelection, ...]


@dataclass(frozen=True)
class SealedQuizItem:
    """Server-only reveal material supplied to, never owned by, the machine.

    The caller must construct this from a verifier-approved sealed bundle.  The
    quiz machine enforces frozen public-contract shape and bounds, but semantic,
    control-character, HTML, and child-safety checks remain verifier authority.
    """

    item_id: str
    correct_option_id: str
    correct_answer: str
    trusted_steps: tuple[str, ...]
    possible_errors: tuple[tuple[str, str], ...]
    reliable_method: str


@dataclass(frozen=True)
class SealedQuiz:
    batch_id: str
    items: tuple[SealedQuizItem, ...]


@dataclass(frozen=True)
class PublicWrongCountResult:
    schema_version: str
    batch_id: str
    item_count: int
    wrong_count: int
    revision_required: bool

    def to_public_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "batchId": self.batch_id,
            "itemCount": self.item_count,
            "wrongCount": self.wrong_count,
            "revisionRequired": self.revision_required,
        }


@dataclass(frozen=True)
class RevealedSelectionResult:
    option_id: str
    confidence: str
    is_correct: bool

    def to_public_dict(self) -> dict[str, object]:
        return {
            "optionId": self.option_id,
            "confidence": self.confidence,
            "isCorrect": self.is_correct,
        }


@dataclass(frozen=True)
class FinalItemResult:
    item_id: str
    first_selection: RevealedSelectionResult
    final_selection: RevealedSelectionResult
    correct_option_id: str
    correct_answer: str
    trusted_steps: tuple[str, ...]
    possible_error: str | None
    reliable_method: str
    self_corrected: bool

    def to_public_dict(self) -> dict[str, object]:
        return {
            "itemId": self.item_id,
            "firstSelection": self.first_selection.to_public_dict(),
            "finalSelection": self.final_selection.to_public_dict(),
            "correctOptionId": self.correct_option_id,
            "correctAnswer": self.correct_answer,
            "trustedSteps": list(self.trusted_steps),
            "possibleError": self.possible_error,
            "reliableMethod": self.reliable_method,
            "selfCorrected": self.self_corrected,
        }


@dataclass(frozen=True)
class FinalQuizResult:
    schema_version: str
    batch_id: str
    item_count: int
    first_pass_wrong_count: int
    final_correct_count: int
    revision_used: bool
    items: tuple[FinalItemResult, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "batchId": self.batch_id,
            "itemCount": self.item_count,
            "firstPassWrongCount": self.first_pass_wrong_count,
            "finalCorrectCount": self.final_correct_count,
            "revisionUsed": self.revision_used,
            "items": [item.to_public_dict() for item in self.items],
        }


@dataclass(frozen=True)
class TransitionReceipt:
    """Deterministic internal receipt; initial receipts contain no item truth."""

    action: str
    batch_id: str
    request_id: str
    payload_sha256: str
    from_version: int
    to_version: int
    output_sha256: str
    receipt_sha256: str


@dataclass(frozen=True)
class QuizMachine:
    batch_id: str
    state: QuizState
    version: int
    item_layouts: tuple[QuizItemLayout, ...] = field(repr=False)
    sealed_quiz_sha256: str | None = field(default=None, repr=False)
    initial_submission: QuizSubmission | None = field(default=None, repr=False)
    initial_payload_sha256: str | None = field(default=None, repr=False)
    initial_result: PublicWrongCountResult | None = field(default=None, repr=False)
    initial_receipt: TransitionReceipt | None = None
    revision_submission: QuizSubmission | None = field(default=None, repr=False)
    revision_payload_sha256: str | None = field(default=None, repr=False)
    revision_receipt: TransitionReceipt | None = None
    final_result: FinalQuizResult | None = field(default=None, repr=False)


@dataclass(frozen=True)
class InitialTransition:
    machine: QuizMachine
    public_result: PublicWrongCountResult
    receipt: TransitionReceipt


@dataclass(frozen=True)
class RevisionTransition:
    machine: QuizMachine
    final_result: FinalQuizResult
    receipt: TransitionReceipt


def new_quiz(
    batch_id: str,
    item_layouts: Iterable[QuizItemLayout],
) -> QuizMachine:
    """Create a preparing machine after validating its public option layout."""

    normalized_layouts = tuple(_normalize_layout(item) for item in item_layouts)
    if not _valid_identifier(batch_id):
        raise SubmissionValidationError("batch_id must be a non-empty identifier")
    if not 3 <= len(normalized_layouts) <= 10:
        raise SubmissionValidationError("a quiz must contain between 3 and 10 items")
    item_ids = tuple(item.item_id for item in normalized_layouts)
    if len(set(item_ids)) != len(item_ids):
        raise SubmissionValidationError("item_id must be unique within a quiz")
    return QuizMachine(
        batch_id=batch_id,
        state=QuizState.PREPARING,
        version=0,
        item_layouts=normalized_layouts,
    )


def mark_ready(
    machine: QuizMachine,
    *,
    sealed_quiz: object,
    expected_version: int,
) -> QuizMachine:
    """Move a completely prepared batch into the submission-ready state."""

    _require_version(machine, expected_version)
    _require_state(machine, QuizState.PREPARING, "mark_ready")
    sealed = _normalize_and_validate_sealed(machine, sealed_quiz)
    return replace(
        machine,
        state=QuizState.READY,
        version=machine.version + 1,
        sealed_quiz_sha256=_sealed_quiz_sha256(machine, sealed),
    )


def lock_initial(
    machine: QuizMachine,
    submission: object,
    *,
    expected_version: int,
) -> QuizMachine:
    """Durably-shaped first half of initial submission for interruption recovery."""

    normalized = _normalize_submission(submission)
    payload_sha256 = _submission_sha256(normalized)

    if machine.initial_submission is not None:
        if normalized.request_id != machine.initial_submission.request_id:
            raise InitialAlreadySubmittedError("an initial submission is already locked")
        if payload_sha256 != machine.initial_payload_sha256:
            raise IdempotencyConflictError(
                "initial request_id was reused with a different payload"
            )
        return machine

    _require_version(machine, expected_version)
    _require_state(machine, QuizState.READY, "lock_initial")
    _validate_submission(machine, normalized)
    return replace(
        machine,
        state=QuizState.INITIAL_LOCKED,
        version=machine.version + 1,
        initial_submission=normalized,
        initial_payload_sha256=payload_sha256,
    )


def resolve_initial(
    machine: QuizMachine,
    sealed_quiz: object,
    *,
    expected_version: int,
) -> InitialTransition:
    """Score a locked first pass and publish only its aggregate exact count."""

    _require_version(machine, expected_version)
    _require_state(machine, QuizState.INITIAL_LOCKED, "resolve_initial")
    if machine.initial_submission is None or machine.initial_payload_sha256 is None:
        raise InvalidQuizTransitionError("initial_locked state lacks a locked payload")

    sealed = _require_committed_sealed(machine, sealed_quiz)
    correct_by_item = {
        item.item_id: item.correct_option_id
        for item in sealed.items
    }
    wrong_count = sum(
        selection.option_id != correct_by_item[selection.item_id]
        for selection in machine.initial_submission.selections
    )
    public_result = PublicWrongCountResult(
        schema_version=SCHEMA_VERSION,
        batch_id=machine.batch_id,
        item_count=len(machine.item_layouts),
        wrong_count=wrong_count,
        revision_required=wrong_count > 0,
    )

    next_state = QuizState.REVISION_OPEN if wrong_count else QuizState.REVEALED
    next_version = machine.version + 1
    receipt = _make_receipt(
        action="initial",
        batch_id=machine.batch_id,
        request_id=machine.initial_submission.request_id,
        payload_sha256=machine.initial_payload_sha256,
        from_version=machine.version - 1,
        to_version=next_version,
        output=public_result.to_public_dict(),
    )
    final_result = None
    if wrong_count == 0:
        final_result = _build_final_result(
            machine,
            sealed,
            machine.initial_submission,
            revision_used=False,
        )

    resolved = replace(
        machine,
        state=next_state,
        version=next_version,
        initial_result=public_result,
        initial_receipt=receipt,
        final_result=final_result,
    )
    return InitialTransition(resolved, public_result, receipt)


def submit_initial(
    machine: QuizMachine,
    submission: object,
    sealed_quiz: object,
    *,
    expected_version: int,
) -> InitialTransition:
    """Atomically-shaped initial action with deterministic retry semantics."""

    sealed = _require_committed_sealed(machine, sealed_quiz)
    locked_or_existing = lock_initial(
        machine,
        submission,
        expected_version=expected_version,
    )
    if locked_or_existing.initial_result is not None:
        if locked_or_existing.initial_receipt is None:
            raise InvalidQuizTransitionError("resolved initial has no transition receipt")
        return InitialTransition(
            locked_or_existing,
            locked_or_existing.initial_result,
            locked_or_existing.initial_receipt,
        )
    return resolve_initial(
        locked_or_existing,
        sealed,
        expected_version=locked_or_existing.version,
    )


def submit_revision(
    machine: QuizMachine,
    submission: object,
    sealed_quiz: object,
    *,
    expected_version: int,
) -> RevisionTransition:
    """Accept exactly one complete revision and reveal item-level truth."""

    sealed = _require_committed_sealed(machine, sealed_quiz)
    normalized = _normalize_submission(submission)
    payload_sha256 = _submission_sha256(normalized)

    if machine.revision_submission is not None:
        if normalized.request_id != machine.revision_submission.request_id:
            raise RevisionAlreadyUsedError("the one revision has already been used")
        if payload_sha256 != machine.revision_payload_sha256:
            raise IdempotencyConflictError(
                "revision request_id was reused with a different payload"
            )
        if machine.revision_receipt is None or machine.final_result is None:
            raise InvalidQuizTransitionError("resolved revision lacks its receipt")
        return RevisionTransition(
            machine,
            machine.final_result,
            machine.revision_receipt,
        )

    _require_version(machine, expected_version)
    _require_state(machine, QuizState.REVISION_OPEN, "submit_revision")
    _validate_submission(machine, normalized)
    if machine.initial_submission is None or machine.initial_result is None:
        raise InvalidQuizTransitionError("revision_open state lacks an initial record")
    if not machine.initial_result.revision_required:
        raise InvalidQuizTransitionError("a zero-wrong batch cannot be revised")

    final_result = _build_final_result(
        machine,
        sealed,
        normalized,
        revision_used=True,
    )
    next_version = machine.version + 1
    receipt = _make_receipt(
        action="revision",
        batch_id=machine.batch_id,
        request_id=normalized.request_id,
        payload_sha256=payload_sha256,
        from_version=machine.version,
        to_version=next_version,
        output=final_result.to_public_dict(),
    )
    revealed = replace(
        machine,
        state=QuizState.REVEALED,
        version=next_version,
        revision_submission=normalized,
        revision_payload_sha256=payload_sha256,
        revision_receipt=receipt,
        final_result=final_result,
    )
    return RevisionTransition(revealed, final_result, receipt)


def revealed_result(machine: QuizMachine) -> FinalQuizResult:
    """Return final item truth only once the machine has reached reveal."""

    if machine.state not in (QuizState.REVEALED, QuizState.CLOSED):
        raise ResultNotRevealedError("item results are sealed until revealed")
    if machine.final_result is None:
        raise ResultNotRevealedError("revealed state has no final result")
    return machine.final_result


def close_quiz(machine: QuizMachine, *, expected_version: int) -> QuizMachine:
    """Acknowledge the reveal and close the batch."""

    _require_version(machine, expected_version)
    _require_state(machine, QuizState.REVEALED, "close_quiz")
    return replace(machine, state=QuizState.CLOSED, version=machine.version + 1)


def _build_final_result(
    machine: QuizMachine,
    sealed: SealedQuiz,
    final_submission: QuizSubmission,
    *,
    revision_used: bool,
) -> FinalQuizResult:
    if machine.initial_submission is None:
        raise InvalidQuizTransitionError("cannot reveal without an initial submission")

    first_by_item = {
        selection.item_id: selection
        for selection in machine.initial_submission.selections
    }
    final_by_item = {
        selection.item_id: selection
        for selection in final_submission.selections
    }
    sealed_by_item = {item.item_id: item for item in sealed.items}
    items: list[FinalItemResult] = []

    for layout in machine.item_layouts:
        item = sealed_by_item[layout.item_id]
        first = first_by_item[layout.item_id]
        final = final_by_item[layout.item_id]
        first_correct = first.option_id == item.correct_option_id
        final_correct = final.option_id == item.correct_option_id
        error_by_option = dict(item.possible_errors)

        # A self-correction retains the first route's teaching note.  Any final
        # wrong answer uses its final verified route; correct-to-correct has no
        # possible-error note.
        if final_correct and not first_correct:
            possible_error = error_by_option[first.option_id]
        elif final_correct:
            possible_error = None
        else:
            possible_error = error_by_option[final.option_id]

        items.append(FinalItemResult(
            item_id=layout.item_id,
            first_selection=RevealedSelectionResult(
                option_id=first.option_id,
                confidence=first.confidence,
                is_correct=first_correct,
            ),
            final_selection=RevealedSelectionResult(
                option_id=final.option_id,
                confidence=final.confidence,
                is_correct=final_correct,
            ),
            correct_option_id=item.correct_option_id,
            correct_answer=item.correct_answer,
            trusted_steps=item.trusted_steps,
            possible_error=possible_error,
            reliable_method=item.reliable_method,
            self_corrected=not first_correct and final_correct,
        ))

    return FinalQuizResult(
        schema_version=SCHEMA_VERSION,
        batch_id=machine.batch_id,
        item_count=len(items),
        first_pass_wrong_count=sum(
            not item.first_selection.is_correct for item in items
        ),
        final_correct_count=sum(item.final_selection.is_correct for item in items),
        revision_used=revision_used,
        items=tuple(items),
    )


def _normalize_layout(value: object) -> QuizItemLayout:
    try:
        item_id = getattr(value, "item_id")
        option_ids = tuple(getattr(value, "option_ids"))
    except (AttributeError, TypeError) as exc:
        raise SubmissionValidationError("invalid quiz item layout") from exc
    if not _valid_identifier(item_id):
        raise SubmissionValidationError("item_id must be a non-empty identifier")
    if len(option_ids) != 4:
        raise SubmissionValidationError("each item must contain exactly four options")
    if any(not _valid_identifier(option_id) for option_id in option_ids):
        raise SubmissionValidationError("option_id must be a non-empty identifier")
    if len(set(option_ids)) != len(option_ids):
        raise SubmissionValidationError("option_id must be unique within an item")
    return QuizItemLayout(item_id=item_id, option_ids=option_ids)


def _normalize_submission(value: object) -> QuizSubmission:
    try:
        schema_version = getattr(value, "schema_version")
        request_id = getattr(value, "request_id")
        batch_id = getattr(value, "batch_id")
        item_count = getattr(value, "item_count")
        raw_selections = tuple(getattr(value, "selections"))
    except (AttributeError, TypeError) as exc:
        raise SubmissionValidationError("invalid submission shape") from exc

    if schema_version != SCHEMA_VERSION:
        raise SubmissionValidationError("unsupported schema_version")
    if not _valid_identifier(request_id):
        raise SubmissionValidationError("request_id must be a non-empty identifier")
    if not _valid_identifier(batch_id):
        raise SubmissionValidationError("batch_id must be a non-empty identifier")
    if isinstance(item_count, bool) or not isinstance(item_count, int):
        raise SubmissionValidationError("item_count must be an integer")

    selections: list[QuizSelection] = []
    for raw in raw_selections:
        try:
            item_id = getattr(raw, "item_id")
            option_id = getattr(raw, "option_id")
            confidence_value = getattr(raw, "confidence")
        except AttributeError as exc:
            raise SubmissionValidationError("invalid selection shape") from exc
        if isinstance(confidence_value, Enum):
            confidence_value = confidence_value.value
        if not _valid_identifier(item_id) or not _valid_identifier(option_id):
            raise SubmissionValidationError("selection IDs must be non-empty identifiers")
        if confidence_value not in ALLOWED_CONFIDENCE:
            raise SubmissionValidationError("confidence is not allowed")
        selections.append(QuizSelection(item_id, option_id, confidence_value))

    return QuizSubmission(
        schema_version=schema_version,
        request_id=request_id,
        batch_id=batch_id,
        item_count=item_count,
        selections=tuple(selections),
    )


def _validate_submission(machine: QuizMachine, submission: QuizSubmission) -> None:
    if submission.batch_id != machine.batch_id:
        raise SubmissionValidationError("submission batch_id does not match")
    if submission.item_count != len(machine.item_layouts):
        raise SubmissionValidationError("item_count must match the complete batch")
    if len(submission.selections) != len(machine.item_layouts):
        raise SubmissionValidationError("every item needs exactly one selection")

    selection_by_item: dict[str, QuizSelection] = {}
    for selection in submission.selections:
        if selection.item_id in selection_by_item:
            raise SubmissionValidationError("each item can be selected only once")
        selection_by_item[selection.item_id] = selection

    layout_by_item = {item.item_id: item for item in machine.item_layouts}
    if set(selection_by_item) != set(layout_by_item):
        raise SubmissionValidationError("selection item IDs must equal the batch item IDs")
    for item_id, selection in selection_by_item.items():
        if selection.option_id not in layout_by_item[item_id].option_ids:
            raise SubmissionValidationError("selected option does not belong to its item")


def _normalize_and_validate_sealed(
    machine: QuizMachine,
    value: object,
) -> SealedQuiz:
    try:
        batch_id = getattr(value, "batch_id")
        raw_items = tuple(getattr(value, "items"))
    except (AttributeError, TypeError) as exc:
        raise SubmissionValidationError("invalid sealed quiz shape") from exc
    if batch_id != machine.batch_id:
        raise SubmissionValidationError("sealed quiz batch_id does not match")

    items: list[SealedQuizItem] = []
    for raw in raw_items:
        try:
            item_id = getattr(raw, "item_id")
            correct_option_id = getattr(raw, "correct_option_id")
            correct_answer = getattr(raw, "correct_answer")
            trusted_steps = tuple(getattr(raw, "trusted_steps"))
            possible_errors = tuple(
                tuple(pair) for pair in getattr(raw, "possible_errors")
            )
            reliable_method = getattr(raw, "reliable_method")
        except (AttributeError, TypeError) as exc:
            raise SubmissionValidationError("invalid sealed quiz item") from exc
        if (
            not _valid_identifier(item_id)
            or not _valid_identifier(correct_option_id)
            or not _valid_text(correct_answer, maximum=256)
            or not 1 <= len(trusted_steps) <= 8
            or any(not _valid_text(step, maximum=512) for step in trusted_steps)
            or not _valid_text(reliable_method, maximum=512)
        ):
            raise SubmissionValidationError("invalid sealed quiz reveal material")
        if any(
            len(pair) != 2
            or not _valid_identifier(pair[0])
            or not _valid_text(pair[1], maximum=512)
            for pair in possible_errors
        ):
            raise SubmissionValidationError("invalid possible-error mapping")
        items.append(SealedQuizItem(
            item_id=item_id,
            correct_option_id=correct_option_id,
            correct_answer=correct_answer,
            trusted_steps=trusted_steps,
            possible_errors=possible_errors,
            reliable_method=reliable_method,
        ))

    layout_by_item = {item.item_id: item for item in machine.item_layouts}
    sealed_by_item = {item.item_id: item for item in items}
    if len(sealed_by_item) != len(items) or set(sealed_by_item) != set(layout_by_item):
        raise SubmissionValidationError("sealed item IDs must equal the batch item IDs")

    for item_id, sealed_item in sealed_by_item.items():
        layout = layout_by_item[item_id]
        if sealed_item.correct_option_id not in layout.option_ids:
            raise SubmissionValidationError("correct option does not belong to its item")
        error_option_ids = tuple(option_id for option_id, _ in sealed_item.possible_errors)
        expected_wrong_ids = set(layout.option_ids) - {sealed_item.correct_option_id}
        if (
            len(set(error_option_ids)) != len(error_option_ids)
            or set(error_option_ids) != expected_wrong_ids
        ):
            raise SubmissionValidationError(
                "possible errors must map each wrong option exactly once"
            )
    return SealedQuiz(batch_id=batch_id, items=tuple(items))


def _require_committed_sealed(
    machine: QuizMachine,
    value: object,
) -> SealedQuiz:
    sealed = _normalize_and_validate_sealed(machine, value)
    if machine.sealed_quiz_sha256 is None:
        raise SubmissionValidationError("quiz has no ready-time sealed commitment")
    supplied_sha256 = _sealed_quiz_sha256(machine, sealed)
    if not hmac.compare_digest(supplied_sha256, machine.sealed_quiz_sha256):
        raise SubmissionValidationError(
            "sealed quiz does not match the ready-time commitment"
        )
    return sealed


def _sealed_quiz_sha256(machine: QuizMachine, sealed: SealedQuiz) -> str:
    """Hash all sealed truth in stable layout/option order."""

    sealed_by_item = {item.item_id: item for item in sealed.items}
    canonical_items: list[dict[str, object]] = []
    for layout in machine.item_layouts:
        item = sealed_by_item[layout.item_id]
        error_by_option = dict(item.possible_errors)
        canonical_items.append({
            "itemId": item.item_id,
            "correctOptionId": item.correct_option_id,
            "correctAnswer": item.correct_answer,
            "trustedSteps": list(item.trusted_steps),
            "possibleErrors": [
                {
                    "optionId": option_id,
                    "text": error_by_option[option_id],
                }
                for option_id in layout.option_ids
                if option_id != item.correct_option_id
            ],
            "reliableMethod": item.reliable_method,
        })
    return _canonical_sha256({
        "schemaVersion": "wayline.sealed-quiz.v1",
        "batchId": sealed.batch_id,
        "items": canonical_items,
    })


def _submission_sha256(submission: QuizSubmission) -> str:
    return _canonical_sha256({
        "schemaVersion": submission.schema_version,
        "requestId": submission.request_id,
        "batchId": submission.batch_id,
        "itemCount": submission.item_count,
        "selections": [
            {
                "itemId": selection.item_id,
                "optionId": selection.option_id,
                "confidence": selection.confidence,
            }
            for selection in submission.selections
        ],
    })


def _make_receipt(
    *,
    action: str,
    batch_id: str,
    request_id: str,
    payload_sha256: str,
    from_version: int,
    to_version: int,
    output: object,
) -> TransitionReceipt:
    output_sha256 = _canonical_sha256(output)
    receipt_fields = {
        "action": action,
        "batchId": batch_id,
        "requestId": request_id,
        "payloadSha256": payload_sha256,
        "fromVersion": from_version,
        "toVersion": to_version,
        "outputSha256": output_sha256,
    }
    return TransitionReceipt(
        action=action,
        batch_id=batch_id,
        request_id=request_id,
        payload_sha256=payload_sha256,
        from_version=from_version,
        to_version=to_version,
        output_sha256=output_sha256,
        receipt_sha256=_canonical_sha256(receipt_fields),
    )


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_version(machine: QuizMachine, expected_version: int) -> None:
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise StaleQuizStateError("expected_version must be an integer")
    if expected_version != machine.version:
        raise StaleQuizStateError(
            f"stale quiz version {expected_version}; current version is {machine.version}"
        )


def _require_state(
    machine: QuizMachine,
    expected_state: QuizState,
    action: str,
) -> None:
    if machine.state is not expected_state:
        raise InvalidQuizTransitionError(
            f"{action} requires {expected_state.value}, got {machine.state.value}"
        )


def _valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and IDENTIFIER_PATTERN.fullmatch(value) is not None


def _valid_text(value: Any, *, maximum: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= maximum
