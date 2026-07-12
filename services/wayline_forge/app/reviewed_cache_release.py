"""Fail-closed loader for immutable reviewed-cache release generations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any
import unicodedata

from .curriculum import CURRICULUM_V1_SHA256
from .procedure_registry import PROCEDURE_REGISTRY_V1_SHA256
from .providers.distractor import PinnedSlmManifest
from .question_kernel import QuestionCompiler
from .reviewed_cache import (
    CACHE_SCHEMA_VERSION,
    ReviewedCache,
    ReviewedCacheError,
    ReviewedCacheHit,
)


POINTER_SCHEMA_VERSION = "wayline.reviewed-cache-pointer.v1"
MANIFEST_SCHEMA_VERSION = "wayline.reviewed-cache-build-manifest.v1"
LOGICAL_SCHEMA_VERSION = "wayline.reviewed-cache-logical-content.v1"
BUILD_INPUT_SCHEMA_VERSION = "wayline.reviewed-cache-build-input.v1"

_BUILDER_VERSION = "wayline-reviewed-cache-builder-v1"
_SQLITE_SETTINGS = (
    "foreign_keys=ON",
    "journal_mode=DELETE",
    "secure_delete=ON",
    "synchronous=FULL",
    "trusted_schema=OFF",
)
_POINTER_FIELDS = {"generationId", "manifestSha256", "schemaVersion"}
_MANIFEST_FIELDS = {
    "builder",
    "buildInputCanonicalSha256",
    "database",
    "items",
    "logicalContentSha256",
    "runtime",
    "schemaVersion",
}
_BUILDER_FIELDS = {"determinism", "sqliteSettings", "version"}
_DATABASE_FIELDS = {
    "cacheSchemaVersion",
    "platformMachine",
    "platformSystem",
    "pythonVersion",
    "rowCount",
    "rowSha256s",
    "sha256",
    "sizeBytes",
    "sqliteVersion",
}
_RUNTIME_FIELDS = {
    "adapterIdentityReceiptSha256",
    "curriculumId",
    "curriculumSha256",
    "generatorIdentityReceiptSha256",
    "ggufSha256",
    "modelId",
    "modelSha256",
    "promptTemplateSha256",
    "registryId",
    "registrySha256",
}
_ITEM_FIELDS = {
    "adapterIdentityReceiptSha256",
    "approvalRecordSha256",
    "cacheContentSha256",
    "decision",
    "generatorIdentityReceiptSha256",
    "ggufSha256",
    "holdoutReceipt",
    "modelSha256",
    "promptSha256",
    "promptTemplateSha256",
    "registryId",
    "reviewDecisionReceiptSha256",
    "reviewedAtUtc",
    "reviewerAlias",
    "rowSha256",
    "semanticContentSha256",
    "verifierReceiptSha256",
    "verifierVersion",
}
_HOLDOUT_FIELDS = {
    "boundaryVersion",
    "canonicalSha256",
    "excluded",
    "maximumSimilarityBits",
    "questionFingerprint",
    "recordCount",
    "similarityThresholdBits",
    "sourceSha256",
}
_GENERATION_FILES = {
    "reviewed_cache.sqlite3",
    "reviewed_cache_manifest.json",
}
_MAX_POINTER_BYTES = 4 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_DATABASE_BYTES = 1024 * 1024 * 1024
_MAX_ITEMS = 4096
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SAFE_POINTER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)


class ReviewedCacheReleaseError(RuntimeError):
    """Stable, non-sensitive failure to validate a reviewed-cache release."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int
    mode: int
    owner: int
    group: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(slots=True, eq=False)
class _OwnedDescriptor:
    descriptor: int
    device: int | None = None
    inode: int | None = None
    file_type: int | None = None
    owner: int | None = None


def _identity(details: os.stat_result) -> _Identity:
    return _Identity(
        device=details.st_dev,
        inode=details.st_ino,
        mode=details.st_mode,
        owner=details.st_uid,
        group=details.st_gid,
        links=details.st_nlink,
        size=details.st_size,
        modified_ns=details.st_mtime_ns,
        changed_ns=details.st_ctime_ns,
    )


