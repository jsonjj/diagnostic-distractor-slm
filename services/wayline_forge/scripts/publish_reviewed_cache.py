"""Publish one immutable reviewed-cache generation and atomic strict pointer.

The release root, ``generations`` directory, and empty 0600 ``.publish.lock``
must be provisioned by packaging.  This module never deletes final generations.
Random staging and pointer leaves are cleaned only while their names still bind
the exact owner-created inode.  A malicious concurrent process with the same
uid remains outside Python/macOS pathname-race guarantees.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import stat
from typing import Any, NoReturn

from services.wayline_forge.app.providers.distractor import PinnedSlmManifest
from services.wayline_forge.app.question_kernel import QuestionCompiler
from services.wayline_forge.app.reviewed_cache_release import (
    POINTER_SCHEMA_VERSION,
    ReviewedCacheRelease,
    ReviewedCacheReleaseError,
)
from services.wayline_forge.scripts.build_reviewed_cache import (
    CacheBuildError,
    build_reviewed_cache,
)


_LOCK_NAME = ".publish.lock"
_GENERATIONS_NAME = "generations"
_CURRENT_POINTER = "current.json"
_DATABASE_NAME = "reviewed_cache.sqlite3"
_MANIFEST_NAME = "reviewed_cache_manifest.json"
_GENERATION_ENTRIES = {_DATABASE_NAME, _MANIFEST_NAME}
_SHA256_LENGTH = 64


class CachePublicationError(RuntimeError):
    """Stable, non-sensitive reviewed-cache publication failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PublishedCacheGeneration:
    generation_id: str
    pointer_sha256: str
    manifest_sha256: str
    database_sha256: str
    item_count: int


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int
    file_type: int
    mode: int
    owner: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


def _identity(details: os.stat_result) -> _Identity:
    return _Identity(
        device=details.st_dev,
        inode=details.st_ino,
        file_type=stat.S_IFMT(details.st_mode),
        mode=stat.S_IMODE(details.st_mode),
        owner=details.st_uid,
        links=details.st_nlink,
        size=details.st_size,
        modified_ns=details.st_mtime_ns,
        changed_ns=details.st_ctime_ns,
    )


@dataclass(eq=False, slots=True)
class _OwnedDescriptor:
    descriptor: int
    device: int | None = None
    inode: int | None = None
    file_type: int | None = None
    owner: int | None = None
    leaf_kind: str | None = None
    leaf_parent_descriptor: int | None = None
    leaf_name: str | None = None


