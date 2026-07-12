"""Tamper-evident reviewed-question cache for build and learner runtimes.

Only canonical :class:`VerifiedQuestionBundle` data is persisted.  Raw model
output and learner data have no field in this schema.  Build tools must opt in
to writable mode; learner processes open the same database through SQLite's
read-only URI mode and revalidate every compatible row before selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any
import unicodedata

from .curriculum import CURRICULUM_V1_SHA256, Curriculum
from .procedure_registry import PROCEDURE_REGISTRY_V1_SHA256, ProcedureRegistry
from .providers.distractor import PinnedSlmManifest
from .question_kernel import QuestionBlueprint, QuestionCompiler
from .verified_question import (
    VerifiedQuestionBundle,
    VerifiedQuestionError,
)


CACHE_SCHEMA_VERSION = "wayline.reviewed-cache.v2"
CACHE_ROW_SCHEMA_VERSION = "wayline.reviewed-cache-row.v2"
CACHE_USER_VERSION = 2

_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_MAX_ROW_BYTES = 1024 * 1024
_MAX_KEY_VALUES = 256
_LOOKUP_INDEX_NAME = "reviewed_questions_lookup_v2"
_LOOKUP_INDEX_COLUMNS = (
    "world_id",
    "skill_id",
    "family_id",
    "difficulty",
    "registry_id",
    "curriculum_id",
)


class ReviewedCacheError(RuntimeError):
    """Base error with a stable, non-sensitive code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CacheSchemaError(ReviewedCacheError):
    """The SQLite file is not the exact supported cache schema."""


class CacheCorruptionError(ReviewedCacheError):
    """A cache receipt, row, or reconstructed bundle failed validation."""


class CacheModeError(ReviewedCacheError):
    """An operation is forbidden in the cache's explicit runtime mode."""


class CacheWriteError(ReviewedCacheError):
    """A transactional build-mode insert failed."""


def _safe_text(value: object, *, maximum: int = 256) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= maximum
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _validate_text_tuple(name: str, value: object) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be a tuple")
    if len(value) > _MAX_KEY_VALUES:
        raise ValueError(f"{name} is too large")
    if any(not _safe_text(item, maximum=256) for item in value):
        raise ValueError(f"{name} contains an invalid identifier")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} contains duplicates")


def _validate_sha256_tuple(name: str, value: object) -> None:
    _validate_text_tuple(name, value)
    if any(not _SHA256.fullmatch(item) for item in value):
        raise ValueError(f"{name} contains an invalid SHA-256")