def _bind_owned_descriptor(
    record: _OwnedDescriptor,
    details: os.stat_result,
) -> None:
    record.device = details.st_dev
    record.inode = details.st_ino
    record.file_type = stat.S_IFMT(details.st_mode)
    record.owner = details.st_uid


def _acquire_owned_descriptor(
    ledger: list[_OwnedDescriptor],
    path: str,
    flags: int,
    *,
    dir_fd: int | None = None,
) -> tuple[_OwnedDescriptor, os.stat_result]:
    if dir_fd is None:
        descriptor = os.open(path, flags)
    else:
        descriptor = os.open(path, flags, dir_fd=dir_fd)
    record = _OwnedDescriptor(descriptor)
    ledger.append(record)
    details = os.fstat(descriptor)
    _bind_owned_descriptor(record, details)
    return record, details


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
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("nonstandard JSON number")


def _decode_canonical_object(raw: bytes, *, code: str) -> dict[str, Any]:
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
        if not isinstance(decoded, dict):
            raise ValueError("object required")
        if _canonical_json(decoded).encode("utf-8") != raw:
            raise ValueError("canonical JSON required")
        return decoded
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        raise ReviewedCacheReleaseError(code) from None


def _expect_fields(value: object, fields: set[str], *, code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ReviewedCacheReleaseError(code)
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_text(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= maximum
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _is_exact_utc(value: object) -> bool:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]{1,9})?Z",
            value,
            re.ASCII,
        )
        is None
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _normalized_absolute_root(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise ReviewedCacheReleaseError("unsafe_release_path")
    raw = os.fspath(value)
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or not os.path.isabs(raw)
        or os.path.normpath(raw) != raw
    ):
        raise ReviewedCacheReleaseError("unsafe_release_path")
    return Path(raw)


def _validate_pointer_name(value: object) -> str:
    if not isinstance(value, str) or _SAFE_POINTER_NAME.fullmatch(value) is None:
        raise ReviewedCacheReleaseError("invalid_pointer_name")
    return value


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_release_root(
    path: Path,
    ledger: list[_OwnedDescriptor],
) -> tuple[_OwnedDescriptor, _Identity]:
    try:
        record, details = _acquire_owned_descriptor(
            ledger,
            path.anchor,
            _directory_flags(),
        )
        for component in path.parts[1:]:
            child, child_details = _acquire_owned_descriptor(
                ledger,
                component,
                _directory_flags(),
                dir_fd=record.descriptor,
            )
            _close_owned_record_or_raise(record, ledger)
            record, details = child, child_details
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            raise ReviewedCacheReleaseError("unsafe_release_path")
        return record, _identity(details)
    except ReviewedCacheReleaseError:
        raise
    except OSError:
        raise ReviewedCacheReleaseError("unsafe_release_path") from None


def _open_child_directory(
    parent_descriptor: int,
    name: str,
    *,
    parent_device: int,
    exact_mode: int | None,
    ledger: list[_OwnedDescriptor],
) -> tuple[_OwnedDescriptor, _Identity]:
    try:
        record, details = _acquire_owned_descriptor(
            ledger,
            name,
            _directory_flags(),
            dir_fd=parent_descriptor,
        )
        mode = stat.S_IMODE(details.st_mode)
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_dev != parent_device
            or mode & 0o077
            or (exact_mode is not None and mode != exact_mode)
        ):
            raise ReviewedCacheReleaseError("generation_invalid")
        return record, _identity(details)
    except ReviewedCacheReleaseError:
        raise
    except OSError:
        raise ReviewedCacheReleaseError("generation_invalid") from None


def _open_owned_file(
    parent_descriptor: int,
    name: str,
    *,
    parent_device: int,
    maximum_bytes: int,
    code: str,
    ledger: list[_OwnedDescriptor],
) -> tuple[_OwnedDescriptor, _Identity]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        record, details = _acquire_owned_descriptor(
            ledger,
            name,
            flags,
            dir_fd=parent_descriptor,
        )
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != os.getuid()
            or details.st_dev != parent_device
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) != 0o400
            or not 0 < details.st_size <= maximum_bytes
        ):
            raise ReviewedCacheReleaseError(code)
        return record, _identity(details)
    except ReviewedCacheReleaseError:
        raise
    except OSError:
        raise ReviewedCacheReleaseError(code) from None


