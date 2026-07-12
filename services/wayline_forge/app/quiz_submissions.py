"""Authenticated application service for durable two-pass quiz submissions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Callable, Final
import weakref

from pydantic import ValidationError

from services.wayline_forge.app.batch_material import VerifiedBatchMaterial
from services.wayline_forge.app.contracts import (
    FinalQuizResult,
    InitialSubmission,
    InitialSubmissionResult,
    RevisionSubmission,
)
from services.wayline_forge.app.evidence_reducer import EvidenceReplayError
from services.wayline_forge.app.profile_store import (
    EventLogCorruptionError,
    EventOrderError,
    IdempotencyConflictError as ProfileIdempotencyConflictError,
    IdentityStoreCorruptionError,
    OutboxReservationError,
    ProfileNotFoundError,
    ProfileStoreError,
    SemanticEventConflictError,
    SessionNotFoundError,
)
from services.wayline_forge.app.quiz_machine import (
    IdempotencyConflictError,
    InitialAlreadySubmittedError,
    InvalidQuizTransitionError,
    RevisionAlreadyUsedError,
    StaleQuizStateError,
    SubmissionValidationError,
    lock_initial,
    resolve_initial,
    submit_revision as apply_revision,
)
from services.wayline_forge.app.quiz_observations import (
    QuizObservationError,
    build_reveal_observations,
)
from services.wayline_forge.app.quiz_store import (
    QuizNotFoundError,
    QuizOwnershipError,
    QuizStoreBusyError,
    QuizStoreCorruptionError,
    QuizStoreError,
    QuizTransitionConflictError,
)


_COMMAND_LOCKS_GUARD = threading.Lock()
_COMMAND_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)


def _command_lock(
    quiz_store: object,
) -> threading.RLock:
    try:
        database = str(Path(getattr(quiz_store, "path")).resolve())
    except (AttributeError, TypeError, ValueError, OSError):
        database = f"store:{id(quiz_store)}"
    with _COMMAND_LOCKS_GUARD:
        lock = _COMMAND_LOCKS.get(database)
        if lock is None:
            lock = threading.RLock()
            _COMMAND_LOCKS[database] = lock
        return lock


class QuizSubmissionError(RuntimeError):
    """Stable, non-sensitive failure at the submission application boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "session_not_current",
            "batch_unavailable",
            "invalid_submission",
            "idempotency_conflict",
            "quiz_state_conflict",
            "storage_busy",
            "evidence_sync_unavailable",
            "integrity_failure",
        }
    )

    def __init__(self, code: str):
        if code not in self._CODES:
            raise ValueError("unknown quiz submission error code")
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code!r})"


