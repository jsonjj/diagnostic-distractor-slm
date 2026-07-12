"""Authenticated, redacted application boundary for local profile export."""

from __future__ import annotations

import re
import sqlite3
from typing import Final

from services.wayline_forge.app.contracts import ProfileExportV1
from services.wayline_forge.app.current_session import (
    CurrentSessionError,
    CurrentSessionResolver,
)
from services.wayline_forge.app.profile_store import (
    ProfileStore,
    SessionNotFoundError,
)


_IDENTIFIER_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}",
    re.ASCII,
)


class ProfileExportError(RuntimeError):
    """Stable, non-sensitive failure at the profile-export boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "session_not_current",
            "storage_busy",
            "integrity_failure",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown profile export error code")
        self.code = code
        super().__init__(code)


class ProfileExportService:
    """Export only the profile owned by the exact current local session."""

    def __init__(self, profile_store: ProfileStore) -> None:
        if type(profile_store) is not ProfileStore:
            raise TypeError("profile_store must be an exact ProfileStore")
        self._profile_store = profile_store
        self._current_sessions = CurrentSessionResolver(profile_store)

    def export(
        self,
        profile_id: str,
        current_session_id: str,
    ) -> ProfileExportV1:
        """Return the authenticated profile's strict portable export."""

        try:
            return self._export(profile_id, current_session_id)
        except ProfileExportError as error:
            code = (
                error.code
                if type(error) is ProfileExportError
                and type(error.code) is str
                and error.code in ProfileExportError._CODES
                else "integrity_failure"
            )
            raise ProfileExportError(code) from None
        except CurrentSessionError as error:
            raise ProfileExportError(error.code) from None
        except Exception as error:
            code = (
                "storage_busy"
                if _caused_by_busy_storage(error)
                else "integrity_failure"
            )
            raise ProfileExportError(code) from None

    def _export(
        self,
        profile_id: object,
        current_session_id: object,
    ) -> ProfileExportV1:
        try:
            requested_profile_id = _require_identifier(profile_id)
            requested_session_id = _require_identifier(current_session_id)
        except (TypeError, ValueError):
            raise ProfileExportError("session_not_current") from None
        try:
            resolved = self._current_sessions.resolve(requested_session_id)
        except CurrentSessionError as error:
            raise ProfileExportError(error.code) from None
        if resolved.profile_id != requested_profile_id:
            raise ProfileExportError("session_not_current")
        try:
            exported = self._profile_store.export_current_profile(
                resolved.profile_id,
                resolved.session_id,
            )
        except SessionNotFoundError:
            raise ProfileExportError("session_not_current") from None
        if type(exported) is not ProfileExportV1:
            raise ProfileExportError("integrity_failure")
        try:
            validated = ProfileExportV1.model_validate(
                exported.model_dump(mode="json", by_alias=True)
            )
        except (AttributeError, TypeError, ValueError):
            raise ProfileExportError("integrity_failure") from None
        if validated.profile_id != resolved.profile_id:
            raise ProfileExportError("integrity_failure")
        current_sessions = tuple(
            session
            for session in validated.sessions
            if session.session_id == resolved.session_id
        )
        if (
            len(current_sessions) != 1
            or current_sessions[0].closed_at_utc is not None
        ):
            raise ProfileExportError("integrity_failure")
        return validated


def _require_identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError("identifier is invalid")
    return value


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


__all__ = ["ProfileExportError", "ProfileExportService"]