def _require_descriptor_identity(
    descriptor: int,
    expected: _Identity,
    *,
    code: str,
) -> None:
    try:
        actual = _identity(os.fstat(descriptor))
    except OSError:
        raise ReviewedCacheReleaseError(code) from None
    if actual != expected:
        raise ReviewedCacheReleaseError(code)


def _require_name_identity(
    parent_descriptor: int,
    name: str,
    expected: _Identity,
    *,
    code: str,
) -> None:
    try:
        actual = _identity(
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        )
    except OSError:
        raise ReviewedCacheReleaseError(code) from None
    if actual != expected:
        raise ReviewedCacheReleaseError(code)


def _read_bounded(
    descriptor: int,
    expected: _Identity,
    *,
    maximum_bytes: int,
    code: str,
) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks = bytearray()
        while len(chunks) <= maximum_bytes:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, maximum_bytes + 1 - len(chunks)),
            )
            if not chunk:
                break
            chunks.extend(chunk)
        _require_descriptor_identity(descriptor, expected, code=code)
        if len(chunks) != expected.size or len(chunks) > maximum_bytes:
            raise ReviewedCacheReleaseError(code)
        return bytes(chunks)
    except ReviewedCacheReleaseError:
        raise
    except OSError:
        raise ReviewedCacheReleaseError(code) from None


def _hash_file_descriptor(
    descriptor: int,
    expected: _Identity,
) -> tuple[str, int]:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        while total <= _MAX_DATABASE_BYTES:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        _require_descriptor_identity(
            descriptor,
            expected,
            code="database_invalid",
        )
        if total != expected.size or total > _MAX_DATABASE_BYTES:
            raise ReviewedCacheReleaseError("database_invalid")
        return digest.hexdigest(), total
    except ReviewedCacheReleaseError:
        raise
    except OSError:
        raise ReviewedCacheReleaseError("database_invalid") from None


def _directory_entries(descriptor: int) -> set[str]:
    try:
        entries = os.listdir(descriptor)
    except OSError:
        raise ReviewedCacheReleaseError("generation_invalid") from None
    if any(not isinstance(entry, str) for entry in entries):
        raise ReviewedCacheReleaseError("generation_invalid")
    return set(entries)


def _decode_pointer(raw: bytes) -> tuple[str, str]:
    pointer = _decode_canonical_object(raw, code="pointer_invalid")
    _expect_fields(pointer, _POINTER_FIELDS, code="pointer_invalid")
    manifest_sha256 = pointer.get("manifestSha256")
    generation_id = pointer.get("generationId")
    if (
        pointer.get("schemaVersion") != POINTER_SCHEMA_VERSION
        or not _is_sha256(manifest_sha256)
        or generation_id != "generation-" + manifest_sha256
    ):
        raise ReviewedCacheReleaseError("pointer_invalid")
    return generation_id, manifest_sha256