class QuizSubmissionService:
    """Authenticate and durably apply learner quiz submissions."""

    def __init__(
        self,
        profile_store: object,
        quiz_store: object,
        *,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._profile_store = profile_store
        self._quiz_store = quiz_store
        self._utc_now = utc_now

    def submit_initial(
        self,
        submission: InitialSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> InitialSubmissionResult:
        """Persist and score one complete initial pass."""

        try:
            if type(submission) is not InitialSubmission:
                raise QuizSubmissionError("invalid_submission")
            try:
                batch_id = submission.batch_id
            except AttributeError:
                raise QuizSubmissionError("invalid_submission") from None
            with _command_lock(self._quiz_store):
                return self._submit_initial(
                    submission,
                    profile_id=profile_id,
                    current_session_id=current_session_id,
                )
        except QuizSubmissionError:
            raise
        except IdempotencyConflictError:
            raise QuizSubmissionError("idempotency_conflict") from None
        except (InitialAlreadySubmittedError, InvalidQuizTransitionError):
            raise QuizSubmissionError("quiz_state_conflict") from None
        except SubmissionValidationError:
            raise QuizSubmissionError("invalid_submission") from None
        except StaleQuizStateError:
            raise QuizSubmissionError("quiz_state_conflict") from None
        except QuizStoreBusyError:
            raise QuizSubmissionError("storage_busy") from None
        except QuizStoreCorruptionError:
            raise QuizSubmissionError("integrity_failure") from None
        except QuizTransitionConflictError:
            raise QuizSubmissionError("quiz_state_conflict") from None
        except QuizStoreError:
            raise QuizSubmissionError("integrity_failure") from None
        except (QuizObservationError, ValidationError):
            raise QuizSubmissionError("integrity_failure") from None

    def _submit_initial(
        self,
        submission: InitialSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> InitialSubmissionResult:
        if type(submission) is not InitialSubmission:
            raise QuizSubmissionError("invalid_submission")

        self._authenticate(profile_id, current_session_id)
        self._drain(profile_id)
        machine, material = self._load_bound_batch(
            submission.batch_id,
            profile_id,
        )
        locked = lock_initial(
            machine,
            submission,
            expected_version=machine.version,
        )
        if locked is not machine:
            try:
                stored = self._quiz_store.save_transition(
                    locked,
                    profile_id=profile_id,
                    expected_version=machine.version,
                )
                locked = stored.machine
            except (StaleQuizStateError, QuizTransitionConflictError):
                # A separate process may have won after our verified read.  One
                # authoritative reload is enough to replay the exact command or
                # fail closed; it never repeats the write optimistically.
                locked, material = self._reload_initial_after_contention(
                    submission,
                    profile_id,
                )

        if locked.initial_result is not None:
            return self._initial_result(locked)

        self._authenticate(profile_id, current_session_id)
        transition = resolve_initial(
            locked,
            material.sealed_quiz,
            expected_version=locked.version,
        )
        observations = ()
        observation_session_id = None
        if transition.public_result.wrong_count == 0:
            occurred_at = self._canonical_utc_now()
            observations = build_reveal_observations(
                material,
                transition.machine,
                transition.receipt,
                profile_id=profile_id,
                reveal_session_id=current_session_id,
                first_ordinal=self._quiz_store.next_profile_ordinal(profile_id),
                occurred_at=occurred_at,
            )
            observation_session_id = current_session_id
            self._authenticate(profile_id, current_session_id)
        try:
            stored = self._quiz_store.save_transition(
                transition.machine,
                profile_id=profile_id,
                expected_version=locked.version,
                receipt=transition.receipt,
                observation_events=observations,
                observation_session_id=observation_session_id,
            )
        except (StaleQuizStateError, QuizTransitionConflictError):
            replay, _ = self._reload_initial_after_contention(
                submission,
                profile_id,
            )
            if replay.initial_result is None:
                raise QuizSubmissionError("quiz_state_conflict") from None
            if replay.initial_result.wrong_count == 0:
                self._drain(profile_id)
            return self._initial_result(replay)
        if transition.public_result.wrong_count == 0:
            self._drain(profile_id)
        return self._initial_result(stored.machine)

    def submit_revision(
        self,
        submission: RevisionSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> FinalQuizResult:
        """Persist the sole complete revision and reveal verified item truth."""

        try:
            if type(submission) is not RevisionSubmission:
                raise QuizSubmissionError("invalid_submission")
            try:
                batch_id = submission.batch_id
            except AttributeError:
                raise QuizSubmissionError("invalid_submission") from None
            with _command_lock(self._quiz_store):
                return self._submit_revision(
                    submission,
                    profile_id=profile_id,
                    current_session_id=current_session_id,
                )
        except QuizSubmissionError:
            raise
        except IdempotencyConflictError:
            raise QuizSubmissionError("idempotency_conflict") from None
        except (RevisionAlreadyUsedError, InvalidQuizTransitionError):
            raise QuizSubmissionError("quiz_state_conflict") from None
        except SubmissionValidationError:
            raise QuizSubmissionError("invalid_submission") from None
        except StaleQuizStateError:
            raise QuizSubmissionError("quiz_state_conflict") from None
        except QuizStoreBusyError:
            raise QuizSubmissionError("storage_busy") from None
        except QuizStoreCorruptionError:
            raise QuizSubmissionError("integrity_failure") from None
        except QuizTransitionConflictError:
            raise QuizSubmissionError("quiz_state_conflict") from None
        except QuizStoreError:
            raise QuizSubmissionError("integrity_failure") from None
        except (QuizObservationError, ValidationError):
            raise QuizSubmissionError("integrity_failure") from None

    def _submit_revision(
        self,
        submission: RevisionSubmission,
        *,
        profile_id: str,
        current_session_id: str,
    ) -> FinalQuizResult:
        if type(submission) is not RevisionSubmission:
            raise QuizSubmissionError("invalid_submission")

        self._authenticate(profile_id, current_session_id)
        self._drain(profile_id)
        machine, material = self._load_bound_batch(
            submission.batch_id,
            profile_id,
        )
        transition = apply_revision(
            machine,
            submission,
            material.sealed_quiz,
            expected_version=machine.version,
        )
        if machine.revision_submission is not None:
            return FinalQuizResult.model_validate(
                transition.final_result.to_public_dict()
            )
        occurred_at = self._canonical_utc_now()
        observations = build_reveal_observations(
            material,
            transition.machine,
            transition.receipt,
            profile_id=profile_id,
            reveal_session_id=current_session_id,
            first_ordinal=self._quiz_store.next_profile_ordinal(profile_id),
            occurred_at=occurred_at,
        )
        self._authenticate(profile_id, current_session_id)
        try:
            stored = self._quiz_store.save_transition(
                transition.machine,
                profile_id=profile_id,
                expected_version=machine.version,
                receipt=transition.receipt,
                observation_events=observations,
                observation_session_id=current_session_id,
            )
        except (StaleQuizStateError, QuizTransitionConflictError):
            replay_machine, replay_material = self._load_bound_batch(
                submission.batch_id,
                profile_id,
            )
            replay = apply_revision(
                replay_machine,
                submission,
                replay_material.sealed_quiz,
                expected_version=replay_machine.version,
            )
            if replay_machine.revision_submission is None:
                raise QuizSubmissionError("quiz_state_conflict") from None
            self._drain(profile_id)
            return FinalQuizResult.model_validate(
                replay.final_result.to_public_dict()
            )
        self._drain(profile_id)
        return FinalQuizResult.model_validate(
            stored.machine.final_result.to_public_dict()
        )

    def _authenticate(self, profile_id: str, session_id: str) -> None:
        try:
            profile = self._profile_store.load_profile(profile_id)
            session = self._profile_store.load_session(session_id)
            current = self._profile_store.load_open_session(profile.profile_id)
        except (ProfileNotFoundError, SessionNotFoundError, ValueError):
            raise QuizSubmissionError("session_not_current") from None
        except (IdentityStoreCorruptionError, ProfileStoreError):
            raise QuizSubmissionError("integrity_failure") from None
        if (
            session.profile_id != profile.profile_id
            or session.closed_at is not None
            or current is None
            or current.session_id != session.session_id
            or current.profile_id != profile.profile_id
            or current.closed_at is not None
        ):
            raise QuizSubmissionError("session_not_current")

    def _drain(self, profile_id: str) -> None:
        try:
            self._quiz_store.drain_observations(
                profile_id,
                profile_store=self._profile_store,
            )
        except QuizStoreBusyError:
            raise QuizSubmissionError("storage_busy") from None
        except (
            QuizStoreCorruptionError,
            EvidenceReplayError,
            EventLogCorruptionError,
            IdentityStoreCorruptionError,
            ProfileIdempotencyConflictError,
            SemanticEventConflictError,
            EventOrderError,
            OutboxReservationError,
        ):
            raise QuizSubmissionError("integrity_failure") from None
        except (QuizStoreError, ProfileStoreError, RuntimeError):
            raise QuizSubmissionError("evidence_sync_unavailable") from None

    def _load_bound_batch(
        self,
        batch_id: str,
        profile_id: str,
    ) -> tuple[object, VerifiedBatchMaterial]:
        try:
            machine = self._quiz_store.load(batch_id, profile_id=profile_id)
            material = self._quiz_store.load_batch_material(
                batch_id,
                profile_id=profile_id,
            )
            machine = self._quiz_store.load(batch_id, profile_id=profile_id)
        except (QuizNotFoundError, QuizOwnershipError, ValueError):
            raise QuizSubmissionError("batch_unavailable") from None
        except QuizStoreBusyError:
            raise QuizSubmissionError("storage_busy") from None
        except QuizStoreError:
            raise QuizSubmissionError("integrity_failure") from None
        try:
            origin = self._profile_store.load_session(material.context.session_id)
        except (SessionNotFoundError, ValueError, ProfileStoreError):
            raise QuizSubmissionError("integrity_failure") from None
        if (
            machine.batch_id != batch_id
            or material.batch_id != batch_id
            or material.context.profile_id != profile_id
            or origin.profile_id != profile_id
        ):
            raise QuizSubmissionError("integrity_failure")
        return machine, material

    def _reload_initial_after_contention(
        self,
        submission: InitialSubmission,
        profile_id: str,
    ) -> tuple[object, VerifiedBatchMaterial]:
        machine, material = self._load_bound_batch(
            submission.batch_id,
            profile_id,
        )
        replay = lock_initial(
            machine,
            submission,
            expected_version=machine.version,
        )
        if replay is not machine:
            raise QuizSubmissionError("quiz_state_conflict")
        return replay, material

    @staticmethod
    def _initial_result(machine: object) -> InitialSubmissionResult:
        payload = machine.initial_result.to_public_dict()
        payload["finalResult"] = (
            None
            if (
                machine.final_result is None
                or machine.initial_result.wrong_count != 0
            )
            else FinalQuizResult.model_validate(
                machine.final_result.to_public_dict()
            ).model_dump(mode="json", by_alias=True)
        )
        return InitialSubmissionResult.model_validate(payload)

    def _canonical_utc_now(self) -> str:
        try:
            value = self._utc_now()
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() != timedelta(0)
            ):
                raise ValueError("clock did not return an aware UTC datetime")
            return value.isoformat(timespec="microseconds").replace(
                "+00:00",
                "Z",
            )
        except Exception:
            raise QuizSubmissionError("integrity_failure") from None
