"""Authenticated query boundary for deterministic boss-gate evidence."""

from __future__ import annotations

import sqlite3
from typing import Final

from services.wayline_forge.app.boss_gate import evaluate_boss_gate
from services.wayline_forge.app.campaign_catalog import (
    CampaignCatalog,
    CampaignCatalogError,
)
from services.wayline_forge.app.contracts import BossGateResult
from services.wayline_forge.app.evidence_reducer import (
    EvidenceReplayError,
    LearnerState,
    reduce_events,
)
from services.wayline_forge.app.events import (
    LearningEvent,
    ObservationEvent,
    WorldActivatedEvent,
)
from services.wayline_forge.app.profile_store import (
    EventLogCorruptionError,
    EventOrderError,
    IdempotencyConflictError,
    IdentityStoreCorruptionError,
    OutboxReservationError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
    SemanticEventConflictError,
    SessionNotFoundError,
)
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreBusyError,
    QuizStoreCorruptionError,
    QuizStoreError,
)


class BossGateQueryError(RuntimeError):
    """Stable, non-sensitive failure at the boss-gate query boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "session_not_current",
            "evidence_sync_unavailable",
            "storage_busy",
            "catalog_conflict",
            "integrity_failure",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown boss-gate query error code")
        self.code = code
        super().__init__(code)


class BossGateQueryService:
    """Synchronize durable evidence and return one learner-safe boss gate."""

    def __init__(
        self,
        profile_store: ProfileStore,
        quiz_store: QuizStore,
    ) -> None:
        self._profile_store = profile_store
        self._quiz_store = quiz_store
        try:
            self._catalog = CampaignCatalog.packaged_v1()
        except CampaignCatalogError as error:
            raise BossGateQueryError("catalog_conflict") from error

    def get(
        self,
        *,
        profile_id: str,
        current_session_id: str,
        world_id: str,
    ) -> BossGateResult:
        """Return the current world's gate after exactly-once outbox repair."""

        self._authenticate(profile_id, current_session_id)
        self._drain(profile_id)
        events = self._load_events(profile_id)
        try:
            state = reduce_events(events)
        except (EvidenceReplayError, TypeError, ValueError) as error:
            raise BossGateQueryError("integrity_failure") from error
        if (
            not events
            or state.profile_id != profile_id
            or state.events != events
        ):
            raise BossGateQueryError("integrity_failure")

        self._require_current_catalog_world(
            profile_id=profile_id,
            requested_world_id=world_id,
            events=events,
            state=state,
        )
        try:
            return evaluate_boss_gate(state, world_id)
        except (TypeError, ValueError) as error:
            raise BossGateQueryError("integrity_failure") from error

    def _authenticate(
        self,
        profile_id: str,
        current_session_id: str,
    ) -> None:
        try:
            profile = self._profile_store.load_profile(profile_id)
            session = self._profile_store.load_session(current_session_id)
            current = self._profile_store.load_open_session(profile.profile_id)
        except (ProfileNotFoundError, SessionNotFoundError, ValueError) as error:
            raise BossGateQueryError("session_not_current") from error
        except IdentityStoreCorruptionError as error:
            raise BossGateQueryError("integrity_failure") from error
        except sqlite3.OperationalError as error:
            code = (
                "storage_busy"
                if _caused_by_busy_storage(error)
                else "integrity_failure"
            )
            raise BossGateQueryError(code) from error
        except (sqlite3.DatabaseError, ProfileStoreError) as error:
            raise BossGateQueryError("integrity_failure") from error

        if (
            profile.profile_id != profile_id
            or session.session_id != current_session_id
            or session.profile_id != profile.profile_id
            or session.closed_at is not None
            or current is None
            or current.profile_id != profile.profile_id
            or current.session_id != session.session_id
            or current.closed_at is not None
        ):
            raise BossGateQueryError("session_not_current")

    def _drain(self, profile_id: str) -> None:
        try:
            self._quiz_store.drain_observations(
                profile_id,
                profile_store=self._profile_store,
            )
        except QuizStoreBusyError as error:
            raise BossGateQueryError("storage_busy") from error
        except QuizStoreCorruptionError as error:
            raise BossGateQueryError("integrity_failure") from error
        except sqlite3.OperationalError as error:
            code = (
                "storage_busy"
                if _caused_by_busy_storage(error)
                else "integrity_failure"
            )
            raise BossGateQueryError(code) from error
        except (
            EventLogCorruptionError,
            IdentityStoreCorruptionError,
            IdempotencyConflictError,
            SemanticEventConflictError,
            EventOrderError,
            OutboxReservationError,
            EvidenceReplayError,
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
        ) as error:
            raise BossGateQueryError("integrity_failure") from error
        except ProfileStoreError as error:
            code = "storage_busy" if _caused_by_busy_storage(error) else (
                "evidence_sync_unavailable"
            )
            raise BossGateQueryError(code) from error
        except QuizStoreError as error:
            raise BossGateQueryError("evidence_sync_unavailable") from error
        except RuntimeError as error:
            raise BossGateQueryError("evidence_sync_unavailable") from error

    def _load_events(self, profile_id: str) -> tuple[LearningEvent, ...]:
        try:
            return self._profile_store.load_events(profile_id)
        except sqlite3.OperationalError as error:
            code = (
                "storage_busy"
                if _caused_by_busy_storage(error)
                else "integrity_failure"
            )
            raise BossGateQueryError(code) from error
        except (
            EventLogCorruptionError,
            IdentityStoreCorruptionError,
            sqlite3.DatabaseError,
            ProfileStoreError,
            TypeError,
            ValueError,
        ) as error:
            raise BossGateQueryError("integrity_failure") from error

    def _require_current_catalog_world(
        self,
        *,
        profile_id: str,
        requested_world_id: str,
        events: tuple[LearningEvent, ...],
        state: LearnerState,
    ) -> None:
        active_world_id: str | None = None
        active_sequence = 0
        activated_world_ids: list[str] = []

        for event in events:
            if event.profile_id != profile_id:
                raise BossGateQueryError("integrity_failure")
            if isinstance(event, WorldActivatedEvent):
                expected_sequence = active_sequence + 1
                if expected_sequence > len(self._catalog.worlds):
                    raise BossGateQueryError("catalog_conflict")
                expected = self._catalog.worlds[expected_sequence - 1]
                if (
                    event.world_id != expected.world_id
                    or event.battle_id != "campaign-map"
                    or event.core_subskill_ids != expected.core_subskill_ids
                    or event.curriculum_receipt
                    != self._catalog.curriculum_receipt
                ):
                    raise BossGateQueryError("catalog_conflict")
                active_sequence = expected_sequence
                active_world_id = expected.world_id
                activated_world_ids.append(expected.world_id)
                continue

            if active_world_id is None:
                raise BossGateQueryError("catalog_conflict")
            if isinstance(event, ObservationEvent):
                if event.world_id != active_world_id and (
                    not event.is_transfer
                    or event.world_id not in activated_world_ids
                ):
                    raise BossGateQueryError("catalog_conflict")
            elif event.world_id != active_world_id:
                raise BossGateQueryError("catalog_conflict")

        if (
            active_world_id is None
            or state.active_world_id != active_world_id
            or requested_world_id != active_world_id
        ):
            raise BossGateQueryError("catalog_conflict")

        for sequence, activated_world_id in enumerate(
            activated_world_ids,
            start=1,
        ):
            expected = self._catalog.worlds[sequence - 1]
            derived = state.world(activated_world_id)
            if (
                activated_world_id != expected.world_id
                or derived.activated_ordinal < 1
                or derived.core_subskill_ids != expected.core_subskill_ids
                or derived.curriculum_receipt
                != self._catalog.curriculum_receipt
            ):
                raise BossGateQueryError("catalog_conflict")


def _caused_by_busy_storage(error: BaseException) -> bool:
    """Classify wrapped SQLite lock failures without exposing their text."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sqlite3.OperationalError):
            normalized = str(current).casefold()
            return "locked" in normalized or "busy" in normalized
        current = current.__cause__ or current.__context__
    return False


__all__ = ["BossGateQueryError", "BossGateQueryService"]