def _validate_manifest_structure(manifest: dict[str, Any]) -> None:
    _expect_fields(manifest, _MANIFEST_FIELDS, code="manifest_invalid")
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
        raise ReviewedCacheReleaseError("manifest_invalid")
    for field in (
        "buildInputCanonicalSha256",
        "logicalContentSha256",
    ):
        if not _is_sha256(manifest.get(field)):
            raise ReviewedCacheReleaseError("manifest_invalid")

    builder = _expect_fields(
        manifest.get("builder"),
        _BUILDER_FIELDS,
        code="manifest_invalid",
    )
    if builder != {
        "determinism": "logical-only-across-platforms",
        "sqliteSettings": list(_SQLITE_SETTINGS),
        "version": _BUILDER_VERSION,
    }:
        raise ReviewedCacheReleaseError("manifest_invalid")

    database = _expect_fields(
        manifest.get("database"),
        _DATABASE_FIELDS,
        code="manifest_invalid",
    )
    row_count = database.get("rowCount")
    database_size = database.get("sizeBytes")
    row_hashes = database.get("rowSha256s")
    if (
        database.get("cacheSchemaVersion") != CACHE_SCHEMA_VERSION
        or not _is_sha256(database.get("sha256"))
        or not _is_int(row_count)
        or not 1 <= row_count <= _MAX_ITEMS
        or not _is_int(database_size)
        or not 0 < database_size <= _MAX_DATABASE_BYTES
        or not isinstance(row_hashes, list)
        or len(row_hashes) != row_count
        or any(not _is_sha256(value) for value in row_hashes)
        or row_hashes != sorted(row_hashes)
        or len(set(row_hashes)) != len(row_hashes)
        or any(
            not isinstance(database.get(field), str)
            or len(database[field]) > 256
            for field in (
                "platformMachine",
                "platformSystem",
                "pythonVersion",
                "sqliteVersion",
            )
        )
    ):
        raise ReviewedCacheReleaseError("manifest_invalid")

    runtime = _expect_fields(
        manifest.get("runtime"),
        _RUNTIME_FIELDS,
        code="manifest_invalid",
    )
    for field in (
        "adapterIdentityReceiptSha256",
        "curriculumSha256",
        "generatorIdentityReceiptSha256",
        "ggufSha256",
        "modelSha256",
        "promptTemplateSha256",
        "registrySha256",
    ):
        if not _is_sha256(runtime.get(field)):
            raise ReviewedCacheReleaseError("manifest_invalid")
    for field, maximum in (
        ("curriculumId", 128),
        ("modelId", 256),
        ("registryId", 128),
    ):
        if not _safe_text(runtime.get(field), maximum=maximum):
            raise ReviewedCacheReleaseError("manifest_invalid")

    items = manifest.get("items")
    if not isinstance(items, list) or len(items) != row_count:
        raise ReviewedCacheReleaseError("manifest_invalid")
    sort_keys: list[tuple[str, str, str]] = []
    cache_hashes: list[str] = []
    semantic_hashes: list[str] = []
    approval_hashes: list[str] = []
    item_row_hashes: list[str] = []
    for raw_item in items:
        item = _expect_fields(raw_item, _ITEM_FIELDS, code="manifest_invalid")
        for field in (
            "adapterIdentityReceiptSha256",
            "approvalRecordSha256",
            "cacheContentSha256",
            "generatorIdentityReceiptSha256",
            "ggufSha256",
            "modelSha256",
            "promptSha256",
            "promptTemplateSha256",
            "reviewDecisionReceiptSha256",
            "rowSha256",
            "semanticContentSha256",
            "verifierReceiptSha256",
        ):
            if not _is_sha256(item.get(field)):
                raise ReviewedCacheReleaseError("manifest_invalid")
        if (
            item.get("decision") != "approved"
            or not _safe_text(item.get("registryId"), maximum=128)
            or not _safe_text(item.get("reviewerAlias"), maximum=128)
            or not _is_exact_utc(item.get("reviewedAtUtc"))
            or not _safe_text(item.get("verifierVersion"), maximum=128)
        ):
            raise ReviewedCacheReleaseError("manifest_invalid")
        holdout = _expect_fields(
            item.get("holdoutReceipt"),
            _HOLDOUT_FIELDS,
            code="manifest_invalid",
        )
        if (
            not _safe_text(holdout.get("boundaryVersion"), maximum=128)
            or any(
                not _is_sha256(holdout.get(field))
                for field in (
                    "canonicalSha256",
                    "questionFingerprint",
                    "sourceSha256",
                )
            )
            or holdout.get("excluded") is not False
            or not _is_int(holdout.get("maximumSimilarityBits"))
            or not 0 <= holdout["maximumSimilarityBits"] <= 64
            or not _is_int(holdout.get("similarityThresholdBits"))
            or not 0 <= holdout["similarityThresholdBits"] <= 64
            or not _is_int(holdout.get("recordCount"))
            or not 0 < holdout["recordCount"] <= 1_000_000
        ):
            raise ReviewedCacheReleaseError("manifest_invalid")
        key = (
            item["semanticContentSha256"],
            item["cacheContentSha256"],
            item["approvalRecordSha256"],
        )
        sort_keys.append(key)
        semantic_hashes.append(key[0])
        cache_hashes.append(key[1])
        approval_hashes.append(key[2])
        item_row_hashes.append(item["rowSha256"])
    if (
        sort_keys != sorted(sort_keys)
        or len(set(cache_hashes)) != row_count
        or len(set(semantic_hashes)) != row_count
        or len(set(approval_hashes)) != row_count
        or len(set(item_row_hashes)) != row_count
        or sorted(item_row_hashes) != row_hashes
    ):
        raise ReviewedCacheReleaseError("manifest_invalid")

    canonical_input = {
        "items": [
            {
                "approvalRecordSha256": item["approvalRecordSha256"],
                "cacheContentSha256": item["cacheContentSha256"],
                "semanticContentSha256": item["semanticContentSha256"],
            }
            for item in items
        ],
        "schemaVersion": BUILD_INPUT_SCHEMA_VERSION,
    }
    if _sha256_json(canonical_input) != manifest["buildInputCanonicalSha256"]:
        raise ReviewedCacheReleaseError("manifest_invalid")
    logical = {
        "buildInputCanonicalSha256": manifest["buildInputCanonicalSha256"],
        "items": items,
        "runtime": runtime,
        "schemaVersion": LOGICAL_SCHEMA_VERSION,
    }
    if _sha256_json(logical) != manifest["logicalContentSha256"]:
        raise ReviewedCacheReleaseError("manifest_invalid")


