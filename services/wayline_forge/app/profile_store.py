"""SQLite append-only event log with rebuildable deterministic projections."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
import uuid

from services.wayline_forge.app.campaign_catalog import (
    CAMPAIGN_CATALOG_V1_SHA256,
    CampaignCatalog,
)
from services.wayline_forge.app.contracts import (
    PROFILE_EXPORT_GENESIS_EVENT_HASH,
    ProfileExportEventV1,
    ProfileExportSessionV1,
    ProfileExportV1,
)

from services.wayline_forge.app.evidence_reducer import (
    DuplicateSemanticEventError,
    LearnerState,
    reduce_events,
)
from services.wayline_forge.app.events import (
    EVENT_SCHEMA_VERSION,
    GENESIS_EVENT_HASH,
    LEGACY_EVENT_SCHEMA_VERSION,
    LearningEvent,
    ObservationEvent,
    WorldActivatedEvent,
    canonical_event_json,
    compute_event_hash,
    event_from_json,
    is_legacy_outcome_event,
)


SCHEMA_VERSION = 6
LOCAL_PROFILE_SCHEMA_VERSION = "wayline.local-profile.v1"
LOCAL_SESSION_SCHEMA_VERSION = "wayline.local-session.v2"
_LEGACY_LOCAL_SESSION_SCHEMA_VERSION = "wayline.local-session.v1"
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_CANONICAL_UTC_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{6})?Z"
)
_IDENTITY_ACTIONS = frozenset(("create_profile", "create_session"))
_AUTHORITATIVE_SEMANTIC_EVENT_TYPES = frozenset(
    {
        "assisted_route_completion",
        "battle_completion",
        "boss_completion",
        "seal_trial_completion",
        "second_wind_started",
        "second_wind_quiz_completion",
        "second_wind_combat_outcome",
        "world_progression_activated",
    }
)
_DATABASE_IDENTITY_LOCKS_GUARD = threading.Lock()
_DATABASE_IDENTITY_LOCKS: dict[str, threading.RLock] = {}
_IDENTITY_RECEIPTS_TABLE_SQL = " ".join(
    """
    CREATE TABLE identity_command_receipts (
        request_id TEXT PRIMARY KEY,
        action TEXT NOT NULL
            CHECK (action IN ('create_profile', 'create_session')),
        payload_sha256 TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        session_id TEXT,
        response_json TEXT NOT NULL,
        response_sha256 TEXT NOT NULL,
        FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
            ON DELETE CASCADE
    )
    """.split()
)
# Threat boundary: this unkeyed chain detects partial SQLite corruption. It does
# not claim resistance to an attacker who rewrites every authority source;
# Keychain/HMAC hardening is deliberately outside this local-store contract.
_SESSION_OPENING_TABLE_SQL = " ".join(
    """
    CREATE TABLE session_opening_log (
        profile_id TEXT NOT NULL,
        opening_ordinal INTEGER NOT NULL CHECK (opening_ordinal >= 1),
        session_id TEXT NOT NULL UNIQUE,
        opened_at TEXT NOT NULL,
        active_world_id TEXT NOT NULL,
        event_ordinal_at_opening INTEGER NOT NULL
            CHECK (event_ordinal_at_opening >= 0),
        event_hash_at_opening TEXT NOT NULL,
        previous_opening_hash TEXT NOT NULL,
        opening_hash TEXT NOT NULL,
        PRIMARY KEY (profile_id, opening_ordinal),
        FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
            ON DELETE CASCADE,
        FOREIGN KEY (session_id) REFERENCES local_sessions(session_id)
            ON DELETE CASCADE
    )
    """.split()
)


class ProfileStoreError(RuntimeError):
    """Base error for durable local profile storage."""


class EventLogCorruptionError(ProfileStoreError):
    """Raised when canonical payloads or the event hash chain no longer verify."""


class IdempotencyConflictError(ProfileStoreError):
    """Raised when an idempotency identifier is reused for different content."""


class SemanticEventConflictError(ProfileStoreError):
    """Raised when regenerated IDs attempt to append the same semantic observation."""


class EventOrderError(ProfileStoreError):
    """Raised when a new event does not advance the profile ordinal."""


class OutboxReservationError(ProfileStoreError):
    """Raised when an unrelated event attempts to bypass pending quiz evidence."""


class IdentityStoreCorruptionError(ProfileStoreError):
    """Raised when durable local identity rows or receipts do not verify."""


class ProfileNotFoundError(ProfileStoreError):
    """Raised when a session or profile read names no local profile."""


class SessionNotFoundError(ProfileStoreError):
    """Raised when a session read names no server-minted local session."""


class CampaignStateConflictError(ProfileStoreError):
    """Raised when durable events cannot select one pinned campaign snapshot."""


class LegacyOutcomeProfileError(ProfileStoreError):
    """A preserved profile cannot progress until its v1 outcomes are resolved."""


@dataclass(frozen=True, slots=True)
class LocalProfile:
    schema_version: str
    profile_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class LocalSession:
    schema_version: str
    session_id: str
    profile_id: str
    client_build: str
    opened_at: str
    closed_at: str | None
    active_world_id: str
    campaign_catalog_sha256: str
    event_ordinal_at_opening: int
    event_hash_at_opening: str


@dataclass(frozen=True, slots=True)
class _LegacyLocalSession:
    schema_version: str
    session_id: str
    profile_id: str
    client_build: str
    opened_at: str
    closed_at: str | None


@dataclass(frozen=True, slots=True)
class _SessionOpening:
    profile_id: str
    opening_ordinal: int
    session_id: str
    opened_at: str
    active_world_id: str
    event_ordinal_at_opening: int
    event_hash_at_opening: str
    previous_opening_hash: str
    opening_hash: str


def _require_identifier(name: str, value: str) -> str:
    if (
        not isinstance(value, str)
        or _IDENTIFIER_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"{name} is not a valid identifier")
    return value


def _require_canonical_utc_timestamp(value: str) -> str:
    if (
        not isinstance(value, str)
        or _CANONICAL_UTC_TIMESTAMP_PATTERN.fullmatch(value) is None
    ):
        raise IdentityStoreCorruptionError(
            "identity timestamp must be a real canonical UTC value"
        )
    timestamp_format = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
    try:
        datetime.strptime(value, timestamp_format)
    except ValueError as exc:
        raise IdentityStoreCorruptionError(
            "identity timestamp must be a real canonical UTC value"
        ) from exc
    return value


def _parse_identity_datetime(value: str) -> datetime:
    canonical = _require_canonical_utc_timestamp(value)
    timestamp_format = (
        "%Y-%m-%dT%H:%M:%S.%fZ" if "." in canonical else "%Y-%m-%dT%H:%M:%SZ"
    )
    return datetime.strptime(canonical, timestamp_format)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _payload_sha256(value: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(value))


def _progression_replay_payload(event: LearningEvent) -> dict[str, Any]:
    """Return request-owned event content, excluding store/server fields."""

    payload = asdict(event)
    payload.pop("ordinal")
    payload.pop("occurred_at")
    return payload


def _schema_sql_without_whitespace(value: str) -> str:
    normalized: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(value):
        character = value[index]
        if quote is not None:
            normalized.append(character)
            if character == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    normalized.append(value[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
            normalized.append(character)
        elif not character.isspace():
            normalized.append(character)
        index += 1
    return "".join(normalized)


def _session_opening_payload(
    *,
    profile_id: str,
    opening_ordinal: int,
    session_id: str,
    opened_at: str,
    active_world_id: str,
    event_ordinal_at_opening: int,
    event_hash_at_opening: str,
    previous_opening_hash: str,
) -> dict[str, object]:
    return {
        "activeWorldId": active_world_id,
        "eventHashAtOpening": event_hash_at_opening,
        "eventOrdinalAtOpening": event_ordinal_at_opening,
        "openedAt": opened_at,
        "openingOrdinal": opening_ordinal,
        "previousOpeningHash": previous_opening_hash,
        "profileId": profile_id,
        "sessionId": session_id,
    }


def _make_session_opening(
    session: LocalSession,
    *,
    opening_ordinal: int,
    previous_opening_hash: str,
) -> _SessionOpening:
    payload = _session_opening_payload(
        profile_id=session.profile_id,
        opening_ordinal=opening_ordinal,
        session_id=session.session_id,
        opened_at=session.opened_at,
        active_world_id=session.active_world_id,
        event_ordinal_at_opening=session.event_ordinal_at_opening,
        event_hash_at_opening=session.event_hash_at_opening,
        previous_opening_hash=previous_opening_hash,
    )
    return _SessionOpening(
        profile_id=session.profile_id,
        opening_ordinal=opening_ordinal,
        session_id=session.session_id,
        opened_at=session.opened_at,
        active_world_id=session.active_world_id,
        event_ordinal_at_opening=session.event_ordinal_at_opening,
        event_hash_at_opening=session.event_hash_at_opening,
        previous_opening_hash=previous_opening_hash,
        opening_hash=_payload_sha256(payload),
    )


def _server_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _server_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _database_identity_lock(path: Path) -> threading.RLock:
    key = str(path.expanduser().resolve())
    with _DATABASE_IDENTITY_LOCKS_GUARD:
        lock = _DATABASE_IDENTITY_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _DATABASE_IDENTITY_LOCKS[key] = lock
        return lock


class ProfileStore:
    """Authoritative append log; projections are caches and may always be replayed."""

    def __init__(
        self,
        path: Path | str,
        *,
        disposable_development_profile_ids: set[str] | frozenset[str] = frozenset(),
    ):
        self._campaign_catalog = CampaignCatalog.packaged_v1()
        self.path = Path(path)
        if not isinstance(
            disposable_development_profile_ids,
            (set, frozenset),
        ):
            raise TypeError(
                "disposable_development_profile_ids must be an explicit set"
            )
        self._disposable_development_profile_ids = frozenset(
            _require_identifier("disposable profile_id", profile_id)
            for profile_id in disposable_development_profile_ids
        )
        self._identity_lock = _database_identity_lock(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA secure_delete = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        try:
            self._ensure_schema(existed)
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "ProfileStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None  # type: ignore[assignment]

    def _migration_backup_paths(self) -> tuple[Path, ...]:
        return tuple(
            sorted(self.path.parent.glob(self.path.name + ".backup-v*"))
        )

    def _remove_migration_backups(self) -> None:
        for backup in self._migration_backup_paths():
            backup.unlink(missing_ok=True)

    def _remove_migration_backups_best_effort(self) -> None:
        for backup in self._migration_backup_paths():
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                # Schema commit already succeeded. Retain and retry this stale
                # recovery artifact on the next clean constructor run.
                continue

    def _truncate_wal_checkpoint(self, *, require_complete: bool) -> bool:
        row = self._connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        ).fetchone()
        if row is None or len(row) < 3:
            if require_complete:
                raise ProfileStoreError("WAL checkpoint did not report its status")
            return False
        try:
            busy = int(row[0])
        except (TypeError, ValueError) as exc:
            if require_complete:
                raise ProfileStoreError(
                    "WAL checkpoint returned an invalid busy status"
                ) from exc
            return False
        if busy != 0 and require_complete:
            busy_error = sqlite3.OperationalError("database is busy")
            raise ProfileStoreError(
                "profile deletion cannot safely truncate a busy WAL"
            ) from busy_error
        return busy == 0

    def _post_delete_maintenance(self) -> None:
        try:
            self._connection.execute("VACUUM")
        finally:
            self._truncate_wal_checkpoint(require_complete=False)

    def _ensure_schema(self, existed: bool) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise ProfileStoreError(
                f"profile database schema {version} is newer than supported {SCHEMA_VERSION}"
            )
        existing_tables = {
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        backup: Path | None = None
        if existed and version < SCHEMA_VERSION and existing_tables:
            backup = self.path.with_suffix(self.path.suffix + f".backup-v{version}")
            for candidate in (backup, Path(str(backup) + "-wal"), Path(str(backup) + "-shm")):
                candidate.unlink(missing_ok=True)
            self._connection.commit()
            self._connection.execute("PRAGMA wal_checkpoint(FULL)")
            backup_connection = sqlite3.connect(backup)
            try:
                self._connection.backup(backup_connection)
            finally:
                backup_connection.close()

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                v3_migration_plan = (
                    self._plan_identity_sessions_v4() if version == 3 else None
                )
                v4_opening_plan = (
                    self._plan_session_openings_v5_from_v4()
                    if version == 4
                    else None
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS event_log (
                        profile_id TEXT NOT NULL,
                        ordinal INTEGER NOT NULL,
                        event_id TEXT NOT NULL UNIQUE,
                        idempotency_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        semantic_key TEXT NOT NULL,
                        canonical_json TEXT NOT NULL,
                        previous_event_hash TEXT NOT NULL,
                        event_hash TEXT NOT NULL,
                        PRIMARY KEY (profile_id, ordinal),
                        UNIQUE (profile_id, idempotency_id)
                    )
                    """
                )
                columns = {
                    row[1]
                    for row in self._connection.execute("PRAGMA table_info(event_log)")
                }
                if "semantic_key" not in columns:
                    self._connection.execute(
                        "ALTER TABLE event_log ADD COLUMN semantic_key TEXT"
                    )
                    rows = self._connection.execute(
                        "SELECT profile_id, ordinal, canonical_json FROM event_log"
                    ).fetchall()
                    for row in rows:
                        migrated_event = event_from_json(str(row["canonical_json"]))
                        self._connection.execute(
                            """
                            UPDATE event_log SET semantic_key = ?
                            WHERE profile_id = ? AND ordinal = ?
                            """,
                            (
                                migrated_event.semantic_key,
                                row["profile_id"],
                                row["ordinal"],
                            ),
                        )
                self._connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    unique_observation_semantic_key
                    ON event_log(profile_id, semantic_key)
                    WHERE event_type = 'observation'
                    """
                )
                self._connection.execute(
                    "DROP INDEX IF EXISTS unique_authoritative_progression_semantic_key"
                )
                self._connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    unique_authoritative_progression_semantic_key_v2
                    ON event_log(profile_id, semantic_key)
                    WHERE event_type IN (
                        'assisted_route_completion',
                        'battle_completion',
                        'boss_completion',
                        'seal_trial_completion',
                        'second_wind_started',
                        'second_wind_quiz_completion',
                        'second_wind_combat_outcome',
                        'world_progression_activated'
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learner_projection (
                        profile_id TEXT PRIMARY KEY,
                        through_ordinal INTEGER NOT NULL,
                        projection BLOB NOT NULL
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS legacy_outcome_profiles (
                        profile_id TEXT PRIMARY KEY,
                        legacy_schema_version TEXT NOT NULL
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS local_profiles (
                        profile_id TEXT PRIMARY KEY,
                        schema_version TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS local_sessions (
                        session_id TEXT PRIMARY KEY,
                        schema_version TEXT NOT NULL,
                        profile_id TEXT NOT NULL,
                        client_build TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        closed_at TEXT,
                        active_world_id TEXT NOT NULL,
                        campaign_catalog_sha256 TEXT NOT NULL,
                        event_ordinal_at_opening INTEGER NOT NULL,
                        event_hash_at_opening TEXT NOT NULL,
                        FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS identity_command_receipts (
                        request_id TEXT PRIMARY KEY,
                        action TEXT NOT NULL
                            CHECK (action IN ('create_profile', 'create_session')),
                        payload_sha256 TEXT NOT NULL,
                        profile_id TEXT NOT NULL,
                        session_id TEXT,
                        response_json TEXT NOT NULL,
                        response_sha256 TEXT NOT NULL,
                        FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                if v3_migration_plan is not None:
                    self._apply_identity_sessions_v4(v3_migration_plan)
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_opening_log (
                        profile_id TEXT NOT NULL,
                        opening_ordinal INTEGER NOT NULL
                            CHECK (opening_ordinal >= 1),
                        session_id TEXT NOT NULL UNIQUE,
                        opened_at TEXT NOT NULL,
                        active_world_id TEXT NOT NULL,
                        event_ordinal_at_opening INTEGER NOT NULL
                            CHECK (event_ordinal_at_opening >= 0),
                        event_hash_at_opening TEXT NOT NULL,
                        previous_opening_hash TEXT NOT NULL,
                        opening_hash TEXT NOT NULL,
                        PRIMARY KEY (profile_id, opening_ordinal),
                        FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
                            ON DELETE CASCADE,
                        FOREIGN KEY (session_id) REFERENCES local_sessions(session_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                if v3_migration_plan is not None:
                    self._insert_session_openings(
                        tuple(
                            _make_session_opening(
                                migrated_response,
                                opening_ordinal=1,
                                previous_opening_hash=GENESIS_EVENT_HASH,
                            )
                            for _, _, migrated_response in v3_migration_plan
                        )
                    )
                elif v4_opening_plan is not None:
                    self._insert_session_openings(v4_opening_plan)
                self._connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS one_open_session_per_profile
                    ON local_sessions(profile_id)
                    WHERE closed_at IS NULL
                    """
                )
                self._connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS local_sessions_by_profile
                    ON local_sessions(profile_id, opened_at)
                    """
                )
                # This audit is intentionally repeatable at schema v6. Old
                # pre-release assisted events share the v2 envelope, so a DB
                # version alone cannot distinguish them from the fresh route.
                self._migrate_outcome_events_v2()
                self._validate_identity_table_shapes()
                self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        except Exception:
            # A failed migration intentionally retains its recovery artifact.
            raise
        else:
            self._remove_migration_backups_best_effort()

    def _migrate_outcome_events_v2(self) -> None:
        """Block legacy learner profiles or reset only explicit disposable IDs."""

        tables = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "event_log" not in tables:
            return
        markers: dict[str, str] = {}
        rows = self._connection.execute(
            """
            SELECT profile_id, canonical_json
            FROM event_log
            WHERE event_type IN (
                'battle_outcome', 'boss_outcome', 'seal_trial_outcome'
            )
            """
        ).fetchall()
        for row in rows:
            try:
                event = event_from_json(str(row["canonical_json"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise EventLogCorruptionError(
                    "legacy outcome cannot be inspected during migration"
                ) from exc
            if event.profile_id != row["profile_id"]:
                raise EventLogCorruptionError(
                    "legacy outcome profile differs from its event index"
                )
            if is_legacy_outcome_event(event):
                markers.setdefault(event.profile_id, LEGACY_EVENT_SCHEMA_VERSION)

        assisted_rows = self._connection.execute(
            """
            SELECT profile_id, canonical_json
            FROM event_log
            WHERE event_type = 'assisted_route_completion'
            """
        ).fetchall()
        for row in assisted_rows:
            canonical = str(row["canonical_json"])
            try:
                payload = json.loads(canonical)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise EventLogCorruptionError(
                    "assisted outcome cannot be inspected during migration"
                ) from exc
            if not isinstance(payload, dict) or canonical != json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ):
                raise EventLogCorruptionError(
                    "assisted outcome is not canonical during migration"
                )
            if (
                payload.get("event_type") != "assisted_route_completion"
                or payload.get("profile_id") != row["profile_id"]
            ):
                raise EventLogCorruptionError(
                    "assisted outcome identity differs from its event index"
                )
            profile_id = str(row["profile_id"])
            if payload.get("route_revision") != "fresh-assisted-v1":
                markers[profile_id] = "wayline.assisted-reused.v0"
                continue
            try:
                event_from_json(canonical)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise EventLogCorruptionError(
                    "fresh assisted outcome cannot be decoded during migration"
                ) from exc

        affected = set(markers)
        reset = affected & self._disposable_development_profile_ids
        for profile_id in sorted(reset):
            self._reset_disposable_development_profile(profile_id, tables)
        for profile_id in sorted(affected - reset):
            self._connection.execute(
                """
                INSERT INTO legacy_outcome_profiles (
                    profile_id, legacy_schema_version
                ) VALUES (?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    legacy_schema_version = excluded.legacy_schema_version
                """,
                (profile_id, markers[profile_id]),
            )

    def _reset_disposable_development_profile(
        self,
        profile_id: str,
        tables: set[str],
    ) -> None:
        """Delete one explicitly allowlisted pre-release profile transactionally."""

        ordered_tables = (
            "quiz_observation_outbox",
            "quiz_transition_receipts",
            "quiz_preparation_receipts",
            "quiz_batch_material",
            "quiz_machines",
            "learner_projection",
            "event_log",
            "session_opening_log",
            "identity_command_receipts",
            "local_sessions",
            "legacy_outcome_profiles",
            "local_profiles",
        )
        for table in ordered_tables:
            if table in tables:
                self._connection.execute(
                    f"DELETE FROM {table} WHERE profile_id = ?",
                    (profile_id,),
                )

    def _require_mutable_progression_profile(self, profile_id: str) -> None:
        row = self._connection.execute(
            """
            SELECT legacy_schema_version
            FROM legacy_outcome_profiles
            WHERE profile_id = ?
            """,
            (profile_id,),
        ).fetchone()
        if row is not None:
            raise LegacyOutcomeProfileError(
                "profile has preserved legacy outcomes and cannot progress"
            )

    def _validate_identity_table_shapes(
        self,
        *,
        require_opening_log: bool = True,
    ) -> None:
        expected_columns = {
            "local_profiles": (
                ("profile_id", "TEXT", 0, None, 1),
                ("schema_version", "TEXT", 1, None, 0),
                ("created_at", "TEXT", 1, None, 0),
            ),
            "local_sessions": (
                ("session_id", "TEXT", 0, None, 1),
                ("schema_version", "TEXT", 1, None, 0),
                ("profile_id", "TEXT", 1, None, 0),
                ("client_build", "TEXT", 1, None, 0),
                ("opened_at", "TEXT", 1, None, 0),
                ("closed_at", "TEXT", 0, None, 0),
                ("active_world_id", "TEXT", 1, None, 0),
                ("campaign_catalog_sha256", "TEXT", 1, None, 0),
                ("event_ordinal_at_opening", "INTEGER", 1, None, 0),
                ("event_hash_at_opening", "TEXT", 1, None, 0),
            ),
            "identity_command_receipts": (
                ("request_id", "TEXT", 0, None, 1),
                ("action", "TEXT", 1, None, 0),
                ("payload_sha256", "TEXT", 1, None, 0),
                ("profile_id", "TEXT", 1, None, 0),
                ("session_id", "TEXT", 0, None, 0),
                ("response_json", "TEXT", 1, None, 0),
                ("response_sha256", "TEXT", 1, None, 0),
            ),
        }
        for table, expected in expected_columns.items():
            actual = tuple(
                tuple(row[1:6])
                for row in self._connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != expected:
                raise ProfileStoreError(
                    f"{table} does not match profile schema version {SCHEMA_VERSION}"
                )

        expected_foreign_keys = {
            "local_profiles": (),
            "local_sessions": (
                (
                    "local_profiles",
                    "profile_id",
                    "profile_id",
                    "NO ACTION",
                    "CASCADE",
                    "NONE",
                ),
            ),
            "identity_command_receipts": (
                (
                    "local_profiles",
                    "profile_id",
                    "profile_id",
                    "NO ACTION",
                    "CASCADE",
                    "NONE",
                ),
            ),
        }
        for table, expected in expected_foreign_keys.items():
            actual = tuple(
                tuple(row[2:])
                for row in self._connection.execute(
                    f"PRAGMA foreign_key_list({table})"
                )
            )
            if actual != expected:
                raise ProfileStoreError(
                    f"{table} foreign keys do not match profile schema version "
                    f"{SCHEMA_VERSION}"
                )
        self._validate_identity_receipt_table_constraints(legacy=False)

        expected_session_indexes = {
            "sqlite_autoindex_local_sessions_1": (1, "pk", 0, ("session_id",)),
            "one_open_session_per_profile": (1, "c", 1, ("profile_id",)),
            "local_sessions_by_profile": (
                0,
                "c",
                0,
                ("profile_id", "opened_at"),
            ),
        }
        actual_session_indexes: dict[
            str,
            tuple[int, str, int, tuple[str, ...]],
        ] = {}
        for row in self._connection.execute("PRAGMA index_list(local_sessions)"):
            name = str(row[1])
            indexed_columns = tuple(
                str(info[2])
                for info in self._connection.execute(f"PRAGMA index_info({name})")
            )
            actual_session_indexes[name] = (
                int(row[2]),
                str(row[3]),
                int(row[4]),
                indexed_columns,
            )
        if actual_session_indexes != expected_session_indexes:
            raise ProfileStoreError(
                "local session indexes do not match profile schema version "
                f"{SCHEMA_VERSION}"
            )
        expected_index_sql = {
            "one_open_session_per_profile": (
                "CREATE UNIQUE INDEX one_open_session_per_profile "
                "ON local_sessions(profile_id) WHERE closed_at IS NULL"
            ),
            "local_sessions_by_profile": (
                "CREATE INDEX local_sessions_by_profile "
                "ON local_sessions(profile_id, opened_at)"
            ),
        }
        for name, expected in expected_index_sql.items():
            row = self._connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (name,),
            ).fetchone()
            actual = None if row is None else " ".join(str(row[0]).split())
            if actual != expected:
                raise ProfileStoreError(
                    "local session indexes do not match profile schema version "
                    f"{SCHEMA_VERSION}"
                )
        if not require_opening_log:
            return
        opening_row = self._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("session_opening_log",),
        ).fetchone()
        opening_sql = None if opening_row is None else str(opening_row[0])
        if (
            opening_sql is None
            or _schema_sql_without_whitespace(opening_sql)
            != _schema_sql_without_whitespace(_SESSION_OPENING_TABLE_SQL)
        ):
            raise ProfileStoreError(
                "session opening authority does not match profile schema version "
                f"{SCHEMA_VERSION}"
            )

    def _validate_identity_receipt_table_constraints(self, *, legacy: bool) -> None:
        row = self._connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("identity_command_receipts",),
        ).fetchone()
        actual = None if row is None else str(row[0])
        if (
            actual is None
            or _schema_sql_without_whitespace(actual)
            != _schema_sql_without_whitespace(_IDENTITY_RECEIPTS_TABLE_SQL)
        ):
            prefix = "legacy " if legacy else ""
            raise ProfileStoreError(
                f"{prefix}identity_command_receipts constraints do not match "
                f"profile schema version {3 if legacy else SCHEMA_VERSION}"
            )

    def _validate_identity_coverage(
        self,
        profile_rows: tuple[sqlite3.Row, ...] | list[sqlite3.Row],
        *,
        label: str,
    ) -> None:
        orphan_event = self._connection.execute(
            """
            SELECT event.profile_id
            FROM event_log AS event
            LEFT JOIN local_profiles AS profile
              ON profile.profile_id = event.profile_id
            WHERE profile.profile_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if orphan_event is not None:
            raise IdentityStoreCorruptionError(
                f"{label} identity preflight found events without profiles"
            )

        for profile_row in profile_rows:
            profile = self._profile_from_row(profile_row)
            session_ids = {
                str(row[0])
                for row in self._connection.execute(
                    "SELECT session_id FROM local_sessions WHERE profile_id = ?",
                    (profile.profile_id,),
                ).fetchall()
            }
            receipt_rows = self._connection.execute(
                """
                SELECT request_id, action, session_id
                FROM identity_command_receipts
                WHERE profile_id = ?
                ORDER BY request_id
                """,
                (profile.profile_id,),
            ).fetchall()
            profile_receipts = tuple(
                row for row in receipt_rows if row["action"] == "create_profile"
            )
            session_receipts = tuple(
                row for row in receipt_rows if row["action"] == "create_session"
            )
            if not session_ids and session_receipts:
                raise ProfileStoreError(
                    f"{label} profile with zero sessions has orphan receipts or events"
                )
            if (
                len(profile_receipts) != 1
                or len(receipt_rows) != len(session_ids) + 1
                or len(session_receipts) != len(session_ids)
                or {str(row["session_id"]) for row in session_receipts}
                != session_ids
            ):
                raise IdentityStoreCorruptionError(
                    f"{label} identity preflight receipt coverage is incomplete"
                )
            profile_replay = self._replay_identity_receipt(
                str(profile_receipts[0]["request_id"]),
                action="create_profile",
                payload_sha256=_payload_sha256({"schemaVersion": "wayline.v1"}),
            )
            if profile_replay != profile:
                raise IdentityStoreCorruptionError(
                    f"{label} identity preflight profile receipt is invalid"
                )

    def _plan_session_openings_v5_from_v4(
        self,
    ) -> tuple[_SessionOpening, ...]:
        """Bootstrap only independently provable single-session v4 profiles."""

        self._validate_identity_table_shapes(require_opening_log=False)
        violations = self._connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise IdentityStoreCorruptionError(
                "v4 identity preflight foreign keys do not verify"
            )
        profile_rows = self._connection.execute(
            "SELECT * FROM local_profiles ORDER BY profile_id"
        ).fetchall()
        self._validate_identity_coverage(profile_rows, label="v4")
        openings: list[_SessionOpening] = []
        for profile_row in profile_rows:
            profile = self._profile_from_row(profile_row)
            session_rows = self._connection.execute(
                "SELECT * FROM local_sessions WHERE profile_id = ? "
                "ORDER BY opened_at, session_id",
                (profile.profile_id,),
            ).fetchall()
            if len(session_rows) > 1:
                raise ProfileStoreError(
                    "v4 identity migration cannot independently prove "
                    "multi-session openings"
                )
            if not session_rows:
                if self._connection.execute(
                    "SELECT 1 FROM event_log WHERE profile_id = ? LIMIT 1",
                    (profile.profile_id,),
                ).fetchone() is not None:
                    raise IdentityStoreCorruptionError(
                        "v4 identity preflight found events without sessions"
                    )
                continue
            session = self._session_from_row(session_rows[0])
            self._validate_session_receipt_equality(
                session,
                validate_authority=False,
            )
            self._validate_session_snapshot_authority(
                session,
                require_opening=False,
            )
            events = self._load_events_from_rows(profile.profile_id)
            self._validate_campaign_history(profile, (session,), events)
            openings.append(
                _make_session_opening(
                    session,
                    opening_ordinal=1,
                    previous_opening_hash=GENESIS_EVENT_HASH,
                )
            )
        return tuple(openings)

    def _insert_session_openings(
        self,
        openings: tuple[_SessionOpening, ...],
    ) -> None:
        for opening in openings:
            self._connection.execute(
                """
                INSERT INTO session_opening_log (
                    profile_id, opening_ordinal, session_id, opened_at,
                    active_world_id, event_ordinal_at_opening,
                    event_hash_at_opening, previous_opening_hash, opening_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    opening.profile_id,
                    opening.opening_ordinal,
                    opening.session_id,
                    opening.opened_at,
                    opening.active_world_id,
                    opening.event_ordinal_at_opening,
                    opening.event_hash_at_opening,
                    opening.previous_opening_hash,
                    opening.opening_hash,
                ),
            )

    def _plan_identity_sessions_v4(
        self,
    ) -> tuple[tuple[_LegacyLocalSession, str, LocalSession], ...]:
        """Validate v3 read-only and plan only snapshots that are provable."""

        expected_legacy_columns = {
            "local_profiles": (
                ("profile_id", "TEXT", 0, None, 1),
                ("schema_version", "TEXT", 1, None, 0),
                ("created_at", "TEXT", 1, None, 0),
            ),
            "local_sessions": (
                ("session_id", "TEXT", 0, None, 1),
                ("schema_version", "TEXT", 1, None, 0),
                ("profile_id", "TEXT", 1, None, 0),
                ("client_build", "TEXT", 1, None, 0),
                ("opened_at", "TEXT", 1, None, 0),
                ("closed_at", "TEXT", 0, None, 0),
            ),
            "identity_command_receipts": (
                ("request_id", "TEXT", 0, None, 1),
                ("action", "TEXT", 1, None, 0),
                ("payload_sha256", "TEXT", 1, None, 0),
                ("profile_id", "TEXT", 1, None, 0),
                ("session_id", "TEXT", 0, None, 0),
                ("response_json", "TEXT", 1, None, 0),
                ("response_sha256", "TEXT", 1, None, 0),
            ),
        }
        for table, expected in expected_legacy_columns.items():
            actual = tuple(
                tuple(row[1:6])
                for row in self._connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != expected:
                raise ProfileStoreError(
                    f"legacy {table} schema cannot be migrated"
                )
        expected_legacy_foreign_keys = {
            "local_profiles": (),
            "local_sessions": (
                (
                    "local_profiles",
                    "profile_id",
                    "profile_id",
                    "NO ACTION",
                    "CASCADE",
                    "NONE",
                ),
            ),
            "identity_command_receipts": (
                (
                    "local_profiles",
                    "profile_id",
                    "profile_id",
                    "NO ACTION",
                    "CASCADE",
                    "NONE",
                ),
            ),
        }
        for table, expected in expected_legacy_foreign_keys.items():
            actual = tuple(
                tuple(row[2:])
                for row in self._connection.execute(
                    f"PRAGMA foreign_key_list({table})"
                )
            )
            if actual != expected:
                raise ProfileStoreError(
                    f"legacy {table} foreign keys cannot be migrated"
                )
        self._validate_identity_receipt_table_constraints(legacy=True)
        foreign_key_violations = self._connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_violations:
            raise IdentityStoreCorruptionError(
                "legacy identity foreign keys do not verify"
            )
        migrations: list[tuple[_LegacyLocalSession, str, LocalSession]] = []
        profile_rows = self._connection.execute(
            "SELECT * FROM local_profiles ORDER BY profile_id ASC"
        ).fetchall()
        self._validate_identity_coverage(profile_rows, label="v3")
        for profile_row in profile_rows:
            profile = self._profile_from_row(profile_row)
            session_rows = self._connection.execute(
                """
                SELECT session_id, schema_version, profile_id, client_build,
                       opened_at, closed_at
                FROM local_sessions
                WHERE profile_id = ?
                ORDER BY opened_at ASC, session_id ASC
                """,
                (profile.profile_id,),
            ).fetchall()
            sessions = tuple(
                self._legacy_session_from_row(row) for row in session_rows
            )
            if not sessions:
                session_receipt_count = int(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM identity_command_receipts "
                        "WHERE profile_id = ? AND action = 'create_session'",
                        (profile.profile_id,),
                    ).fetchone()[0]
                )
                event_count = int(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM event_log WHERE profile_id = ?",
                        (profile.profile_id,),
                    ).fetchone()[0]
                )
                if session_receipt_count or event_count:
                    raise ProfileStoreError(
                        "v3 profile with zero sessions has orphan receipts or events"
                    )
                continue
            if len(sessions) != 1:
                raise ProfileStoreError(
                    "v3 identity migration cannot prove multi-session campaign snapshots"
                )
            events = self._load_events_from_rows(profile.profile_id)
            activations = self._validate_campaign_history(
                profile,
                sessions,
                events,
            )
            receipt_rows = self._connection.execute(
                """
                SELECT request_id, action, payload_sha256, profile_id, session_id,
                       response_json, response_sha256
                FROM identity_command_receipts
                WHERE profile_id = ? AND action = 'create_session'
                ORDER BY request_id ASC
                """,
                (profile.profile_id,),
            ).fetchall()
            if len(receipt_rows) != len(sessions):
                raise IdentityStoreCorruptionError(
                    "legacy sessions and creation receipts do not agree"
                )
            receipts_by_session: dict[str, tuple[str, _LegacyLocalSession]] = {}
            for receipt_row in receipt_rows:
                request_id, response = self._legacy_session_from_receipt_row(
                    receipt_row
                )
                if response.session_id in receipts_by_session:
                    raise IdentityStoreCorruptionError(
                        "legacy session has duplicate creation receipts"
                    )
                receipts_by_session[response.session_id] = (request_id, response)

            for session in sessions:
                receipt = receipts_by_session.get(session.session_id)
                if receipt is None:
                    raise IdentityStoreCorruptionError(
                        "legacy session lacks its creation receipt"
                    )
                request_id, response = receipt
                if (
                    response.profile_id != session.profile_id
                    or response.client_build != session.client_build
                    or response.opened_at != session.opened_at
                    or response.closed_at is not None
                ):
                    raise IdentityStoreCorruptionError(
                        "legacy session receipt differs from its durable session"
                    )
                world_id = self._world_id_at_opening(
                    activations,
                    session_id=session.session_id,
                    opened_at=session.opened_at,
                )
                migrated_response = LocalSession(
                    schema_version=LOCAL_SESSION_SCHEMA_VERSION,
                    session_id=response.session_id,
                    profile_id=response.profile_id,
                    client_build=response.client_build,
                    opened_at=response.opened_at,
                    closed_at=response.closed_at,
                    active_world_id=world_id,
                    campaign_catalog_sha256=CAMPAIGN_CATALOG_V1_SHA256,
                    event_ordinal_at_opening=0,
                    event_hash_at_opening=GENESIS_EVENT_HASH,
                )
                migrations.append((session, request_id, migrated_response))

        total_session_rows = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM local_sessions"
            ).fetchone()[0]
        )
        if total_session_rows != len(migrations):
            raise IdentityStoreCorruptionError(
                "legacy local sessions are not covered by profile identities"
            )
        return tuple(migrations)

    def _apply_identity_sessions_v4(
        self,
        migrations: tuple[tuple[_LegacyLocalSession, str, LocalSession], ...],
    ) -> None:
        """Rebuild v3 sessions with v4 constraints inside the schema transaction."""

        self._connection.execute(
            "ALTER TABLE local_sessions RENAME TO local_sessions_v3"
        )
        self._connection.execute(
            """
            CREATE TABLE local_sessions (
                session_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                client_build TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                active_world_id TEXT NOT NULL,
                campaign_catalog_sha256 TEXT NOT NULL,
                event_ordinal_at_opening INTEGER NOT NULL,
                event_hash_at_opening TEXT NOT NULL,
                FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
                    ON DELETE CASCADE
            )
            """
        )
        for session, _, migrated_response in migrations:
            self._connection.execute(
                """
                INSERT INTO local_sessions (
                    session_id, schema_version, profile_id, client_build,
                    opened_at, closed_at, active_world_id,
                    campaign_catalog_sha256, event_ordinal_at_opening,
                    event_hash_at_opening
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    LOCAL_SESSION_SCHEMA_VERSION,
                    session.profile_id,
                    session.client_build,
                    session.opened_at,
                    session.closed_at,
                    migrated_response.active_world_id,
                    migrated_response.campaign_catalog_sha256,
                    migrated_response.event_ordinal_at_opening,
                    migrated_response.event_hash_at_opening,
                ),
            )
        self._connection.execute("DROP TABLE local_sessions_v3")
        for _, request_id, migrated_response in migrations:
            response_json = _canonical_json(asdict(migrated_response))
            self._connection.execute(
                """
                UPDATE identity_command_receipts
                SET response_json = ?, response_sha256 = ?
                WHERE request_id = ?
                """,
                (
                    response_json,
                    _sha256_text(response_json),
                    request_id,
                ),
            )

    @staticmethod
    def _legacy_session_from_row(row: sqlite3.Row) -> _LegacyLocalSession:
        payload = {
            "schema_version": row["schema_version"],
            "session_id": row["session_id"],
            "profile_id": row["profile_id"],
            "client_build": row["client_build"],
            "opened_at": row["opened_at"],
            "closed_at": row["closed_at"],
        }
        return ProfileStore._legacy_session_from_payload(payload)

    @staticmethod
    def _legacy_session_from_payload(
        payload: dict[str, Any],
    ) -> _LegacyLocalSession:
        if set(payload) != {
            "schema_version",
            "session_id",
            "profile_id",
            "client_build",
            "opened_at",
            "closed_at",
        }:
            raise IdentityStoreCorruptionError(
                "legacy local session fields do not match the v1 contract"
            )
        result = _LegacyLocalSession(**payload)
        if result.schema_version != _LEGACY_LOCAL_SESSION_SCHEMA_VERSION:
            raise IdentityStoreCorruptionError(
                "legacy local session schema is unsupported"
            )
        try:
            _require_identifier("session_id", result.session_id)
            _require_identifier("profile_id", result.profile_id)
            _require_identifier("client_build", result.client_build)
        except ValueError as exc:
            raise IdentityStoreCorruptionError(
                "legacy local session identity is invalid"
            ) from exc
        _require_canonical_utc_timestamp(result.opened_at)
        if result.closed_at is not None:
            _require_canonical_utc_timestamp(result.closed_at)
        return result

    def _legacy_session_from_receipt_row(
        self,
        row: sqlite3.Row,
    ) -> tuple[str, _LegacyLocalSession]:
        request_id = row["request_id"]
        try:
            _require_identifier("request_id", request_id)
        except ValueError as exc:
            raise IdentityStoreCorruptionError(
                "legacy session receipt request is invalid"
            ) from exc
        response_json = row["response_json"]
        response_sha256 = row["response_sha256"]
        if (
            row["action"] != "create_session"
            or not isinstance(response_json, str)
            or not isinstance(response_sha256, str)
            or _SHA256_PATTERN.fullmatch(response_sha256) is None
            or _sha256_text(response_json) != response_sha256
        ):
            raise IdentityStoreCorruptionError(
                "legacy session receipt digest does not verify"
            )
        try:
            payload = json.loads(response_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise IdentityStoreCorruptionError(
                "legacy session receipt cannot be decoded"
            ) from exc
        if not isinstance(payload, dict) or _canonical_json(payload) != response_json:
            raise IdentityStoreCorruptionError(
                "legacy session receipt is not canonical JSON"
            )
        response = self._legacy_session_from_payload(payload)
        if (
            row["profile_id"] != response.profile_id
            or row["session_id"] != response.session_id
            or row["payload_sha256"]
            != _payload_sha256(
                {
                    "clientBuild": response.client_build,
                    "profileId": response.profile_id,
                }
            )
        ):
            raise IdentityStoreCorruptionError(
                "legacy session receipt index does not match its response"
            )
        return request_id, response

    @staticmethod
    def _validate_session_timeline(
        sessions: tuple[_LegacyLocalSession | LocalSession, ...],
        *,
        prospective_close: tuple[str, str] | None = None,
    ) -> None:
        open_session_ids: list[str] = []
        sessions_by_id = {session.session_id: session for session in sessions}
        if len(sessions_by_id) != len(sessions):
            raise IdentityStoreCorruptionError("duplicate local session identity")
        for session in sessions:
            opened = _parse_identity_datetime(session.opened_at)
            if session.closed_at is None:
                open_session_ids.append(session.session_id)
            elif _parse_identity_datetime(session.closed_at) < opened:
                raise IdentityStoreCorruptionError(
                    "local session closes before it opens"
                )
        if len(open_session_ids) > 1:
            raise IdentityStoreCorruptionError(
                "profile has more than one open local session"
            )
        if prospective_close is not None:
            closing_session_id, closing_at = prospective_close
            if open_session_ids != [closing_session_id]:
                raise IdentityStoreCorruptionError(
                    "new session cannot identify one prior open session"
                )
            if _parse_identity_datetime(closing_at) < _parse_identity_datetime(
                sessions_by_id[closing_session_id].opened_at
            ):
                raise IdentityStoreCorruptionError(
                    "new session predates the prior open session"
                )

        ordered_sessions = tuple(
            sorted(
                sessions,
                key=lambda candidate: (
                    _parse_identity_datetime(candidate.opened_at),
                    candidate.session_id,
                ),
            )
        )
        for index, session in enumerate(ordered_sessions):
            is_terminal = index == len(ordered_sessions) - 1
            if not is_terminal:
                next_session = ordered_sessions[index + 1]
                if (
                    _parse_identity_datetime(session.opened_at)
                    >= _parse_identity_datetime(next_session.opened_at)
                    or session.closed_at != next_session.opened_at
                ):
                    raise IdentityStoreCorruptionError(
                        "local session closure does not match the next opening"
                    )
                continue
            if prospective_close is None:
                if session.closed_at is not None:
                    raise IdentityStoreCorruptionError(
                        "terminal local session is unexpectedly closed"
                    )
            elif (
                session.session_id != prospective_close[0]
                or session.closed_at is not None
            ):
                raise IdentityStoreCorruptionError(
                    "new session does not close the terminal local session"
                )

    def _validate_campaign_history(
        self,
        profile: LocalProfile,
        sessions: tuple[_LegacyLocalSession | LocalSession, ...],
        events: tuple[LearningEvent, ...],
        *,
        prospective_close: tuple[str, str] | None = None,
    ) -> tuple[tuple[WorldActivatedEvent, str, datetime], ...]:
        profile_created = _parse_identity_datetime(profile.created_at)
        sessions_by_id: dict[str, _LegacyLocalSession | LocalSession] = {}
        for session in sessions:
            if session.profile_id != profile.profile_id:
                raise IdentityStoreCorruptionError(
                    "local session belongs to a different profile"
                )
            if session.session_id in sessions_by_id:
                raise IdentityStoreCorruptionError("duplicate local session identity")
            opened = _parse_identity_datetime(session.opened_at)
            if opened < profile_created:
                raise IdentityStoreCorruptionError(
                    "local session predates profile creation"
                )
            if isinstance(session, LocalSession):
                self._validate_session_snapshot(session)
            sessions_by_id[session.session_id] = session
        self._validate_session_timeline(
            sessions,
            prospective_close=prospective_close,
        )

        if bool(sessions) != bool(events):
            raise CampaignStateConflictError(
                "profile sessions and campaign events do not agree"
            )
        if events and not isinstance(events[0], WorldActivatedEvent):
            raise CampaignStateConflictError(
                "profile campaign does not begin with activation"
            )
        if tuple(event.ordinal for event in events) != tuple(
            range(1, len(events) + 1)
        ):
            raise CampaignStateConflictError(
                "profile campaign event ordinals are not contiguous"
            )

        activations: list[tuple[WorldActivatedEvent, str, datetime]] = []
        active_world_id: str | None = None
        activated_world_ids: list[str] = []
        previous_activation_time: datetime | None = None
        for event in events:
            if event.profile_id != profile.profile_id:
                raise CampaignStateConflictError(
                    "campaign event belongs to a different profile"
                )
            try:
                occurred_at = _parse_identity_datetime(event.occurred_at)
            except IdentityStoreCorruptionError as exc:
                raise CampaignStateConflictError(
                    "campaign event timestamp is invalid"
                ) from exc
            session = sessions_by_id.get(event.session_id)
            if session is None:
                raise CampaignStateConflictError(
                    "campaign event references an unknown profile session"
                )
            opened_at = _parse_identity_datetime(session.opened_at)
            if occurred_at < opened_at:
                raise CampaignStateConflictError(
                    "campaign event predates its local session"
                )
            closed_at = session.closed_at
            if (
                prospective_close is not None
                and event.session_id == prospective_close[0]
            ):
                closed_at = prospective_close[1]
            if closed_at is not None and occurred_at > _parse_identity_datetime(
                closed_at
            ):
                raise CampaignStateConflictError(
                    "campaign event occurs after its local session"
                )

            if isinstance(event, WorldActivatedEvent):
                sequence = len(activations) + 1
                if sequence > len(self._campaign_catalog.worlds):
                    raise CampaignStateConflictError(
                        "campaign activation exceeds the pinned catalog"
                    )
                expected = self._campaign_catalog.worlds[sequence - 1]
                if (
                    event.world_id != expected.world_id
                    or event.battle_id != "campaign-map"
                    or event.core_subskill_ids != expected.core_subskill_ids
                    or event.curriculum_receipt
                    != self._campaign_catalog.curriculum_receipt
                    or (
                        previous_activation_time is not None
                        and occurred_at <= previous_activation_time
                    )
                ):
                    raise CampaignStateConflictError(
                        "campaign activation differs from pinned authority"
                    )
                activations.append((event, expected.world_id, occurred_at))
                active_world_id = expected.world_id
                activated_world_ids.append(expected.world_id)
                previous_activation_time = occurred_at
                continue

            if active_world_id is None:
                raise CampaignStateConflictError(
                    "campaign event precedes world activation"
                )
            if isinstance(event, ObservationEvent):
                if event.world_id != active_world_id and (
                    not event.is_transfer
                    or event.world_id not in activated_world_ids
                ):
                    raise CampaignStateConflictError(
                        "observation uses an unauthored campaign world"
                    )
            elif event.world_id != active_world_id:
                raise CampaignStateConflictError(
                    "progression event is outside the active campaign world"
                )

        if sessions and not activations:
            raise CampaignStateConflictError("campaign has no world activation")
        return tuple(activations)

    def _validate_session_snapshot(self, session: LocalSession) -> None:
        if session.campaign_catalog_sha256 != CAMPAIGN_CATALOG_V1_SHA256:
            raise IdentityStoreCorruptionError(
                "local session campaign catalog digest is invalid"
            )
        if session.active_world_id not in {
            world.world_id for world in self._campaign_catalog.worlds
        }:
            raise IdentityStoreCorruptionError(
                "local session campaign world is invalid"
            )
        if (
            type(session.event_ordinal_at_opening) is not int
            or session.event_ordinal_at_opening < 0
            or not isinstance(session.event_hash_at_opening, str)
            or _SHA256_PATTERN.fullmatch(session.event_hash_at_opening) is None
            or (
                session.event_ordinal_at_opening == 0
                and session.event_hash_at_opening != GENESIS_EVENT_HASH
            )
        ):
            raise IdentityStoreCorruptionError(
                "local session event boundary is invalid"
            )

    def _session_opening_from_row(self, row: sqlite3.Row) -> _SessionOpening:
        opening = _SessionOpening(
            profile_id=row["profile_id"],
            opening_ordinal=row["opening_ordinal"],
            session_id=row["session_id"],
            opened_at=row["opened_at"],
            active_world_id=row["active_world_id"],
            event_ordinal_at_opening=row["event_ordinal_at_opening"],
            event_hash_at_opening=row["event_hash_at_opening"],
            previous_opening_hash=row["previous_opening_hash"],
            opening_hash=row["opening_hash"],
        )
        try:
            _require_identifier("profile_id", opening.profile_id)
            _require_identifier("session_id", opening.session_id)
            _require_identifier("active_world_id", opening.active_world_id)
        except ValueError as exc:
            raise IdentityStoreCorruptionError(
                "session opening identity is invalid"
            ) from exc
        _require_canonical_utc_timestamp(opening.opened_at)
        if (
            type(opening.opening_ordinal) is not int
            or opening.opening_ordinal < 1
            or type(opening.event_ordinal_at_opening) is not int
            or opening.event_ordinal_at_opening < 0
            or not isinstance(opening.event_hash_at_opening, str)
            or _SHA256_PATTERN.fullmatch(opening.event_hash_at_opening) is None
            or not isinstance(opening.previous_opening_hash, str)
            or _SHA256_PATTERN.fullmatch(opening.previous_opening_hash) is None
            or not isinstance(opening.opening_hash, str)
            or _SHA256_PATTERN.fullmatch(opening.opening_hash) is None
        ):
            raise IdentityStoreCorruptionError(
                "session opening boundary is invalid"
            )
        expected_hash = _payload_sha256(
            _session_opening_payload(
                profile_id=opening.profile_id,
                opening_ordinal=opening.opening_ordinal,
                session_id=opening.session_id,
                opened_at=opening.opened_at,
                active_world_id=opening.active_world_id,
                event_ordinal_at_opening=opening.event_ordinal_at_opening,
                event_hash_at_opening=opening.event_hash_at_opening,
                previous_opening_hash=opening.previous_opening_hash,
            )
        )
        if expected_hash != opening.opening_hash:
            raise IdentityStoreCorruptionError(
                "session opening content hash does not verify"
            )
        return opening

    def _load_session_openings(
        self,
        profile_id: str,
    ) -> tuple[_SessionOpening, ...]:
        rows = self._connection.execute(
            "SELECT * FROM session_opening_log WHERE profile_id = ? "
            "ORDER BY opening_ordinal",
            (profile_id,),
        ).fetchall()
        openings: list[_SessionOpening] = []
        previous_hash = GENESIS_EVENT_HASH
        for expected_ordinal, row in enumerate(rows, start=1):
            opening = self._session_opening_from_row(row)
            if (
                opening.profile_id != profile_id
                or opening.opening_ordinal != expected_ordinal
                or opening.previous_opening_hash != previous_hash
            ):
                raise IdentityStoreCorruptionError(
                    "session opening hash chain is invalid"
                )
            openings.append(opening)
            previous_hash = opening.opening_hash
        return tuple(openings)

    @staticmethod
    def _validate_session_opening_binding(
        session: LocalSession,
        opening: _SessionOpening,
    ) -> None:
        if (
            opening.profile_id,
            opening.session_id,
            opening.opened_at,
            opening.active_world_id,
            opening.event_ordinal_at_opening,
            opening.event_hash_at_opening,
        ) != (
            session.profile_id,
            session.session_id,
            session.opened_at,
            session.active_world_id,
            session.event_ordinal_at_opening,
            session.event_hash_at_opening,
        ):
            raise IdentityStoreCorruptionError(
                "session differs from its opening authority"
            )

    def _validate_opening_coverage(
        self,
        sessions: tuple[LocalSession, ...],
        openings: tuple[_SessionOpening, ...],
    ) -> dict[str, _SessionOpening]:
        opening_by_session = {opening.session_id: opening for opening in openings}
        if (
            len(opening_by_session) != len(openings)
            or set(opening_by_session)
            != {session.session_id for session in sessions}
        ):
            raise IdentityStoreCorruptionError(
                "sessions and opening authority do not agree"
            )
        for session in sessions:
            self._validate_session_opening_binding(
                session,
                opening_by_session[session.session_id],
            )
        return opening_by_session

    def _validate_session_receipt_equality(
        self,
        session: LocalSession,
        *,
        validate_authority: bool,
        opening: _SessionOpening | None = None,
    ) -> None:
        rows = self._connection.execute(
            """
            SELECT request_id FROM identity_command_receipts
            WHERE profile_id = ? AND session_id = ? AND action = 'create_session'
            """,
            (session.profile_id, session.session_id),
        ).fetchall()
        if len(rows) != 1:
            raise IdentityStoreCorruptionError(
                "session lacks one creation receipt"
            )
        try:
            replay = self._replay_identity_receipt(
                str(rows[0]["request_id"]),
                action="create_session",
                payload_sha256=_payload_sha256(
                    {
                        "clientBuild": session.client_build,
                        "profileId": session.profile_id,
                    }
                ),
                validate_authority=False,
            )
        except IdempotencyConflictError as exc:
            raise IdentityStoreCorruptionError(
                "session creation receipt payload does not match its row"
            ) from exc
        if not isinstance(replay, LocalSession):
            raise IdentityStoreCorruptionError(
                "session receipt contains a profile"
            )
        if validate_authority:
            if opening is None:
                openings = self._load_session_openings(session.profile_id)
                session_rows = self._connection.execute(
                    "SELECT * FROM local_sessions WHERE profile_id = ?",
                    (session.profile_id,),
                ).fetchall()
                sessions = tuple(self._session_from_row(row) for row in session_rows)
                opening = self._validate_opening_coverage(
                    sessions,
                    openings,
                )[session.session_id]
            self._validate_session_opening_binding(session, opening)
            self._validate_session_snapshot_authority(
                session,
                opening=opening,
            )

    def _validate_profile_identity_authority(
        self,
        profile_id: str,
    ) -> tuple[LocalProfile, tuple[LocalSession, ...]]:
        """Verify every identity authority source for one durable profile."""

        profile_row = self._connection.execute(
            "SELECT * FROM local_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if profile_row is None:
            raise IdentityStoreCorruptionError(
                "session authority lacks its durable profile"
            )
        profile = self._profile_from_row(profile_row)
        self._validate_identity_coverage((profile_row,), label="durable")
        session_rows = self._connection.execute(
            "SELECT * FROM local_sessions WHERE profile_id = ? "
            "ORDER BY opened_at, session_id",
            (profile_id,),
        ).fetchall()
        sessions = tuple(self._session_from_row(row) for row in session_rows)
        self._validate_session_timeline(sessions)
        openings = self._load_session_openings(profile_id)
        opening_by_session = self._validate_opening_coverage(sessions, openings)
        for session in sessions:
            opening = opening_by_session[session.session_id]
            self._validate_session_receipt_equality(
                session,
                validate_authority=False,
                opening=opening,
            )
            self._validate_session_snapshot_authority(
                session,
                opening=opening,
            )
        return profile, sessions

    def _validate_session_snapshot_authority(
        self,
        session: LocalSession,
        *,
        opening: _SessionOpening | None = None,
        require_opening: bool = True,
    ) -> None:
        """Bind a snapshot to its independent opening record and event prefix."""

        if require_opening:
            if opening is None:
                openings = self._load_session_openings(session.profile_id)
                opening = next(
                    (
                        candidate
                        for candidate in openings
                        if candidate.session_id == session.session_id
                    ),
                    None,
                )
            if opening is None:
                raise IdentityStoreCorruptionError(
                    "session lacks opening authority"
                )
            self._validate_session_opening_binding(session, opening)

        profile_row = self._connection.execute(
            "SELECT * FROM local_profiles WHERE profile_id = ?",
            (session.profile_id,),
        ).fetchone()
        if profile_row is None:
            raise IdentityStoreCorruptionError(
                "session snapshot profile is missing"
            )
        profile = self._profile_from_row(profile_row)
        events = self._load_events_from_rows(session.profile_id)
        initial_world = self._campaign_catalog.initial_world
        if not events:
            raise EventLogCorruptionError(
                "session snapshot lacks its initial campaign activation"
            )
        if not isinstance(events[0], WorldActivatedEvent):
            raise EventLogCorruptionError(
                "session snapshot lacks its initial campaign activation"
            )
        initial_activation = events[0]
        if (
            initial_activation.ordinal != 1
            or initial_activation.profile_id != session.profile_id
            or initial_activation.world_id != initial_world.world_id
            or initial_activation.battle_id != "campaign-map"
            or initial_activation.core_subskill_ids != initial_world.core_subskill_ids
            or initial_activation.curriculum_receipt
            != self._campaign_catalog.curriculum_receipt
        ):
            raise EventLogCorruptionError(
                "session snapshot initial activation is invalid"
            )
        try:
            initial_activated_at = _parse_identity_datetime(
                initial_activation.occurred_at
            )
        except IdentityStoreCorruptionError as exc:
            raise EventLogCorruptionError(
                "session snapshot activation timestamp is invalid"
            ) from exc

        target_opening = _parse_identity_datetime(session.opened_at)
        if session.event_ordinal_at_opening == 0:
            if (
                session.event_hash_at_opening != GENESIS_EVENT_HASH
                or initial_activation.session_id != session.session_id
                or session.active_world_id != initial_world.world_id
                or initial_activated_at != target_opening
            ):
                raise IdentityStoreCorruptionError(
                    "first session snapshot boundary is invalid"
                )
            return

        if session.event_ordinal_at_opening > len(events):
            raise EventLogCorruptionError(
                "session snapshot event boundary is unavailable"
            )
        boundary_events = events[: session.event_ordinal_at_opening]
        if tuple(event.ordinal for event in boundary_events) != tuple(
            range(1, session.event_ordinal_at_opening + 1)
        ):
            raise EventLogCorruptionError(
                "session snapshot event boundary is unavailable"
            )
        boundary_hash = GENESIS_EVENT_HASH
        for event in boundary_events:
            boundary_hash = compute_event_hash(boundary_hash, event)
        if boundary_hash != session.event_hash_at_opening:
            raise IdentityStoreCorruptionError(
                "session snapshot event boundary does not verify"
            )

        session_rows = self._connection.execute(
            "SELECT * FROM local_sessions WHERE profile_id = ? "
            "ORDER BY opened_at, session_id",
            (session.profile_id,),
        ).fetchall()
        sessions = tuple(self._session_from_row(row) for row in session_rows)
        sessions_by_id = {candidate.session_id: candidate for candidate in sessions}
        for event in boundary_events:
            event_session = sessions_by_id.get(event.session_id)
            if (
                event_session is None
                or event.session_id == session.session_id
                or _parse_identity_datetime(event_session.opened_at) >= target_opening
            ):
                raise IdentityStoreCorruptionError(
                    "session snapshot boundary references a later session"
                )
        try:
            activations = self._validate_campaign_history(
                profile,
                sessions,
                boundary_events,
            )
            selected_world_id = self._world_id_at_opening(
                activations,
                session_id=session.session_id,
                opened_at=session.opened_at,
            )
        except CampaignStateConflictError as exc:
            raise IdentityStoreCorruptionError(
                "session snapshot event prefix is invalid"
            ) from exc
        if selected_world_id != session.active_world_id:
            raise IdentityStoreCorruptionError(
                "session snapshot differs from its event boundary"
            )

    @staticmethod
    def _world_id_at_opening(
        activations: tuple[tuple[WorldActivatedEvent, str, datetime], ...],
        *,
        session_id: str,
        opened_at: str,
    ) -> str:
        opening = _parse_identity_datetime(opened_at)
        initial_activation = activations[0][0]
        selected: str | None = None
        for activation, world_id, activated_at in activations:
            if activated_at < opening:
                selected = world_id
                continue
            if activated_at == opening:
                if (
                    activation is initial_activation
                    and activation.session_id == session_id
                ):
                    selected = world_id
                    continue
                raise CampaignStateConflictError(
                    "campaign activation at session opening is ambiguous"
                )
            break
        if selected is None:
            raise CampaignStateConflictError(
                "campaign has no world at session opening"
            )
        return selected

    def _campaign_snapshot_for_new_session(
        self,
        profile: LocalProfile,
        *,
        session_id: str,
        opened_at: str,
    ) -> tuple[str, int, str]:
        """Validate the pre-command history and select one immutable world."""

        self._require_mutable_progression_profile(profile.profile_id)

        opening = _parse_identity_datetime(opened_at)
        if opening < _parse_identity_datetime(profile.created_at):
            raise IdentityStoreCorruptionError(
                "session opening predates profile creation"
            )
        session_rows = self._connection.execute(
            """
            SELECT * FROM local_sessions
            WHERE profile_id = ?
            ORDER BY opened_at ASC, session_id ASC
            """,
            (profile.profile_id,),
        ).fetchall()
        sessions = tuple(self._session_from_row(row) for row in session_rows)
        events = self._load_events_from_rows(profile.profile_id)
        if not sessions:
            if self._load_session_openings(profile.profile_id):
                raise IdentityStoreCorruptionError(
                    "profile has opening authority without sessions"
                )
            if events:
                raise CampaignStateConflictError(
                    "campaign events exist before the first local session"
                )
            return (
                self._campaign_catalog.initial_world.world_id,
                0,
                GENESIS_EVENT_HASH,
            )

        openings = self._load_session_openings(profile.profile_id)
        opening_by_session = self._validate_opening_coverage(sessions, openings)
        for prior in sessions:
            self._validate_session_receipt_equality(
                prior,
                validate_authority=False,
                opening=opening_by_session[prior.session_id],
            )
            self._validate_session_snapshot_authority(
                prior,
                opening=opening_by_session[prior.session_id],
            )

        for prior in sessions:
            if _parse_identity_datetime(prior.opened_at) > opening:
                raise IdentityStoreCorruptionError(
                    "new session predates a durable local session"
                )
            if prior.closed_at is not None and (
                _parse_identity_datetime(prior.closed_at) > opening
            ):
                raise IdentityStoreCorruptionError(
                    "new session overlaps a closed local session"
                )
        open_sessions = tuple(
            session for session in sessions if session.closed_at is None
        )
        if len(open_sessions) != 1:
            raise IdentityStoreCorruptionError(
                "new session cannot identify one prior open session"
            )
        activations = self._validate_campaign_history(
            profile,
            sessions,
            events,
            prospective_close=(open_sessions[0].session_id, opened_at),
        )
        active_world_id = self._world_id_at_opening(
            activations,
            session_id=session_id,
            opened_at=opened_at,
        )
        event_hash = GENESIS_EVENT_HASH
        for event in events:
            event_hash = compute_event_hash(event_hash, event)
        return active_world_id, events[-1].ordinal, event_hash

    def create_profile(
        self,
        *,
        request_id: str,
    ) -> LocalProfile:
        """Mint one pseudonymous local profile with durable request replay."""

        with self._identity_lock:
            return self._create_profile_locked(request_id=request_id)

    def _create_profile_locked(
        self,
        *,
        request_id: str,
    ) -> LocalProfile:

        request = _require_identifier("request_id", request_id)
        payload_digest = _payload_sha256({"schemaVersion": "wayline.v1"})
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_identity_receipt(
                    request,
                    action="create_profile",
                    payload_sha256=payload_digest,
                )
                if replay is not None:
                    if not isinstance(replay, LocalProfile):
                        raise IdentityStoreCorruptionError(
                            "profile command receipt contains a session"
                        )
                    result = replay
                else:
                    result = LocalProfile(
                        schema_version=LOCAL_PROFILE_SCHEMA_VERSION,
                        profile_id=_server_id("profile"),
                        created_at=_server_timestamp(),
                    )
                    self._connection.execute(
                        """
                        INSERT INTO local_profiles (
                            profile_id, schema_version, created_at
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            result.profile_id,
                            result.schema_version,
                            result.created_at,
                        ),
                    )
                    self._insert_identity_receipt(
                        request,
                        action="create_profile",
                        payload_sha256=payload_digest,
                        profile_id=result.profile_id,
                        session_id=None,
                        response=result,
                    )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        except sqlite3.IntegrityError as exc:
            raise ProfileStoreError("local profile identity conflicts") from exc
        except sqlite3.OperationalError as exc:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise ProfileStoreError(
                "profile creation could not acquire its write lock"
            ) from exc
        return result

    def create_session(
        self,
        *,
        request_id: str,
        profile_id: str,
        client_build: str,
    ) -> LocalSession:
        """Mint one open session and initialize the first authored world atomically."""

        with self._identity_lock:
            return self._create_session_locked(
                request_id=request_id,
                profile_id=profile_id,
                client_build=client_build,
            )

    def _create_session_locked(
        self,
        *,
        request_id: str,
        profile_id: str,
        client_build: str,
    ) -> LocalSession:

        request = _require_identifier("request_id", request_id)
        owner = _require_identifier("profile_id", profile_id)
        build = _require_identifier("client_build", client_build)
        payload_digest = _payload_sha256(
            {"clientBuild": build, "profileId": owner}
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._replay_identity_receipt(
                    request,
                    action="create_session",
                    payload_sha256=payload_digest,
                )
                if replay is not None:
                    if not isinstance(replay, LocalSession):
                        raise IdentityStoreCorruptionError(
                            "session command receipt contains a profile"
                        )
                    result = replay
                else:
                    profile_row = self._connection.execute(
                        "SELECT * FROM local_profiles WHERE profile_id = ?",
                        (owner,),
                    ).fetchone()
                    if profile_row is None:
                        raise ProfileNotFoundError("local profile was not found")
                    profile = self._profile_from_row(profile_row)
                    prior_session_count = int(
                        self._connection.execute(
                            "SELECT COUNT(*) FROM local_sessions WHERE profile_id = ?",
                            (owner,),
                        ).fetchone()[0]
                    )
                    opened_at = _server_timestamp()
                    session_id = _server_id("session")
                    (
                        active_world_id,
                        event_ordinal_at_opening,
                        event_hash_at_opening,
                    ) = self._campaign_snapshot_for_new_session(
                        profile,
                        session_id=session_id,
                        opened_at=opened_at,
                    )
                    result = LocalSession(
                        schema_version=LOCAL_SESSION_SCHEMA_VERSION,
                        session_id=session_id,
                        profile_id=owner,
                        client_build=build,
                        opened_at=opened_at,
                        closed_at=None,
                        active_world_id=active_world_id,
                        campaign_catalog_sha256=CAMPAIGN_CATALOG_V1_SHA256,
                        event_ordinal_at_opening=event_ordinal_at_opening,
                        event_hash_at_opening=event_hash_at_opening,
                    )
                    self._connection.execute(
                        """
                        UPDATE local_sessions SET closed_at = ?
                        WHERE profile_id = ? AND closed_at IS NULL
                        """,
                        (opened_at, owner),
                    )
                    self._connection.execute(
                        """
                        INSERT INTO local_sessions (
                            session_id, schema_version, profile_id, client_build,
                            opened_at, closed_at, active_world_id,
                            campaign_catalog_sha256, event_ordinal_at_opening,
                            event_hash_at_opening
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                        """,
                        (
                            result.session_id,
                            result.schema_version,
                            result.profile_id,
                            result.client_build,
                            result.opened_at,
                            result.active_world_id,
                            result.campaign_catalog_sha256,
                            result.event_ordinal_at_opening,
                            result.event_hash_at_opening,
                        ),
                    )
                    if prior_session_count == 0:
                        initial = self._campaign_catalog.initial_world
                        latest = self._connection.execute(
                            """
                            SELECT ordinal FROM event_log
                            WHERE profile_id = ?
                            ORDER BY ordinal DESC LIMIT 1
                            """,
                            (owner,),
                        ).fetchone()
                        ordinal = 1 if latest is None else int(latest["ordinal"]) + 1
                        activation = WorldActivatedEvent(
                            schema_version=EVENT_SCHEMA_VERSION,
                            event_id=_server_id("world-activation"),
                            idempotency_id=_server_id("initial-world"),
                            ordinal=ordinal,
                            profile_id=owner,
                            session_id=result.session_id,
                            world_id=initial.world_id,
                            battle_id="campaign-map",
                            occurred_at=opened_at,
                            core_subskill_ids=initial.core_subskill_ids,
                            curriculum_receipt=(
                                self._campaign_catalog.curriculum_receipt
                            ),
                        )
                        self._append_event_in_transaction(
                            activation,
                            canonical_event_json(activation),
                        )
                    prior_openings = self._load_session_openings(owner)
                    previous_opening_hash = (
                        GENESIS_EVENT_HASH
                        if not prior_openings
                        else prior_openings[-1].opening_hash
                    )
                    self._insert_session_openings(
                        (
                            _make_session_opening(
                                result,
                                opening_ordinal=len(prior_openings) + 1,
                                previous_opening_hash=previous_opening_hash,
                            ),
                        )
                    )
                    self._insert_identity_receipt(
                        request,
                        action="create_session",
                        payload_sha256=payload_digest,
                        profile_id=owner,
                        session_id=result.session_id,
                        response=result,
                    )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        except sqlite3.IntegrityError as exc:
            raise ProfileStoreError("local session identity conflicts") from exc
        except sqlite3.OperationalError as exc:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise ProfileStoreError(
                "session creation could not acquire its write lock"
            ) from exc

        return result

    def load_profile(self, profile_id: str) -> LocalProfile:
        owner = _require_identifier("profile_id", profile_id)
        row = self._connection.execute(
            "SELECT * FROM local_profiles WHERE profile_id = ?",
            (owner,),
        ).fetchone()
        if row is None:
            raise ProfileNotFoundError("local profile was not found")
        return self._profile_from_row(row)

    def load_session(self, session_id: str) -> LocalSession:
        requested = _require_identifier("session_id", session_id)
        row = self._connection.execute(
            "SELECT * FROM local_sessions WHERE session_id = ?",
            (requested,),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError("local session was not found")
        owner = self._session_from_row(row).profile_id
        _, sessions = self._validate_profile_identity_authority(owner)
        for session in sessions:
            if session.session_id == requested:
                return session
        raise IdentityStoreCorruptionError(
            "requested session escaped whole-profile authority"
        )

    def load_open_session(self, profile_id: str) -> LocalSession | None:
        with self._identity_lock:
            return self._load_open_session_locked(profile_id)

    def _load_open_session_locked(
        self,
        profile_id: str,
    ) -> LocalSession | None:
        owner = _require_identifier("profile_id", profile_id)
        profile_row = self._connection.execute(
            "SELECT 1 FROM local_profiles WHERE profile_id = ?",
            (owner,),
        ).fetchone()
        if profile_row is None:
            orphan = self._connection.execute(
                "SELECT 1 FROM local_sessions WHERE profile_id = ? LIMIT 1",
                (owner,),
            ).fetchone()
            if orphan is None:
                return None
            raise IdentityStoreCorruptionError(
                "session authority lacks its durable profile"
            )
        _, sessions = self._validate_profile_identity_authority(owner)
        open_sessions = tuple(
            session for session in sessions if session.closed_at is None
        )
        if len(open_sessions) > 1:
            raise IdentityStoreCorruptionError(
                "profile has more than one open local session"
            )
        if not open_sessions:
            return None
        return open_sessions[0]

    def _insert_identity_receipt(
        self,
        request_id: str,
        *,
        action: str,
        payload_sha256: str,
        profile_id: str,
        session_id: str | None,
        response: LocalProfile | LocalSession,
    ) -> None:
        if action not in _IDENTITY_ACTIONS:
            raise ValueError("unsupported identity command action")
        response_json = _canonical_json(asdict(response))
        self._connection.execute(
            """
            INSERT INTO identity_command_receipts (
                request_id, action, payload_sha256, profile_id, session_id,
                response_json, response_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                action,
                payload_sha256,
                profile_id,
                session_id,
                response_json,
                _sha256_text(response_json),
            ),
        )

    def _replay_identity_receipt(
        self,
        request_id: str,
        *,
        action: str,
        payload_sha256: str,
        validate_authority: bool = True,
    ) -> LocalProfile | LocalSession | None:
        row = self._connection.execute(
            """
            SELECT action, payload_sha256, profile_id, session_id,
                   response_json, response_sha256
            FROM identity_command_receipts WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        if row["action"] != action or row["payload_sha256"] != payload_sha256:
            raise IdempotencyConflictError(
                "request identifier was already used for a different identity payload"
            )
        response_json = row["response_json"]
        response_sha256 = row["response_sha256"]
        if (
            not isinstance(response_json, str)
            or not isinstance(response_sha256, str)
            or _SHA256_PATTERN.fullmatch(response_sha256) is None
            or _sha256_text(response_json) != response_sha256
        ):
            raise IdentityStoreCorruptionError(
                "identity command response digest does not verify"
            )
        try:
            payload = json.loads(response_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise IdentityStoreCorruptionError(
                "identity command response cannot be decoded"
            ) from exc
        if not isinstance(payload, dict) or _canonical_json(payload) != response_json:
            raise IdentityStoreCorruptionError(
                "identity command response is not canonical JSON"
            )

        if action == "create_profile":
            response = self._profile_from_payload(payload)
            if row["profile_id"] != response.profile_id or row["session_id"] is not None:
                raise IdentityStoreCorruptionError(
                    "profile command receipt index does not match its response"
                )
            live_row = self._connection.execute(
                "SELECT * FROM local_profiles WHERE profile_id = ?",
                (response.profile_id,),
            ).fetchone()
            if live_row is None or self._profile_from_row(live_row) != response:
                raise IdentityStoreCorruptionError(
                    "profile command response lacks its durable profile"
                )
            return response

        response = self._session_from_payload(payload)
        if response.closed_at is not None:
            raise IdentityStoreCorruptionError(
                "session creation receipt contains a closed session"
            )
        if (
            row["profile_id"] != response.profile_id
            or row["session_id"] != response.session_id
        ):
            raise IdentityStoreCorruptionError(
                "session command receipt index does not match its response"
            )
        live_row = self._connection.execute(
            "SELECT * FROM local_sessions WHERE session_id = ?",
            (response.session_id,),
        ).fetchone()
        if live_row is None:
            raise IdentityStoreCorruptionError(
                "session command response lacks its durable session"
            )
        live = self._session_from_row(live_row)
        if (
            live.schema_version,
            live.session_id,
            live.profile_id,
            live.client_build,
            live.opened_at,
            live.active_world_id,
            live.campaign_catalog_sha256,
            live.event_ordinal_at_opening,
            live.event_hash_at_opening,
        ) != (
            response.schema_version,
            response.session_id,
            response.profile_id,
            response.client_build,
            response.opened_at,
            response.active_world_id,
            response.campaign_catalog_sha256,
            response.event_ordinal_at_opening,
            response.event_hash_at_opening,
        ):
            raise IdentityStoreCorruptionError(
                "session command response differs from its durable session"
            )
        if validate_authority:
            _, sessions = self._validate_profile_identity_authority(
                response.profile_id
            )
            if response.session_id not in {
                session.session_id for session in sessions
            }:
                raise IdentityStoreCorruptionError(
                    "session command response escaped whole-profile authority"
                )
        return response

    @staticmethod
    def _profile_from_payload(payload: dict[str, Any]) -> LocalProfile:
        if set(payload) != {
            "schema_version",
            "profile_id",
            "created_at",
        }:
            raise IdentityStoreCorruptionError(
                "local profile fields do not match the v1 contract"
            )
        result = LocalProfile(**payload)
        if result.schema_version != LOCAL_PROFILE_SCHEMA_VERSION:
            raise IdentityStoreCorruptionError("unsupported local profile schema")
        try:
            _require_identifier("profile_id", result.profile_id)
        except ValueError as exc:
            raise IdentityStoreCorruptionError("invalid local profile identity") from exc
        _require_canonical_utc_timestamp(result.created_at)
        return result

    def _session_from_payload(self, payload: dict[str, Any]) -> LocalSession:
        if set(payload) != {
            "schema_version",
            "session_id",
            "profile_id",
            "client_build",
            "opened_at",
            "closed_at",
            "active_world_id",
            "campaign_catalog_sha256",
            "event_ordinal_at_opening",
            "event_hash_at_opening",
        }:
            raise IdentityStoreCorruptionError(
                "local session fields do not match the v2 contract"
            )
        result = LocalSession(**payload)
        if result.schema_version != LOCAL_SESSION_SCHEMA_VERSION:
            raise IdentityStoreCorruptionError("unsupported local session schema")
        try:
            _require_identifier("session_id", result.session_id)
            _require_identifier("profile_id", result.profile_id)
            _require_identifier("client_build", result.client_build)
            _require_identifier("active_world_id", result.active_world_id)
        except ValueError as exc:
            raise IdentityStoreCorruptionError("invalid local session identity") from exc
        if result.campaign_catalog_sha256 != CAMPAIGN_CATALOG_V1_SHA256:
            raise IdentityStoreCorruptionError(
                "invalid local session campaign catalog digest"
            )
        self._validate_session_snapshot(result)
        _require_canonical_utc_timestamp(result.opened_at)
        if result.closed_at is not None:
            _require_canonical_utc_timestamp(result.closed_at)
        return result

    def _profile_from_row(self, row: sqlite3.Row) -> LocalProfile:
        return self._profile_from_payload(
            {
                "schema_version": row["schema_version"],
                "profile_id": row["profile_id"],
                "created_at": row["created_at"],
            }
        )

    def _session_from_row(self, row: sqlite3.Row) -> LocalSession:
        return self._session_from_payload(
            {
                "schema_version": row["schema_version"],
                "session_id": row["session_id"],
                "profile_id": row["profile_id"],
                "client_build": row["client_build"],
                "opened_at": row["opened_at"],
                "closed_at": row["closed_at"],
                "active_world_id": row["active_world_id"],
                "campaign_catalog_sha256": row["campaign_catalog_sha256"],
                "event_ordinal_at_opening": row["event_ordinal_at_opening"],
                "event_hash_at_opening": row["event_hash_at_opening"],
            }
        )

    def append_progression_event(self, event: LearningEvent) -> LearningEvent:
        """Atomically allocate, append, and exactly replay one progression event."""

        if event.event_type not in _AUTHORITATIVE_SEMANTIC_EVENT_TYPES:
            raise TypeError("event must be an authoritative progression event")
        if is_legacy_outcome_event(event):
            raise LegacyOutcomeProfileError(
                "legacy outcome events are inspection-only in learner mode"
            )

        with self._identity_lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._require_mutable_progression_profile(event.profile_id)
                    self._require_current_event_session_in_transaction(event)
                    existing_events = self._load_events_from_rows(event.profile_id)
                    replay = next(
                        (
                            existing
                            for existing in existing_events
                            if existing.idempotency_id == event.idempotency_id
                        ),
                        None,
                    )
                    if replay is not None:
                        if _progression_replay_payload(
                            replay
                        ) != _progression_replay_payload(event):
                            raise IdempotencyConflictError(
                                "idempotency identifier was already used for "
                                "different progression content"
                            )
                        self._connection.commit()
                        return replay

                    expected_ordinal = len(existing_events) + 1
                    authoritative = replace(event, ordinal=expected_ordinal)
                    canonical = canonical_event_json(authoritative)
                    self._append_event_in_transaction(authoritative, canonical)
                    state = reduce_events(existing_events + (authoritative,))
                    self._write_projection_in_transaction(
                        authoritative.profile_id,
                        state,
                    )
                    self._connection.commit()
                    return authoritative
                except BaseException:
                    self._connection.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise ProfileStoreError(
                    "progression append could not acquire its write lock"
                ) from exc

    def append(self, event: LearningEvent) -> str:
        """Serialize predecessor selection and append, then refresh the projection."""

        if event.event_type in _AUTHORITATIVE_SEMANTIC_EVENT_TYPES:
            return self._append_authoritative_event(event)

        self._require_mutable_progression_profile(event.profile_id)
        if is_legacy_outcome_event(event):
            raise LegacyOutcomeProfileError(
                "legacy outcome events are inspection-only in learner mode"
            )

        canonical = canonical_event_json(event)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                event_hash = self._append_event_in_transaction(event, canonical)
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise ProfileStoreError("profile append could not acquire its write lock") from exc

        # Deliberately outside the durable append transaction. An interruption loses
        # no evidence; exact retry or load_state repairs this replaceable projection.
        state = reduce_events(self._load_events_from_rows(event.profile_id))
        self._write_projection(event.profile_id, state)
        return event_hash

    def _append_authoritative_event(self, event: LearningEvent) -> str:
        """Linearize current-session authority, outcome append, and projection."""

        canonical = canonical_event_json(event)
        with self._identity_lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    self._require_mutable_progression_profile(event.profile_id)
                    self._require_current_event_session_in_transaction(event)
                    event_hash = self._append_event_in_transaction(
                        event,
                        canonical,
                    )
                    state = reduce_events(
                        self._load_events_from_rows(event.profile_id)
                    )
                    self._write_projection_in_transaction(
                        event.profile_id,
                        state,
                    )
                    self._connection.commit()
                    return event_hash
                except BaseException:
                    self._connection.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise ProfileStoreError(
                    "authoritative append could not acquire its write lock"
                ) from exc

    def _require_current_event_session_in_transaction(
        self,
        event: LearningEvent,
    ) -> None:
        profile_row = self._connection.execute(
            "SELECT profile_id FROM local_profiles WHERE profile_id = ?",
            (event.profile_id,),
        ).fetchone()
        if profile_row is None:
            raise ProfileNotFoundError("local profile was not found")

        _profile, sessions = self._validate_profile_identity_authority(
            event.profile_id
        )
        open_sessions = tuple(
            session for session in sessions if session.closed_at is None
        )
        if (
            len(open_sessions) != 1
            or open_sessions[0].session_id != event.session_id
            or open_sessions[0].profile_id != event.profile_id
        ):
            raise CampaignStateConflictError(
                "authoritative event session is not current"
            )

    def _append_event_in_transaction(
        self,
        event: LearningEvent,
        canonical: str,
    ) -> str:
        """Append under a caller-owned immediate transaction without committing."""

        self._validate_delivered_outbox_rows(event.profile_id)
        existing = self._connection.execute(
            """
            SELECT canonical_json, event_hash
            FROM event_log
            WHERE profile_id = ? AND idempotency_id = ?
            """,
            (event.profile_id, event.idempotency_id),
        ).fetchone()
        if existing is not None:
            if existing["canonical_json"] != canonical:
                raise IdempotencyConflictError(
                    "idempotency identifier was already used for different event content"
                )
            return str(existing["event_hash"])

        self._require_earliest_outbox_reservation(event, canonical)
        if (
            isinstance(event, ObservationEvent)
            or event.event_type in _AUTHORITATIVE_SEMANTIC_EVENT_TYPES
        ):
            semantic_existing = self._connection.execute(
                """
                SELECT event_id FROM event_log
                WHERE profile_id = ? AND semantic_key = ?
                """,
                (event.profile_id, event.semantic_key),
            ).fetchone()
            if semantic_existing is not None:
                raise SemanticEventConflictError(
                    "an event already exists for this authoritative semantic target"
                )

        latest = self._connection.execute(
            """
            SELECT ordinal, event_hash
            FROM event_log
            WHERE profile_id = ?
            ORDER BY ordinal DESC
            LIMIT 1
            """,
            (event.profile_id,),
        ).fetchone()
        expected_ordinal = 1 if latest is None else int(latest["ordinal"]) + 1
        if event.ordinal != expected_ordinal:
            raise EventOrderError(f"event ordinal must be exactly {expected_ordinal}")
        previous_hash = (
            GENESIS_EVENT_HASH if latest is None else str(latest["event_hash"])
        )
        existing_events = self._load_events_from_rows(event.profile_id)
        try:
            reduce_events(existing_events + (event,))
        except DuplicateSemanticEventError as exc:
            raise SemanticEventConflictError(str(exc)) from exc
        event_hash = compute_event_hash(previous_hash, event)
        try:
            self._connection.execute(
                """
                INSERT INTO event_log (
                    profile_id, ordinal, event_id, idempotency_id,
                    event_type, semantic_key, canonical_json,
                    previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.profile_id,
                    event.ordinal,
                    event.event_id,
                    event.idempotency_id,
                    event.event_type,
                    event.semantic_key,
                    canonical,
                    previous_hash,
                    event_hash,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if (
                isinstance(event, ObservationEvent)
                or event.event_type in _AUTHORITATIVE_SEMANTIC_EVENT_TYPES
            ):
                raise SemanticEventConflictError(
                    "event semantic identity conflicts with the existing log"
                ) from exc
            raise IdempotencyConflictError(
                "event identity conflicts with the existing log"
            ) from exc
        return event_hash

    def _validate_delivered_outbox_rows(self, profile_id: str) -> None:
        table = self._connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'quiz_observation_outbox'"
        ).fetchone()
        if table is None:
            return
        rows = self._connection.execute(
            """
            SELECT ordinal, event_id, idempotency_id, canonical_json, event_sha256
            FROM quiz_observation_outbox
            WHERE profile_id = ? AND delivered = 1
            """,
            (profile_id,),
        ).fetchall()
        for row in rows:
            canonical = row["canonical_json"]
            digest = row["event_sha256"]
            if (
                not isinstance(canonical, str)
                or not isinstance(digest, str)
                or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != digest
            ):
                raise OutboxReservationError(
                    "delivered observation reservation is corrupt"
                )
            durable = self._connection.execute(
                """
                SELECT ordinal, event_id, canonical_json FROM event_log
                WHERE profile_id = ? AND idempotency_id = ?
                """,
                (profile_id, row["idempotency_id"]),
            ).fetchone()
            if (
                durable is None
                or int(durable["ordinal"]) != int(row["ordinal"])
                or durable["event_id"] != row["event_id"]
                or durable["canonical_json"] != canonical
            ):
                raise OutboxReservationError(
                    "delivered observation lacks its canonical event log row"
                )

    def _require_earliest_outbox_reservation(
        self,
        event: LearningEvent,
        canonical: str,
    ) -> None:
        table = self._connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'quiz_observation_outbox'"
        ).fetchone()
        if table is None:
            return
        row = self._connection.execute(
            """
            SELECT profile_id, batch_id, item_id, ordinal, event_id,
                   idempotency_id, canonical_json, event_sha256
            FROM quiz_observation_outbox
            WHERE profile_id = ? AND delivered = 0
            ORDER BY ordinal, batch_id, item_id
            LIMIT 1
            """,
            (event.profile_id,),
        ).fetchone()
        if row is None:
            return
        reserved_json = row["canonical_json"]
        reserved_sha256 = row["event_sha256"]
        if (
            not isinstance(reserved_json, str)
            or not isinstance(reserved_sha256, str)
            or hashlib.sha256(reserved_json.encode("utf-8")).hexdigest()
            != reserved_sha256
        ):
            raise OutboxReservationError(
                "earliest observation reservation is corrupt"
            )
        try:
            reserved = event_from_json(reserved_json)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OutboxReservationError(
                "earliest observation reservation cannot be decoded"
            ) from exc
        if (
            not isinstance(reserved, ObservationEvent)
            or canonical_event_json(reserved) != reserved_json
            or row["profile_id"] != reserved.profile_id
            or row["batch_id"] != reserved.batch_id
            or row["item_id"] != reserved.item_id
            or int(row["ordinal"]) != reserved.ordinal
            or row["event_id"] != reserved.event_id
            or row["idempotency_id"] != reserved.idempotency_id
        ):
            raise OutboxReservationError(
                "earliest observation reservation index is corrupt"
            )
        if canonical != reserved_json:
            raise OutboxReservationError(
                "earliest pending observation reserves the next profile ordinal"
            )

    def load_events(self, profile_id: str) -> tuple[LearningEvent, ...]:
        return self._load_events_from_rows(profile_id)

    def event_head(self, profile_id: str) -> tuple[int, str]:
        """Return the verified append-log head for optimistic preparation."""

        events = self._load_events_from_rows(profile_id)
        row = self._connection.execute(
            "SELECT ordinal, event_hash FROM event_log WHERE profile_id = ? "
            "ORDER BY ordinal DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if row is None:
            if events:
                raise EventLogCorruptionError("event head is missing")
            return 0, GENESIS_EVENT_HASH
        if not events or int(row["ordinal"]) != events[-1].ordinal:
            raise EventLogCorruptionError("event head does not match replay")
        digest = str(row["event_hash"])
        if _SHA256_PATTERN.fullmatch(digest) is None:
            raise EventLogCorruptionError("event head hash is invalid")
        return int(row["ordinal"]), digest

    def inspect_events(self, profile_id: str) -> tuple[LearningEvent, ...]:
        """Decode events without granting legacy outcomes progression authority."""

        return self._load_events_from_rows(profile_id)

    def _load_events_from_rows(self, profile_id: str) -> tuple[LearningEvent, ...]:
        rows = self._connection.execute(
            """
            SELECT ordinal, event_id, idempotency_id, event_type, semantic_key,
                   canonical_json, previous_event_hash, event_hash
            FROM event_log
            WHERE profile_id = ?
            ORDER BY ordinal ASC
            """,
            (profile_id,),
        ).fetchall()
        events: list[LearningEvent] = []
        previous_hash = GENESIS_EVENT_HASH
        try:
            for row in rows:
                if row["previous_event_hash"] != previous_hash:
                    raise EventLogCorruptionError("event hash chain has a broken link")
                event = event_from_json(str(row["canonical_json"]))
                if event.profile_id != profile_id or event.ordinal != int(row["ordinal"]):
                    raise EventLogCorruptionError("event identity differs from its log index")
                if event.event_id != row["event_id"]:
                    raise EventLogCorruptionError("event_id differs from its log index")
                if event.idempotency_id != row["idempotency_id"]:
                    raise EventLogCorruptionError(
                        "idempotency_id differs from its log index"
                    )
                if event.event_type != row["event_type"]:
                    raise EventLogCorruptionError("event type differs from its log index")
                if event.semantic_key != row["semantic_key"]:
                    raise EventLogCorruptionError("semantic key differs from its log index")
                if canonical_event_json(event) != row["canonical_json"]:
                    raise EventLogCorruptionError("event payload is not canonical JSON")
                expected_hash = compute_event_hash(previous_hash, event)
                if expected_hash != row["event_hash"]:
                    raise EventLogCorruptionError("event content hash does not verify")
                events.append(event)
                previous_hash = expected_hash
        except EventLogCorruptionError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EventLogCorruptionError("event payload cannot be replayed") from exc
        return tuple(events)

    def load_state(self, profile_id: str) -> LearnerState:
        self._require_mutable_progression_profile(profile_id)
        state = reduce_events(self.load_events(profile_id))
        projection = state.canonical_bytes()
        row = self._connection.execute(
            "SELECT through_ordinal, projection FROM learner_projection WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        through_ordinal = max((event.ordinal for event in state.events), default=0)
        if (
            row is None
            or int(row["through_ordinal"]) != through_ordinal
            or bytes(row["projection"]) != projection
        ):
            self._write_projection(profile_id, state)
        return state

    def rebuild_projection(self, profile_id: str) -> LearnerState:
        self._require_mutable_progression_profile(profile_id)
        state = reduce_events(self.load_events(profile_id))
        self._write_projection(profile_id, state)
        return state

    def _write_projection(self, profile_id: str, state: LearnerState) -> None:
        with self._connection:
            self._write_projection_in_transaction(profile_id, state)

    def _write_projection_in_transaction(
        self,
        profile_id: str,
        state: LearnerState,
    ) -> None:
        through_ordinal = max((event.ordinal for event in state.events), default=0)
        self._connection.execute(
            """
            INSERT INTO learner_projection (profile_id, through_ordinal, projection)
            VALUES (?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                through_ordinal = excluded.through_ordinal,
                projection = excluded.projection
            """,
            (profile_id, through_ordinal, state.canonical_bytes()),
        )

    def projection_bytes(self, profile_id: str) -> bytes:
        self.load_state(profile_id)
        row = self._connection.execute(
            "SELECT projection FROM learner_projection WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            return reduce_events(()).canonical_bytes()
        return bytes(row["projection"])

    def _validate_export_identity(
        self,
        profile: LocalProfile,
        sessions: tuple[LocalSession, ...],
    ) -> None:
        """Recheck every durable identity row and its creation receipt."""

        profile_created = datetime.strptime(
            profile.created_at,
            "%Y-%m-%dT%H:%M:%S.%fZ"
            if "." in profile.created_at
            else "%Y-%m-%dT%H:%M:%SZ",
        )
        session_by_id: dict[str, LocalSession] = {}
        open_session_count = 0
        for session in sessions:
            if session.profile_id != profile.profile_id:
                raise IdentityStoreCorruptionError(
                    "local session belongs to a different profile"
                )
            if session.session_id in session_by_id:
                raise IdentityStoreCorruptionError(
                    "duplicate local session identity"
                )
            session_by_id[session.session_id] = session
            opened = datetime.strptime(
                session.opened_at,
                "%Y-%m-%dT%H:%M:%S.%fZ"
                if "." in session.opened_at
                else "%Y-%m-%dT%H:%M:%SZ",
            )
            if opened < profile_created:
                raise IdentityStoreCorruptionError(
                    "local session predates profile creation"
                )
            if session.closed_at is None:
                open_session_count += 1
            else:
                closed = datetime.strptime(
                    session.closed_at,
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                    if "." in session.closed_at
                    else "%Y-%m-%dT%H:%M:%SZ",
                )
                if closed < opened:
                    raise IdentityStoreCorruptionError(
                        "local session closes before it opens"
                    )
        if open_session_count > 1:
            raise IdentityStoreCorruptionError(
                "profile has more than one open local session"
            )

        receipt_rows = self._connection.execute(
            """
            SELECT request_id, action, session_id
            FROM identity_command_receipts
            WHERE profile_id = ?
            ORDER BY request_id ASC
            """,
            (profile.profile_id,),
        ).fetchall()
        if len(receipt_rows) != len(sessions) + 1:
            raise IdentityStoreCorruptionError(
                "profile identity receipts are missing or duplicated"
            )

        profile_receipt_count = 0
        session_receipts: set[str] = set()
        for row in receipt_rows:
            request_id = row["request_id"]
            action = row["action"]
            if not isinstance(request_id, str):
                raise IdentityStoreCorruptionError(
                    "identity receipt request is invalid"
                )
            if action == "create_profile":
                profile_receipt_count += 1
                replay = self._replay_identity_receipt(
                    request_id,
                    action=action,
                    payload_sha256=_payload_sha256(
                        {"schemaVersion": "wayline.v1"}
                    ),
                )
                if replay != profile:
                    raise IdentityStoreCorruptionError(
                        "profile identity receipt does not match its profile"
                    )
                continue
            if action != "create_session":
                raise IdentityStoreCorruptionError(
                    "identity receipt action is unsupported"
                )
            session_id = row["session_id"]
            if not isinstance(session_id, str) or session_id in session_receipts:
                raise IdentityStoreCorruptionError(
                    "session identity receipt index is invalid"
                )
            session = session_by_id.get(session_id)
            if session is None:
                raise IdentityStoreCorruptionError(
                    "session identity receipt lacks its durable session"
                )
            replay = self._replay_identity_receipt(
                request_id,
                action=action,
                payload_sha256=_payload_sha256(
                    {
                        "clientBuild": session.client_build,
                        "profileId": profile.profile_id,
                    }
                ),
            )
            if (
                not isinstance(replay, LocalSession)
                or replay.session_id != session.session_id
                or replay.profile_id != profile.profile_id
                or replay.client_build != session.client_build
                or replay.opened_at != session.opened_at
                or replay.closed_at is not None
                or replay.active_world_id != session.active_world_id
                or replay.campaign_catalog_sha256
                != session.campaign_catalog_sha256
                or replay.event_ordinal_at_opening
                != session.event_ordinal_at_opening
                or replay.event_hash_at_opening != session.event_hash_at_opening
            ):
                raise IdentityStoreCorruptionError(
                    "session identity receipt does not match its session"
                )
            session_receipts.add(session_id)

        if profile_receipt_count != 1 or session_receipts != set(session_by_id):
            raise IdentityStoreCorruptionError(
                "identity receipts do not cover the exported profile"
            )

    @staticmethod
    def _validate_export_event_timestamps_and_sessions(
        events: tuple[LearningEvent, ...],
        sessions: tuple[LocalSession, ...],
    ) -> None:
        """Ensure exported events belong to real sessions at canonical UTC times."""

        session_by_id = {session.session_id: session for session in sessions}
        for event in events:
            try:
                _require_canonical_utc_timestamp(event.occurred_at)
            except IdentityStoreCorruptionError as exc:
                raise EventLogCorruptionError(
                    "event timestamp is not canonical UTC"
                ) from exc
            session = session_by_id.get(event.session_id)
            if session is None:
                raise EventLogCorruptionError(
                    "event references a session outside its profile"
                )
            event_time = datetime.strptime(
                event.occurred_at,
                "%Y-%m-%dT%H:%M:%S.%fZ"
                if "." in event.occurred_at
                else "%Y-%m-%dT%H:%M:%SZ",
            )
            opened = datetime.strptime(
                session.opened_at,
                "%Y-%m-%dT%H:%M:%S.%fZ"
                if "." in session.opened_at
                else "%Y-%m-%dT%H:%M:%SZ",
            )
            if event_time < opened:
                raise EventLogCorruptionError(
                    "event predates its local session"
                )
            if session.closed_at is not None:
                closed = datetime.strptime(
                    session.closed_at,
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                    if "." in session.closed_at
                    else "%Y-%m-%dT%H:%M:%SZ",
                )
                if event_time > closed:
                    raise EventLogCorruptionError(
                        "event occurs after its local session closed"
                    )

    def _validate_export_campaign(
        self,
        profile_id: str,
        sessions: tuple[LocalSession, ...],
        events: tuple[LearningEvent, ...],
        state: LearnerState,
    ) -> int | None:
        """Re-derive campaign position from the pinned catalog and event stream."""

        if tuple(event.ordinal for event in events) != tuple(
            range(1, len(events) + 1)
        ):
            raise EventLogCorruptionError(
                "profile event ordinals are not contiguous"
            )
        if bool(sessions) != bool(events):
            raise EventLogCorruptionError(
                "profile sessions and initial campaign activation disagree"
            )
        if events and not isinstance(events[0], WorldActivatedEvent):
            raise EventLogCorruptionError(
                "profile event stream does not begin with campaign activation"
            )

        active_world_id: str | None = None
        active_sequence = 0
        activated_world_ids: list[str] = []
        for event in events:
            if isinstance(event, WorldActivatedEvent):
                expected_sequence = active_sequence + 1
                if expected_sequence > len(self._campaign_catalog.worlds):
                    raise EventLogCorruptionError(
                        "campaign activation exceeds the pinned catalog"
                    )
                expected_world = self._campaign_catalog.worlds[
                    expected_sequence - 1
                ]
                if (
                    event.world_id != expected_world.world_id
                    or event.core_subskill_ids != expected_world.core_subskill_ids
                    or event.curriculum_receipt
                    != self._campaign_catalog.curriculum_receipt
                    or event.battle_id != "campaign-map"
                ):
                    raise EventLogCorruptionError(
                        "campaign activation differs from pinned campaign authority"
                    )
                active_sequence = expected_sequence
                active_world_id = expected_world.world_id
                activated_world_ids.append(active_world_id)
            elif isinstance(event, ObservationEvent):
                if active_world_id is None:
                    raise EventLogCorruptionError(
                        "observation precedes campaign activation"
                    )
                if event.world_id != active_world_id:
                    if (
                        not event.is_transfer
                        or event.world_id not in activated_world_ids
                    ):
                        raise EventLogCorruptionError(
                            "observation uses an unauthored campaign world"
                        )
            elif active_world_id is None or event.world_id != active_world_id:
                raise EventLogCorruptionError(
                    "progression event is outside the active campaign world"
                )

        expected_profile = profile_id if events else None
        if (
            state.profile_id != expected_profile
            or state.events != events
            or state.active_world_id != active_world_id
        ):
            raise EventLogCorruptionError(
                "reduced learner state differs from canonical campaign events"
            )
        for world_id in activated_world_ids:
            catalog_world = next(
                world
                for world in self._campaign_catalog.worlds
                if world.world_id == world_id
            )
            derived_world = state.world(world_id)
            if (
                derived_world.core_subskill_ids
                != catalog_world.core_subskill_ids
                or derived_world.curriculum_receipt
                != self._campaign_catalog.curriculum_receipt
            ):
                raise EventLogCorruptionError(
                    "derived world state differs from pinned campaign authority"
                )
        return active_sequence or None

    def _build_profile_export(self, profile_id: str) -> ProfileExportV1:
        """Build one export from rows visible in the caller's snapshot."""

        profile = self.load_profile(profile_id)
        session_rows = self._connection.execute(
            """
            SELECT * FROM local_sessions
            WHERE profile_id = ?
            ORDER BY opened_at ASC, session_id ASC
            """,
            (profile.profile_id,),
        ).fetchall()
        sessions = tuple(self._session_from_row(row) for row in session_rows)
        self._validate_export_identity(profile, sessions)
        events = self.load_events(profile.profile_id)
        self._validate_export_event_timestamps_and_sessions(events, sessions)
        try:
            state = reduce_events(events)
        except (TypeError, ValueError) as exc:
            raise EventLogCorruptionError(
                "canonical events cannot produce a learner state"
            ) from exc
        active_world_id = state.active_world_id
        campaign_ordinal = self._validate_export_campaign(
            profile.profile_id,
            sessions,
            events,
            state,
        )

        exported_sessions = [
            ProfileExportSessionV1.model_validate(
                {
                    "sessionId": session.session_id,
                    "clientBuild": session.client_build,
                    "openedAtUtc": session.opened_at,
                    "closedAtUtc": session.closed_at,
                }
            )
            for session in sessions
        ]
        exported_events: list[ProfileExportEventV1] = []
        chain_hash = PROFILE_EXPORT_GENESIS_EVENT_HASH
        for event in events:
            canonical = canonical_event_json(event)
            exported_events.append(
                ProfileExportEventV1.model_validate(
                    {
                        "ordinal": event.ordinal,
                        "canonicalEventJson": canonical,
                        "eventSha256": _sha256_text(canonical),
                    }
                )
            )
            chain_hash = compute_event_hash(chain_hash, event)
        return ProfileExportV1.model_validate(
            {
                "schemaVersion": "wayline.profile-export.v1",
                "profileId": profile.profile_id,
                "createdAtUtc": profile.created_at,
                "campaignCatalogSha256": CAMPAIGN_CATALOG_V1_SHA256,
                "activeWorldId": active_world_id,
                "campaignOrdinal": campaign_ordinal,
                "sessions": exported_sessions,
                "events": exported_events,
                "terminalEventChainSha256": chain_hash,
            }
        )

    def export_profile(self, profile_id: str) -> ProfileExportV1:
        """Return one strict, portable export of an existing local profile."""

        return self._build_profile_export(profile_id)

    def export_current_profile(
        self,
        profile_id: str,
        current_session_id: str,
    ) -> ProfileExportV1:
        """Authorize and build an export at one write-serialized snapshot."""

        with self._identity_lock:
            return ProfileStore._export_current_profile_locked(
                self,
                profile_id,
                current_session_id,
            )

    def _export_current_profile_locked(
        self,
        profile_id: str,
        current_session_id: str,
    ) -> ProfileExportV1:
        owner = _require_identifier("profile_id", profile_id)
        requested_session = _require_identifier(
            "current_session_id",
            current_session_id,
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            total_changes_before_export = self._connection.total_changes
            try:
                profile, sessions = ProfileStore._validate_profile_identity_authority(
                    self,
                    owner,
                )
                open_sessions = tuple(
                    session
                    for session in sessions
                    if session.closed_at is None
                )
                if (
                    len(open_sessions) != 1
                    or open_sessions[0].session_id != requested_session
                ):
                    raise SessionNotFoundError(
                        "requested local session is not current"
                    )

                candidate = ProfileStore._build_profile_export(self, owner)
                if type(candidate) is not ProfileExportV1:
                    raise IdentityStoreCorruptionError(
                        "profile export has the wrong contract type"
                    )
                try:
                    validated = ProfileExportV1.model_validate(
                        candidate.model_dump(mode="json", by_alias=True)
                    )
                except (AttributeError, TypeError, ValueError) as exc:
                    raise IdentityStoreCorruptionError(
                        "profile export failed strict revalidation"
                    ) from exc

                # Rebuild directly from authoritative rows rather than trusting
                # nested export records, which intentionally omit profileId.
                authoritative = ProfileStore._build_profile_export(
                    self,
                    profile.profile_id,
                )
                if validated != authoritative:
                    raise IdentityStoreCorruptionError(
                        "profile export differs from its authoritative rows"
                    )
                exported_current = tuple(
                    session
                    for session in validated.sessions
                    if session.session_id == requested_session
                )
                if (
                    len(exported_current) != 1
                    or exported_current[0].closed_at_utc is not None
                ):
                    raise IdentityStoreCorruptionError(
                        "profile export omits its authorized current session"
                    )
                if not self._connection.in_transaction:
                    raise IdentityStoreCorruptionError(
                        "profile export lost its transaction ownership"
                    )
                if self._connection.total_changes != total_changes_before_export:
                    raise IdentityStoreCorruptionError(
                        "profile export transaction performed an unexpected write"
                    )

                # This commit is the authorization linearization point. A
                # cross-process identity writer cannot rotate the session before
                # the validated snapshot is complete because BEGIN IMMEDIATE owns
                # SQLite's write reservation until here.
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        except sqlite3.OperationalError as exc:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise ProfileStoreError(
                "profile export could not acquire its write lock"
            ) from exc
        return validated

    def delete_profile(
        self,
        profile_id: str,
        *,
        expected_session_id: str | None = None,
    ) -> None:
        with self._identity_lock:
            self._delete_profile_locked(
                profile_id,
                expected_session_id=expected_session_id,
            )

    def _delete_profile_locked(
        self,
        profile_id: str,
        *,
        expected_session_id: str | None,
    ) -> None:
        owner = _require_identifier("profile_id", profile_id)
        expected = (
            None
            if expected_session_id is None
            else _require_identifier("expected_session_id", expected_session_id)
        )
        self._truncate_wal_checkpoint(require_complete=True)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            if expected is not None:
                open_rows = self._connection.execute(
                    "SELECT session_id FROM local_sessions "
                    "WHERE profile_id = ? AND closed_at IS NULL",
                    (owner,),
                ).fetchall()
                if len(open_rows) != 1 or open_rows[0]["session_id"] != expected:
                    raise CampaignStateConflictError(
                        "profile deletion session is not current"
                    )
                session_row = self._connection.execute(
                    "SELECT * FROM local_sessions WHERE session_id = ? "
                    "AND profile_id = ? AND closed_at IS NULL",
                    (expected, owner),
                ).fetchone()
                if session_row is None:
                    raise CampaignStateConflictError(
                        "profile deletion session is not current"
                    )
                _, sessions = self._validate_profile_identity_authority(owner)
                if expected not in {
                    session.session_id
                    for session in sessions
                    if session.closed_at is None
                }:
                    raise CampaignStateConflictError(
                        "profile deletion session is not current"
                    )
            self._remove_migration_backups()
            tables = {
                str(row[0])
                for row in self._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self._connection.execute(
                "DELETE FROM learner_projection WHERE profile_id = ?", (owner,)
            )
            self._connection.execute(
                "DELETE FROM event_log WHERE profile_id = ?", (owner,)
            )
            if "legacy_outcome_profiles" in tables:
                self._connection.execute(
                    "DELETE FROM legacy_outcome_profiles WHERE profile_id = ?",
                    (owner,),
                )
            if "quiz_observation_outbox" in tables:
                self._connection.execute(
                    "DELETE FROM quiz_observation_outbox WHERE profile_id = ?",
                    (owner,),
                )
            if "quiz_transition_receipts" in tables:
                self._connection.execute(
                    "DELETE FROM quiz_transition_receipts WHERE profile_id = ?",
                    (owner,),
                )
            if "quiz_preparation_receipts" in tables:
                self._connection.execute(
                    "DELETE FROM quiz_preparation_receipts WHERE profile_id = ?",
                    (owner,),
                )
            if "quiz_batch_material" in tables:
                self._connection.execute(
                    "DELETE FROM quiz_batch_material WHERE profile_id = ?",
                    (owner,),
                )
            if "quiz_machines" in tables:
                self._connection.execute(
                    "DELETE FROM quiz_machines WHERE profile_id = ?",
                    (owner,),
                )
            if "identity_command_receipts" in tables:
                self._connection.execute(
                    "DELETE FROM identity_command_receipts WHERE profile_id = ?",
                    (owner,),
                )
            if "session_opening_log" in tables:
                self._connection.execute(
                    "DELETE FROM session_opening_log WHERE profile_id = ?",
                    (owner,),
                )
            if "local_sessions" in tables:
                self._connection.execute(
                    "DELETE FROM local_sessions WHERE profile_id = ?",
                    (owner,),
                )
            if "local_profiles" in tables:
                self._connection.execute(
                    "DELETE FROM local_profiles WHERE profile_id = ?",
                    (owner,),
                )
            for assisted_table in (
                "assisted_route_preparation_receipts",
                "assisted_route_material",
            ):
                if assisted_table not in tables:
                    continue
                remaining = self._connection.execute(
                    f"SELECT COUNT(*) FROM {assisted_table} WHERE profile_id = ?",
                    (owner,),
                ).fetchone()[0]
                if type(remaining) is not int or remaining != 0:
                    raise IdentityStoreCorruptionError(
                        "profile deletion did not cascade assisted route data"
                    )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        try:
            self._post_delete_maintenance()
        except Exception:
            # The durable deletion already committed; maintenance cannot turn
            # that success into a false failure result.
            pass
