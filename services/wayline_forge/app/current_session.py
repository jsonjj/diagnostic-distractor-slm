"""Framework-independent resolution of the exact current local session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
import sqlite3
from typing import Callable, Final, TypeVar

from services.wayline_forge.app.profile_store import (
    IdentityStoreCorruptionError,
    LOCAL_PROFILE_SCHEMA_VERSION,
    LOCAL_SESSION_SCHEMA_VERSION,
    LocalProfile,
    LocalSession,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
    SessionNotFoundError,
)


_IDENTIFIER_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}",
    re.ASCII,
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}", re.ASCII)
_CANONICAL_UTC_PATTERN = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{6})?Z",
    re.ASCII,
)


class CurrentSessionError(RuntimeError):
    """Stable, non-sensitive failure at the current-session boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "session_not_current",
            "storage_busy",
            "integrity_failure",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown current-session error code")
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"CurrentSessionError({self.code!r})"


@dataclass(frozen=True, slots=True)
class ResolvedCurrentSession:
    """The only identity derived from a validated current local session."""

    profile_id: str
    session_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.profile_id)
        _require_identifier(self.session_id)


_Result = TypeVar("_Result")


class CurrentSessionResolver:
    """Resolve a public session ID to its exact current profile authority."""

    def __init__(self, profile_store: ProfileStore) -> None:
        if not isinstance(profile_store, ProfileStore):
            raise TypeError("profile_store must be a ProfileStore")
        self._profile_store = profile_store

    def resolve(self, session_id: str) -> ResolvedCurrentSession:
        """Return the current identity or one redacted stable failure."""

        try:
            requested_session_id = _require_identifier(session_id)
        except (TypeError, ValueError):
            raise CurrentSessionError("session_not_current") from None

        try:
            session = self._profile_store.load_session(requested_session_id)
        except SessionNotFoundError:
            raise CurrentSessionError("session_not_current") from None
        except ProfileNotFoundError:
            raise CurrentSessionError("integrity_failure") from None
        except Exception as error:
            self._raise_translated(error)

        try:
            _validate_local_session(session)
        except (TypeError, ValueError):
            raise CurrentSessionError("integrity_failure") from None
        if session.session_id != requested_session_id:
            raise CurrentSessionError("integrity_failure")
        if session.closed_at is not None:
            raise CurrentSessionError("session_not_current")

        profile = self._read_authority(
            lambda: self._profile_store.load_profile(session.profile_id)
        )
        try:
            _validate_local_profile(profile)
        except (TypeError, ValueError):
            raise CurrentSessionError("integrity_failure") from None
        if profile.profile_id != session.profile_id:
            raise CurrentSessionError("session_not_current")

        current = self._read_authority(
            lambda: self._profile_store.load_open_session(profile.profile_id)
        )
        if current is None:
            raise CurrentSessionError("session_not_current")
        try:
            _validate_local_session(current)
        except (TypeError, ValueError):
            raise CurrentSessionError("integrity_failure") from None
        if (
            current.profile_id != profile.profile_id
            or current.session_id != session.session_id
            or current.closed_at is not None
        ):
            raise CurrentSessionError("session_not_current")
        if current != session:
            raise CurrentSessionError("integrity_failure")

        try:
            return ResolvedCurrentSession(
                profile_id=profile.profile_id,
                session_id=session.session_id,
            )
        except (TypeError, ValueError):
            raise CurrentSessionError("integrity_failure") from None

    def _read_authority(self, operation: Callable[[], _Result]) -> _Result:
        try:
            return operation()
        except (ProfileNotFoundError, SessionNotFoundError):
            raise CurrentSessionError("integrity_failure") from None
        except Exception as error:
            self._raise_translated(error)

    @staticmethod
    def _raise_translated(error: BaseException) -> None:
        if _caused_by_busy_storage(error):
            raise CurrentSessionError("storage_busy") from None
        if isinstance(
            error,
            (
                IdentityStoreCorruptionError,
                ProfileStoreError,
                sqlite3.DatabaseError,
                AttributeError,
                TypeError,
                ValueError,
            ),
        ):
            raise CurrentSessionError("integrity_failure") from None
        raise CurrentSessionError("integrity_failure") from None


def _require_identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError("identifier is invalid")
    return value


def _require_canonical_utc(value: object) -> str:
    if type(value) is not str or _CANONICAL_UTC_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp is invalid")
    timestamp_format = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else (
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        datetime.strptime(value, timestamp_format)
    except ValueError as error:
        raise ValueError("timestamp is invalid") from error
    return value


def _validate_local_profile(profile: object) -> None:
    if type(profile) is not LocalProfile:
        raise TypeError("profile authority has the wrong type")
    if profile.schema_version != LOCAL_PROFILE_SCHEMA_VERSION:
        raise ValueError("profile authority has the wrong version")
    _require_identifier(profile.profile_id)
    _require_canonical_utc(profile.created_at)


def _validate_local_session(session: object) -> None:
    if type(session) is not LocalSession:
        raise TypeError("session authority has the wrong type")
    if session.schema_version != LOCAL_SESSION_SCHEMA_VERSION:
        raise ValueError("session authority has the wrong version")
    _require_identifier(session.session_id)
    _require_identifier(session.profile_id)
    _require_identifier(session.client_build)
    _require_canonical_utc(session.opened_at)
    if session.closed_at is not None:
        _require_canonical_utc(session.closed_at)
    _require_identifier(session.active_world_id)
    if (
        type(session.campaign_catalog_sha256) is not str
        or _SHA256_PATTERN.fullmatch(session.campaign_catalog_sha256) is None
    ):
        raise ValueError("campaign catalog receipt is invalid")
    if (
        type(session.event_ordinal_at_opening) is not int
        or session.event_ordinal_at_opening < 0
    ):
        raise ValueError("session opening ordinal is invalid")
    if (
        type(session.event_hash_at_opening) is not str
        or _SHA256_PATTERN.fullmatch(session.event_hash_at_opening) is None
    ):
        raise ValueError("session opening receipt is invalid")


def _caused_by_busy_storage(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sqlite3.OperationalError):
            normalized = str(current).casefold()
            if "locked" in normalized or "busy" in normalized:
                return True
        current = current.__cause__ or current.__context__
    return False


__all__ = [
    "CurrentSessionError",
    "CurrentSessionResolver",
    "ResolvedCurrentSession",
]