def _expected_runtime(
    compiler: QuestionCompiler,
    model_manifest: PinnedSlmManifest,
) -> dict[str, Any]:
    return {
        "adapterIdentityReceiptSha256": (
            model_manifest.adapter_identity_receipt_sha256
        ),
        "curriculumId": compiler.curriculum.curriculum_id,
        "curriculumSha256": CURRICULUM_V1_SHA256,
        "generatorIdentityReceiptSha256": (
            model_manifest.generator_identity_receipt_sha256
        ),
        "ggufSha256": model_manifest.gguf_sha256,
        "modelId": model_manifest.model_id,
        "modelSha256": model_manifest.model_sha256,
        "promptTemplateSha256": model_manifest.prompt_template_sha256,
        "registryId": compiler.registry.registry_id,
        "registrySha256": PROCEDURE_REGISTRY_V1_SHA256,
    }


def _holdout_payload(hit: ReviewedCacheHit) -> dict[str, Any]:
    receipt = hit.bundle.blueprint.holdout_receipt
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


def _expected_item(hit: ReviewedCacheHit) -> dict[str, Any]:
    provenance = hit.bundle.provenance
    return {
        "adapterIdentityReceiptSha256": (
            provenance.adapter_identity_receipt_sha256
        ),
        "approvalRecordSha256": hit.approval_record_sha256,
        "cacheContentSha256": hit.cache_content_sha256,
        "decision": "approved",
        "generatorIdentityReceiptSha256": (
            provenance.generator_identity_receipt_sha256
        ),
        "ggufSha256": provenance.gguf_sha256,
        "holdoutReceipt": _holdout_payload(hit),
        "modelSha256": provenance.model_sha256,
        "promptSha256": provenance.prompt_sha256,
        "promptTemplateSha256": provenance.prompt_template_sha256,
        "registryId": provenance.registry_id,
        "reviewDecisionReceiptSha256": hit.review_decision_receipt_sha256,
        "reviewedAtUtc": hit.reviewed_at_utc,
        "reviewerAlias": hit.reviewer_alias,
        "rowSha256": hit.cache_row_sha256,
        "semanticContentSha256": hit.bundle.semantic_content_sha256,
        "verifierReceiptSha256": provenance.verifier_receipt_sha256,
        "verifierVersion": provenance.verifier_version,
    }


def _close_cache_bounded(cache: ReviewedCache, *, attempts: int = 2) -> bool:
    for _attempt in range(attempts):
        try:
            cache.close()
            return True
        except BaseException:
            pass
    return False


