"""Build a sealed learner cache from canonical owner-approval artifacts.

The approval SHA-256 values used here are integrity receipts, not signatures.
They bind exact canonical approval content to an already-verified bundle but do
not prove who created that content.

SQLite bytes are not promised to be universal across SQLite versions or
platforms.  ``logical_content_sha256`` is the reproducibility contract: it
binds sorted bundle, approval, holdout, verifier, model, registry, and
curriculum receipts.  The physical manifest additionally binds the exact
database bytes, SQLite version, platform, and build settings used for this
particular build.

Publication has one explicit durability boundary.  All failures before
``os.replace`` leave the destination absent.  If the parent-directory fsync
fails after a successful replace, the function reports
``publish_durability_uncertain`` and leaves the read-only database in place;
POSIX cannot honestly promise rollback after that uncertain commit point.
Later validation failures use an owner-only quarantine before removal.  If
that cleanup's parent-directory fsync fails, the stable result is
``cleanup_durability_uncertain`` rather than a claim that rollback persisted.
The quarantine excludes other uids; macOS offers Python no fd-addressed
unlink that can defeat a malicious concurrent process with the same uid.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import sqlite3
import stat
import sys
from typing import Any

from services.wayline_forge.app.curriculum import CURRICULUM_V1_SHA256
from services.wayline_forge.app.procedure_registry import (
    PROCEDURE_REGISTRY_V1_SHA256,
)
from services.wayline_forge.app.providers.distractor import PinnedSlmManifest
from services.wayline_forge.app.question_kernel import QuestionCompiler
from services.wayline_forge.app.reviewed_cache import (
    CACHE_SCHEMA_VERSION,
    ReviewReceipt,
    ReviewedCache,
    ReviewedCacheError,
)
from services.wayline_forge.app.verified_question import (
    VerifiedQuestionBundle,
    VerifiedQuestionError,
)


BUILD_APPROVAL_SCHEMA_VERSION = "wayline.review-approval.v1"
BUILD_INPUT_SCHEMA_VERSION = "wayline.reviewed-cache-build-input.v1"
BUILD_MANIFEST_SCHEMA_VERSION = "wayline.reviewed-cache-build-manifest.v1"
_LOGICAL_SCHEMA_VERSION = "wayline.reviewed-cache-logical-content.v1"
_BUILDER_VERSION = "wayline-reviewed-cache-builder-v1"
_MAX_INPUT_BYTES = 64 * 1024 * 1024
_MAX_ITEMS = 4096
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")
_APPROVAL_FIELDS = {
    "approvalRecordSha256",
    "approvedCacheContentSha256",
    "approvedSemanticContentSha256",
    "decision",
    "ownerAlias",
    "reviewedAtUtc",
    "schemaVersion",
}
_SQLITE_SETTINGS = (
    "foreign_keys=ON",
    "journal_mode=DELETE",
    "secure_delete=ON",
    "synchronous=FULL",
    "trusted_schema=OFF",
)


class CacheBuildError(RuntimeError):
    """Stable, non-sensitive reviewed-cache build failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CacheBuildResult:
    schema_version: str
    item_count: int
    manifest_json: str
    manifest_sha256: str
    logical_content_sha256: str
    database_sha256: str
    database_size: int


@dataclass(frozen=True, slots=True)
class _BuildEntry:
    bundle: VerifiedQuestionBundle
    review: ReviewReceipt
    approval: dict[str, Any]

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (
            self.bundle.semantic_content_sha256,
            self.bundle.cache_content_sha256,
            self.review.approval_record_sha256,
        )


@dataclass(frozen=True, slots=True)
class _AuditResult:
    cache_row_hashes: tuple[tuple[str, str], ...]

    @property
    def row_hashes(self) -> tuple[str, ...]:
        return tuple(sorted(row_hash for _, row_hash in self.cache_row_hashes))

    def row_hash_for(self, cache_content_sha256: str) -> str:
        matches = tuple(
            row_hash
            for cache_hash, row_hash in self.cache_row_hashes
            if cache_hash == cache_content_sha256
        )
        if len(matches) != 1:
            raise CacheBuildError("cache_audit_failed")
        return matches[0]


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    owner: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


