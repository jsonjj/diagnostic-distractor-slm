"""Pure construction of immutable learner observations at final quiz reveal."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re

from .batch_material import VerifiedBatchMaterial
from .events import EVENT_SCHEMA_VERSION, ObservationEvent
from .quiz_machine import (
    FinalItemResult,
    FinalQuizResult,
    PublicWrongCountResult,
    QuizItemLayout,
    QuizMachine,
    QuizMachineError,
    QuizState,
    QuizSubmission,
    RevealedSelectionResult,
    TransitionReceipt,
    mark_ready,
    new_quiz,
    submit_initial,
    submit_revision,
)


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}", re.ASCII)
_CANONICAL_UTC = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{6})?Z",
    re.ASCII,
)


class QuizObservationError(ValueError):
    """Stable, non-sensitive failure at the reveal-to-evidence boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise QuizObservationError(f"invalid_{name}")
    return value


def _require_timestamp(value: object) -> str:
    if not isinstance(value, str) or _CANONICAL_UTC.fullmatch(value) is None:
        raise QuizObservationError("invalid_occurred_at")
    timestamp_format = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
    try:
        datetime.strptime(value, timestamp_format)
    except ValueError:
        raise QuizObservationError("invalid_occurred_at") from None
    return value


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_layouts(material: VerifiedBatchMaterial) -> tuple[QuizItemLayout, ...]:
    return tuple(
        QuizItemLayout(
            item_id=item.item_id,
            option_ids=tuple(option.option_id for option in item.options),
        )
        for item in material.public_batch.items
    )


def _receipt_dict(receipt: TransitionReceipt) -> dict[str, object]:
    return {
        "action": receipt.action,
        "batchId": receipt.batch_id,
        "requestId": receipt.request_id,
        "payloadSha256": receipt.payload_sha256,
        "fromVersion": receipt.from_version,
        "toVersion": receipt.to_version,
        "outputSha256": receipt.output_sha256,
        "receiptSha256": receipt.receipt_sha256,
    }


def _observation_commitment(
    material: VerifiedBatchMaterial,
    receipt: TransitionReceipt,
    *,
    item_id: str,
    slot_index: int,
    placement_sha256: str,
) -> str:
    """Commit only stable receipts and identifiers, never reveal text."""

    return _canonical_sha256({
        "schemaVersion": "wayline.observation-identity.v1",
        "revealReceipt": _receipt_dict(receipt),
        "batch": {
            "batchId": material.batch_id,
            "batchMaterialSha256": material.batch_material_sha256,
        },
        "item": {
            "itemId": item_id,
            "slotIndex": slot_index,
            "placementSha256": placement_sha256,
        },
    })


def _require_exact_result_types(machine: QuizMachine) -> None:
    if type(machine.initial_submission) is not QuizSubmission:
        raise QuizObservationError("invalid_quiz_machine")
    if type(machine.initial_result) is not PublicWrongCountResult:
        raise QuizObservationError("invalid_quiz_machine")
    if type(machine.initial_receipt) is not TransitionReceipt:
        raise QuizObservationError("invalid_quiz_machine")
    if type(machine.final_result) is not FinalQuizResult:
        raise QuizObservationError("invalid_final_result")
    if not isinstance(machine.final_result.items, tuple) or any(
        type(item) is not FinalItemResult for item in machine.final_result.items
    ):
        raise QuizObservationError("invalid_final_result")
    if any(
        type(result.first_selection) is not RevealedSelectionResult
        or type(result.final_selection) is not RevealedSelectionResult
        for result in machine.final_result.items
    ):
        raise QuizObservationError("invalid_final_result")


def _replay_reveal(
    material: VerifiedBatchMaterial,
    machine: QuizMachine,
    receipt: TransitionReceipt,
) -> FinalQuizResult:
    """Recompute the exact machine transition instead of trusting its records."""

    if machine.state is not QuizState.REVEALED:
        raise QuizObservationError("quiz_not_revealed")
    _require_exact_result_types(machine)

    try:
        layouts = _public_layouts(material)
        preparing = new_quiz(material.batch_id, layouts)
        ready = mark_ready(
            preparing,
            sealed_quiz=material.sealed_quiz,
            expected_version=preparing.version,
        )
        initial = submit_initial(
            ready,
            machine.initial_submission,
            material.sealed_quiz,
            expected_version=ready.version,
        )
        if initial.public_result.wrong_count == 0:
            expected_machine = initial.machine
            expected_receipt = initial.receipt
            expected_action = "initial"
            if machine.revision_submission is not None or machine.revision_receipt is not None:
                raise QuizObservationError("invalid_quiz_machine")
        else:
            if type(machine.revision_submission) is not QuizSubmission:
                raise QuizObservationError("invalid_quiz_machine")
            if type(machine.revision_receipt) is not TransitionReceipt:
                raise QuizObservationError("invalid_quiz_machine")
            revision = submit_revision(
                initial.machine,
                machine.revision_submission,
                material.sealed_quiz,
                expected_version=initial.machine.version,
            )
            expected_machine = revision.machine
            expected_receipt = revision.receipt
            expected_action = "revision"
    except QuizObservationError:
        raise
    except (QuizMachineError, TypeError, ValueError, AttributeError):
        raise QuizObservationError("quiz_replay_failed") from None

    if machine.item_layouts != layouts:
        raise QuizObservationError("layout_mismatch")
    if machine != expected_machine:
        raise QuizObservationError("quiz_replay_mismatch")
    if receipt.action != expected_action or receipt != expected_receipt:
        raise QuizObservationError("reveal_receipt_mismatch")
    return expected_machine.final_result  # type: ignore[return-value]