def _descriptor_is_still_owned(record: _OwnedDescriptor) -> bool | None:
    try:
        details = os.fstat(record.descriptor)
    except OSError as error:
        if error.errno == errno.EBADF:
            return False
        return None
    if record.device is None:
        _bind_owned_descriptor(record, details)
        return True
    return (
        details.st_dev == record.device
        and details.st_ino == record.inode
        and stat.S_IFMT(details.st_mode) == record.file_type
        and details.st_uid == record.owner
    )


def _attempt_owned_descriptor_close(
    record: _OwnedDescriptor,
) -> tuple[bool, BaseException | None]:
    """Close only a still-identical FD and report whether ownership ended.

    A successful ``close`` is removed from ownership immediately.  If close is
    interrupted (including EINTR), fstat decides whether the exact original
    descriptor remains retryable.  EBADF or a different dev/inode/type/owner
    means the numeric FD is no longer ours and must never be closed again.
    This also prevents a later retry from closing a foreign FD that reused the
    same integer.  The unavoidable same-inode reuse case is constrained by
    removing every confirmed successful close before control can be yielded.
    """

    try:
        still_owned = _descriptor_is_still_owned(record)
    except BaseException as error:
        return False, error
    if still_owned is False:
        return True, None
    if still_owned is None:
        return False, OSError(errno.EIO, "descriptor identity unavailable")
    try:
        os.close(record.descriptor)
    except BaseException as error:
        try:
            still_owned = _descriptor_is_still_owned(record)
        except BaseException:
            return False, error
        return still_owned is False, error
    return True, None


def _close_owned_record_or_raise(
    record: _OwnedDescriptor,
    ledger: list[_OwnedDescriptor],
) -> None:
    ended, error = _attempt_owned_descriptor_close(record)
    if ended and record in ledger:
        ledger.remove(record)
    if error is not None:
        raise error
    if not ended:
        raise OSError(errno.EIO, "owned descriptor close failed")


def _cleanup_owned_descriptors_bounded(
    records: tuple[_OwnedDescriptor, ...] | list[_OwnedDescriptor],
    *,
    attempts: int = 2,
) -> tuple[_OwnedDescriptor, ...]:
    """Best-effort cleanup that never masks a primary open failure."""

    remaining = list(records)
    for _attempt in range(attempts):
        if not remaining:
            break
        for record in tuple(reversed(remaining)):
            ended, _error = _attempt_owned_descriptor_close(record)
            if ended and record in remaining:
                remaining.remove(record)
    return tuple(remaining)


def _open_and_audit_cache(
    database_descriptor: int,
    *,
    compiler: QuestionCompiler,
    model_manifest: PinnedSlmManifest,
    manifest: dict[str, Any],
) -> ReviewedCache:
    cache: ReviewedCache | None = None
    try:
        cache = ReviewedCache.open_learner_fd(
            database_descriptor,
            compiler=compiler,
            manifest=model_manifest,
        )
        integrity = tuple(
            str(row[0])
            for row in cache._connection.execute(
                "PRAGMA integrity_check"
            ).fetchall()
        )
        if integrity != ("ok",):
            raise ValueError("integrity")
        rows = cache._connection.execute(
            "SELECT * FROM reviewed_questions "
            "ORDER BY semantic_content_sha256, cache_content_sha256"
        ).fetchall()
        if len(rows) != manifest["database"]["rowCount"]:
            raise ValueError("row count")
        hits = [cache._validate_row(row) for row in rows]
    except (ReviewedCacheError, sqlite3.Error, TypeError, ValueError, AttributeError):
        if cache is not None:
            _close_cache_bounded(cache)
        raise ReviewedCacheReleaseError("database_invalid") from None
    except BaseException:
        if cache is not None:
            _close_cache_bounded(cache)
        raise

    expected_items = [_expected_item(hit) for hit in hits]
    actual_items = manifest["items"]
    if (
        actual_items != expected_items
        or manifest["database"]["rowSha256s"]
        != sorted(hit.cache_row_sha256 for hit in hits)
    ):
        cache.close()
        raise ReviewedCacheReleaseError("row_manifest_mismatch")
    return cache