def _file_identity(details: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=details.st_dev,
        inode=details.st_ino,
        mode=stat.S_IMODE(details.st_mode),
        owner=details.st_uid,
        links=details.st_nlink,
        size=details.st_size,
        modified_ns=details.st_mtime_ns,
        changed_ns=details.st_ctime_ns,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonstandard_number(_value: str) -> None:
    raise ValueError("nonstandard JSON number")


def _normalized_absolute_path(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise CacheBuildError("unsafe_path")
    raw = os.fspath(value)
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or not os.path.isabs(raw)
        or os.path.normpath(raw) != raw
    ):
        raise CacheBuildError("unsafe_path")
    path = Path(raw)
    if path.name in {"", ".", ".."}:
        raise CacheBuildError("unsafe_path")
    return path


def _open_trusted_parent(path: Path, *, error_code: str) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path.anchor, flags)
        for component in path.parent.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        details = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o022
        ):
            raise CacheBuildError(error_code)
        return descriptor
    except CacheBuildError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise CacheBuildError(error_code) from None


def _stable_file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_trusted_input(path: Path) -> bytes:
    parent_descriptor = _open_trusted_parent(
        path,
        error_code="unsafe_build_input",
    )
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size <= 0
            or before.st_size > _MAX_INPUT_BYTES
        ):
            raise CacheBuildError(
                "build_input_size_invalid"
                if before.st_size <= 0 or before.st_size > _MAX_INPUT_BYTES
                else "unsafe_build_input"
            )
        chunks = bytearray()
        while len(chunks) <= _MAX_INPUT_BYTES:
            chunk = os.read(descriptor, min(1024 * 1024, _MAX_INPUT_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        after = os.fstat(descriptor)
        path_details = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            len(chunks) > _MAX_INPUT_BYTES
            or _stable_file_identity(before) != _stable_file_identity(after)
            or _stable_file_identity(after) != _stable_file_identity(path_details)
        ):
            raise CacheBuildError("unsafe_build_input")
        return bytes(chunks)
    except CacheBuildError:
        raise
    except OSError:
        raise CacheBuildError("unsafe_build_input") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _decode_build_input(raw: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonstandard_number,
        )
    except _DuplicateJsonKey:
        raise CacheBuildError("duplicate_json_key") from None
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise CacheBuildError("invalid_build_input") from None
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"items", "schemaVersion"}
        or decoded.get("schemaVersion") != BUILD_INPUT_SCHEMA_VERSION
        or not isinstance(decoded.get("items"), list)
        or not 1 <= len(decoded["items"]) <= _MAX_ITEMS
    ):
        raise CacheBuildError("invalid_build_input")
    return decoded


def _approval_and_review(
    value: object,
    bundle: VerifiedQuestionBundle,
) -> tuple[dict[str, Any], ReviewReceipt]:
    if not isinstance(value, dict) or set(value) != _APPROVAL_FIELDS:
        raise CacheBuildError("invalid_approval")
    unsigned = dict(value)
    approval_record_sha256 = unsigned.pop("approvalRecordSha256")
    if (
        unsigned.get("schemaVersion") != BUILD_APPROVAL_SCHEMA_VERSION
        or unsigned.get("decision") != "approved"
        or not isinstance(approval_record_sha256, str)
        or _SHA256.fullmatch(approval_record_sha256) is None
        or _sha256_json(unsigned) != approval_record_sha256
    ):
        raise CacheBuildError("invalid_approval")
    if (
        unsigned.get("approvedSemanticContentSha256")
        != bundle.semantic_content_sha256
        or unsigned.get("approvedCacheContentSha256")
        != bundle.cache_content_sha256
    ):
        raise CacheBuildError("approval_bundle_mismatch")
    try:
        review = ReviewReceipt.approved(
            owner_alias=unsigned["ownerAlias"],
            reviewed_at_utc=unsigned["reviewedAtUtc"],
            approved_semantic_content_sha256=unsigned[
                "approvedSemanticContentSha256"
            ],
            approval_record_sha256=approval_record_sha256,
        )
    except (KeyError, TypeError, ValueError):
        raise CacheBuildError("invalid_approval") from None
    return dict(value), review


