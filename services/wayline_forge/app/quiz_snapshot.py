"""Authenticated public reload snapshots for persisted Wayline quizzes."""

from __future__ import annotations

import sqlite3
from typing import Final, TYPE_CHECKING

from pydantic import ValidationError

from services.wayline_forge.app.contracts import (
    FinalQuizResult,
    InitialSubmission,
    InitialSubmissionResult,
    QuizSnapshot,
    QuizSnapshotState,
    RevisionSubmission,
)
from services.wayline_forge.app.profile_store import (
    IdentityStoreCorruptionError,
    ProfileNotFoundError,
    ProfileStoreError,
    SessionNotFoundError,
)
from services.wayline_forge.app.quiz_machine import QuizState
from services.wayline_forge.app.quiz_store import (
    QuizNotFoundError,
    QuizOwnershipError,
    QuizStoreBusyError,
    QuizStoreCorruptionError,
    QuizStoreError,
)

if TYPE_CHECKING:
    from services.wayline_forge.app.batch_material import VerifiedBatchMaterial
    from services.wayline_forge.app.profile_store import ProfileStore
    from services.wayline_forge.app.quiz_machine import (
        QuizMachine,
        QuizSubmission,
    )
    from services.wayline_forge.app.quiz_store import QuizStore


_ACCESS_CODE = "snapshot_unavailable"