class ReviewedCacheRelease:
    """One validated immutable generation and its exact read-only cache."""

    def __init__(
        self,
        *,
        cache: ReviewedCache,
        generation_id: str,
        descriptors: tuple[_OwnedDescriptor, ...],
    ):
        self.cache = cache
        self.generation_id = generation_id
        self._descriptors = descriptors
        self._cache_closed = False
        self._closed = False

    @classmethod
    def open_current(
        cls,
        root: str | Path,
        *,
        compiler: QuestionCompiler,
        model_manifest: PinnedSlmManifest,
    ) -> "ReviewedCacheRelease":
        return cls.open_pointer(
            root,
            "current.json",
            compiler=compiler,
            model_manifest=model_manifest,
        )

    @classmethod
    def open_pointer(
        cls,
        root: str | Path,
        pointer_name: str,
        *,
        compiler: QuestionCompiler,
        model_manifest: PinnedSlmManifest,
    ) -> "ReviewedCacheRelease":
        release_root = _normalized_absolute_root(root)
        safe_pointer_name = _validate_pointer_name(pointer_name)
        descriptors: list[_OwnedDescriptor] = []
        cache: ReviewedCache | None = None
        success = False
        try:
            root_record, root_identity = _open_release_root(
                release_root,
                descriptors,
            )
            root_descriptor = root_record.descriptor
            pointer_record, pointer_identity = _open_owned_file(
                root_descriptor,
                safe_pointer_name,
                parent_device=root_identity.device,
                maximum_bytes=_MAX_POINTER_BYTES,
                code="pointer_invalid",
                ledger=descriptors,
            )
            pointer_descriptor = pointer_record.descriptor
            pointer_raw = _read_bounded(
                pointer_descriptor,
                pointer_identity,
                maximum_bytes=_MAX_POINTER_BYTES,
                code="pointer_invalid",
            )
            _require_name_identity(
                root_descriptor,
                safe_pointer_name,
                pointer_identity,
                code="pointer_invalid",
            )
            generation_id, manifest_sha256 = _decode_pointer(pointer_raw)

            generations_record, generations_identity = (
                _open_child_directory(
                    root_descriptor,
                    "generations",
                    parent_device=root_identity.device,
                    exact_mode=None,
                    ledger=descriptors,
                )
            )
            generations_descriptor = generations_record.descriptor
            generation_record, generation_identity = (
                _open_child_directory(
                    generations_descriptor,
                    generation_id,
                    parent_device=root_identity.device,
                    exact_mode=0o500,
                    ledger=descriptors,
                )
            )
            generation_descriptor = generation_record.descriptor
            if _directory_entries(generation_descriptor) != _GENERATION_FILES:
                raise ReviewedCacheReleaseError("generation_invalid")

            manifest_record, manifest_identity = _open_owned_file(
                generation_descriptor,
                "reviewed_cache_manifest.json",
                parent_device=root_identity.device,
                maximum_bytes=_MAX_MANIFEST_BYTES,
                code="generation_invalid",
                ledger=descriptors,
            )
            manifest_descriptor = manifest_record.descriptor
            database_record, database_identity = _open_owned_file(
                generation_descriptor,
                "reviewed_cache.sqlite3",
                parent_device=root_identity.device,
                maximum_bytes=_MAX_DATABASE_BYTES,
                code="generation_invalid",
                ledger=descriptors,
            )
            database_descriptor = database_record.descriptor

            manifest_raw = _read_bounded(
                manifest_descriptor,
                manifest_identity,
                maximum_bytes=_MAX_MANIFEST_BYTES,
                code="manifest_invalid",
            )
            _require_name_identity(
                generation_descriptor,
                "reviewed_cache_manifest.json",
                manifest_identity,
                code="manifest_invalid",
            )
            if _sha256_bytes(manifest_raw) != manifest_sha256:
                raise ReviewedCacheReleaseError("manifest_invalid")
            manifest = _decode_canonical_object(
                manifest_raw,
                code="manifest_invalid",
            )
            _validate_manifest_structure(manifest)
            try:
                expected_runtime = _expected_runtime(compiler, model_manifest)
            except (AttributeError, TypeError, ValueError):
                raise ReviewedCacheReleaseError(
                    "runtime_receipt_mismatch"
                ) from None
            if manifest["runtime"] != expected_runtime:
                raise ReviewedCacheReleaseError("runtime_receipt_mismatch")

            database_sha256, database_size = _hash_file_descriptor(
                database_descriptor,
                database_identity,
            )
            if (
                manifest["database"]["sha256"] != database_sha256
                or manifest["database"]["sizeBytes"] != database_size
            ):
                raise ReviewedCacheReleaseError("database_invalid")
            _require_name_identity(
                generation_descriptor,
                "reviewed_cache.sqlite3",
                database_identity,
                code="database_invalid",
            )

            cache = _open_and_audit_cache(
                database_descriptor,
                compiler=compiler,
                model_manifest=model_manifest,
                manifest=manifest,
            )

            _require_descriptor_identity(
                pointer_descriptor,
                pointer_identity,
                code="pointer_changed",
            )
            _require_name_identity(
                root_descriptor,
                safe_pointer_name,
                pointer_identity,
                code="pointer_changed",
            )
            for descriptor, expected, code in (
                (manifest_descriptor, manifest_identity, "manifest_invalid"),
                (database_descriptor, database_identity, "database_invalid"),
                (generation_descriptor, generation_identity, "generation_invalid"),
                (
                    generations_descriptor,
                    generations_identity,
                    "generation_invalid",
                ),
                (root_descriptor, root_identity, "unsafe_release_path"),
            ):
                _require_descriptor_identity(descriptor, expected, code=code)
            _require_name_identity(
                generation_descriptor,
                "reviewed_cache_manifest.json",
                manifest_identity,
                code="manifest_invalid",
            )
            _require_name_identity(
                generation_descriptor,
                "reviewed_cache.sqlite3",
                database_identity,
                code="database_invalid",
            )
            _require_name_identity(
                generations_descriptor,
                generation_id,
                generation_identity,
                code="generation_invalid",
            )
            _require_name_identity(
                root_descriptor,
                "generations",
                generations_identity,
                code="generation_invalid",
            )
            if _directory_entries(generation_descriptor) != _GENERATION_FILES:
                raise ReviewedCacheReleaseError("generation_invalid")
            rebound_record, rebound_identity = _open_release_root(
                release_root,
                descriptors,
            )
            if rebound_identity != root_identity:
                raise ReviewedCacheReleaseError("unsafe_release_path")
            _close_owned_record_or_raise(rebound_record, descriptors)

            release = cls(
                cache=cache,
                generation_id=generation_id,
                descriptors=tuple(descriptors),
            )
            success = True
            return release
        except ReviewedCacheReleaseError:
            raise
        except Exception:
            raise ReviewedCacheReleaseError("release_invalid") from None
        finally:
            if not success:
                if cache is not None:
                    _close_cache_bounded(cache)
                _cleanup_owned_descriptors_bounded(descriptors)

    def close(self) -> None:
        if self._closed:
            return
        if not self._cache_closed:
            try:
                self.cache.close()
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                raise
            except BaseException:
                raise ReviewedCacheReleaseError(
                    "release_close_failed"
                ) from None
            self._cache_closed = True

        remaining = list(self._descriptors)
        ordinary_failure = False
        for record in tuple(reversed(remaining)):
            ended, error = _attempt_owned_descriptor_close(record)
            if ended and record in remaining:
                remaining.remove(record)
            self._descriptors = tuple(remaining)
            if error is None:
                continue
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise error
            ordinary_failure = True
        if self._descriptors or ordinary_failure:
            raise ReviewedCacheReleaseError("release_close_failed") from None
        self._closed = True

    def __enter__(self) -> "ReviewedCacheRelease":
        if self._closed:
            raise ReviewedCacheReleaseError("release_is_closed")
        return self

    def __exit__(self, exc_type: object, _exc: object, _traceback: object) -> None:
        try:
            self.close()
        except BaseException:
            if exc_type is None:
                raise
            try:
                self.close()
            except BaseException:
                pass


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "POINTER_SCHEMA_VERSION",
    "ReviewedCacheRelease",
    "ReviewedCacheReleaseError",
]
