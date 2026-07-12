"""Authenticated, redacted application boundary for local-profile deletion."""

from __future__ import annotations

import sqlite3
from typing import Final

from services.wayline_forge.app.profile_store import (
    CampaignStateConflictError,
    IdentityStoreCorruptionError,
    LocalProfile,
    LocalSession,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
    SessionNotFoundError,
)


class ProfileDeletionError(RuntimeError):
    """Stable, non-sensitive failure at the profile-deletion boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "session_not_current",
            "storage_busy",
            "integrity_failure",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown profile deletion error code")
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code!r})"


class ProfileDeletionService:
    """Delete only the profile owned by the exact current local session."""

    def __init__(self, profile_store: ProfileStore) -> None:
        if not isinstance(profile_store, ProfileStore):
            raise TypeError("profile_store must be a ProfileStore")
        self._profile_store = profile_store

    def delete(self, profile_id: str, current_session_id: str) -> None:
        """Authenticate immediately before invoking the store-owned deletion."""

        try:
            self._delete(profile_id, current_session_id)
        except ProfileDeletionError:
            raise
        except CampaignStateConflictError:
            raise ProfileDeletionError("session_not_current") from None
        except sqlite3.OperationalError as error:
            code = (
                "storage_busy"
                if _caused_by_busy_storage(error)
                else "integrity_failure"
            )
            raise ProfileDeletionError(code) from None
        except (IdentityStoreCorruptionError, sqlite3.DatabaseError):
            raise ProfileDeletionError("integrity_failure") from None
        except ProfileStoreError as error:
            code = (
                "storage_busy"
                if _caused_by_busy_storage(error)
                else "integrity_failure"
            )
            raise ProfileDeletionError(code) from None
        except (AttributeError, TypeError, ValueError):
            raise ProfileDeletionError("integrity_failure") from None
        except Exception:
            raise ProfileDeletionError("integrity_failure") from None

    def _delete(self, profile_id: str, current_session_id: str) -> None:
        try:
            profile = self._profile_store.load_profile(profile_id)
            session = self._profile_store.load_session(current_session_id)
        except (ProfileNotFoundError, SessionNotFoundError, ValueError):
            raise ProfileDeletionError("session_not_current") from None

        if type(profile) is not LocalProfile or profile.profile_id != profile_id:
            raise ProfileDeletionError("integrity_failure")
        if type(session) is not LocalSession or session.session_id != current_session_id:
            raise ProfileDeletionError("integrity_failure")
        if session.profile_id != profile.profile_id or session.closed_at is not None:
            raise ProfileDeletionError("session_not_current")

        try:
            current = self._profile_store.load_open_session(profile.profile_id)
        except (ProfileNotFoundError, SessionNotFoundError):
            raise ProfileDeletionError("session_not_current") from None
        if current is None:
            raise ProfileDeletionError("session_not_current")
        if type(current) is not LocalSession:
            raise ProfileDeletionError("integrity_failure")
        if (
            current.profile_id != profile.profile_id
            or current.session_id != session.session_id
            or current.closed_at is not None
        ):
            raise ProfileDeletionError("session_not_current")
        if current != session:
            raise ProfileDeletionError("integrity_failure")

        self._profile_store.delete_profile(
            profile.profile_id,
            expected_session_id=session.session_id,
        )


def _caused_by_busy_storage(error: BaseException) -> bool:
    """Classify direct or wrapped SQLite contention without exposing its text."""

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
    "ProfileDeletionError",
    "ProfileDeletionService",
]