@dataclass(slots=True)
class _CleanupOutcome:
    ordinary_failure: bool = False
    special_failure: BaseException | None = None

    def record(self, failure: BaseException) -> None:
        if isinstance(failure, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            if self.special_failure is None:
                self.special_failure = failure
            return
        self.ordinary_failure = True


def _register_provisional_descriptor(
    records: list[_OwnedDescriptor],
    descriptor: int,
    *,
    leaf_kind: str | None = None,
    leaf_parent_descriptor: int | None = None,
    leaf_name: str | None = None,
) -> _OwnedDescriptor:
    record = _OwnedDescriptor(
        descriptor=descriptor,
        leaf_kind=leaf_kind,
        leaf_parent_descriptor=leaf_parent_descriptor,
        leaf_name=leaf_name,
    )
    records.append(record)
    return record


def _bind_owned_descriptor(
    record: _OwnedDescriptor,
    details: os.stat_result,
) -> None:
    record.device = details.st_dev
    record.inode = details.st_ino
    record.file_type = stat.S_IFMT(details.st_mode)
    record.owner = details.st_uid


def _descriptor_is_still_owned(
    record: _OwnedDescriptor,
) -> tuple[bool | None, BaseException | None]:
    try:
        details = os.fstat(record.descriptor)
    except BaseException as failure:
        if isinstance(failure, OSError) and failure.errno == errno.EBADF:
            return False, None
        return None, failure
    if record.device is None:
        _bind_owned_descriptor(record, details)
    return (
        details.st_dev == record.device
        and details.st_ino == record.inode
        and stat.S_IFMT(details.st_mode) == record.file_type
        and details.st_uid == record.owner
    ), None


def _attempt_owned_descriptor_close(
    record: _OwnedDescriptor,
) -> tuple[bool, BaseException | None]:
    """Close only the exact acquired FD, surviving numeric-descriptor reuse."""

    still_owned, probe_failure = _descriptor_is_still_owned(record)
    if probe_failure is not None:
        return False, probe_failure
    if still_owned is False:
        return True, None
    if still_owned is None:
        return False, OSError(errno.EIO, "descriptor identity unavailable")
    try:
        os.close(record.descriptor)
    except BaseException as failure:
        still_owned, _probe_failure = _descriptor_is_still_owned(record)
        return still_owned is False, failure
    return True, None


def _discard_owned_descriptor(
    records: list[_OwnedDescriptor],
    record: _OwnedDescriptor,
) -> None:
    if record in records:
        records.remove(record)


def _close_owned_descriptor_now(
    records: list[_OwnedDescriptor],
    record: _OwnedDescriptor,
) -> None:
    ended, failure = _attempt_owned_descriptor_close(record)
    if ended:
        _discard_owned_descriptor(records, record)
    if failure is not None:
        raise failure
    if not ended:
        raise OSError(errno.EIO, "descriptor close incomplete")


def _attempt_owned_lock_release(
    record: _OwnedDescriptor,
) -> tuple[bool, BaseException | None]:
    still_owned, probe_failure = _descriptor_is_still_owned(record)
    if probe_failure is not None:
        return False, probe_failure
    if still_owned is False:
        return True, None
    if still_owned is None:
        return False, OSError(errno.EIO, "lock identity unavailable")
    try:
        fcntl.flock(record.descriptor, fcntl.LOCK_UN)
    except BaseException as failure:
        still_owned, _probe_failure = _descriptor_is_still_owned(record)
        return still_owned is False, failure
    return True, None


def _cleanup_owned_descriptors_bounded(
    records: list[_OwnedDescriptor],
    *,
    lock_record: _OwnedDescriptor | None,
    outcome: _CleanupOutcome,
    selected_records: list[_OwnedDescriptor] | None = None,
    attempts: int = 2,
) -> None:
    lock_released = lock_record is None
    remaining = list(records if selected_records is None else selected_records)
    for _attempt in range(attempts):
        if lock_record is not None and not lock_released:
            ended, failure = _attempt_owned_lock_release(lock_record)
            if ended:
                lock_released = True
            if failure is not None:
                outcome.record(failure)
        for record in tuple(reversed(remaining)):
            ended, failure = _attempt_owned_descriptor_close(record)
            if ended:
                _discard_owned_descriptor(records, record)
                if record in remaining:
                    remaining.remove(record)
                if record == lock_record:
                    lock_released = True
            if failure is not None:
                outcome.record(failure)
        if not remaining:
            break
    if remaining or not lock_released:
        outcome.ordinary_failure = True


def _cleanup_registered_leaf(record: _OwnedDescriptor) -> None:
    if record.leaf_kind is None:
        return
    still_owned, probe_failure = _descriptor_is_still_owned(record)
    if probe_failure is not None:
        raise probe_failure
    parent = record.leaf_parent_descriptor
    name = record.leaf_name
    if parent is None or name is None:
        raise OSError(errno.EIO, "owned leaf metadata incomplete")
    actual = _stat_identity(parent, name)
    if (
        actual is None
        or actual.device != record.device
        or actual.inode != record.inode
        or actual.owner != record.owner
    ):
        record.leaf_kind = None
        record.leaf_parent_descriptor = None
        record.leaf_name = None
        return
    if still_owned is not True:
        raise OSError(errno.EIO, "owned leaf descriptor unavailable")
    if record.leaf_kind == "pointer":
        os.unlink(name, dir_fd=parent)
    elif record.leaf_kind == "stage":
        os.fchmod(record.descriptor, 0o700)
        for entry in os.listdir(record.descriptor):
            details = os.stat(
                entry,
                dir_fd=record.descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISDIR(details.st_mode):
                raise OSError(errno.EIO, "nested stage directory")
            os.unlink(entry, dir_fd=record.descriptor)
        os.rmdir(name, dir_fd=parent)
    else:
        raise OSError(errno.EIO, "unknown owned leaf")
    record.leaf_kind = None
    record.leaf_parent_descriptor = None
    record.leaf_name = None


def _cleanup_owned_leaves_bounded(
    records: list[_OwnedDescriptor],
    *,
    outcome: _CleanupOutcome,
    selected_records: list[_OwnedDescriptor] | None = None,
    attempts: int = 2,
) -> None:
    remaining = [
        record
        for record in (records if selected_records is None else selected_records)
        if record.leaf_kind is not None
    ]
    for _attempt in range(attempts):
        for record in tuple(reversed(remaining)):
            try:
                _cleanup_registered_leaf(record)
            except BaseException as failure:
                outcome.record(failure)
            if record.leaf_kind is None and record in remaining:
                remaining.remove(record)
        if not remaining:
            break
    if remaining:
        outcome.ordinary_failure = True


def _raise_after_acquisition_failure(
    failure: BaseException,
    records: list[_OwnedDescriptor],
    *,
    acquired_records: list[_OwnedDescriptor] | None = None,
    lock_record: _OwnedDescriptor | None = None,
) -> NoReturn:
    """Preserve a pre-ledger primary while exhausting exact-owned cleanup."""

    outcome = _CleanupOutcome()
    try:
        _cleanup_owned_leaves_bounded(
            records,
            outcome=outcome,
            selected_records=acquired_records,
        )
    except BaseException as cleanup_failure:
        outcome.record(cleanup_failure)
        outcome.ordinary_failure = True
    try:
        _cleanup_owned_descriptors_bounded(
            records,
            lock_record=lock_record,
            outcome=outcome,
            selected_records=acquired_records,
        )
    except BaseException as cleanup_failure:
        outcome.record(cleanup_failure)
        outcome.ordinary_failure = True

    if isinstance(
        failure,
        (CachePublicationError, KeyboardInterrupt, SystemExit, GeneratorExit),
    ):
        primary = failure
        primary_traceback = failure.__traceback__
    else:
        primary = CachePublicationError("publication_failed")
        primary_traceback = None
    primary_with_traceback = primary.with_traceback(primary_traceback)
    if outcome.ordinary_failure or outcome.special_failure is not None:
        raise primary_with_traceback from CachePublicationError(
            "publication_cleanup_failed"
        )
    raise primary_with_traceback from None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalized_absolute_path(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise CachePublicationError("publication_failed")
    raw = os.fspath(value)
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or not os.path.isabs(raw)
        or os.path.normpath(raw) != raw
    ):
        raise CachePublicationError("publication_failed")
    return Path(raw)


def _directory_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _open_absolute_directory(
    path: Path,
    *,
    records: list[_OwnedDescriptor],
) -> tuple[int, _Identity, _OwnedDescriptor]:
    start_index = len(records)
    try:
        descriptor = os.open(path.anchor, _directory_flags())
        current_record = _register_provisional_descriptor(records, descriptor)
        details = os.fstat(descriptor)
        _bind_owned_descriptor(current_record, details)
        for component in path.parts[1:]:
            next_descriptor = os.open(
                component,
                _directory_flags(),
                dir_fd=descriptor,
            )
            next_record = _register_provisional_descriptor(
                records,
                next_descriptor,
            )
            next_details = os.fstat(next_descriptor)
            _bind_owned_descriptor(next_record, next_details)
            _close_owned_descriptor_now(records, current_record)
            descriptor = next_descriptor
            details = next_details
            current_record = next_record
        identity = _identity(details)
        if (
            not stat.S_ISDIR(details.st_mode)
            or identity.owner != os.getuid()
            or identity.mode & 0o077
            or identity.mode & 0o700 != 0o700
        ):
            raise CachePublicationError("publication_failed")
        return descriptor, identity, current_record
    except BaseException as failure:
        _raise_after_acquisition_failure(
            failure,
            records,
            acquired_records=list(records[start_index:]),
        )


def _open_directory_at(
    parent_descriptor: int,
    name: str,
    *,
    device: int,
    records: list[_OwnedDescriptor],
) -> tuple[int, _Identity, _OwnedDescriptor]:
    start_index = len(records)
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
        record = _register_provisional_descriptor(records, descriptor)
        details = os.fstat(descriptor)
        _bind_owned_descriptor(record, details)
        identity = _identity(details)
        if (
            not stat.S_ISDIR(details.st_mode)
            or identity.device != device
            or identity.owner != os.getuid()
            or identity.mode & 0o077
            or identity.mode & 0o700 != 0o700
        ):
            raise CachePublicationError("publication_failed")
        return descriptor, identity, record
    except BaseException as failure:
        _raise_after_acquisition_failure(
            failure,
            records,
            acquired_records=list(records[start_index:]),
        )


def _open_lock(
    root_descriptor: int,
    *,
    device: int,
    records: list[_OwnedDescriptor],
) -> tuple[int, _Identity, _OwnedDescriptor]:
    start_index = len(records)
    lock_record: _OwnedDescriptor | None = None
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(_LOCK_NAME, flags, dir_fd=root_descriptor)
        acquired_record = _register_provisional_descriptor(records, descriptor)
        details = os.fstat(descriptor)
        _bind_owned_descriptor(acquired_record, details)
        identity = _identity(details)
        if (
            not stat.S_ISREG(details.st_mode)
            or identity.device != device
            or identity.owner != os.getuid()
            or identity.links != 1
            or identity.mode != 0o600
            or identity.size != 0
        ):
            raise CachePublicationError("publication_failed")
        lock_record = acquired_record
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise CachePublicationError("publication_busy") from None
            raise CachePublicationError("publication_failed") from None
        _require_name_identity(root_descriptor, _LOCK_NAME, identity)
        return descriptor, identity, acquired_record
    except BaseException as failure:
        _raise_after_acquisition_failure(
            failure,
            records,
            acquired_records=list(records[start_index:]),
            lock_record=lock_record,
        )


def _stat_identity(parent_descriptor: int, name: str) -> _Identity | None:
    try:
        details = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise CachePublicationError("publication_failed") from None
    return _identity(details)


def _require_name_identity(
    parent_descriptor: int,
    name: str,
    expected: _Identity,
) -> None:
    actual = _stat_identity(parent_descriptor, name)
    if actual != expected:
        raise CachePublicationError("publication_failed")


def _same_owned_inode(actual: _Identity | None, expected: _Identity) -> bool:
    return bool(
        actual is not None
        and actual.device == expected.device
        and actual.inode == expected.inode
        and actual.owner == expected.owner
    )


def _require_same_owned_inode(
    parent_descriptor: int,
    name: str,
    expected: _Identity,
) -> None:
    if not _same_owned_inode(_stat_identity(parent_descriptor, name), expected):
        raise CachePublicationError("publication_failed")


def _create_stage(
    generations_descriptor: int,
    *,
    device: int,
    generations_path: Path,
    records: list[_OwnedDescriptor],
) -> tuple[str, Path, int, _Identity, _OwnedDescriptor]:
    for _attempt in range(32):
        name = f".stage-{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=generations_descriptor)
        except FileExistsError:
            continue
        except OSError:
            raise CachePublicationError("publication_failed") from None
        start_index = len(records)
        descriptor = -1
        identity: _Identity | None = None
        try:
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=generations_descriptor,
            )
            record = _register_provisional_descriptor(
                records,
                descriptor,
                leaf_kind="stage",
                leaf_parent_descriptor=generations_descriptor,
                leaf_name=name,
            )
            details = os.fstat(descriptor)
            _bind_owned_descriptor(record, details)
            identity = _identity(details)
            if (
                not stat.S_ISDIR(details.st_mode)
                or identity.device != device
                or identity.owner != os.getuid()
                or identity.mode != 0o700
            ):
                raise CachePublicationError("publication_failed")
            return name, generations_path / name, descriptor, identity, record
        except BaseException as failure:
            _raise_after_acquisition_failure(
                failure,
                records,
                acquired_records=list(records[start_index:]),
            )
    raise CachePublicationError("publication_failed")


def _open_stage_file(
    stage_descriptor: int,
    name: str,
    *,
    device: int,
    expected_mode: int,
    records: list[_OwnedDescriptor],
) -> tuple[int, _Identity, _OwnedDescriptor]:
    start_index = len(records)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=stage_descriptor)
        record = _register_provisional_descriptor(records, descriptor)
        details = os.fstat(descriptor)
        _bind_owned_descriptor(record, details)
        identity = _identity(details)
        if (
            not stat.S_ISREG(details.st_mode)
            or identity.device != device
            or identity.owner != os.getuid()
            or identity.links != 1
            or identity.mode != expected_mode
            or identity.size <= 0
        ):
            raise CachePublicationError("publication_failed")
        return descriptor, identity, record
    except BaseException as failure:
        _raise_after_acquisition_failure(
            failure,
            records,
            acquired_records=list(records[start_index:]),
        )