def _decode_entries(
    decoded: dict[str, Any],
    *,
    compiler: QuestionCompiler,
    manifest: PinnedSlmManifest,
) -> tuple[_BuildEntry, ...]:
    entries: list[_BuildEntry] = []
    for value in decoded["items"]:
        if not isinstance(value, dict) or set(value) != {"approval", "bundle"}:
            raise CacheBuildError("invalid_build_input")
        if not isinstance(value["bundle"], dict):
            raise CacheBuildError("invalid_verified_bundle")
        try:
            bundle = VerifiedQuestionBundle.from_private_json(
                _canonical_json(value["bundle"]),
                compiler=compiler,
                manifest=manifest,
            )
        except (VerifiedQuestionError, TypeError, ValueError):
            raise CacheBuildError("invalid_verified_bundle") from None
        if bundle.blueprint.holdout_receipt.excluded:
            raise CacheBuildError("invalid_verified_bundle")
        approval, review = _approval_and_review(value["approval"], bundle)
        entries.append(_BuildEntry(bundle, review, approval))

    entries.sort(key=lambda entry: entry.sort_key)
    semantic_hashes = [entry.bundle.semantic_content_sha256 for entry in entries]
    cache_hashes = [entry.bundle.cache_content_sha256 for entry in entries]
    approval_hashes = [entry.review.approval_record_sha256 for entry in entries]
    if (
        len(set(semantic_hashes)) != len(entries)
        or len(set(cache_hashes)) != len(entries)
        or len(set(approval_hashes)) != len(entries)
    ):
        raise CacheBuildError("duplicate_build_item")
    return tuple(entries)


def _check_destination_absent(parent_descriptor: int, name: str) -> None:
    try:
        details = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise CacheBuildError("unsafe_destination") from None
    if stat.S_ISLNK(details.st_mode):
        raise CacheBuildError("unsafe_destination")
    raise CacheBuildError("destination_exists")


def _new_private_temp(
    parent_descriptor: int,
    destination: Path,
) -> tuple[str, Path]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(32):
        name = f".{destination.name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        except OSError:
            raise CacheBuildError("cache_build_failed") from None
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or details.st_nlink != 1
                or stat.S_IMODE(details.st_mode) != 0o600
            ):
                raise CacheBuildError("cache_build_failed")
        finally:
            os.close(descriptor)
        return name, destination.parent / name
    raise CacheBuildError("cache_build_failed")


def _configure_build_database(cache: ReviewedCache) -> None:
    try:
        connection = cache._connection
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA secure_delete = ON")
    except (AttributeError, sqlite3.Error):
        raise CacheBuildError("cache_build_failed") from None


