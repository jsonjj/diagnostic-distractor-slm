"""Authenticated, read-only reconstruction of the public runtime state."""

from __future__ import annotations

import sqlite3
from typing import Final

from services.wayline_forge.app.campaign_catalog import (
    CAMPAIGN_CATALOG_V1_SHA256,
    CampaignCatalog,
    CampaignCatalogError,
)
from services.wayline_forge.app.contracts import RuntimeState
from services.wayline_forge.app.evidence_reducer import (
    EvidenceReplayError,
    reduce_events,
)
from services.wayline_forge.app.profile_store import (
    EventLogCorruptionError,
    IdentityStoreCorruptionError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
    SessionNotFoundError,
)
from services.wayline_forge.app.quiz_store import (
    QuizStore,
    QuizStoreBusyError,
    QuizStoreCorruptionError,
    QuizStoreError,
)


class RuntimeStateError(RuntimeError):
    """Stable, non-sensitive failure at the runtime-state boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "session_not_current",
            "runtime_state_unavailable",
            "storage_busy",
            "catalog_conflict",
            "integrity_failure",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown runtime-state error code")
        self.code = code
        super().__init__(code)


class RuntimeStateAuthenticationError(RuntimeStateError):
    """The supplied local profile/session pair is not the current identity."""


class RuntimeStateUnavailableError(RuntimeStateError):
    """Durable learner evidence cannot produce a runtime state."""


class RuntimeStateCatalogError(RuntimeStateError):
    """Durable campaign state disagrees with the hash-pinned catalog."""


class RuntimeStateIntegrityError(RuntimeStateError):
    """Authenticated durable records or dependencies cannot be verified."""


class RuntimeStateService:
    """Build the strict learner-safe state used to resume the Mac client."""

    def __init__(
        self,
        profile_store: ProfileStore,
        quiz_store: QuizStore,
    ) -> None:
        self._profile_store = profile_store
        self._quiz_store = quiz_store
        try:
            self._catalog = CampaignCatalog.packaged_v1()
        except CampaignCatalogError:
            raise RuntimeStateCatalogError("catalog_conflict") from None
        except Exception:
            raise RuntimeStateIntegrityError("integrity_failure") from None

    def get(self, profile_id: str, session_id: str) -> RuntimeState:
        """Authenticate and return a strict state or one stable typed failure."""

        try:
            return self._get(profile_id, session_id)
        except RuntimeStateError:
            raise
        except QuizStoreBusyError:
            raise RuntimeStateUnavailableError("storage_busy") from None
        except sqlite3.OperationalError as error:
            if _caused_by_busy_storage(error):
                raise RuntimeStateUnavailableError("storage_busy") from None
            raise RuntimeStateIntegrityError("integrity_failure") from None
        except (EventLogCorruptionError, IdentityStoreCorruptionError):
            raise RuntimeStateIntegrityError("integrity_failure") from None
        except ProfileStoreError as error:
            if _caused_by_busy_storage(error):
                raise RuntimeStateUnavailableError("storage_busy") from None
            raise RuntimeStateIntegrityError("integrity_failure") from None
        except (
            QuizStoreCorruptionError,
            sqlite3.DatabaseError,
            QuizStoreError,
            EvidenceReplayError,
            AttributeError,
            TypeError,
            ValueError,
        ):
            raise RuntimeStateIntegrityError("integrity_failure") from None
        except Exception:
            raise RuntimeStateIntegrityError("integrity_failure") from None

    def _get(self, profile_id: str, session_id: str) -> RuntimeState:
        """Reconstruct the state after boundary-level error translation."""

        try:
            profile = self._profile_store.load_profile(profile_id)
            session = self._profile_store.load_session(session_id)
            current = self._profile_store.load_open_session(profile.profile_id)
        except (ProfileNotFoundError, SessionNotFoundError, ValueError):
            raise RuntimeStateAuthenticationError(
                "session_not_current"
            ) from None

        if (
            session.profile_id != profile.profile_id
            or session.closed_at is not None
        ):
            raise RuntimeStateAuthenticationError("session_not_current")
        if (
            current is None
            or current.session_id != session.session_id
            or current.profile_id != profile.profile_id
            or current.closed_at is not None
        ):
            raise RuntimeStateAuthenticationError("session_not_current")

        events = self._profile_store.load_events(profile.profile_id)
        if not events:
            raise RuntimeStateUnavailableError("runtime_state_unavailable")
        try:
            learner_state = reduce_events(events)
        except (EvidenceReplayError, TypeError, ValueError):
            raise RuntimeStateIntegrityError("integrity_failure") from None
        if learner_state.profile_id != profile.profile_id:
            raise RuntimeStateIntegrityError("integrity_failure")
        active_world_id = learner_state.active_world_id
        if active_world_id is None:
            raise RuntimeStateUnavailableError("runtime_state_unavailable")

        catalog_world = next(
            (
                world
                for world in self._catalog.worlds
                if world.world_id == active_world_id
            ),
            None,
        )
        world_evidence = learner_state.world(active_world_id)
        if (
            catalog_world is None
            or world_evidence.activated_ordinal < 1
            or world_evidence.core_subskill_ids
            != catalog_world.core_subskill_ids
            or world_evidence.curriculum_receipt
            != self._catalog.curriculum_receipt
        ):
            raise RuntimeStateCatalogError("catalog_conflict")

        resumable_batch_id = self._quiz_store.resumable_batch_id(
            profile.profile_id
        )
        return RuntimeState(
            schemaVersion="wayline.v1",
            profileId=profile.profile_id,
            sessionId=session.session_id,
            activeWorldId=catalog_world.world_id,
            campaignOrdinal=catalog_world.sequence,
            resumableBatchId=resumable_batch_id,
            campaignCatalogSha256=CAMPAIGN_CATALOG_V1_SHA256,
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
    "RuntimeStateAuthenticationError",
    "RuntimeStateCatalogError",
    "RuntimeStateError",
    "RuntimeStateIntegrityError",
    "RuntimeStateService",
    "RuntimeStateUnavailableError",
]