def _write_all(descriptor: int, raw: bytes) -> None:
    remaining = memoryview(raw)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write")
        remaining = remaining[written:]


def _hash_descriptor(descriptor: int) -> tuple[str, int]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest(), size


def _fsync_database(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_manifest(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_staging_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_generations_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_pointer(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_release_root(descriptor: int) -> None:
    os.fsync(descriptor)


def _rename_exclusive(
    source_descriptor: int,
    source_name: str,
    destination_descriptor: int,
    destination_name: str,
) -> None:
    if platform.system() != "Darwin":
        raise OSError(errno.ENOTSUP, "exclusive rename unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = getattr(libc, "renameatx_np", None)
    if renameatx_np is None:
        raise OSError(errno.ENOTSUP, "exclusive rename unavailable")
    renameatx_np.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameatx_np.restype = ctypes.c_int
    result = renameatx_np(
        source_descriptor,
        os.fsencode(source_name),
        destination_descriptor,
        os.fsencode(destination_name),
        0x00000004,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _rename_staging_generation(
    generations_descriptor: int,
    stage_name: str,
    generation_id: str,
) -> None:
    _rename_exclusive(
        generations_descriptor,
        stage_name,
        generations_descriptor,
        generation_id,
    )


def _replace_pointer(root_descriptor: int, pointer_name: str) -> None:
    os.replace(
        pointer_name,
        _CURRENT_POINTER,
        src_dir_fd=root_descriptor,
        dst_dir_fd=root_descriptor,
    )


def _runtime_reopen(
    release_root: Path,
    *,
    compiler: QuestionCompiler,
    model_manifest: PinnedSlmManifest,
) -> ReviewedCacheRelease:
    return ReviewedCacheRelease.open_current(
        release_root,
        compiler=compiler,
        model_manifest=model_manifest,
    )


def _validate_candidate_pointer(
    release_root: Path,
    pointer_name: str,
    *,
    compiler: QuestionCompiler,
    model_manifest: PinnedSlmManifest,
    generation_id: str,
) -> None:
    with ReviewedCacheRelease.open_pointer(
        release_root,
        pointer_name,
        compiler=compiler,
        model_manifest=model_manifest,
    ) as release:
        if release.generation_id != generation_id:
            raise ReviewedCacheReleaseError("generation_invalid")


def _directory_entries(descriptor: int) -> set[str]:
    try:
        return set(os.listdir(descriptor))
    except OSError:
        raise CachePublicationError("publication_failed") from None


def _capture_current_pointer(
    root_descriptor: int,
    release_root: Path,
    *,
    compiler: QuestionCompiler,
    model_manifest: PinnedSlmManifest,
) -> _Identity | None:
    identity = _stat_identity(root_descriptor, _CURRENT_POINTER)
    if identity is None:
        return None
    try:
        with ReviewedCacheRelease.open_current(
            release_root,
            compiler=compiler,
            model_manifest=model_manifest,
        ):
            pass
    except ReviewedCacheReleaseError:
        raise CachePublicationError("publication_failed") from None
    _require_name_identity(root_descriptor, _CURRENT_POINTER, identity)
    return identity


def _create_pointer(
    root_descriptor: int,
    payload: bytes,
    *,
    device: int,
    records: list[_OwnedDescriptor],
) -> tuple[str, int, _Identity, _OwnedDescriptor]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(32):
        name = f"candidate-{secrets.token_hex(16)}.json"
        start_index = len(records)
        identity: _Identity | None = None
        try:
            try:
                descriptor = os.open(
                    name,
                    flags,
                    0o600,
                    dir_fd=root_descriptor,
                )
            except FileExistsError:
                continue
            record = _register_provisional_descriptor(
                records,
                descriptor,
                leaf_kind="pointer",
                leaf_parent_descriptor=root_descriptor,
                leaf_name=name,
            )
            details = os.fstat(descriptor)
            _bind_owned_descriptor(record, details)
            identity = _identity(details)
            _write_all(descriptor, payload)
            os.fchmod(descriptor, 0o400)
            _fsync_pointer(descriptor)
            details = os.fstat(descriptor)
            identity = _identity(details)
            if (
                not stat.S_ISREG(details.st_mode)
                or identity.device != device
                or identity.owner != os.getuid()
                or identity.links != 1
                or identity.mode != 0o400
                or identity.size != len(payload)
            ):
                raise CachePublicationError("publication_failed")
            return name, descriptor, identity, record
        except BaseException as failure:
            _raise_after_acquisition_failure(
                failure,
                records,
                acquired_records=list(records[start_index:]),
            )
    raise CachePublicationError("publication_failed")


def _cleanup_owned_pointer(
    root_descriptor: int,
    name: str | None,
    expected: _Identity | None,
) -> None:
    if name is None or expected is None:
        return
    try:
        actual = _stat_identity(root_descriptor, name)
    except CachePublicationError:
        return
    if not _same_owned_inode(actual, expected):
        return
    try:
        os.unlink(name, dir_fd=root_descriptor)
    except OSError:
        pass


def _cleanup_owned_stage(
    generations_descriptor: int,
    name: str | None,
    descriptor: int,
    expected: _Identity | None,
) -> None:
    if name is None or descriptor < 0 or expected is None:
        return
    try:
        actual = _stat_identity(generations_descriptor, name)
    except CachePublicationError:
        return
    if not _same_owned_inode(actual, expected):
        return
    try:
        os.fchmod(descriptor, 0o700)
        for entry in os.listdir(descriptor):
            details = os.stat(entry, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(details.st_mode):
                return
            os.unlink(entry, dir_fd=descriptor)
        os.rmdir(name, dir_fd=generations_descriptor)
    except OSError:
        pass


def _re_attest_current(
    root_descriptor: int,
    expected: _Identity | None,
) -> None:
    actual = _stat_identity(root_descriptor, _CURRENT_POINTER)
    if actual != expected:
        raise CachePublicationError("publication_failed")


def publish_reviewed_cache(
    input_path: str | Path,
    release_root: str | Path,
    *,
    compiler: QuestionCompiler,
    model_manifest: PinnedSlmManifest,
) -> PublishedCacheGeneration:
    """Build, promote, atomically select, and reopen one cache generation."""

    source = _normalized_absolute_path(input_path)
    root = _normalized_absolute_path(release_root)
    root_descriptor = -1
    generations_descriptor = -1
    lock_descriptor = -1
    stage_descriptor = -1
    stage_name: str | None = None
    stage_identity: _Identity | None = None
    pointer_descriptor = -1
    pointer_name: str | None = None
    pointer_identity: _Identity | None = None
    pointer_switch_may_have_occurred = False
    pointer_switched = False
    pointer_durable = False
    stage_promoted = False
    result: PublishedCacheGeneration | None = None
    owned_descriptors: list[_OwnedDescriptor] = []
    lock_record: _OwnedDescriptor | None = None
    primary_failure: BaseException | None = None
    primary_traceback = None
    primary_cause: CachePublicationError | None = None
    try:
        root_descriptor, root_identity, _root_record = _open_absolute_directory(
            root,
            records=owned_descriptors,
        )
        (
            generations_descriptor,
            generations_identity,
            _generations_record,
        ) = _open_directory_at(
            root_descriptor,
            _GENERATIONS_NAME,
            device=root_identity.device,
            records=owned_descriptors,
        )
        lock_descriptor, lock_identity, lock_record = _open_lock(
            root_descriptor,
            device=root_identity.device,
            records=owned_descriptors,
        )
        current_identity = _capture_current_pointer(
            root_descriptor,
            root,
            compiler=compiler,
            model_manifest=model_manifest,
        )
        (
            stage_name,
            stage_path,
            stage_descriptor,
            stage_identity,
            _stage_record,
        ) = _create_stage(
            generations_descriptor,
            device=root_identity.device,
            generations_path=root / _GENERATIONS_NAME,
            records=owned_descriptors,
        )

        try:
            _require_same_owned_inode(
                generations_descriptor,
                stage_name,
                stage_identity,
            )
            build_result = build_reviewed_cache(
                source,
                stage_path / _DATABASE_NAME,
                compiler=compiler,
                manifest=model_manifest,
            )
        except CacheBuildError:
            raise CachePublicationError("publication_failed") from None
        _require_same_owned_inode(
            generations_descriptor,
            stage_name,
            stage_identity,
        )

        database_descriptor, database_identity, database_record = _open_stage_file(
            stage_descriptor,
            _DATABASE_NAME,
            device=root_identity.device,
            expected_mode=0o400,
            records=owned_descriptors,
        )
        os.fchmod(database_descriptor, 0o400)
        _fsync_database(database_descriptor)
        database_sha256, database_size = _hash_descriptor(database_descriptor)
        database_details = os.fstat(database_descriptor)
        durable_database_identity = _identity(database_details)
        if (
            database_sha256 != build_result.database_sha256
            or database_size != build_result.database_size
            or not stat.S_ISREG(database_details.st_mode)
            or durable_database_identity.device != database_identity.device
            or durable_database_identity.inode != database_identity.inode
            or durable_database_identity.owner != database_identity.owner
            or durable_database_identity.links != 1
            or durable_database_identity.mode != 0o400
        ):
            raise CachePublicationError("publication_failed")
        _close_owned_descriptor_now(owned_descriptors, database_record)

        manifest_raw = build_result.manifest_json.encode("utf-8")
        if hashlib.sha256(manifest_raw).hexdigest() != build_result.manifest_sha256:
            raise CachePublicationError("publication_failed")
        manifest_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        manifest_flags |= getattr(os, "O_CLOEXEC", 0)
        manifest_flags |= getattr(os, "O_NOFOLLOW", 0)
        manifest_descriptor = os.open(
            _MANIFEST_NAME,
            manifest_flags,
            0o600,
            dir_fd=stage_descriptor,
        )
        manifest_record = _register_provisional_descriptor(
            owned_descriptors,
            manifest_descriptor,
        )
        manifest_details = os.fstat(manifest_descriptor)
        _bind_owned_descriptor(manifest_record, manifest_details)
        manifest_identity = _identity(manifest_details)
        _write_all(manifest_descriptor, manifest_raw)
        os.fchmod(manifest_descriptor, 0o400)
        _fsync_manifest(manifest_descriptor)
        details = os.fstat(manifest_descriptor)
        manifest_identity = _identity(details)
        if (
            not stat.S_ISREG(details.st_mode)
            or manifest_identity.device != root_identity.device
            or manifest_identity.owner != os.getuid()
            or manifest_identity.links != 1
            or manifest_identity.mode != 0o400
            or manifest_identity.size != len(manifest_raw)
        ):
            raise CachePublicationError("publication_failed")
        _close_owned_descriptor_now(owned_descriptors, manifest_record)

        if _directory_entries(stage_descriptor) != _GENERATION_ENTRIES:
            raise CachePublicationError("publication_failed")
        os.fchmod(stage_descriptor, 0o500)
        _fsync_staging_directory(stage_descriptor)
        stage_identity = _identity(os.fstat(stage_descriptor))
        if stage_identity.mode != 0o500 or stage_identity.device != root_identity.device:
            raise CachePublicationError("publication_failed")

        generation_id = "generation-" + build_result.manifest_sha256
        _require_same_owned_inode(
            generations_descriptor,
            stage_name,
            stage_identity,
        )
        try:
            _rename_staging_generation(
                generations_descriptor,
                stage_name,
                generation_id,
            )
        except OSError as error:
            if error.errno != errno.EEXIST:
                raise CachePublicationError("publication_failed") from None
        else:
            stage_promoted = True
            _fsync_generations_directory(generations_descriptor)

        pointer_payload = _canonical_json(
            {
                "generationId": generation_id,
                "manifestSha256": build_result.manifest_sha256,
                "schemaVersion": POINTER_SCHEMA_VERSION,
            }
        ).encode("utf-8")
        (
            pointer_name,
            pointer_descriptor,
            pointer_identity,
            pointer_record,
        ) = _create_pointer(
            root_descriptor,
            pointer_payload,
            device=root_identity.device,
            records=owned_descriptors,
        )
        _close_owned_descriptor_now(owned_descriptors, pointer_record)
        pointer_descriptor = -1
        try:
            _validate_candidate_pointer(
                root,
                pointer_name,
                compiler=compiler,
                model_manifest=model_manifest,
                generation_id=generation_id,
            )
        except (ReviewedCacheReleaseError, OSError):
            raise CachePublicationError("publication_failed") from None

        if not stage_promoted:
            _cleanup_owned_stage(
                generations_descriptor,
                stage_name,
                stage_descriptor,
                stage_identity,
            )
            stage_name = None
        _require_name_identity(root_descriptor, _LOCK_NAME, lock_identity)
        _re_attest_current(root_descriptor, current_identity)
        _require_name_identity(root_descriptor, pointer_name, pointer_identity)
        pointer_switch_may_have_occurred = True
        try:
            _replace_pointer(root_descriptor, pointer_name)
        except OSError:
            pointer_switch_may_have_occurred = False
            raise CachePublicationError("publication_failed") from None
        pointer_switched = True
        pointer_name = None
        try:
            _fsync_release_root(root_descriptor)
        except OSError:
            raise CachePublicationError("pointer_durability_uncertain") from None
        pointer_durable = True

        try:
            reopened = _runtime_reopen(
                root,
                compiler=compiler,
                model_manifest=model_manifest,
            )
            try:
                if reopened.generation_id != generation_id:
                    raise ReviewedCacheReleaseError("generation_invalid")
            finally:
                reopened.close()
        except ReviewedCacheReleaseError:
            raise CachePublicationError("published_release_unavailable") from None

        result = PublishedCacheGeneration(
            generation_id=generation_id,
            pointer_sha256=hashlib.sha256(pointer_payload).hexdigest(),
            manifest_sha256=build_result.manifest_sha256,
            database_sha256=build_result.database_sha256,
            item_count=build_result.item_count,
        )
    except CachePublicationError as failure:
        primary_failure = failure
        primary_traceback = failure.__traceback__
    except Exception:
        if pointer_switch_may_have_occurred:
            code = (
                "published_release_unavailable"
                if pointer_durable
                else "pointer_durability_uncertain"
            )
            primary_failure = CachePublicationError(code)
        else:
            primary_failure = CachePublicationError("publication_failed")
    except BaseException as failure:
        primary_failure = failure
        primary_traceback = failure.__traceback__
        if pointer_switch_may_have_occurred:
            code = (
                "published_release_unavailable"
                if pointer_durable
                else "pointer_durability_uncertain"
            )
            primary_cause = CachePublicationError(code)

    cleanup_outcome = _CleanupOutcome()
    if not pointer_switched:
        try:
            _cleanup_owned_pointer(
                root_descriptor,
                pointer_name,
                pointer_identity,
            )
        except BaseException as failure:
            cleanup_outcome.record(failure)
    if not stage_promoted:
        try:
            _cleanup_owned_stage(
                generations_descriptor,
                stage_name,
                stage_descriptor,
                stage_identity,
            )
        except BaseException as failure:
            cleanup_outcome.record(failure)
    try:
        _cleanup_owned_leaves_bounded(
            owned_descriptors,
            outcome=cleanup_outcome,
        )
    except BaseException as failure:
        cleanup_outcome.record(failure)
        cleanup_outcome.ordinary_failure = True
    try:
        _cleanup_owned_descriptors_bounded(
            owned_descriptors,
            lock_record=lock_record,
            outcome=cleanup_outcome,
        )
    except BaseException as failure:
        cleanup_outcome.record(failure)
        cleanup_outcome.ordinary_failure = True

    if primary_failure is not None:
        failure_with_traceback = primary_failure.with_traceback(primary_traceback)
        if primary_cause is not None:
            raise failure_with_traceback from primary_cause
        raise failure_with_traceback from None
    if cleanup_outcome.special_failure is not None:
        raise cleanup_outcome.special_failure from CachePublicationError(
            "publication_cleanup_failed"
        )
    if cleanup_outcome.ordinary_failure:
        raise CachePublicationError("publication_cleanup_failed") from None
    if result is None:
        raise CachePublicationError("publication_failed") from None
    return result


__all__ = [
    "CachePublicationError",
    "PublishedCacheGeneration",
    "publish_reviewed_cache",
]
