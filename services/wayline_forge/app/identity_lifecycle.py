"""Framework-independent public profile and session creation boundary."""

from __future__ import annotations

import sqlite3
from typing import Final

from services.wayline_forge.app.campaign_catalog import (
    CampaignCatalog,
    CampaignCatalogError,
)
from services.wayline_forge.app.contracts import (
    ProfileCreate,
    ProfileCreated,
    SessionCreate,
    SessionCreated,
)
from services.wayline_forge.app.profile_store import (
    CampaignStateConflictError,
    EventLogCorruptionError,
    IdempotencyConflictError,
    IdentityStoreCorruptionError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
    SessionNotFoundError,
)


class IdentityLifecycleError(RuntimeError):
    """Stable, non-sensitive failure at the public identity boundary."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "idempotency_conflict",
            "profile_not_found",
            "storage_busy",
            "catalog_conflict",
            "integrity_failure",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown identity lifecycle error code")
        self.code = code
        super().__init__(code)


class IdentityLifecycleService:
    """Translate strict public creation contracts to durable local identity."""

    def __init__(self, profile_store: ProfileStore) -> None:
        self._profile_store = profile_store
        try:
            CampaignCatalog.packaged_v1()
        except CampaignCatalogError as error:
            raise IdentityLifecycleError("catalog_conflict") from error

    def create_profile(self, request: ProfileCreate) -> ProfileCreated:
        """Create or exactly replay one pseudonymous local profile."""

        if type(request) is not ProfileCreate:
            raise IdentityLifecycleError("integrity_failure")
        try:
            request = ProfileCreate.model_validate(
                _strict_revalidation_payload(
                    request,
                    {
                        "schema_version": "schemaVersion",
                        "request_id": "requestId",
                    },
                )
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise IdentityLifecycleError("integrity_failure") from error
        try:
            local = self._profile_store.create_profile(
                request_id=request.request_id,
            )
            return ProfileCreated(
                schemaVersion="wayline.v1",
                profileId=local.profile_id,
                createdAtUtc=local.created_at,
            )
        except IdempotencyConflictError as error:
            raise IdentityLifecycleError("idempotency_conflict") from error
        except IdentityLifecycleError:
            raise
        except sqlite3.OperationalError as error:
            raise IdentityLifecycleError(
                "storage_busy" if _caused_by_busy_storage(error) else "integrity_failure"
            ) from error
        except (IdentityStoreCorruptionError, sqlite3.DatabaseError) as error:
            raise IdentityLifecycleError("integrity_failure") from error
        except ProfileStoreError as error:
            code = (
                "storage_busy"
                if _caused_by_busy_storage(error)
                else "integrity_failure"
            )
            raise IdentityLifecycleError(code) from error
        except (AttributeError, TypeError, ValueError) as error:
            raise IdentityLifecycleError("integrity_failure") from error
        except Exception:
            raise IdentityLifecycleError("integrity_failure") from None

    def create_session(self, request: SessionCreate) -> SessionCreated:
        """Create/replay a session with its world at the original opening instant."""

        if type(request) is not SessionCreate:
            raise IdentityLifecycleError("integrity_failure")
        try:
            request = SessionCreate.model_validate(
                _strict_revalidation_payload(
                    request,
                    {
                        "schema_version": "schemaVersion",
                        "request_id": "requestId",
                        "profile_id": "profileId",
                        "client_build": "clientBuild",
                    },
                )
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise IdentityLifecycleError("integrity_failure") from error
        try:
            local = self._profile_store.create_session(
                request_id=request.request_id,
                profile_id=request.profile_id,
                client_build=request.client_build,
            )
            if (
                local.profile_id != request.profile_id
                or local.client_build != request.client_build
            ):
                raise IdentityStoreCorruptionError(
                    "session command result differs from its public request"
                )
            return SessionCreated(
                schemaVersion="wayline.v1",
                profileId=local.profile_id,
                sessionId=local.session_id,
                createdAtUtc=local.opened_at,
                activeWorldId=local.active_world_id,
                campaignCatalogSha256=local.campaign_catalog_sha256,
            )
        except IdempotencyConflictError as error:
            raise IdentityLifecycleError("idempotency_conflict") from error
        except ProfileNotFoundError as error:
            raise IdentityLifecycleError("profile_not_found") from error
        except CampaignStateConflictError as error:
            raise IdentityLifecycleError("catalog_conflict") from error
        except IdentityLifecycleError:
            raise
        except sqlite3.OperationalError as error:
            raise IdentityLifecycleError(
                "storage_busy" if _caused_by_busy_storage(error) else "integrity_failure"
            ) from error
        except (
            EventLogCorruptionError,
            IdentityStoreCorruptionError,
            SessionNotFoundError,
            sqlite3.DatabaseError,
        ) as error:
            raise IdentityLifecycleError("integrity_failure") from error
        except ProfileStoreError as error:
            code = (
                "storage_busy"
                if _caused_by_busy_storage(error)
                else "integrity_failure"
            )
            raise IdentityLifecycleError(code) from error
        except (AttributeError, TypeError, ValueError) as error:
            raise IdentityLifecycleError("integrity_failure") from error
        except Exception:
            raise IdentityLifecycleError("integrity_failure") from None


def _strict_revalidation_payload(
    request: ProfileCreate | SessionCreate,
    aliases: dict[str, str],
) -> dict[str, object]:
    raw = dict(vars(request))
    extra = getattr(request, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        raw.update(extra)
    return {aliases.get(name, name): value for name, value in raw.items()}


def _caused_by_busy_storage(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sqlite3.OperationalError):
            normalized = str(current).casefold()
            return "locked" in normalized or "busy" in normalized
        current = current.__cause__ or current.__context__
    return False


__all__ = ["IdentityLifecycleError", "IdentityLifecycleService"]