class QuizSnapshotError(RuntimeError):
    """Stable, non-sensitive failure at the public snapshot boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            _ACCESS_CODE,
            "snapshot_not_ready",
            "storage_busy",
            "integrity_failure",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown quiz snapshot error code")
        self.code = code
        super().__init__(code)


class QuizSnapshotAccessError(QuizSnapshotError):
    """The named local identity and batch are not jointly accessible."""


class QuizSnapshotUnavailableError(QuizSnapshotError):
    """The batch has no public reload state."""


class QuizSnapshotIntegrityError(QuizSnapshotError):
    """Authenticated persisted records cannot form one truthful snapshot."""


class QuizSnapshotService:
    """Authenticate and reconstruct public quiz reload state."""

    def __init__(self, profile_store: "ProfileStore", quiz_store: "QuizStore"):
        self._profile_store = profile_store
        self._quiz_store = quiz_store

    def get(
        self,
        profile_id: str,
        current_session_id: str,
        batch_id: str,
    ) -> "QuizSnapshot":
        """Return a public snapshot or one stable non-sensitive failure."""

        try:
            return self._get(profile_id, current_session_id, batch_id)
        except QuizSnapshotError:
            raise
        except QuizStoreBusyError:
            raise QuizSnapshotUnavailableError("storage_busy") from None
        except sqlite3.OperationalError as error:
            if _caused_by_busy_storage(error):
                raise QuizSnapshotUnavailableError("storage_busy") from None
            raise QuizSnapshotIntegrityError("integrity_failure") from None
        except IdentityStoreCorruptionError:
            raise QuizSnapshotIntegrityError("integrity_failure") from None
        except ProfileStoreError as error:
            if _caused_by_busy_storage(error):
                raise QuizSnapshotUnavailableError("storage_busy") from None
            raise QuizSnapshotIntegrityError("integrity_failure") from None
        except (
            QuizStoreCorruptionError,
            sqlite3.DatabaseError,
            QuizStoreError,
            ValidationError,
            AttributeError,
            TypeError,
            ValueError,
        ):
            raise QuizSnapshotIntegrityError("integrity_failure") from None
        except Exception:
            raise QuizSnapshotIntegrityError("integrity_failure") from None

    def _get(
        self,
        profile_id: str,
        current_session_id: str,
        batch_id: str,
    ) -> "QuizSnapshot":
        self._authenticate_current_session(profile_id, current_session_id)

        try:
            machine = self._quiz_store.load(
                batch_id,
                profile_id=profile_id,
            )
        except (QuizNotFoundError, QuizOwnershipError, ValueError):
            raise QuizSnapshotAccessError(_ACCESS_CODE) from None
        except QuizStoreBusyError:
            raise QuizSnapshotUnavailableError("storage_busy") from None
        except QuizStoreError:
            raise QuizSnapshotIntegrityError("integrity_failure") from None

        if machine.state is QuizState.PREPARING:
            raise QuizSnapshotUnavailableError("snapshot_not_ready")

        try:
            material = self._quiz_store.load_batch_material(
                batch_id,
                profile_id=profile_id,
            )
            # A transition may commit between the two public reads.  Re-read the
            # immutable machine after material verification so the returned
            # state is never older than the material check.
            machine = self._quiz_store.load(
                batch_id,
                profile_id=profile_id,
            )
        except (QuizNotFoundError, QuizOwnershipError, ValueError):
            raise QuizSnapshotAccessError(_ACCESS_CODE) from None
        except QuizStoreBusyError:
            raise QuizSnapshotUnavailableError("storage_busy") from None
        except QuizStoreError:
            raise QuizSnapshotIntegrityError("integrity_failure") from None

        self._validate_origin_and_bindings(
            profile_id,
            batch_id,
            machine,
            material,
        )
        try:
            return _public_snapshot(machine, material)
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise QuizSnapshotIntegrityError("integrity_failure") from None

    def _authenticate_current_session(
        self,
        profile_id: str,
        current_session_id: str,
    ) -> None:
        try:
            profile = self._profile_store.load_profile(profile_id)
            session = self._profile_store.load_session(current_session_id)
            current = self._profile_store.load_open_session(profile.profile_id)
        except (ProfileNotFoundError, SessionNotFoundError, ValueError):
            raise QuizSnapshotAccessError(_ACCESS_CODE) from None
        except IdentityStoreCorruptionError:
            raise QuizSnapshotIntegrityError("integrity_failure") from None
        if (
            session.profile_id != profile.profile_id
            or session.closed_at is not None
            or current is None
            or current.profile_id != profile.profile_id
            or current.session_id != session.session_id
            or current.closed_at is not None
        ):
            raise QuizSnapshotAccessError(_ACCESS_CODE)

    def _validate_origin_and_bindings(
        self,
        profile_id: str,
        batch_id: str,
        machine: "QuizMachine",
        material: "VerifiedBatchMaterial",
    ) -> None:
        try:
            origin = self._profile_store.load_session(
                material.context.session_id
            )
        except (SessionNotFoundError, ValueError, IdentityStoreCorruptionError):
            raise QuizSnapshotIntegrityError("integrity_failure") from None

        public_layouts = tuple(
            (
                item.item_id,
                tuple(option.option_id for option in item.options),
            )
            for item in material.public_batch.items
        )
        machine_layouts = tuple(
            (layout.item_id, layout.option_ids)
            for layout in machine.item_layouts
        )
        if (
            machine.batch_id != batch_id
            or material.batch_id != batch_id
            or material.public_batch.batch_id != batch_id
            or material.context.profile_id != profile_id
            or origin.profile_id != profile_id
            or machine_layouts != public_layouts
        ):
            raise QuizSnapshotIntegrityError("integrity_failure")


def _submission_contract(
    model_type: type[InitialSubmission] | type[RevisionSubmission],
    submission: "QuizSubmission | None",
) -> InitialSubmission | RevisionSubmission | None:
    if submission is None:
        return None
    return model_type.model_validate(
        {
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
        }
    )


def _final_result_contract(machine: "QuizMachine") -> FinalQuizResult | None:
    if machine.final_result is None:
        return None
    return FinalQuizResult.model_validate(
        machine.final_result.to_public_dict()
    )


def _initial_result_contract(
    machine: "QuizMachine",
    final_result: FinalQuizResult | None,
) -> InitialSubmissionResult | None:
    if machine.initial_result is None:
        return None
    payload = machine.initial_result.to_public_dict()
    payload["finalResult"] = (
        final_result.model_dump(mode="json", by_alias=True)
        if machine.initial_result.wrong_count == 0 and final_result is not None
        else None
    )
    return InitialSubmissionResult.model_validate(payload)


def _public_snapshot(
    machine: "QuizMachine",
    material: "VerifiedBatchMaterial",
) -> QuizSnapshot:
    try:
        quiz_state = QuizSnapshotState(machine.state.value)
    except ValueError:
        raise QuizSnapshotIntegrityError("integrity_failure") from None

    final_result = _final_result_contract(machine)
    initial_result = _initial_result_contract(machine, final_result)
    return QuizSnapshot(
        schemaVersion="wayline.v1",
        batchId=machine.batch_id,
        quizState=quiz_state,
        stateVersion=machine.version,
        publicBatch=material.public_batch,
        initialSubmission=_submission_contract(
            InitialSubmission,
            machine.initial_submission,
        ),
        initialResult=initial_result,
        revisionSubmission=_submission_contract(
            RevisionSubmission,
            machine.revision_submission,
        ),
        finalResult=final_result,
    )


def _caused_by_busy_storage(error: BaseException) -> bool:
    """Classify direct or wrapped SQLite lock errors without exposing text."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sqlite3.OperationalError):
            normalized = str(current).casefold()
            return "locked" in normalized or "busy" in normalized
        current = current.__cause__ or current.__context__
    return False


__all__ = [
    "QuizSnapshotAccessError",
    "QuizSnapshotError",
    "QuizSnapshotIntegrityError",
    "QuizSnapshotService",
    "QuizSnapshotUnavailableError",
]