def build_reveal_observations(
    material: VerifiedBatchMaterial,
    machine: QuizMachine,
    receipt: TransitionReceipt,
    *,
    profile_id: str,
    reveal_session_id: str,
    first_ordinal: int,
    occurred_at: str,
) -> tuple[ObservationEvent, ...]:
    """Build one validated observation per verified item after final reveal.

    The function is side-effect free.  It replays scoring from the verified seal
    before deriving events, and it validates each event against the exact batch
    material before returning anything.
    """

    if type(material) is not VerifiedBatchMaterial:
        raise QuizObservationError("invalid_material")
    if type(machine) is not QuizMachine:
        raise QuizObservationError("invalid_quiz_machine")
    if type(receipt) is not TransitionReceipt:
        raise QuizObservationError("invalid_reveal_receipt")

    profile_id = _require_identifier("profile_id", profile_id)
    reveal_session_id = _require_identifier("reveal_session_id", reveal_session_id)
    occurred_at = _require_timestamp(occurred_at)
    if (
        not isinstance(first_ordinal, int)
        or isinstance(first_ordinal, bool)
        or first_ordinal < 1
    ):
        raise QuizObservationError("invalid_first_ordinal")
    if profile_id != material.context.profile_id:
        raise QuizObservationError("profile_mismatch")

    try:
        material.__post_init__()
    except (TypeError, ValueError, AttributeError, UnicodeError):
        raise QuizObservationError("invalid_material") from None

    final_result = _replay_reveal(material, machine, receipt)
    if len(final_result.items) != len(material.items):
        raise QuizObservationError("final_result_count_mismatch")

    events: list[ObservationEvent] = []
    try:
        for offset, (item, result) in enumerate(
            zip(material.items, final_result.items, strict=True)
        ):
            first_route = item.route_for_option(result.first_selection.option_id)
            final_route = item.route_for_option(result.final_selection.option_id)
            commitment = _observation_commitment(
                material,
                receipt,
                item_id=item.item_id,
                slot_index=item.slot_index,
                placement_sha256=item.placement.placement_sha256,
            )
            event = ObservationEvent(
                schema_version=EVENT_SCHEMA_VERSION,
                event_id=f"obs-{commitment}",
                idempotency_id=f"obs-idem-{commitment}",
                ordinal=first_ordinal + offset,
                profile_id=profile_id,
                session_id=reveal_session_id,
                world_id=item.bundle.blueprint.world_id,
                battle_id=material.context.battle_id,
                occurred_at=occurred_at,
                batch_id=material.batch_id,
                item_id=item.item_id,
                question_id=item.bundle.blueprint.question_id,
                template_id=item.bundle.template_id,
                content_version_id=material.context.content_version_id,
                skill_id=item.bundle.blueprint.skill_id,
                world_core_subskill_ids=material.context.core_subskill_ids,
                operand_signature=item.bundle.operand_signature,
                context_id=item.bundle.context_id,
                first_option_id=result.first_selection.option_id,
                final_option_id=result.final_selection.option_id,
                first_confidence=result.first_selection.confidence,
                final_confidence=result.final_selection.confidence,
                first_correct=result.first_selection.is_correct,
                final_correct=result.final_selection.is_correct,
                choice_changed=(
                    result.first_selection.option_id
                    != result.final_selection.option_id
                ),
                self_corrected=result.self_corrected,
                first_procedure_id=first_route.procedure_id,
                final_procedure_id=final_route.procedure_id,
                targeted_procedure_ids=item.required_procedure_ids,
                is_transfer=item.is_transfer,
                is_changed_context_transfer=item.is_changed_context_transfer,
                valid_for_progression=item.valid_for_progression,
                batch_wrong_count=final_result.first_pass_wrong_count,
                canonical_feedback=tuple(
                    value
                    for value in (result.possible_error, result.reliable_method)
                    if value is not None
                ),
                optional_wording_shown=None,
                receipts=item.event_receipts,
            )
            material.validate_observation(
                event,
                result,
                observation_session_id=reveal_session_id,
            )
            events.append(event)
    except (TypeError, ValueError, AttributeError):
        raise QuizObservationError("observation_validation_failed") from None

    return tuple(events)
