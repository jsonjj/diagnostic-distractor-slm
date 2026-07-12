"""Pure projection and sealed scoring for fresh assisted-route material."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .batch_material import VerifiedBatchMaterial
from .contracts import (
    AssistedItemResult,
    AssistedRouteBatch,
    AssistedSelection,
    AssistedSupportedItem,
    AssistedWorkedExample,
)
from .events import ProvenanceReceipts


_ASSISTED_KINDS: Final[tuple[str, str, str]] = (
    "assisted_worked_example",
    "assisted_supported_mcq",
    "assisted_supported_mcq",
)
_ASSISTED_DIFFICULTIES: Final[tuple[int, int, int]] = (2, 1, 1)


class AssistedRouteMachineError(ValueError):
    """Stable fail-closed error for invalid material or selections."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "material_context_mismatch",
            "material_count_mismatch",
            "material_kind_mismatch",
            "material_difficulty_mismatch",
            "material_source_unverified",
            "selection_count_mismatch",
            "selection_identity_mismatch",
            "selection_option_invalid",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown assisted route machine error")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AssistedRouteScore:
    route_id: str
    final_correct: int
    items: tuple[AssistedItemResult, AssistedItemResult]
    selected_procedure_ids: tuple[str | None, str | None]
    receipts: tuple[ProvenanceReceipts, ProvenanceReceipts]
    material_sha256: str

    def __post_init__(self) -> None:
        if self.final_correct != sum(item.is_correct for item in self.items):
            raise ValueError("final_correct must match assisted item results")
        if tuple(
            procedure is None
            for procedure in self.selected_procedure_ids
        ) != tuple(item.is_correct for item in self.items):
            raise ValueError("selected procedures must match assisted correctness")


def require_assisted_material(material: VerifiedBatchMaterial) -> None:
    """Revalidate the isolated assisted material contract at every boundary."""

    if not isinstance(material, VerifiedBatchMaterial):
        raise AssistedRouteMachineError("material_context_mismatch")
    if material.context.battle_tier != "assisted_route":
        raise AssistedRouteMachineError("material_context_mismatch")
    if len(material.items) != 3:
        raise AssistedRouteMachineError("material_count_mismatch")
    if tuple(item.kind for item in material.items) != _ASSISTED_KINDS:
        raise AssistedRouteMachineError("material_kind_mismatch")
    if tuple(
        item.bundle.blueprint.difficulty for item in material.items
    ) != _ASSISTED_DIFFICULTIES:
        raise AssistedRouteMachineError("material_difficulty_mismatch")
    if any(item.valid_for_progression for item in material.items):
        raise AssistedRouteMachineError("material_kind_mismatch")
    if any(
        item.source_proof.source_kind not in {"live_verified", "reviewed_cache"}
        for item in material.items
    ):
        raise AssistedRouteMachineError("material_source_unverified")


def public_assisted_batch(
    route_id: str,
    material: VerifiedBatchMaterial,
) -> AssistedRouteBatch:
    """Project one worked example and two keyless supported MCQs."""

    require_assisted_material(material)
    public_items = material.public_batch.items
    sealed_items = material.sealed_quiz.items
    try:
        return AssistedRouteBatch(
            routeId=route_id,
            worldId=material.context.world_id,
            workedExample=AssistedWorkedExample(
                itemId=public_items[0].item_id,
                prompt=public_items[0].prompt,
                correctAnswer=sealed_items[0].correct_answer,
                trustedSteps=sealed_items[0].trusted_steps,
                reliableMethod=sealed_items[0].reliable_method,
            ),
            items=tuple(
                AssistedSupportedItem(
                    itemId=public_item.item_id,
                    prompt=public_item.prompt,
                    options=public_item.options,
                )
                for public_item in public_items[1:]
            ),
        )
    except (TypeError, ValueError):
        raise AssistedRouteMachineError("material_context_mismatch") from None


def score_assisted_route(
    route_id: str,
    material: VerifiedBatchMaterial,
    selections: tuple[AssistedSelection, AssistedSelection],
) -> AssistedRouteScore:
    """Score only the two supported items against sealed server truth."""

    require_assisted_material(material)
    if (
        not isinstance(selections, tuple)
        or len(selections) != 2
        or any(not isinstance(item, AssistedSelection) for item in selections)
    ):
        raise AssistedRouteMachineError("selection_count_mismatch")

    public_items = material.public_batch.items[1:]
    sealed_items = material.sealed_quiz.items[1:]
    verified_items = material.items[1:]
    expected_ids = tuple(item.item_id for item in public_items)
    if tuple(selection.item_id for selection in selections) != expected_ids:
        raise AssistedRouteMachineError("selection_identity_mismatch")

    results: list[AssistedItemResult] = []
    procedures: list[str | None] = []
    receipts: list[ProvenanceReceipts] = []
    for selection, public_item, sealed_item, verified_item in zip(
        selections,
        public_items,
        sealed_items,
        verified_items,
        strict=True,
    ):
        selected_option = next(
            (
                option
                for option in public_item.options
                if option.option_id == selection.option_id
            ),
            None,
        )
        if selected_option is None:
            raise AssistedRouteMachineError("selection_option_invalid")
        is_correct = selection.option_id == sealed_item.correct_option_id
        possible_error = None
        if not is_correct:
            possible_error = dict(sealed_item.possible_errors).get(
                selection.option_id
            )
            if possible_error is None:
                raise AssistedRouteMachineError("selection_option_invalid")
        canonical_feedback = (
            *((possible_error,) if possible_error is not None else ()),
            sealed_item.reliable_method,
            *sealed_item.trusted_steps,
        )
        results.append(
            AssistedItemResult(
                itemId=selection.item_id,
                selectedOptionId=selection.option_id,
                selectedAnswer=selected_option.display_text,
                confidence=selection.confidence,
                correctOptionId=sealed_item.correct_option_id,
                correctAnswer=sealed_item.correct_answer,
                isCorrect=is_correct,
                possibleError=possible_error,
                reliableMethod=sealed_item.reliable_method,
                trustedSteps=sealed_item.trusted_steps,
                canonicalFeedback=canonical_feedback,
            )
        )
        procedures.append(
            verified_item.route_for_option(selection.option_id).procedure_id
        )
        receipts.append(verified_item.event_receipts)

    result_tuple = tuple(results)
    procedure_tuple = tuple(procedures)
    receipt_tuple = tuple(receipts)
    return AssistedRouteScore(
        route_id=route_id,
        final_correct=sum(item.is_correct for item in result_tuple),
        items=result_tuple,  # type: ignore[arg-type]
        selected_procedure_ids=procedure_tuple,  # type: ignore[arg-type]
        receipts=receipt_tuple,  # type: ignore[arg-type]
        material_sha256=material.batch_material_sha256,
    )


__all__ = [
    "AssistedRouteMachineError",
    "AssistedRouteScore",
    "public_assisted_batch",
    "require_assisted_material",
    "score_assisted_route",
]