def question_semantic_sha256(blueprint: QuestionBlueprint) -> str:
    """Hash learner-visible mathematical meaning without source provenance."""

    if not isinstance(blueprint, QuestionBlueprint):
        raise TypeError("blueprint must be a QuestionBlueprint")
    encoded = json.dumps(
        {
            "canonicalAnswer": {
                "denominator": blueprint.canonical_answer.value.denominator,
                "display": blueprint.canonical_answer.display,
                "numerator": blueprint.canonical_answer.value.numerator,
            },
            "familyId": blueprint.family_id,
            "operandNames": list(blueprint.operand_names),
            "operands": list(blueprint.operands),
            "prompt": blueprint.prompt,
            "schemaVersion": "wayline.question-semantic.v1",
            "trustedSteps": list(blueprint.trusted_steps),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_exact_utc(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]{1,9})?Z",
            value,
            re.ASCII,
        )
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Complete deterministic compatibility request for one reviewed item."""

    world_id: str
    skill_id: str
    family_id: str
    difficulty: int
    required_procedure_ids: tuple[str, ...]
    registry_id: str
    curriculum_id: str
    selection_seed: int
    excluded_question_ids: tuple[str, ...] = ()
    excluded_template_ids: tuple[str, ...] = ()
    excluded_operand_signatures: tuple[str, ...] = ()
    excluded_content_ids: tuple[str, ...] = ()
    excluded_question_semantic_sha256s: tuple[str, ...] = ()
    excluded_context_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "world_id",
            "skill_id",
            "family_id",
            "registry_id",
            "curriculum_id",
        ):
            if not _safe_text(getattr(self, name), maximum=128):
                raise ValueError(f"{name} is invalid")
        if (
            not isinstance(self.difficulty, int)
            or isinstance(self.difficulty, bool)
            or self.difficulty not in (1, 2, 3)
        ):
            raise ValueError("difficulty must be 1, 2, or 3")
        if (
            not isinstance(self.selection_seed, int)
            or isinstance(self.selection_seed, bool)
            or not 0 <= self.selection_seed < 2**63
        ):
            raise ValueError("selection_seed must be a nonnegative signed 64-bit integer")
        for name in (
            "required_procedure_ids",
            "excluded_question_ids",
            "excluded_template_ids",
            "excluded_operand_signatures",
            "excluded_content_ids",
            "excluded_context_ids",
        ):
            _validate_text_tuple(name, getattr(self, name))
        _validate_sha256_tuple(
            "excluded_question_semantic_sha256s",
            self.excluded_question_semantic_sha256s,
        )

    @property
    def difficulty_band(self) -> int:
        """Compatibility name used by the authored runtime plan."""

        return self.difficulty


@dataclass(frozen=True, slots=True)
class ReviewReceipt:
    """Owner approval retained with a reviewed cache row."""

    owner_alias: str
    decision: str
    reviewed_at_utc: str
    approved_semantic_content_sha256: str
    approval_record_sha256: str
    decision_receipt_sha256: str

    def __post_init__(self) -> None:
        if not _safe_text(self.owner_alias, maximum=128):
            raise ValueError("owner_alias is invalid")
        if self.decision != "approved":
            raise ValueError("only approved review decisions can enter the cache")
        if not _is_exact_utc(self.reviewed_at_utc):
            raise ValueError("reviewed_at_utc must be a canonical UTC timestamp")
        if (
            not isinstance(self.approved_semantic_content_sha256, str)
            or not _SHA256.fullmatch(self.approved_semantic_content_sha256)
        ):
            raise ValueError("approved semantic content hash is invalid")
        if (
            not isinstance(self.approval_record_sha256, str)
            or not _SHA256.fullmatch(self.approval_record_sha256)
        ):
            raise ValueError("approval record hash is invalid")
        if (
            not isinstance(self.decision_receipt_sha256, str)
            or not _SHA256.fullmatch(self.decision_receipt_sha256)
        ):
            raise ValueError("review decision receipt hash is invalid")
        expected = _review_decision_receipt_sha256(
            owner_alias=self.owner_alias,
            decision=self.decision,
            reviewed_at_utc=self.reviewed_at_utc,
            approved_semantic_content_sha256=(
                self.approved_semantic_content_sha256
            ),
            approval_record_sha256=self.approval_record_sha256,
        )
        if self.decision_receipt_sha256 != expected:
            raise ValueError("review decision receipt hash is invalid")

    @classmethod
    def approved(
        cls,
        *,
        owner_alias: str,
        reviewed_at_utc: str,
        approved_semantic_content_sha256: str,
        approval_record_sha256: str,
    ) -> "ReviewReceipt":
        """Create a canonical approval bound to one external review record."""

        decision = "approved"
        return cls(
            owner_alias=owner_alias,
            decision=decision,
            reviewed_at_utc=reviewed_at_utc,
            approved_semantic_content_sha256=(
                approved_semantic_content_sha256
            ),
            approval_record_sha256=approval_record_sha256,
            decision_receipt_sha256=_review_decision_receipt_sha256(
                owner_alias=owner_alias,
                decision=decision,
                reviewed_at_utc=reviewed_at_utc,
                approved_semantic_content_sha256=(
                    approved_semantic_content_sha256
                ),
                approval_record_sha256=approval_record_sha256,
            ),
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_decision_receipt_sha256(
    *,
    owner_alias: str,
    decision: str,
    reviewed_at_utc: str,
    approved_semantic_content_sha256: str,
    approval_record_sha256: str,
) -> str:
    return _sha256_text(_canonical_json({
        "approvalRecordSha256": approval_record_sha256,
        "approvedSemanticContentSha256": approved_semantic_content_sha256,
        "decision": decision,
        "ownerAlias": owner_alias,
        "reviewedAtUtc": reviewed_at_utc,
        "schemaVersion": "wayline.review-decision-receipt.v1",
    }))


def _reviewed_cache_hit_receipt_sha256(
    *,
    bundle: VerifiedQuestionBundle,
    cache_row_sha256: str,
    cache_content_sha256: str,
    approved_semantic_content_sha256: str,
    review_decision_receipt_sha256: str,
    approval_record_sha256: str,
    reviewer_alias: str,
    reviewed_at_utc: str,
) -> str:
    return _sha256_text(_canonical_json({
        "approvalRecordSha256": approval_record_sha256,
        "approvedSemanticContentSha256": approved_semantic_content_sha256,
        "bundleSourceSha256": bundle.source_bundle_sha256,
        "cacheContentSha256": cache_content_sha256,
        "cacheRowSha256": cache_row_sha256,
        "reviewDecisionReceiptSha256": review_decision_receipt_sha256,
        "reviewedAtUtc": reviewed_at_utc,
        "reviewerAlias": reviewer_alias,
        "schemaVersion": "wayline.reviewed-cache-hit.v1",
    }))


@dataclass(frozen=True, slots=True)
class ReviewedCacheHit:
    """One fully revalidated reviewed row and its immutable approval proof."""

    bundle: VerifiedQuestionBundle
    cache_row_sha256: str
    cache_content_sha256: str
    approved_semantic_content_sha256: str
    review_decision_receipt_sha256: str
    approval_record_sha256: str
    reviewer_alias: str
    reviewed_at_utc: str
    hit_receipt_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, VerifiedQuestionBundle):
            raise TypeError("bundle must be a VerifiedQuestionBundle")
        for name in (
            "cache_row_sha256",
            "cache_content_sha256",
            "approved_semantic_content_sha256",
            "review_decision_receipt_sha256",
            "approval_record_sha256",
            "hit_receipt_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{name} is not a canonical SHA-256")
        review = ReviewReceipt(
            owner_alias=self.reviewer_alias,
            decision="approved",
            reviewed_at_utc=self.reviewed_at_utc,
            approved_semantic_content_sha256=(
                self.approved_semantic_content_sha256
            ),
            approval_record_sha256=self.approval_record_sha256,
            decision_receipt_sha256=self.review_decision_receipt_sha256,
        )
        if self.cache_content_sha256 != self.bundle.cache_content_sha256:
            raise ValueError("cache content does not match reviewed bundle")
        if (
            review.approved_semantic_content_sha256
            != self.bundle.semantic_content_sha256
        ):
            raise ValueError("approval does not match reviewed bundle")
        expected = _reviewed_cache_hit_receipt_sha256(
            bundle=self.bundle,
            cache_row_sha256=self.cache_row_sha256,
            cache_content_sha256=self.cache_content_sha256,
            approved_semantic_content_sha256=(
                self.approved_semantic_content_sha256
            ),
            review_decision_receipt_sha256=(
                self.review_decision_receipt_sha256
            ),
            approval_record_sha256=self.approval_record_sha256,
            reviewer_alias=self.reviewer_alias,
            reviewed_at_utc=self.reviewed_at_utc,
        )
        if self.hit_receipt_sha256 != expected:
            raise ValueError("reviewed cache hit receipt is invalid")

    @classmethod
    def _from_validated_row(
        cls,
        *,
        bundle: VerifiedQuestionBundle,
        cache_row_sha256: str,
        review: ReviewReceipt,
    ) -> "ReviewedCacheHit":
        values = {
            "bundle": bundle,
            "cache_row_sha256": cache_row_sha256,
            "cache_content_sha256": bundle.cache_content_sha256,
            "approved_semantic_content_sha256": (
                review.approved_semantic_content_sha256
            ),
            "review_decision_receipt_sha256": (
                review.decision_receipt_sha256
            ),
            "approval_record_sha256": review.approval_record_sha256,
            "reviewer_alias": review.owner_alias,
            "reviewed_at_utc": review.reviewed_at_utc,
        }
        return cls(
            **values,
            hit_receipt_sha256=_reviewed_cache_hit_receipt_sha256(**values),
        )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("nonstandard JSON number")


def _expect_fields(value: object, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("JSON fields do not match the cache contract")
    return value


def _manifest_receipt_sha256(manifest: PinnedSlmManifest) -> str:
    payload = {
        "adapterIdentityReceiptSha256": manifest.adapter_identity_receipt_sha256,
        "generatorIdentityReceiptSha256": (
            manifest.generator_identity_receipt_sha256
        ),
        "ggufSha256": manifest.gguf_sha256,
        "maxResponseBytes": manifest.max_response_bytes,
        "maxTokens": manifest.max_tokens,
        "modelId": manifest.model_id,
        "modelSha256": manifest.model_sha256,
        "promptTemplateSha256": manifest.prompt_template_sha256,
        "registryId": manifest.registry_id,
        "schemaVersion": "wayline.pinned-slm-manifest-receipt.v1",
    }
    return _sha256_text(_canonical_json(payload))


def _procedure_ids(bundle: VerifiedQuestionBundle) -> tuple[str, ...]:
    return tuple(sorted(item.procedure_id for item in bundle.verified_distractors))


def _derived_payload(bundle: VerifiedQuestionBundle) -> dict[str, Any]:
    return {
        "blueprintContentSha256": bundle.blueprint.content_sha256,
        "cacheContentSha256": bundle.cache_content_sha256,
        "difficulty": bundle.blueprint.difficulty,
        "familyId": bundle.blueprint.family_id,
        "operandSignature": bundle.operand_signature,
        "procedureIds": list(_procedure_ids(bundle)),
        "questionId": bundle.blueprint.question_id,
        "semanticContentSha256": bundle.semantic_content_sha256,
        "skillId": bundle.blueprint.skill_id,
        "templateId": bundle.template_id,
        "worldId": bundle.blueprint.world_id,
    }


_METADATA_COLUMNS = (
    ("singleton", "INTEGER", 1, 1),
    ("schema_version", "TEXT", 1, 0),
    ("manifest_receipt_sha256", "TEXT", 1, 0),
    ("registry_id", "TEXT", 1, 0),
    ("registry_sha256", "TEXT", 1, 0),
    ("curriculum_id", "TEXT", 1, 0),
    ("curriculum_sha256", "TEXT", 1, 0),
)

_QUESTION_COLUMNS = (
    ("cache_content_sha256", "TEXT", 1, 1),
    ("semantic_content_sha256", "TEXT", 1, 0),
    ("world_id", "TEXT", 1, 0),
    ("skill_id", "TEXT", 1, 0),
    ("family_id", "TEXT", 1, 0),
    ("difficulty", "INTEGER", 1, 0),
    ("question_id", "TEXT", 1, 0),
    ("template_id", "TEXT", 1, 0),
    ("operand_signature", "TEXT", 1, 0),
    ("content_id", "TEXT", 1, 0),
    ("procedure_ids_json", "TEXT", 1, 0),
    ("registry_id", "TEXT", 1, 0),
    ("curriculum_id", "TEXT", 1, 0),
    ("row_json", "TEXT", 1, 0),
    ("row_hash", "TEXT", 1, 0),
)


class ReviewedCache:
    """SQLite cache with explicit build-write and learner-read-only modes."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
        writable: bool,
    ):
        self._connection = connection
        self._compiler = compiler
        self._manifest = manifest
        self._writable = writable
        self._closed = False
        self._manifest_receipt_sha256 = _manifest_receipt_sha256(manifest)
        self._registry_id = compiler.registry.registry_id
        self._curriculum_id = compiler.curriculum.curriculum_id

    @property
    def writable(self) -> bool:
        return self._writable

    @classmethod
    def open_build(
        cls,
        path: str | Path,
        *,
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
    ) -> "ReviewedCache":
        """Open or create a cache with transactional insert authority."""

        cls._validate_runtime(compiler, manifest)
        try:
            connection = sqlite3.connect(
                str(path),
                isolation_level=None,
                timeout=5.0,
            )
            connection.row_factory = sqlite3.Row
            cls._configure(connection, writable=True)
            instance = cls(
                connection,
                compiler=compiler,
                manifest=manifest,
                writable=True,
            )
            instance._initialize_or_validate()
            return instance
        except ReviewedCacheError:
            if "connection" in locals():
                connection.close()
            raise
        except sqlite3.Error:
            if "connection" in locals():
                connection.close()
            raise CacheSchemaError("cache_open_failed") from None

    @classmethod
    def open_learner(
        cls,
        path: str | Path,
        *,
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
    ) -> "ReviewedCache":
        """Open an existing cache through SQLite's filesystem read-only mode."""

        cls._validate_runtime(compiler, manifest)
        resolved = Path(path).expanduser().resolve()
        uri = resolved.as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                timeout=5.0,
            )
            connection.row_factory = sqlite3.Row
            cls._configure(connection, writable=False)
            instance = cls(
                connection,
                compiler=compiler,
                manifest=manifest,
                writable=False,
            )
            instance._validate_existing()
            return instance
        except ReviewedCacheError:
            if "connection" in locals():
                connection.close()
            raise
        except sqlite3.Error:
            if "connection" in locals():
                connection.close()
            raise CacheSchemaError("cache_open_failed") from None

    @classmethod
    def open_learner_fd(
        cls,
        descriptor: int,
        *,
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
    ) -> "ReviewedCache":
        """Open the exact regular-file inode held by a caller-owned descriptor."""

        cls._validate_runtime(compiler, manifest)
        if (
            not isinstance(descriptor, int)
            or isinstance(descriptor, bool)
            or descriptor < 0
        ):
            raise TypeError("descriptor must be an open file descriptor")
        try:
            details = os.fstat(descriptor)
        except OSError:
            raise CacheSchemaError("cache_open_failed") from None
        if not stat.S_ISREG(details.st_mode):
            raise CacheSchemaError("cache_open_failed")
        uri = f"file:/dev/fd/{descriptor}?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                timeout=5.0,
            )
            connection.row_factory = sqlite3.Row
            cls._configure(connection, writable=False)
            instance = cls(
                connection,
                compiler=compiler,
                manifest=manifest,
                writable=False,
            )
            instance._validate_existing()
            return instance
        except ReviewedCacheError:
            if "connection" in locals():
                connection.close()
            raise
        except sqlite3.Error:
            if "connection" in locals():
                connection.close()
            raise CacheSchemaError("cache_open_failed") from None

    @staticmethod
    def _validate_runtime(
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
    ) -> None:
        if not isinstance(compiler, QuestionCompiler) or not isinstance(
            manifest, PinnedSlmManifest
        ):
            raise TypeError("compiler and manifest must be validated runtime objects")
        if manifest.registry_id != compiler.registry.registry_id:
            raise ValueError("manifest and compiler registry do not match")
        try:
            packaged_curriculum = Curriculum.packaged_v1()
            packaged_registry = ProcedureRegistry.for_tests()
        except Exception:
            raise CacheCorruptionError("runtime_receipt_mismatch") from None
        if (
            compiler.curriculum != packaged_curriculum
            or compiler.registry.registry_id != packaged_registry.registry_id
            or compiler.registry.entries != packaged_registry.entries
        ):
            raise CacheCorruptionError("runtime_receipt_mismatch")

    @staticmethod
    def _configure(connection: sqlite3.Connection, *, writable: bool) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not writable:
            connection.execute("PRAGMA query_only = ON")

    def _initialize_or_validate(self) -> None:
        user_version = int(self._connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0])
        tables = self._user_tables()
        if user_version == 0 and not tables:
            self._create_schema()
        else:
            self._validate_existing()

    def _create_schema(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute("""
                CREATE TABLE cache_metadata (
                    singleton INTEGER PRIMARY KEY NOT NULL CHECK (singleton = 1),
                    schema_version TEXT NOT NULL,
                    manifest_receipt_sha256 TEXT NOT NULL,
                    registry_id TEXT NOT NULL,
                    registry_sha256 TEXT NOT NULL,
                    curriculum_id TEXT NOT NULL,
                    curriculum_sha256 TEXT NOT NULL
                )
            """)
            self._connection.execute("""
                CREATE TABLE reviewed_questions (
                    cache_content_sha256 TEXT PRIMARY KEY NOT NULL,
                    semantic_content_sha256 TEXT NOT NULL UNIQUE,
                    world_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    family_id TEXT NOT NULL,
                    difficulty INTEGER NOT NULL,
                    question_id TEXT NOT NULL,
                    template_id TEXT NOT NULL,
                    operand_signature TEXT NOT NULL,
                    content_id TEXT NOT NULL,
                    procedure_ids_json TEXT NOT NULL,
                    registry_id TEXT NOT NULL,
                    curriculum_id TEXT NOT NULL,
                    row_json TEXT NOT NULL,
                    row_hash TEXT NOT NULL UNIQUE
                )
            """)
            self._connection.execute("""
                CREATE INDEX reviewed_questions_lookup_v2
                ON reviewed_questions (
                    world_id, skill_id, family_id, difficulty,
                    registry_id, curriculum_id
                )
            """)
            self._connection.execute(
                """
                INSERT INTO cache_metadata (
                    singleton, schema_version, manifest_receipt_sha256,
                    registry_id, registry_sha256, curriculum_id,
                    curriculum_sha256
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    CACHE_SCHEMA_VERSION,
                    self._manifest_receipt_sha256,
                    self._registry_id,
                    PROCEDURE_REGISTRY_V1_SHA256,
                    self._curriculum_id,
                    CURRICULUM_V1_SHA256,
                ),
            )
            self._connection.execute(f"PRAGMA user_version = {CACHE_USER_VERSION}")
            self._connection.execute("COMMIT")
        except sqlite3.Error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise CacheSchemaError("cache_schema_create_failed") from None
        self._validate_existing()

    def _validate_existing(self) -> None:
        try:
            quick_check = self._connection.execute("PRAGMA quick_check").fetchone()[0]
            user_version = int(self._connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0])
        except sqlite3.Error:
            raise CacheSchemaError("cache_schema_unreadable") from None
        if quick_check != "ok":
            raise CacheCorruptionError("sqlite_integrity_failed")
        if user_version != CACHE_USER_VERSION:
            raise CacheSchemaError("unsupported_cache_schema")
        if self._user_tables() != {"cache_metadata", "reviewed_questions"}:
            raise CacheSchemaError("unsupported_cache_schema")
        self._validate_columns("cache_metadata", _METADATA_COLUMNS)
        self._validate_columns("reviewed_questions", _QUESTION_COLUMNS)
        self._validate_indexes()
        self._validate_metadata()

    def _user_tables(self) -> set[str]:
        try:
            rows = self._connection.execute(
                """
                SELECT name FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
            disallowed = self._connection.execute(
                """
                SELECT COUNT(*) FROM sqlite_schema
                WHERE type IN ('trigger', 'view')
                  AND name NOT LIKE 'sqlite_%'
                """
            ).fetchone()[0]
        except sqlite3.Error:
            raise CacheSchemaError("cache_schema_unreadable") from None
        if disallowed:
            raise CacheSchemaError("unsupported_cache_schema")
        return {str(row[0]) for row in rows}

    def _validate_columns(
        self,
        table: str,
        expected: tuple[tuple[str, str, int, int], ...],
    ) -> None:
        try:
            rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.Error:
            raise CacheSchemaError("cache_schema_unreadable") from None
        actual = tuple(
            (str(row[1]), str(row[2]).upper(), int(row[3]), int(row[5]))
            for row in rows
        )
        if actual != expected:
            raise CacheSchemaError("unsupported_cache_schema")

    def _validate_metadata(self) -> None:
        try:
            rows = self._connection.execute(
                """
                SELECT singleton, schema_version, manifest_receipt_sha256,
                       registry_id, registry_sha256, curriculum_id,
                       curriculum_sha256
                FROM cache_metadata
                """
            ).fetchall()
        except sqlite3.Error:
            raise CacheSchemaError("cache_schema_unreadable") from None
        expected = (
            1,
            CACHE_SCHEMA_VERSION,
            self._manifest_receipt_sha256,
            self._registry_id,
            PROCEDURE_REGISTRY_V1_SHA256,
            self._curriculum_id,
            CURRICULUM_V1_SHA256,
        )
        if len(rows) != 1 or tuple(rows[0]) != expected:
            raise CacheCorruptionError("cache_receipt_mismatch")

    def _validate_indexes(self) -> None:
        try:
            metadata_indexes = self._connection.execute(
                "PRAGMA index_list(cache_metadata)"
            ).fetchall()
            rows = self._connection.execute(
                "PRAGMA index_list(reviewed_questions)"
            ).fetchall()
            actual: set[tuple[tuple[str, ...], int, str, int, str | None]] = set()
            for row in rows:
                name = str(row[1])
                columns = tuple(
                    str(item[0])
                    for item in self._connection.execute(
                        "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                        (name,),
                    ).fetchall()
                )
                origin = str(row[3])
                actual.add((
                    columns,
                    int(row[2]),
                    origin,
                    int(row[4]),
                    name if origin == "c" else None,
                ))
        except sqlite3.Error:
            raise CacheSchemaError("cache_schema_unreadable") from None
        expected = {
            (("cache_content_sha256",), 1, "pk", 0, None),
            (("semantic_content_sha256",), 1, "u", 0, None),
            (("row_hash",), 1, "u", 0, None),
            (_LOOKUP_INDEX_COLUMNS, 0, "c", 0, _LOOKUP_INDEX_NAME),
        }
        if metadata_indexes or actual != expected:
            raise CacheSchemaError("unsupported_cache_schema")

    def insert(
        self,
        bundle: VerifiedQuestionBundle,
        review: ReviewReceipt,
    ) -> None:
        """Revalidate and atomically insert one owner-approved bundle."""

        self._ensure_open()
        if not self._writable:
            raise CacheModeError("learner_cache_is_read_only")
        if not isinstance(bundle, VerifiedQuestionBundle) or not isinstance(
            review, ReviewReceipt
        ):
            raise TypeError("bundle and review must use cache contract types")
        try:
            private_json = bundle.to_private_json()
            trusted = VerifiedQuestionBundle.from_private_json(
                private_json,
                compiler=self._compiler,
                manifest=self._manifest,
            )
        except (VerifiedQuestionError, TypeError, ValueError):
            raise CacheWriteError("invalid_verified_bundle") from None
        if (
            review.approved_semantic_content_sha256
            != trusted.semantic_content_sha256
        ):
            raise CacheWriteError("review_semantic_content_mismatch")

        receipts = {
            "curriculumId": self._curriculum_id,
            "curriculumSha256": CURRICULUM_V1_SHA256,
            "manifestReceiptSha256": self._manifest_receipt_sha256,
            "registryId": self._registry_id,
            "registrySha256": PROCEDURE_REGISTRY_V1_SHA256,
        }
        row_payload = {
            "bundle": json.loads(private_json),
            "derived": _derived_payload(trusted),
            "receipts": receipts,
            "review": {
                "approvalRecordSha256": review.approval_record_sha256,
                "approvedSemanticContentSha256": (
                    review.approved_semantic_content_sha256
                ),
                "decision": review.decision,
                "decisionReceiptSha256": review.decision_receipt_sha256,
                "ownerAlias": review.owner_alias,
                "reviewedAtUtc": review.reviewed_at_utc,
            },
            "schemaVersion": CACHE_ROW_SCHEMA_VERSION,
        }
        row_json = _canonical_json(row_payload)
        row_hash = _sha256_text(row_json)
        derived = row_payload["derived"]
        procedure_ids_json = _canonical_json(derived["procedureIds"])
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                """
                SELECT cache_content_sha256, semantic_content_sha256
                FROM reviewed_questions
                WHERE cache_content_sha256 = ? OR semantic_content_sha256 = ?
                LIMIT 1
                """,
                (
                    trusted.cache_content_sha256,
                    trusted.semantic_content_sha256,
                ),
            ).fetchone()
            if existing is not None:
                self._connection.execute("ROLLBACK")
                code = (
                    "duplicate_cache_content"
                    if existing["cache_content_sha256"]
                    == trusted.cache_content_sha256
                    else "duplicate_semantic_content"
                )
                raise CacheWriteError(code)
            self._connection.execute(
                """
                INSERT INTO reviewed_questions (
                    cache_content_sha256, semantic_content_sha256,
                    world_id, skill_id, family_id,
                    difficulty, question_id, template_id, operand_signature,
                    content_id, procedure_ids_json, registry_id, curriculum_id,
                    row_json, row_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trusted.cache_content_sha256,
                    trusted.semantic_content_sha256,
                    trusted.blueprint.world_id,
                    trusted.blueprint.skill_id,
                    trusted.blueprint.family_id,
                    trusted.blueprint.difficulty,
                    trusted.blueprint.question_id,
                    trusted.template_id,
                    trusted.operand_signature,
                    trusted.blueprint.content_sha256,
                    procedure_ids_json,
                    self._registry_id,
                    self._curriculum_id,
                    row_json,
                    row_hash,
                ),
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            code = (
                "duplicate_semantic_content"
                if "semantic_content_sha256" in str(exc)
                else "duplicate_cache_content"
            )
            raise CacheWriteError(code) from None
        except sqlite3.Error:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise CacheWriteError("cache_insert_failed") from None

    def lookup_reviewed(self, key: CacheKey) -> ReviewedCacheHit | None:
        """Return a compatible row with its revalidated approval provenance."""

        self._ensure_open()
        if not isinstance(key, CacheKey):
            raise TypeError("key must be a CacheKey")
        try:
            rows = self._connection.execute(
                """
                SELECT cache_content_sha256, semantic_content_sha256,
                       world_id, skill_id, family_id, difficulty,
                       question_id, template_id,
                       operand_signature, content_id, procedure_ids_json,
                       registry_id, curriculum_id, row_json, row_hash
                FROM reviewed_questions
                WHERE world_id = ? AND skill_id = ? AND family_id = ?
                  AND difficulty = ? AND registry_id = ? AND curriculum_id = ?
                """,
                (
                    key.world_id,
                    key.skill_id,
                    key.family_id,
                    key.difficulty,
                    key.registry_id,
                    key.curriculum_id,
                ),
            ).fetchall()
        except sqlite3.Error:
            raise CacheCorruptionError("cache_read_failed") from None

        candidates: list[ReviewedCacheHit] = []
        required = set(key.required_procedure_ids)
        excluded_questions = set(key.excluded_question_ids)
        excluded_templates = set(key.excluded_template_ids)
        excluded_operands = set(key.excluded_operand_signatures)
        excluded_content = set(key.excluded_content_ids)
        excluded_semantics = set(key.excluded_question_semantic_sha256s)
        excluded_contexts = set(key.excluded_context_ids)
        for row in rows:
            hit = self._validate_row(row)
            bundle = hit.bundle
            if (
                bundle.blueprint.world_id != key.world_id
                or bundle.blueprint.skill_id != key.skill_id
                or bundle.blueprint.family_id != key.family_id
                or bundle.blueprint.difficulty != key.difficulty
                or self._registry_id != key.registry_id
                or self._curriculum_id != key.curriculum_id
            ):
                continue
            if not required.issubset(_procedure_ids(bundle)):
                continue
            if bundle.blueprint.question_id in excluded_questions:
                continue
            if bundle.template_id in excluded_templates:
                continue
            if bundle.operand_signature in excluded_operands:
                continue
            if bundle.context_id in excluded_contexts:
                continue
            if question_semantic_sha256(bundle.blueprint) in excluded_semantics:
                continue
            if (
                bundle.blueprint.content_sha256 in excluded_content
                or bundle.cache_content_sha256 in excluded_content
                or bundle.semantic_content_sha256 in excluded_content
            ):
                continue
            candidates.append(hit)

        if not candidates:
            return None
        return min(
            candidates,
            key=lambda hit: hashlib.sha256(
                (
                    "wayline.reviewed-cache-selection.v2|"
                    + str(key.selection_seed)
                    + "|"
                    + hit.bundle.semantic_content_sha256
                ).encode("ascii")
            ).digest(),
        )

    def get(self, key: CacheKey) -> VerifiedQuestionBundle | None:
        """Compatibility-only bundle lookup; orchestrators use lookup_reviewed."""

        hit = self.lookup_reviewed(key)
        return None if hit is None else hit.bundle

    def _validate_row(self, row: sqlite3.Row) -> ReviewedCacheHit:
        try:
            row_json = row["row_json"]
            row_hash = row["row_hash"]
            if not isinstance(row_json, str) or len(row_json.encode("utf-8")) > _MAX_ROW_BYTES:
                raise ValueError("row JSON is invalid")
            if not isinstance(row_hash, str) or not _SHA256.fullmatch(row_hash):
                raise ValueError("row hash is invalid")
            if _sha256_text(row_json) != row_hash:
                raise ValueError("row hash does not match")
            decoded = json.loads(
                row_json,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
            if _canonical_json(decoded) != row_json:
                raise ValueError("row JSON is not canonical")
            top = _expect_fields(
                decoded,
                {"bundle", "derived", "receipts", "review", "schemaVersion"},
            )
            if top["schemaVersion"] != CACHE_ROW_SCHEMA_VERSION:
                raise ValueError("row schema is unsupported")
            derived = _expect_fields(
                top["derived"],
                {
                    "blueprintContentSha256",
                    "cacheContentSha256",
                    "difficulty",
                    "familyId",
                    "operandSignature",
                    "procedureIds",
                    "questionId",
                    "semanticContentSha256",
                    "skillId",
                    "templateId",
                    "worldId",
                },
            )
            receipts = _expect_fields(
                top["receipts"],
                {
                    "curriculumId",
                    "curriculumSha256",
                    "manifestReceiptSha256",
                    "registryId",
                    "registrySha256",
                },
            )
            review_raw = _expect_fields(
                top["review"],
                {
                    "approvalRecordSha256",
                    "approvedSemanticContentSha256",
                    "decision",
                    "decisionReceiptSha256",
                    "ownerAlias",
                    "reviewedAtUtc",
                },
            )
            review = ReviewReceipt(
                owner_alias=review_raw["ownerAlias"],
                decision=review_raw["decision"],
                reviewed_at_utc=review_raw["reviewedAtUtc"],
                approved_semantic_content_sha256=(
                    review_raw["approvedSemanticContentSha256"]
                ),
                approval_record_sha256=review_raw["approvalRecordSha256"],
                decision_receipt_sha256=review_raw["decisionReceiptSha256"],
            )
            expected_receipts = {
                "curriculumId": self._curriculum_id,
                "curriculumSha256": CURRICULUM_V1_SHA256,
                "manifestReceiptSha256": self._manifest_receipt_sha256,
                "registryId": self._registry_id,
                "registrySha256": PROCEDURE_REGISTRY_V1_SHA256,
            }
            if receipts != expected_receipts:
                raise ValueError("row receipts do not match runtime")
            bundle_raw = top["bundle"]
            if not isinstance(bundle_raw, dict):
                raise ValueError("bundle is not an object")
            bundle = VerifiedQuestionBundle.from_private_json(
                _canonical_json(bundle_raw),
                compiler=self._compiler,
                manifest=self._manifest,
            )
            expected_derived = _derived_payload(bundle)
            if derived != expected_derived:
                raise ValueError("derived row metadata does not match bundle")
            if (
                review.approved_semantic_content_sha256
                != bundle.semantic_content_sha256
            ):
                raise ValueError("review does not approve bundle semantics")
            expected_columns = {
                "cache_content_sha256": bundle.cache_content_sha256,
                "semantic_content_sha256": bundle.semantic_content_sha256,
                "world_id": bundle.blueprint.world_id,
                "skill_id": bundle.blueprint.skill_id,
                "family_id": bundle.blueprint.family_id,
                "difficulty": bundle.blueprint.difficulty,
                "question_id": bundle.blueprint.question_id,
                "template_id": bundle.template_id,
                "operand_signature": bundle.operand_signature,
                "content_id": bundle.blueprint.content_sha256,
                "procedure_ids_json": _canonical_json(
                    expected_derived["procedureIds"]
                ),
                "registry_id": self._registry_id,
                "curriculum_id": self._curriculum_id,
            }
            for column, expected in expected_columns.items():
                if row[column] != expected:
                    raise ValueError("query metadata does not match row")
            return ReviewedCacheHit._from_validated_row(
                bundle=bundle,
                cache_row_sha256=row_hash,
                review=review,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
            VerifiedQuestionError,
        ):
            raise CacheCorruptionError("invalid_cache_row") from None

    def _ensure_open(self) -> None:
        if self._closed:
            raise CacheModeError("cache_is_closed")

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "ReviewedCache":
        self._ensure_open()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


__all__ = [
    "CACHE_ROW_SCHEMA_VERSION",
    "CACHE_SCHEMA_VERSION",
    "CACHE_USER_VERSION",
    "CacheCorruptionError",
    "CacheKey",
    "CacheModeError",
    "CacheSchemaError",
    "CacheWriteError",
    "ReviewReceipt",
    "ReviewedCache",
    "ReviewedCacheError",
    "ReviewedCacheHit",
    "question_semantic_sha256",
]
