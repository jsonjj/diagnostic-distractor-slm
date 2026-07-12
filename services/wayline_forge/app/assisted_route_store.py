"""Hashed private persistence for fresh assisted-route material."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Final

from .assisted_route_machine import (
    public_assisted_batch,
    require_assisted_material,
)
from .batch_material import VerifiedBatchMaterial
from .contracts import AssistedRouteBatch
from .events import GENESIS_EVENT_HASH
from .providers.distractor import PinnedSlmManifest
from .question_kernel import QuestionCompiler


_SCHEMA_VERSION: Final[int] = 1
_MAX_PRIVATE_BYTES: Final[int] = 4 * 1024 * 1024
_SHA256: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}", re.ASCII)
_IDENTIFIER: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{2,95}",
    re.ASCII,
)
_CANONICAL_UTC_TIMESTAMP: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{6})?Z",
    re.ASCII,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


class AssistedRouteStoreError(RuntimeError):
    """Stable storage error with no private-material detail."""

    _CODES: Final[frozenset[str]] = frozenset(
        {
            "activity_in_progress",
            "idempotency_conflict",
            "integrity_failure",
            "profile_not_found",
            "route_not_found",
            "stale_event_head",
            "storage_busy",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown assisted route store error")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class StoredAssistedRoute:
    route_id: str
    profile_id: str
    source_session_id: str
    world_id: str
    event_head_ordinal: int
    event_head_hash: str
    route_plan_sha256: str
    material: VerifiedBatchMaterial
    batch: AssistedRouteBatch
    material_sha256: str
    output_sha256: str
    created_at_utc: str


class AssistedRouteStore:
    """One SQLite connection owning isolated assisted-route persistence."""

    def __init__(
        self,
        path: Path | str,
        *,
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
        timeout_seconds: float = 0.25,
    ) -> None:
        if not isinstance(compiler, QuestionCompiler):
            raise TypeError("compiler must be a QuestionCompiler")
        if not isinstance(manifest, PinnedSlmManifest):
            raise TypeError("manifest must be a PinnedSlmManifest")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds < 0
        ):
            raise ValueError("timeout_seconds must be nonnegative")
        self.path = Path(path)
        self._compiler = compiler
        self._manifest = manifest
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self.path,
            timeout=float(timeout_seconds),
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA secure_delete = ON")
        self._connection.execute(
            f"PRAGMA busy_timeout = {round(float(timeout_seconds) * 1000)}"
        )
        self._busy_timeout_ms = round(float(timeout_seconds) * 1000)
        self._connection.execute("PRAGMA journal_mode = WAL")
        try:
            self._ensure_schema()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "AssistedRouteStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise AssistedRouteStoreError("integrity_failure")
        return self._connection

    def _ensure_schema(self) -> None:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assisted_route_store_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assisted_route_material (
                    route_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    world_id TEXT NOT NULL,
                    event_head_ordinal INTEGER NOT NULL
                        CHECK (event_head_ordinal >= 0),
                    event_head_hash TEXT NOT NULL,
                    route_plan_sha256 TEXT NOT NULL,
                    material_json TEXT NOT NULL,
                    material_sha256 TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE (profile_id, world_id),
                    UNIQUE (profile_id, route_id),
                    FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assisted_route_preparation_receipts (
                    profile_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    route_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    output_sha256 TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL,
                    PRIMARY KEY (profile_id, request_id),
                    FOREIGN KEY (profile_id, route_id)
                        REFERENCES assisted_route_material(profile_id, route_id)
                        ON DELETE CASCADE
                )
                """
            )
            row = connection.execute(
                "SELECT schema_version FROM assisted_route_store_metadata "
                "WHERE singleton = 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO assisted_route_store_metadata "
                    "(singleton, schema_version) VALUES (1, ?)",
                    (_SCHEMA_VERSION,),
                )
            elif int(row["schema_version"]) != _SCHEMA_VERSION:
                raise AssistedRouteStoreError("integrity_failure")
            self._validate_schema(connection, self._busy_timeout_ms)
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    @staticmethod
    def _validate_schema(
        connection: sqlite3.Connection,
        busy_timeout_ms: int,
    ) -> None:
        if (
            int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1
            or int(connection.execute("PRAGMA secure_delete").fetchone()[0]) != 1
            or int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
            != busy_timeout_ms
            or str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            != "wal"
        ):
            raise AssistedRouteStoreError("integrity_failure")

        expected_columns = {
            "assisted_route_store_metadata": (
                (0, "singleton", "INTEGER", 0, None, 1),
                (1, "schema_version", "INTEGER", 1, None, 0),
            ),
            "assisted_route_material": (
                (0, "route_id", "TEXT", 0, None, 1),
                (1, "profile_id", "TEXT", 1, None, 0),
                (2, "source_session_id", "TEXT", 1, None, 0),
                (3, "world_id", "TEXT", 1, None, 0),
                (4, "event_head_ordinal", "INTEGER", 1, None, 0),
                (5, "event_head_hash", "TEXT", 1, None, 0),
                (6, "route_plan_sha256", "TEXT", 1, None, 0),
                (7, "material_json", "TEXT", 1, None, 0),
                (8, "material_sha256", "TEXT", 1, None, 0),
                (9, "created_at_utc", "TEXT", 1, None, 0),
            ),
            "assisted_route_preparation_receipts": (
                (0, "profile_id", "TEXT", 1, None, 1),
                (1, "request_id", "TEXT", 1, None, 2),
                (2, "route_id", "TEXT", 1, None, 0),
                (3, "payload_sha256", "TEXT", 1, None, 0),
                (4, "output_sha256", "TEXT", 1, None, 0),
                (5, "receipt_json", "TEXT", 1, None, 0),
                (6, "receipt_sha256", "TEXT", 1, None, 0),
            ),
        }
        for table, expected in expected_columns.items():
            actual = tuple(
                (
                    int(row["cid"]),
                    str(row["name"]),
                    str(row["type"]).upper(),
                    int(row["notnull"]),
                    row["dflt_value"],
                    int(row["pk"]),
                )
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != expected:
                raise AssistedRouteStoreError("integrity_failure")

        def index_shapes(table: str) -> set[tuple[tuple[str, ...], int, str, int, object]]:
            shapes: set[tuple[tuple[str, ...], int, str, int, object]] = set()
            for row in connection.execute(f"PRAGMA index_list({table})"):
                name = str(row["name"])
                columns = tuple(
                    str(item["name"])
                    for item in connection.execute(f'PRAGMA index_info("{name}")')
                )
                sql_row = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                    (name,),
                ).fetchone()
                shapes.add((
                    columns,
                    int(row["unique"]),
                    str(row["origin"]),
                    int(row["partial"]),
                    None if sql_row is None else sql_row["sql"],
                ))
            return shapes

        expected_indexes = {
            "assisted_route_store_metadata": set(),
            "assisted_route_material": {
                (("route_id",), 1, "pk", 0, None),
                (("profile_id", "world_id"), 1, "u", 0, None),
                (("profile_id", "route_id"), 1, "u", 0, None),
            },
            "assisted_route_preparation_receipts": {
                (("profile_id", "request_id"), 1, "pk", 0, None),
            },
        }
        for table, expected in expected_indexes.items():
            if index_shapes(table) != expected:
                raise AssistedRouteStoreError("integrity_failure")

        def foreign_keys(table: str) -> set[tuple[object, ...]]:
            return {
                (
                    int(row["id"]),
                    int(row["seq"]),
                    str(row["table"]),
                    str(row["from"]),
                    str(row["to"]),
                    str(row["on_update"]),
                    str(row["on_delete"]),
                    str(row["match"]),
                )
                for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            }

        if foreign_keys("assisted_route_material") != {
            (0, 0, "local_profiles", "profile_id", "profile_id", "NO ACTION", "CASCADE", "NONE")
        }:
            raise AssistedRouteStoreError("integrity_failure")
        if foreign_keys("assisted_route_preparation_receipts") != {
            (0, 0, "assisted_route_material", "profile_id", "profile_id", "NO ACTION", "CASCADE", "NONE"),
            (0, 1, "assisted_route_material", "route_id", "route_id", "NO ACTION", "CASCADE", "NONE"),
        }:
            raise AssistedRouteStoreError("integrity_failure")
        metadata = tuple(
            (row["singleton"], row["schema_version"])
            for row in connection.execute(
                "SELECT singleton, schema_version "
                "FROM assisted_route_store_metadata ORDER BY singleton"
            )
        )
        if metadata != ((1, _SCHEMA_VERSION),):
            raise AssistedRouteStoreError("integrity_failure")

    @staticmethod
    def _require_sha256(value: str) -> str:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise AssistedRouteStoreError("integrity_failure")
        return value

    @staticmethod
    def _require_identifier(value: object) -> str:
        if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
            raise AssistedRouteStoreError("integrity_failure")
        return value

    @staticmethod
    def _require_timestamp(value: object) -> str:
        if (
            not isinstance(value, str)
            or _CANONICAL_UTC_TIMESTAMP.fullmatch(value) is None
        ):
            raise AssistedRouteStoreError("integrity_failure")
        try:
            datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            raise AssistedRouteStoreError("integrity_failure") from None
        return value

    @staticmethod
    def _receipt_payload(
        *,
        profile_id: str,
        request_id: str,
        route_id: str,
        payload_sha256: str,
        output_sha256: str,
    ) -> dict[str, object]:
        return {
            "schemaVersion": "wayline.assisted-preparation-receipt.v1",
            "profileId": profile_id,
            "requestId": request_id,
            "routeId": route_id,
            "payloadSha256": payload_sha256,
            "outputSha256": output_sha256,
        }

    def _require_profile(self, profile_id: str) -> None:
        row = self._require_connection().execute(
            "SELECT profile_id FROM local_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise AssistedRouteStoreError("profile_not_found")

    def _event_head(self, profile_id: str) -> tuple[int, str]:
        row = self._require_connection().execute(
            "SELECT ordinal, event_hash FROM event_log WHERE profile_id = ? "
            "ORDER BY ordinal DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if row is None:
            return 0, GENESIS_EVENT_HASH
        ordinal = int(row["ordinal"])
        digest = self._require_sha256(str(row["event_hash"]))
        return ordinal, digest

    def _is_completed(self, profile_id: str, world_id: str) -> bool:
        row = self._require_connection().execute(
            "SELECT 1 FROM event_log WHERE profile_id = ? "
            "AND event_type = 'assisted_route_completion' "
            "AND semantic_key = ? LIMIT 1",
            (profile_id, f"assisted_route_completion:{world_id}"),
        ).fetchone()
        return row is not None

    def _load_row(self, row: sqlite3.Row) -> StoredAssistedRoute:
        route_id = self._require_identifier(row["route_id"])
        profile_id = self._require_identifier(row["profile_id"])
        source_session_id = self._require_identifier(row["source_session_id"])
        world_id = self._require_identifier(row["world_id"])
        event_head_ordinal = row["event_head_ordinal"]
        if type(event_head_ordinal) is not int or event_head_ordinal < 0:
            raise AssistedRouteStoreError("integrity_failure")
        event_head_hash = self._require_sha256(str(row["event_head_hash"]))
        route_plan_sha256 = self._require_sha256(
            str(row["route_plan_sha256"])
        )
        created_at_utc = self._require_timestamp(row["created_at_utc"])
        material_json = row["material_json"]
        material_sha256 = row["material_sha256"]
        if (
            not isinstance(material_json, str)
            or len(material_json.encode("utf-8")) > _MAX_PRIVATE_BYTES
            or not isinstance(material_sha256, str)
            or _SHA256.fullmatch(material_sha256) is None
            or not hmac.compare_digest(
                _sha256_text(material_json),
                material_sha256,
            )
        ):
            raise AssistedRouteStoreError("integrity_failure")
        try:
            material = VerifiedBatchMaterial.from_private_json(
                material_json,
                compiler=self._compiler,
                manifest=self._manifest,
            )
            require_assisted_material(material)
        except Exception:
            raise AssistedRouteStoreError("integrity_failure") from None
        if (
            material.context.profile_id != profile_id
            or material.context.session_id != source_session_id
            or material.context.world_id != world_id
        ):
            raise AssistedRouteStoreError("integrity_failure")
        try:
            batch = public_assisted_batch(route_id, material)
        except Exception:
            raise AssistedRouteStoreError("integrity_failure") from None
        output_sha256 = _sha256_text(
            _canonical_json(batch.model_dump(by_alias=True, mode="json"))
        )
        return StoredAssistedRoute(
            route_id=route_id,
            profile_id=profile_id,
            source_session_id=source_session_id,
            world_id=world_id,
            event_head_ordinal=event_head_ordinal,
            event_head_hash=event_head_hash,
            route_plan_sha256=route_plan_sha256,
            material=material,
            batch=batch,
            material_sha256=material_sha256,
            output_sha256=output_sha256,
            created_at_utc=created_at_utc,
        )

    def load(self, route_id: str, *, profile_id: str) -> StoredAssistedRoute:
        self._require_profile(profile_id)
        row = self._require_connection().execute(
            "SELECT * FROM assisted_route_material "
            "WHERE route_id = ? AND profile_id = ?",
            (route_id, profile_id),
        ).fetchone()
        if row is None:
            raise AssistedRouteStoreError("profile_not_found")
        return self._load_row(row)

    def load_active(
        self,
        profile_id: str,
        *,
        world_id: str | None = None,
    ) -> StoredAssistedRoute | None:
        self._require_profile(profile_id)
        if world_id is None:
            rows = self._require_connection().execute(
                "SELECT * FROM assisted_route_material WHERE profile_id = ? "
                "ORDER BY route_id",
                (profile_id,),
            ).fetchall()
        else:
            rows = self._require_connection().execute(
                "SELECT * FROM assisted_route_material "
                "WHERE profile_id = ? AND world_id = ? ORDER BY route_id",
                (profile_id, world_id),
            ).fetchall()
        active = tuple(
            row
            for row in rows
            if not self._is_completed(profile_id, str(row["world_id"]))
        )
        if len(active) > 1:
            raise AssistedRouteStoreError("integrity_failure")
        return None if not active else self._load_row(active[0])

    def active_route_id(self, profile_id: str) -> str | None:
        active = self.load_active(profile_id)
        return None if active is None else active.route_id

    def _load_receipt(self, row: sqlite3.Row) -> dict[str, Any]:
        profile_id = self._require_identifier(row["profile_id"])
        request_id = self._require_identifier(row["request_id"])
        route_id = self._require_identifier(row["route_id"])
        payload_sha256 = self._require_sha256(str(row["payload_sha256"]))
        output_sha256 = self._require_sha256(str(row["output_sha256"]))
        receipt_json = row["receipt_json"]
        receipt_sha256 = row["receipt_sha256"]
        if (
            not isinstance(receipt_json, str)
            or not isinstance(receipt_sha256, str)
            or _SHA256.fullmatch(receipt_sha256) is None
            or not hmac.compare_digest(_sha256_text(receipt_json), receipt_sha256)
        ):
            raise AssistedRouteStoreError("integrity_failure")
        try:
            payload = json.loads(receipt_json, object_pairs_hook=_strict_object)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise AssistedRouteStoreError("integrity_failure") from None
        expected = self._receipt_payload(
            profile_id=profile_id,
            request_id=request_id,
            route_id=route_id,
            payload_sha256=payload_sha256,
            output_sha256=output_sha256,
        )
        if payload != expected or receipt_json != _canonical_json(expected):
            raise AssistedRouteStoreError("integrity_failure")
        owner = self._require_connection().execute(
            "SELECT 1 FROM assisted_route_material "
            "WHERE profile_id = ? AND route_id = ?",
            (profile_id, route_id),
        ).fetchone()
        if owner is None:
            raise AssistedRouteStoreError("integrity_failure")
        return payload

    def load_preparation(
        self,
        request_id: str,
        *,
        profile_id: str,
        payload_sha256: str | None = None,
    ) -> StoredAssistedRoute | None:
        self._require_profile(profile_id)
        row = self._require_connection().execute(
            "SELECT * FROM assisted_route_preparation_receipts "
            "WHERE profile_id = ? AND request_id = ?",
            (profile_id, request_id),
        ).fetchone()
        if row is None:
            return None
        self._load_receipt(row)
        if (
            payload_sha256 is not None
            and row["payload_sha256"] != payload_sha256
        ):
            raise AssistedRouteStoreError("idempotency_conflict")
        stored = self.load(str(row["route_id"]), profile_id=profile_id)
        if stored.output_sha256 != row["output_sha256"]:
            raise AssistedRouteStoreError("integrity_failure")
        return stored

    def _insert_receipt(
        self,
        *,
        profile_id: str,
        request_id: str,
        route_id: str,
        payload_sha256: str,
        output_sha256: str,
    ) -> None:
        payload = self._receipt_payload(
            profile_id=profile_id,
            request_id=request_id,
            route_id=route_id,
            payload_sha256=payload_sha256,
            output_sha256=output_sha256,
        )
        receipt_json = _canonical_json(payload)
        self._require_connection().execute(
            "INSERT INTO assisted_route_preparation_receipts ("
            "profile_id, request_id, route_id, payload_sha256, output_sha256, "
            "receipt_json, receipt_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                profile_id,
                request_id,
                route_id,
                payload_sha256,
                output_sha256,
                receipt_json,
                _sha256_text(receipt_json),
            ),
        )

    def _normal_quiz_is_active(self, profile_id: str) -> bool:
        table = self._require_connection().execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'quiz_machines'"
        ).fetchone()
        if table is None:
            return False
        row = self._require_connection().execute(
            "SELECT 1 FROM quiz_machines WHERE profile_id = ? "
            "AND state <> 'closed' LIMIT 1",
            (profile_id,),
        ).fetchone()
        return row is not None

    def create_prepared(
        self,
        *,
        route_id: str,
        profile_id: str,
        source_session_id: str,
        world_id: str,
        preparation_request_id: str,
        preparation_payload_sha256: str,
        event_head_ordinal: int,
        event_head_hash: str,
        route_plan_sha256: str,
        material: VerifiedBatchMaterial,
    ) -> StoredAssistedRoute:
        self._require_identifier(route_id)
        self._require_identifier(profile_id)
        self._require_identifier(source_session_id)
        self._require_identifier(world_id)
        self._require_identifier(preparation_request_id)
        self._require_sha256(preparation_payload_sha256)
        self._require_sha256(event_head_hash)
        self._require_sha256(route_plan_sha256)
        require_assisted_material(material)
        if (
            material.context.profile_id != profile_id
            or material.context.session_id != source_session_id
            or material.context.world_id != world_id
        ):
            raise AssistedRouteStoreError("integrity_failure")
        material_json = material.to_private_json()
        if len(material_json.encode("utf-8")) > _MAX_PRIVATE_BYTES:
            raise AssistedRouteStoreError("integrity_failure")
        material_sha256 = _sha256_text(material_json)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_profile(profile_id)
            receipt = connection.execute(
                "SELECT * FROM assisted_route_preparation_receipts "
                "WHERE profile_id = ? AND request_id = ?",
                (profile_id, preparation_request_id),
            ).fetchone()
            if receipt is not None:
                self._load_receipt(receipt)
                if receipt["payload_sha256"] != preparation_payload_sha256:
                    raise AssistedRouteStoreError("idempotency_conflict")
                stored = self.load(
                    str(receipt["route_id"]),
                    profile_id=profile_id,
                )
                if stored.output_sha256 != receipt["output_sha256"]:
                    raise AssistedRouteStoreError("integrity_failure")
                connection.commit()
                return stored

            active = self.load_active(profile_id)
            if active is not None:
                if active.world_id != world_id:
                    raise AssistedRouteStoreError("activity_in_progress")
                if active.route_plan_sha256 != route_plan_sha256:
                    raise AssistedRouteStoreError("stale_event_head")
                self._insert_receipt(
                    profile_id=profile_id,
                    request_id=preparation_request_id,
                    route_id=active.route_id,
                    payload_sha256=preparation_payload_sha256,
                    output_sha256=active.output_sha256,
                )
                connection.commit()
                return active

            if self._normal_quiz_is_active(profile_id):
                raise AssistedRouteStoreError("activity_in_progress")
            if self._event_head(profile_id) != (
                event_head_ordinal,
                event_head_hash,
            ):
                raise AssistedRouteStoreError("stale_event_head")

            created_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            batch = public_assisted_batch(route_id, material)
            output_sha256 = _sha256_text(
                _canonical_json(batch.model_dump(by_alias=True, mode="json"))
            )
            connection.execute(
                "INSERT INTO assisted_route_material ("
                "route_id, profile_id, source_session_id, world_id, "
                "event_head_ordinal, event_head_hash, route_plan_sha256, material_json, "
                "material_sha256, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    route_id,
                    profile_id,
                    source_session_id,
                    world_id,
                    event_head_ordinal,
                    event_head_hash,
                    route_plan_sha256,
                    material_json,
                    material_sha256,
                    created_at,
                ),
            )
            self._insert_receipt(
                profile_id=profile_id,
                request_id=preparation_request_id,
                route_id=route_id,
                payload_sha256=preparation_payload_sha256,
                output_sha256=output_sha256,
            )
            stored = self.load(route_id, profile_id=profile_id)
            connection.commit()
            return stored
        except AssistedRouteStoreError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.OperationalError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise AssistedRouteStoreError("storage_busy") from exc
        except (sqlite3.IntegrityError, TypeError, ValueError):
            if connection.in_transaction:
                connection.rollback()
            raise AssistedRouteStoreError("integrity_failure") from None


__all__ = [
    "AssistedRouteStore",
    "AssistedRouteStoreError",
    "StoredAssistedRoute",
]