def _sidecars_absent(parent_descriptor: int, name: str) -> bool:
    for suffix in _SIDECAR_SUFFIXES:
        try:
            os.stat(name + suffix, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            return False
        return False
    return True


def _identity_at(
    parent_descriptor: int,
    name: str,
    *,
    expected_mode: int,
) -> _FileIdentity:
    try:
        details = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise CacheBuildError("cache_identity_changed") from None
    identity = _file_identity(details)
    if (
        not stat.S_ISREG(details.st_mode)
        or identity.owner != os.getuid()
        or identity.links != 1
        or identity.mode != expected_mode
    ):
        raise CacheBuildError("cache_identity_changed")
    return identity


def _require_identity(
    parent_descriptor: int,
    name: str,
    expected: _FileIdentity,
) -> None:
    actual = _identity_at(
        parent_descriptor,
        name,
        expected_mode=expected.mode,
    )
    if actual != expected:
        raise CacheBuildError("cache_identity_changed")


def _require_parent_binding(
    parent_descriptor: int,
    path: Path,
    *,
    error_code: str = "cache_identity_changed",
) -> None:
    candidate = -1
    try:
        candidate = _open_trusted_parent(path, error_code=error_code)
        expected = os.fstat(parent_descriptor)
        actual = os.fstat(candidate)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise CacheBuildError(error_code)
    finally:
        if candidate >= 0:
            os.close(candidate)


def _guarded_audit(
    parent_descriptor: int,
    name: str,
    path: Path,
    expected_identity: _FileIdentity,
    *,
    compiler: QuestionCompiler,
    manifest: PinnedSlmManifest,
    entries: tuple[_BuildEntry, ...],
) -> _AuditResult:
    _require_parent_binding(parent_descriptor, path)
    _require_identity(parent_descriptor, name, expected_identity)
    result = _audit_database(
        path,
        compiler=compiler,
        manifest=manifest,
        entries=entries,
    )
    _require_parent_binding(parent_descriptor, path)
    _require_identity(parent_descriptor, name, expected_identity)
    return result


def _audit_database(
    path: Path,
    *,
    compiler: QuestionCompiler,
    manifest: PinnedSlmManifest,
    entries: tuple[_BuildEntry, ...],
) -> _AuditResult:
    expected = {
        entry.bundle.cache_content_sha256: entry for entry in entries
    }
    try:
        with ReviewedCache.open_learner(
            path,
            compiler=compiler,
            manifest=manifest,
        ) as learner:
            connection = learner._connection
            integrity = tuple(
                str(row[0])
                for row in connection.execute("PRAGMA integrity_check").fetchall()
            )
            if integrity != ("ok",):
                raise ValueError("integrity check failed")
            rows = connection.execute(
                "SELECT * FROM reviewed_questions "
                "ORDER BY semantic_content_sha256, cache_content_sha256"
            ).fetchall()
            if len(rows) != len(entries):
                raise ValueError("row count mismatch")
            cache_row_hashes: list[tuple[str, str]] = []
            seen: set[str] = set()
            for row in rows:
                hit = learner._validate_row(row)
                entry = expected.get(hit.bundle.cache_content_sha256)
                if (
                    entry is None
                    or hit.bundle.semantic_content_sha256
                    != entry.bundle.semantic_content_sha256
                    or hit.approval_record_sha256
                    != entry.review.approval_record_sha256
                    or hit.review_decision_receipt_sha256
                    != entry.review.decision_receipt_sha256
                    or hit.reviewer_alias != entry.review.owner_alias
                    or hit.reviewed_at_utc != entry.review.reviewed_at_utc
                    or hit.bundle.cache_content_sha256 in seen
                ):
                    raise ValueError("row receipt mismatch")
                seen.add(hit.bundle.cache_content_sha256)
                cache_row_hashes.append(
                    (
                        hit.bundle.cache_content_sha256,
                        hit.cache_row_sha256,
                    )
                )
            if seen != set(expected):
                raise ValueError("row coverage mismatch")
            return _AuditResult(tuple(sorted(cache_row_hashes)))
    except (ReviewedCacheError, sqlite3.Error, TypeError, ValueError, AttributeError):
        raise CacheBuildError("cache_audit_failed") from None


def _fsync_and_hash_database(
    path: Path,
    *,
    expected_identity: _FileIdentity,
    make_read_only: bool,
) -> tuple[str, int]:
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or _file_identity(before) != expected_identity
        ):
            raise CacheBuildError("cache_identity_changed")
        if make_read_only:
            if expected_identity.mode != 0o600:
                raise CacheBuildError("cache_identity_changed")
            os.fchmod(descriptor, 0o400)
            baseline = os.fstat(descriptor)
            baseline_identity = _file_identity(baseline)
            if (
                not stat.S_ISREG(baseline.st_mode)
                or baseline_identity.device != expected_identity.device
                or baseline_identity.inode != expected_identity.inode
                or baseline_identity.owner != expected_identity.owner
                or baseline_identity.links != expected_identity.links
                or baseline_identity.size != expected_identity.size
                or baseline_identity.modified_ns != expected_identity.modified_ns
                or baseline_identity.mode != 0o400
            ):
                raise CacheBuildError("cache_identity_changed")
        elif expected_identity.mode != 0o400:
            raise CacheBuildError("cache_identity_changed")
        else:
            baseline_identity = _file_identity(before)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or _file_identity(after) != baseline_identity
        ):
            raise CacheBuildError("cache_identity_changed")
        return digest.hexdigest(), after.st_size
    except CacheBuildError:
        raise
    except OSError:
        raise CacheBuildError("cache_build_failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_parent_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _remove_owned_published_cache(
    parent_descriptor: int,
    name: str,
    expected_identity: _FileIdentity,
) -> None:
    """Quarantine, verify, and remove only this invocation's published inode.

    The destination is atomically moved into a new owner-only directory before
    it is inspected.  A foreign inode is linked back without overwriting a
    concurrent destination.  Python on macOS has no fd-addressed unlink, so a
    malicious process running as the same uid can still race names inside a
    mode-0700 directory; that same-uid boundary is explicitly out of scope.
    """

    quarantine_name = ""
    quarantine_descriptor = -1
    artifact_descriptor = -1
    for _attempt in range(32):
        candidate = f".{name}.{secrets.token_hex(16)}.cleanup"
        try:
            os.mkdir(candidate, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        except OSError:
            raise CacheBuildError("published_cache_unsafe") from None
        quarantine_name = candidate
        break
    if not quarantine_name:
        raise CacheBuildError("published_cache_unsafe")

    directory_flags = os.O_RDONLY
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        quarantine_descriptor = os.open(
            quarantine_name,
            directory_flags,
            dir_fd=parent_descriptor,
        )
        quarantine_details = os.fstat(quarantine_descriptor)
        if (
            not stat.S_ISDIR(quarantine_details.st_mode)
            or quarantine_details.st_uid != os.getuid()
            or stat.S_IMODE(quarantine_details.st_mode) != 0o700
        ):
            raise CacheBuildError("published_cache_unsafe")
        try:
            os.replace(
                name,
                "artifact",
                src_dir_fd=parent_descriptor,
                dst_dir_fd=quarantine_descriptor,
            )
        except FileNotFoundError:
            os.close(quarantine_descriptor)
            quarantine_descriptor = -1
            os.rmdir(quarantine_name, dir_fd=parent_descriptor)
            try:
                _fsync_parent_directory(parent_descriptor)
            except OSError:
                raise CacheBuildError("cleanup_durability_uncertain") from None
            return
        except OSError:
            raise CacheBuildError("published_cache_unsafe") from None

        artifact_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        artifact_flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            artifact_descriptor = os.open(
                "artifact",
                artifact_flags,
                dir_fd=quarantine_descriptor,
            )
            actual_identity = _file_identity(os.fstat(artifact_descriptor))
        except OSError:
            raise CacheBuildError("published_cache_unsafe") from None

        exact_owned = (
            stat.S_ISREG(os.fstat(artifact_descriptor).st_mode)
            and actual_identity.device == expected_identity.device
            and actual_identity.inode == expected_identity.inode
            and actual_identity.mode == expected_identity.mode
            and actual_identity.owner == expected_identity.owner
            and actual_identity.links == expected_identity.links
            and actual_identity.size == expected_identity.size
            and actual_identity.modified_ns == expected_identity.modified_ns
        )
        os.close(artifact_descriptor)
        artifact_descriptor = -1

        if not exact_owned:
            try:
                os.link(
                    "artifact",
                    name,
                    src_dir_fd=quarantine_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                os.unlink("artifact", dir_fd=quarantine_descriptor)
            except OSError:
                raise CacheBuildError("published_cache_unsafe") from None
            os.close(quarantine_descriptor)
            quarantine_descriptor = -1
            os.rmdir(quarantine_name, dir_fd=parent_descriptor)
            try:
                _fsync_parent_directory(parent_descriptor)
            except OSError:
                raise CacheBuildError("cleanup_durability_uncertain") from None
            raise CacheBuildError("published_cache_unsafe")

        try:
            os.unlink("artifact", dir_fd=quarantine_descriptor)
            os.close(quarantine_descriptor)
            quarantine_descriptor = -1
            os.rmdir(quarantine_name, dir_fd=parent_descriptor)
        except OSError:
            raise CacheBuildError("published_cache_unsafe") from None
        try:
            _fsync_parent_directory(parent_descriptor)
        except OSError:
            raise CacheBuildError("cleanup_durability_uncertain") from None
    finally:
        if artifact_descriptor >= 0:
            os.close(artifact_descriptor)
        if quarantine_descriptor >= 0:
            os.close(quarantine_descriptor)


def _holdout_payload(bundle: VerifiedQuestionBundle) -> dict[str, Any]:
    receipt = bundle.blueprint.holdout_receipt
    return {
        "boundaryVersion": receipt.boundary_version,
        "canonicalSha256": receipt.canonical_sha256,
        "excluded": receipt.excluded,
        "maximumSimilarityBits": receipt.maximum_similarity_bits,
        "questionFingerprint": receipt.question_fingerprint,
        "recordCount": receipt.record_count,
        "similarityThresholdBits": receipt.similarity_threshold_bits,
        "sourceSha256": receipt.source_sha256,
    }


def _runtime_payload(
    compiler: QuestionCompiler,
    manifest: PinnedSlmManifest,
) -> dict[str, Any]:
    return {
        "adapterIdentityReceiptSha256": manifest.adapter_identity_receipt_sha256,
        "curriculumId": compiler.curriculum.curriculum_id,
        "curriculumSha256": CURRICULUM_V1_SHA256,
        "generatorIdentityReceiptSha256": (
            manifest.generator_identity_receipt_sha256
        ),
        "ggufSha256": manifest.gguf_sha256,
        "modelId": manifest.model_id,
        "modelSha256": manifest.model_sha256,
        "promptTemplateSha256": manifest.prompt_template_sha256,
        "registryId": compiler.registry.registry_id,
        "registrySha256": PROCEDURE_REGISTRY_V1_SHA256,
    }


def _item_manifest(entry: _BuildEntry, row_hash: str) -> dict[str, Any]:
    bundle = entry.bundle
    provenance = bundle.provenance
    return {
        "adapterIdentityReceiptSha256": (
            provenance.adapter_identity_receipt_sha256
        ),
        "approvalRecordSha256": entry.review.approval_record_sha256,
        "cacheContentSha256": bundle.cache_content_sha256,
        "decision": entry.review.decision,
        "generatorIdentityReceiptSha256": (
            provenance.generator_identity_receipt_sha256
        ),
        "ggufSha256": provenance.gguf_sha256,
        "holdoutReceipt": _holdout_payload(bundle),
        "modelSha256": provenance.model_sha256,
        "promptSha256": provenance.prompt_sha256,
        "promptTemplateSha256": provenance.prompt_template_sha256,
        "registryId": provenance.registry_id,
        "reviewDecisionReceiptSha256": entry.review.decision_receipt_sha256,
        "reviewedAtUtc": entry.review.reviewed_at_utc,
        "reviewerAlias": entry.review.owner_alias,
        "rowSha256": row_hash,
        "semanticContentSha256": bundle.semantic_content_sha256,
        "verifierReceiptSha256": provenance.verifier_receipt_sha256,
        "verifierVersion": provenance.verifier_version,
    }


def _manifest_result(
    *,
    entries: tuple[_BuildEntry, ...],
    audit: _AuditResult,
    compiler: QuestionCompiler,
    manifest: PinnedSlmManifest,
    database_sha256: str,
    database_size: int,
) -> CacheBuildResult:
    if len(audit.cache_row_hashes) != len(entries):
        raise CacheBuildError("cache_audit_failed")
    item_payloads = [
        _item_manifest(
            entry,
            audit.row_hash_for(entry.bundle.cache_content_sha256),
        )
        for entry in entries
    ]
    runtime = _runtime_payload(compiler, manifest)
    canonical_input = {
        "items": [
            {
                "approvalRecordSha256": entry.review.approval_record_sha256,
                "cacheContentSha256": entry.bundle.cache_content_sha256,
                "semanticContentSha256": entry.bundle.semantic_content_sha256,
            }
            for entry in entries
        ],
        "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
    }
    build_input_canonical_sha256 = _sha256_json(canonical_input)
    logical_payload = {
        "buildInputCanonicalSha256": build_input_canonical_sha256,
        "items": item_payloads,
        "runtime": runtime,
        "schemaVersion": _LOGICAL_SCHEMA_VERSION,
    }
    logical_content_sha256 = _sha256_json(logical_payload)
    payload = {
        "builder": {
            "determinism": "logical-only-across-platforms",
            "sqliteSettings": list(_SQLITE_SETTINGS),
            "version": _BUILDER_VERSION,
        },
        "buildInputCanonicalSha256": build_input_canonical_sha256,
        "database": {
            "cacheSchemaVersion": CACHE_SCHEMA_VERSION,
            "platformMachine": platform.machine(),
            "platformSystem": platform.system(),
            "pythonVersion": platform.python_version(),
            "rowCount": len(entries),
            "rowSha256s": list(audit.row_hashes),
            "sha256": database_sha256,
            "sizeBytes": database_size,
            "sqliteVersion": sqlite3.sqlite_version,
        },
        "items": item_payloads,
        "logicalContentSha256": logical_content_sha256,
        "runtime": runtime,
        "schemaVersion": BUILD_MANIFEST_SCHEMA_VERSION,
    }
    manifest_json = _canonical_json(payload)
    return CacheBuildResult(
        schema_version=BUILD_MANIFEST_SCHEMA_VERSION,
        item_count=len(entries),
        manifest_json=manifest_json,
        manifest_sha256=_sha256_bytes(manifest_json.encode("utf-8")),
        logical_content_sha256=logical_content_sha256,
        database_sha256=database_sha256,
        database_size=database_size,
    )


def build_reviewed_cache(
    input_path: str | Path,
    destination_path: str | Path,
    *,
    compiler: QuestionCompiler,
    manifest: PinnedSlmManifest,
) -> CacheBuildResult:
    """Build, fully audit, and publish one new reviewed-cache database."""

    source = _normalized_absolute_path(input_path)
    destination = _normalized_absolute_path(destination_path)
    output_parent_descriptor = _open_trusted_parent(
        destination,
        error_code="unsafe_output_parent",
    )
    temporary_name: str | None = None
    temporary_path: Path | None = None
    published = False
    publication_may_have_occurred = False
    publication_identity: _FileIdentity | None = None
    try:
        _check_destination_absent(output_parent_descriptor, destination.name)
        raw = _read_trusted_input(source)
        decoded = _decode_build_input(raw)
        entries = _decode_entries(
            decoded,
            compiler=compiler,
            manifest=manifest,
        )

        temporary_name, temporary_path = _new_private_temp(
            output_parent_descriptor,
            destination,
        )
        try:
            with ReviewedCache.open_build(
                temporary_path,
                compiler=compiler,
                manifest=manifest,
            ) as cache:
                _configure_build_database(cache)
                for entry in entries:
                    cache.insert(entry.bundle, entry.review)
                cache._connection.execute("VACUUM")
        except CacheBuildError:
            raise
        except (ReviewedCacheError, sqlite3.Error, TypeError, ValueError):
            raise CacheBuildError("cache_build_failed") from None

        if not _sidecars_absent(output_parent_descriptor, temporary_name):
            raise CacheBuildError("cache_build_failed")
        temporary_identity = _identity_at(
            output_parent_descriptor,
            temporary_name,
            expected_mode=0o600,
        )
        first_audit = _guarded_audit(
            output_parent_descriptor,
            temporary_name,
            temporary_path,
            temporary_identity,
            compiler=compiler,
            manifest=manifest,
            entries=entries,
        )
        _require_parent_binding(output_parent_descriptor, temporary_path)
        _require_identity(
            output_parent_descriptor,
            temporary_name,
            temporary_identity,
        )
        database_sha256, database_size = _fsync_and_hash_database(
            temporary_path,
            expected_identity=temporary_identity,
            make_read_only=True,
        )
        _require_parent_binding(output_parent_descriptor, temporary_path)
        read_only_identity = _identity_at(
            output_parent_descriptor,
            temporary_name,
            expected_mode=0o400,
        )
        if (
            read_only_identity.device != temporary_identity.device
            or read_only_identity.inode != temporary_identity.inode
            or read_only_identity.owner != temporary_identity.owner
            or read_only_identity.links != temporary_identity.links
            or read_only_identity.size != temporary_identity.size
        ):
            raise CacheBuildError("cache_identity_changed")
        result = _manifest_result(
            entries=entries,
            audit=first_audit,
            compiler=compiler,
            manifest=manifest,
            database_sha256=database_sha256,
            database_size=database_size,
        )
        _require_parent_binding(output_parent_descriptor, temporary_path)
        _require_identity(
            output_parent_descriptor,
            temporary_name,
            read_only_identity,
        )
        publication_identity = read_only_identity
        publication_may_have_occurred = True
        try:
            os.replace(
                temporary_name,
                destination.name,
                src_dir_fd=output_parent_descriptor,
                dst_dir_fd=output_parent_descriptor,
            )
        except OSError:
            publication_may_have_occurred = False
            raise CacheBuildError("destination_publish_failed") from None
        published = True
        final_identity = _identity_at(
            output_parent_descriptor,
            destination.name,
            expected_mode=0o400,
        )
        if (
            final_identity.device != read_only_identity.device
            or final_identity.inode != read_only_identity.inode
            or final_identity.mode != read_only_identity.mode
            or final_identity.owner != read_only_identity.owner
            or final_identity.links != read_only_identity.links
            or final_identity.size != read_only_identity.size
            or final_identity.modified_ns != read_only_identity.modified_ns
        ):
            raise CacheBuildError("published_cache_invalid")
        _require_parent_binding(output_parent_descriptor, destination)
        try:
            _fsync_parent_directory(output_parent_descriptor)
        except OSError:
            raise CacheBuildError("publish_durability_uncertain") from None

        if (
            final_identity.size != database_size
            or not _sidecars_absent(output_parent_descriptor, destination.name)
        ):
            raise CacheBuildError("published_cache_invalid")
        final_audit = _guarded_audit(
            output_parent_descriptor,
            destination.name,
            destination,
            final_identity,
            compiler=compiler,
            manifest=manifest,
            entries=entries,
        )
        if final_audit != first_audit:
            raise CacheBuildError("published_cache_invalid")
        _require_parent_binding(output_parent_descriptor, destination)
        _require_identity(
            output_parent_descriptor,
            destination.name,
            final_identity,
        )
        final_database_sha256, final_database_size = _fsync_and_hash_database(
            destination,
            expected_identity=final_identity,
            make_read_only=False,
        )
        _require_parent_binding(output_parent_descriptor, destination)
        _require_identity(
            output_parent_descriptor,
            destination.name,
            final_identity,
        )
        if (
            final_database_sha256 != database_sha256
            or final_database_size != database_size
        ):
            raise CacheBuildError("published_cache_invalid")
        return result
    except CacheBuildError as failure:
        if (
            publication_may_have_occurred
            and publication_identity is not None
            and failure.code != "publish_durability_uncertain"
        ):
            _remove_owned_published_cache(
                output_parent_descriptor,
                destination.name,
                publication_identity,
            )
        raise
    except Exception:
        if publication_may_have_occurred and publication_identity is not None:
            _remove_owned_published_cache(
                output_parent_descriptor,
                destination.name,
                publication_identity,
            )
        raise CacheBuildError("cache_build_failed") from None
    except BaseException as failure:
        if publication_may_have_occurred and publication_identity is not None:
            try:
                _remove_owned_published_cache(
                    output_parent_descriptor,
                    destination.name,
                    publication_identity,
                )
            except CacheBuildError as cleanup_failure:
                raise failure from cleanup_failure
        raise
    finally:
        if temporary_name is not None and not published:
            try:
                os.unlink(temporary_name, dir_fd=output_parent_descriptor)
            except FileNotFoundError:
                pass
            except OSError:
                pass
            for suffix in _SIDECAR_SUFFIXES:
                try:
                    os.unlink(
                        temporary_name + suffix,
                        dir_fd=output_parent_descriptor,
                    )
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        os.close(output_parent_descriptor)


__all__ = [
    "BUILD_APPROVAL_SCHEMA_VERSION",
    "BUILD_INPUT_SCHEMA_VERSION",
    "BUILD_MANIFEST_SCHEMA_VERSION",
    "CacheBuildError",
    "CacheBuildResult",
    "build_reviewed_cache",
]
