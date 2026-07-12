"""Build one immutable, authenticated export-input bundle.

The export notebook is the single source of truth for the input allowlist.
Notebook code is parsed as data and is never imported or executed.  The ZIP
and its receipt are sealed in a private staging directory and become visible
together through one exclusive directory rename.
"""

from __future__ import annotations

import ast
import ctypes
from dataclasses import dataclass, field
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import stat
from typing import Any
import unicodedata
import zipfile


EXPORT_INPUTS_RECEIPT_SCHEMA_VERSION = "wayline.export-inputs-receipt.v1"

_POLICY_NAME = "EXPECTED_INPUT_SHA256"
_EXPECTED_ITEM_COUNT = 22
_MAX_NOTEBOOK_BYTES = 16 * 1024 * 1024
_MAX_INPUT_MEMBER_BYTES = 25 * 1024 * 1024
_MAX_INPUT_TOTAL_BYTES = 100 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_BUNDLE_NAME = "wayline_export_inputs_v1.bundle"
_ARCHIVE_NAME = "wayline_export_inputs_v1.zip"
_RECEIPT_NAME = "wayline_export_inputs_v1.receipt.json"
_BUNDLE_ENTRIES = {_ARCHIVE_NAME, _RECEIPT_NAME}
_KNOWN_LEGACY_ARCHIVE_SHA256 = (
    "39f0b5c64e4a10f1c3e8ae1297b1b090bdeff0b82bd07bbed764a876db8d53db"
)
_KNOWN_LEGACY_RECEIPT_SHA256 = (
    "63cc0a4d01b6257755f3c54502974ef34b872eaef8a25e7686719074a9f82df1"
)
_SECRET_COMPONENT_MARKERS = (
    "credential",
    "private-key",
    "private_key",
    "secret",
)
_SECRET_COMPONENTS = {
    ".aws",
    ".env",
    ".git",
    ".gnupg",
    ".ssh",
    "api-key",
    "api_key",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "token",
    "tokens",
}


class ExportInputsError(RuntimeError):
    """Stable, non-sensitive export-input build failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExportInputsResult:
    archive_sha256: str
    archive_size: int
    item_count: int


@dataclass(frozen=True, slots=True)
class _Identity:
    device: int
    inode: int
    file_type: int
    mode: int
    owner: int
    group: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(eq=False, slots=True)
class _OwnedDescriptor:
    descriptor: int
    device: int | None = None
    inode: int | None = None
    file_type: int | None = None
    owner: int | None = None


@dataclass(eq=False, slots=True)
class _OwnedStage:
    parent_descriptor: int
    name: str
    identity: _Identity | None = None
    descriptor_record: _OwnedDescriptor | None = None
    children: dict[str, _Identity] = field(default_factory=dict)
    active: bool = True


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


@dataclass(slots=True)
class _DirectoryWalk:
    descriptor: int
    ledger: list[_OwnedDescriptor]
    records: list[_OwnedDescriptor]
    bindings: list[tuple[int, str, _Identity]]

    def verify(self, error_code: str) -> None:
        try:
            for parent, name, expected in self.bindings:
                details = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not _same_directory_binding(_identity(details), expected):
                    raise ExportInputsError(error_code)
            if not stat.S_ISDIR(os.fstat(self.descriptor).st_mode):
                raise ExportInputsError(error_code)
        except ExportInputsError:
            raise
        except OSError:
            raise ExportInputsError(error_code) from None

    def close(self) -> None:
        for record in reversed(self.records):
            if record in self.ledger:
                _close_owned_descriptor_now(self.ledger, record)
        self.records.clear()


@dataclass(frozen=True, slots=True)
class _VerifiedInput:
    path: str
    sha256: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class _LegacyArtifact:
    name: str
    identity: _Identity


def _identity(details: os.stat_result) -> _Identity:
    return _Identity(
        device=details.st_dev,
        inode=details.st_ino,
        file_type=stat.S_IFMT(details.st_mode),
        mode=stat.S_IMODE(details.st_mode),
        owner=details.st_uid,
        group=details.st_gid,
        links=details.st_nlink,
        size=details.st_size,
        modified_ns=details.st_mtime_ns,
        changed_ns=details.st_ctime_ns,
    )


def _same_path_identity(actual: _Identity, expected: _Identity) -> bool:
    return (
        actual.device == expected.device
        and actual.inode == expected.inode
        and actual.file_type == expected.file_type
        and actual.mode == expected.mode
        and actual.owner == expected.owner
        and actual.group == expected.group
        and actual.links == expected.links
        and actual.size == expected.size
        and actual.modified_ns == expected.modified_ns
        and actual.changed_ns == expected.changed_ns
    )


def _same_owned_inode(actual: _Identity, expected: _Identity) -> bool:
    return (
        actual.device == expected.device
        and actual.inode == expected.inode
        and actual.file_type == expected.file_type
        and actual.owner == expected.owner
    )


def _same_directory_binding(actual: _Identity, expected: _Identity) -> bool:
    """Compare stable directory binding fields, excluding entry metadata."""

    return (
        actual.device == expected.device
        and actual.inode == expected.inode
        and actual.file_type == expected.file_type == stat.S_IFDIR
        and actual.mode == expected.mode
        and actual.owner == expected.owner
        and actual.group == expected.group
    )


def _register_provisional_descriptor(
    ledger: list[_OwnedDescriptor],
    descriptor: int,
) -> _OwnedDescriptor:
    record = _OwnedDescriptor(descriptor=descriptor)
    ledger.append(record)
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
        still_owned, _ = _descriptor_is_still_owned(record)
        return still_owned is False, failure
    return True, None


def _close_owned_descriptor_now(
    ledger: list[_OwnedDescriptor],
    record: _OwnedDescriptor,
) -> None:
    ended, failure = _attempt_owned_descriptor_close(record)
    if ended and record in ledger:
        ledger.remove(record)
    if isinstance(failure, (KeyboardInterrupt, SystemExit, GeneratorExit)):
        raise failure
    if failure is not None or not ended:
        raise ExportInputsError("export_inputs_cleanup_failed") from None


def _cleanup_owned_descriptors_bounded(
    ledger: list[_OwnedDescriptor],
    *,
    outcome: _CleanupOutcome,
    attempts: int = 2,
) -> None:
    remaining = list(ledger)
    for _attempt in range(attempts):
        for record in tuple(reversed(remaining)):
            ended, failure = _attempt_owned_descriptor_close(record)
            if ended:
                if record in ledger:
                    ledger.remove(record)
                remaining.remove(record)
            if failure is not None:
                outcome.record(failure)
        if not remaining:
            break
    if remaining:
        outcome.ordinary_failure = True


def _directory_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _normalized_root(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise ExportInputsError("unsafe_export_root")
    raw = os.fspath(value)
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or not os.path.isabs(raw)
        or os.path.normpath(raw) != raw
    ):
        raise ExportInputsError("unsafe_export_root")
    return Path(raw)


def _open_absolute_directory(
    path: Path,
    error_code: str,
    ledger: list[_OwnedDescriptor],
) -> _DirectoryWalk:
    records: list[_OwnedDescriptor] = []
    bindings: list[tuple[int, str, _Identity]] = []
    try:
        current = os.open(path.anchor, _directory_flags())
        record = _register_provisional_descriptor(ledger, current)
        records.append(record)
        details = os.fstat(current)
        _bind_owned_descriptor(record, details)
        for component in path.parts[1:]:
            child = os.open(component, _directory_flags(), dir_fd=current)
            child_record = _register_provisional_descriptor(ledger, child)
            records.append(child_record)
            child_details = os.fstat(child)
            _bind_owned_descriptor(child_record, child_details)
            expected = _identity(child_details)
            bound = _identity(
                os.stat(component, dir_fd=current, follow_symlinks=False)
            )
            if (
                not stat.S_ISDIR(child_details.st_mode)
                or not _same_path_identity(bound, expected)
            ):
                raise ExportInputsError(error_code)
            bindings.append((current, component, expected))
            current = child
        final = os.fstat(current)
        if (
            not stat.S_ISDIR(final.st_mode)
            or final.st_uid != os.getuid()
            or stat.S_IMODE(final.st_mode) & 0o022
        ):
            raise ExportInputsError(error_code)
        return _DirectoryWalk(current, ledger, records, bindings)
    except ExportInputsError:
        raise
    except OSError:
        raise ExportInputsError(error_code) from None


def _open_relative_directory(
    root_descriptor: int,
    components: tuple[str, ...],
    error_code: str,
    ledger: list[_OwnedDescriptor],
) -> _DirectoryWalk:
    records: list[_OwnedDescriptor] = []
    bindings: list[tuple[int, str, _Identity]] = []
    current = root_descriptor
    try:
        for component in components:
            child = os.open(component, _directory_flags(), dir_fd=current)
            record = _register_provisional_descriptor(ledger, child)
            records.append(record)
            details = os.fstat(child)
            _bind_owned_descriptor(record, details)
            expected = _identity(details)
            bound = _identity(
                os.stat(component, dir_fd=current, follow_symlinks=False)
            )
            if (
                not stat.S_ISDIR(details.st_mode)
                or not _same_path_identity(bound, expected)
            ):
                raise ExportInputsError(error_code)
            bindings.append((current, component, expected))
            current = child
        return _DirectoryWalk(current, ledger, records, bindings)
    except ExportInputsError:
        raise
    except OSError:
        raise ExportInputsError(error_code) from None


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonstandard_number(_value: str) -> None:
    raise ValueError("nonstandard JSON number")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalized_relative_path(value: object, error_code: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ExportInputsError(error_code)
    if unicodedata.normalize("NFC", value) != value:
        raise ExportInputsError(error_code)
    if "\\" in value or ":" in value or value != value.strip():
        raise ExportInputsError(error_code)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ExportInputsError(error_code)
    return value


def _is_secret_path(path: str) -> bool:
    for component in PurePosixPath(path).parts:
        folded = component.casefold()
        if folded.startswith(".env") or folded in _SECRET_COMPONENTS:
            return True
        if any(marker in folded for marker in _SECRET_COMPONENT_MARKERS):
            return True
    return False


def _read_descriptor(
    descriptor: int,
    maximum_size: int,
    before: os.stat_result,
    error_code: str,
) -> bytes:
    chunks = bytearray()
    try:
        while len(chunks) <= maximum_size:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, maximum_size + 1 - len(chunks)),
            )
            if not chunk:
                break
            chunks.extend(chunk)
    except OSError:
        raise ExportInputsError(error_code) from None
    if len(chunks) > maximum_size or len(chunks) != before.st_size:
        raise ExportInputsError(error_code)
    return bytes(chunks)


def _read_file_at(
    parent_descriptor: int,
    name: str,
    *,
    maximum_size: int,
    error_code: str,
    ledger: list[_OwnedDescriptor],
    expected_mode: int | None = None,
) -> tuple[bytes, _Identity]:
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        record = _register_provisional_descriptor(ledger, descriptor)
        before = os.fstat(descriptor)
        _bind_owned_descriptor(record, before)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size < 0
            or before.st_size > maximum_size
            or (
                expected_mode is not None
                and stat.S_IMODE(before.st_mode) != expected_mode
            )
        ):
            raise ExportInputsError(error_code)
        payload = _read_descriptor(
            descriptor,
            maximum_size,
            before,
            error_code,
        )
        after = os.fstat(descriptor)
        bound = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        before_identity = _identity(before)
        after_identity = _identity(after)
        if (
            not _same_path_identity(before_identity, after_identity)
            or not _same_path_identity(after_identity, _identity(bound))
        ):
            raise ExportInputsError(error_code)
        _close_owned_descriptor_now(ledger, record)
        return payload, after_identity
    except ExportInputsError:
        raise
    except OSError:
        raise ExportInputsError(error_code) from None


def _read_relative_file(
    root_descriptor: int,
    relative: str,
    *,
    maximum_size: int,
    error_code: str,
    ledger: list[_OwnedDescriptor],
) -> bytes:
    components = tuple(PurePosixPath(relative).parts)
    walk = _open_relative_directory(
        root_descriptor,
        components[:-1],
        error_code,
        ledger,
    )
    payload, _ = _read_file_at(
        walk.descriptor,
        components[-1],
        maximum_size=maximum_size,
        error_code=error_code,
        ledger=ledger,
    )
    walk.verify(error_code)
    walk.close()
    return payload


def _decode_notebook(raw: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonstandard_number,
        )
    except (_DuplicateJsonKey, UnicodeError, ValueError, RecursionError):
        raise ExportInputsError("invalid_notebook_policy") from None
    if not isinstance(decoded, dict) or not isinstance(decoded.get("cells"), list):
        raise ExportInputsError("invalid_notebook_policy")
    return decoded


def _cell_source(cell: object) -> str:
    if not isinstance(cell, dict) or cell.get("cell_type") != "code":
        return ""
    source = cell.get("source")
    if isinstance(source, str):
        return source
    if isinstance(source, list) and all(isinstance(part, str) for part in source):
        return "".join(source)
    raise ExportInputsError("invalid_notebook_policy")


def _literal_policy(notebook: dict[str, Any]) -> dict[str, str]:
    candidates: list[ast.Dict] = []
    stored_name_count = 0
    for cell in notebook["cells"]:
        source = _cell_source(cell)
        if _POLICY_NAME not in source:
            continue
        try:
            module = ast.parse(source, mode="exec")
        except (SyntaxError, ValueError, RecursionError):
            raise ExportInputsError("invalid_notebook_policy") from None
        stored_name_count += sum(
            1
            for node in ast.walk(module)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id == _POLICY_NAME
        )
        for statement in module.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id == _POLICY_NAME
                and isinstance(statement.value, ast.Dict)
            ):
                candidates.append(statement.value)
    if stored_name_count != 1 or len(candidates) != 1:
        raise ExportInputsError("invalid_notebook_policy")

    dictionary = candidates[0]
    if any(key is None for key in dictionary.keys):
        raise ExportInputsError("invalid_notebook_policy")
    literal_keys: list[str] = []
    for key, value in zip(dictionary.keys, dictionary.values, strict=True):
        if (
            not isinstance(key, ast.Constant)
            or not isinstance(key.value, str)
            or not isinstance(value, ast.Constant)
            or not isinstance(value.value, str)
        ):
            raise ExportInputsError("invalid_notebook_policy")
        literal_keys.append(key.value)
    if len(literal_keys) != len(set(literal_keys)):
        raise ExportInputsError("invalid_input_allowlist")
    try:
        policy = ast.literal_eval(dictionary)
    except (ValueError, TypeError, MemoryError, RecursionError):
        raise ExportInputsError("invalid_notebook_policy") from None
    if not isinstance(policy, dict):
        raise ExportInputsError("invalid_notebook_policy")
    return policy


def _validate_policy(policy: dict[str, str]) -> dict[str, str]:
    if len(policy) != _EXPECTED_ITEM_COUNT:
        raise ExportInputsError("invalid_input_allowlist")
    normalized: dict[str, str] = {}
    folded_paths: set[str] = set()
    for path, digest in policy.items():
        relative = _normalized_relative_path(path, "invalid_input_allowlist")
        folded = relative.casefold()
        if (
            folded in folded_paths
            or _is_secret_path(relative)
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise ExportInputsError("invalid_input_allowlist")
        folded_paths.add(folded)
        normalized[relative] = digest
    return normalized


def _normalize_repo_path(
    root: Path,
    value: str | Path,
    error_code: str,
) -> tuple[Path, str]:
    if not isinstance(value, (str, Path)):
        raise ExportInputsError(error_code)
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ExportInputsError(error_code)
    if os.path.isabs(raw):
        if os.path.normpath(raw) != raw:
            raise ExportInputsError(error_code)
        absolute = Path(raw)
        try:
            relative_path = absolute.relative_to(root)
        except ValueError:
            raise ExportInputsError(error_code) from None
        relative = relative_path.as_posix()
    else:
        relative = raw
        absolute = root.joinpath(*PurePosixPath(relative).parts)
    relative = _normalized_relative_path(relative, error_code)
    return absolute, relative


def _verified_inputs(
    root_descriptor: int,
    policy: dict[str, str],
    ledger: list[_OwnedDescriptor],
) -> tuple[_VerifiedInput, ...]:
    verified: list[_VerifiedInput] = []
    total_size = 0
    for relative in sorted(policy):
        payload = _read_relative_file(
            root_descriptor,
            relative,
            maximum_size=_MAX_INPUT_MEMBER_BYTES,
            error_code="unsafe_export_input",
            ledger=ledger,
        )
        total_size += len(payload)
        if total_size > _MAX_INPUT_TOTAL_BYTES:
            raise ExportInputsError("unsafe_export_input")
        digest = _sha256(payload)
        if digest != policy[relative]:
            raise ExportInputsError("export_input_digest_mismatch")
        verified.append(_VerifiedInput(relative, digest, payload))
    return tuple(verified)


def _archive_bytes(inputs: tuple[_VerifiedInput, ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as archive:
        archive.comment = b""
        for item in inputs:
            info = zipfile.ZipInfo(item.path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.extra = b""
            info.comment = b""
            archive.writestr(info, item.payload, compress_type=zipfile.ZIP_STORED)
    payload = output.getvalue()
    if len(payload) > _MAX_INPUT_TOTAL_BYTES:
        raise ExportInputsError("unsafe_export_input")
    return payload


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        offset += written


def _create_stage(
    parent_descriptor: int,
    ledger: list[_OwnedDescriptor],
    stages: list[_OwnedStage],
) -> _OwnedStage:
    for _attempt in range(32):
        name = f".{_BUNDLE_NAME}.{secrets.token_hex(16)}.stage"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        except OSError:
            raise ExportInputsError("export_inputs_write_failed") from None
        stage = _OwnedStage(parent_descriptor=parent_descriptor, name=name)
        stages.append(stage)
        try:
            bound = _identity(
                os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            )
            if (
                bound.file_type != stat.S_IFDIR
                or bound.owner != os.getuid()
                or bound.mode != 0o700
            ):
                raise ExportInputsError("export_inputs_write_failed")
            stage.identity = bound
            descriptor = os.open(
                name,
                _directory_flags(),
                dir_fd=parent_descriptor,
            )
            record = _register_provisional_descriptor(ledger, descriptor)
            stage.descriptor_record = record
            details = os.fstat(descriptor)
            _bind_owned_descriptor(record, details)
            if not _same_path_identity(_identity(details), bound):
                raise ExportInputsError("export_inputs_write_failed")
            return stage
        except ExportInputsError:
            raise
        except OSError:
            raise ExportInputsError("export_inputs_write_failed") from None
    raise ExportInputsError("export_inputs_write_failed")


def _create_stage_file(
    stage: _OwnedStage,
    name: str,
    payload: bytes,
    ledger: list[_OwnedDescriptor],
) -> None:
    if stage.descriptor_record is None:
        raise ExportInputsError("export_inputs_write_failed")
    parent = stage.descriptor_record.descriptor
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent)
        record = _register_provisional_descriptor(ledger, descriptor)
        initial = os.fstat(descriptor)
        _bind_owned_descriptor(record, initial)
        initial_identity = _identity(initial)
        bound = _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
        if (
            initial_identity.file_type != stat.S_IFREG
            or initial_identity.owner != os.getuid()
            or initial_identity.links != 1
            or initial_identity.mode != 0o600
            or not _same_path_identity(bound, initial_identity)
        ):
            raise ExportInputsError("export_inputs_write_failed")
        stage.children[name] = initial_identity
        _write_all(descriptor, payload)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        final = os.fstat(descriptor)
        final_identity = _identity(final)
        final_bound = _identity(
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        )
        if (
            final_identity.file_type != stat.S_IFREG
            or final_identity.owner != os.getuid()
            or final_identity.links != 1
            or final_identity.mode != 0o444
            or final_identity.size != len(payload)
            or not _same_path_identity(final_bound, final_identity)
        ):
            raise ExportInputsError("export_inputs_write_failed")
        stage.children[name] = final_identity
        _close_owned_descriptor_now(ledger, record)
    except ExportInputsError:
        raise
    except OSError:
        raise ExportInputsError("export_inputs_write_failed") from None


def _seal_stage(stage: _OwnedStage) -> None:
    if stage.descriptor_record is None:
        raise ExportInputsError("export_inputs_write_failed")
    descriptor = stage.descriptor_record.descriptor
    try:
        if set(os.listdir(descriptor)) != _BUNDLE_ENTRIES:
            raise ExportInputsError("export_inputs_write_failed")
        os.fchmod(descriptor, 0o555)
        os.fsync(descriptor)
        details = os.fstat(descriptor)
        bound = _identity(
            os.stat(
                stage.name,
                dir_fd=stage.parent_descriptor,
                follow_symlinks=False,
            )
        )
        identity = _identity(details)
        if (
            identity.file_type != stat.S_IFDIR
            or identity.owner != os.getuid()
            or identity.mode != 0o555
            or not _same_path_identity(bound, identity)
        ):
            raise ExportInputsError("export_inputs_write_failed")
        stage.identity = identity
    except ExportInputsError:
        raise
    except OSError:
        raise ExportInputsError("export_inputs_write_failed") from None


def _stat_identity_at(parent: int, name: str) -> _Identity | None:
    try:
        return _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
    except FileNotFoundError:
        return None
    except OSError:
        raise ExportInputsError("existing_export_bundle_invalid") from None


def _validate_bundle(
    parent_descriptor: int,
    bundle_name: str,
    archive_payload: bytes,
    receipt_payload: bytes,
    ledger: list[_OwnedDescriptor],
) -> _Identity | None:
    bound = _stat_identity_at(parent_descriptor, bundle_name)
    if bound is None:
        return None
    if (
        bound.file_type != stat.S_IFDIR
        or bound.owner != os.getuid()
        or bound.mode != 0o555
    ):
        raise ExportInputsError("existing_export_bundle_invalid")
    try:
        descriptor = os.open(
            bundle_name,
            _directory_flags(),
            dir_fd=parent_descriptor,
        )
        record = _register_provisional_descriptor(ledger, descriptor)
        details = os.fstat(descriptor)
        _bind_owned_descriptor(record, details)
        identity = _identity(details)
        if (
            not _same_path_identity(identity, bound)
            or set(os.listdir(descriptor)) != _BUNDLE_ENTRIES
        ):
            raise ExportInputsError("existing_export_bundle_invalid")
        archive_raw, _ = _read_file_at(
            descriptor,
            _ARCHIVE_NAME,
            maximum_size=_MAX_INPUT_TOTAL_BYTES,
            error_code="existing_export_bundle_invalid",
            expected_mode=0o444,
            ledger=ledger,
        )
        receipt_raw, _ = _read_file_at(
            descriptor,
            _RECEIPT_NAME,
            maximum_size=_MAX_NOTEBOOK_BYTES,
            error_code="existing_export_bundle_invalid",
            expected_mode=0o444,
            ledger=ledger,
        )
        rebound = _identity(
            os.stat(
                bundle_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        )
        if (
            archive_raw != archive_payload
            or receipt_raw != receipt_payload
            or not _same_path_identity(rebound, identity)
        ):
            raise ExportInputsError("existing_export_bundle_invalid")
        _close_owned_descriptor_now(ledger, record)
        return identity
    except ExportInputsError:
        raise
    except OSError:
        raise ExportInputsError("existing_export_bundle_invalid") from None


def _rename_directory_exclusive(
    source_descriptor: int,
    source_name: str,
    destination_descriptor: int,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if platform.system() == "Darwin":
        function = getattr(libc, "renameatx_np", None)
        flag = 0x00000004
    elif platform.system() == "Linux":
        function = getattr(libc, "renameat2", None)
        flag = 0x00000001
    else:
        function = None
        flag = 0
    if function is None:
        raise OSError(errno.ENOTSUP, "exclusive rename unavailable")
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    function.restype = ctypes.c_int
    result = function(
        source_descriptor,
        os.fsencode(source_name),
        destination_descriptor,
        os.fsencode(destination_name),
        flag,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _cleanup_owned_stage(stage: _OwnedStage) -> None:
    if not stage.active:
        return
    if stage.identity is None:
        raise OSError(errno.EIO, "stage identity unavailable")
    actual = _stat_identity_at(stage.parent_descriptor, stage.name)
    if actual is None:
        stage.active = False
        return
    if not _same_owned_inode(actual, stage.identity):
        stage.active = False
        return
    record = stage.descriptor_record
    if record is None:
        if stage.children:
            raise OSError(errno.EIO, "stage descriptor unavailable")
        os.rmdir(stage.name, dir_fd=stage.parent_descriptor)
        stage.active = False
        os.fsync(stage.parent_descriptor)
        return
    still_owned, probe_failure = _descriptor_is_still_owned(record)
    if probe_failure is not None:
        raise probe_failure
    if still_owned is not True:
        raise OSError(errno.EIO, "stage descriptor unavailable")
    descriptor = record.descriptor
    entries = set(os.listdir(descriptor))
    if entries != set(stage.children):
        raise OSError(errno.EIO, "stage entries changed")
    for name, expected in stage.children.items():
        bound = _identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False))
        if not _same_owned_inode(bound, expected):
            raise OSError(errno.EIO, "stage child changed")
    os.fchmod(descriptor, 0o700)
    for name in tuple(stage.children):
        os.unlink(name, dir_fd=descriptor)
        stage.children.pop(name)
    os.rmdir(stage.name, dir_fd=stage.parent_descriptor)
    stage.active = False
    os.fsync(stage.parent_descriptor)


def _cleanup_owned_stages_bounded(
    stages: list[_OwnedStage],
    *,
    outcome: _CleanupOutcome,
    attempts: int = 2,
) -> None:
    remaining = [stage for stage in stages if stage.active]
    for _attempt in range(attempts):
        for stage in tuple(remaining):
            try:
                _cleanup_owned_stage(stage)
            except BaseException as failure:
                outcome.record(failure)
            if not stage.active:
                remaining.remove(stage)
        if not remaining:
            break
    if remaining:
        outcome.ordinary_failure = True


def _inspect_legacy_artifact(
    parent_descriptor: int,
    name: str,
    expected_payload: bytes,
    known_sha256: str,
    ledger: list[_OwnedDescriptor],
) -> _LegacyArtifact | None:
    try:
        details = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise ExportInputsError("legacy_export_artifact_conflict") from None
    if not stat.S_ISREG(details.st_mode):
        raise ExportInputsError("legacy_export_artifact_conflict")
    payload, identity = _read_file_at(
        parent_descriptor,
        name,
        maximum_size=_MAX_INPUT_TOTAL_BYTES,
        error_code="legacy_export_artifact_conflict",
        expected_mode=0o444,
        ledger=ledger,
    )
    if payload != expected_payload or _sha256(payload) != known_sha256:
        raise ExportInputsError("legacy_export_artifact_conflict")
    return _LegacyArtifact(name=name, identity=identity)


def _remove_exact_legacy_artifacts(
    parent_descriptor: int,
    artifacts: tuple[_LegacyArtifact, ...],
) -> None:
    if not artifacts:
        return
    try:
        for artifact in artifacts:
            details = os.stat(
                artifact.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if not _same_path_identity(_identity(details), artifact.identity):
                raise ExportInputsError("legacy_export_cleanup_failed")
        for artifact in artifacts:
            os.unlink(artifact.name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except ExportInputsError:
        raise
    except OSError:
        raise ExportInputsError("legacy_export_cleanup_failed") from None


def _receipt_bytes(
    archive_payload: bytes,
    notebook_raw: bytes,
    inputs: tuple[_VerifiedInput, ...],
) -> bytes:
    return _canonical_json_bytes(
        {
            "archive": {
                "fileName": _ARCHIVE_NAME,
                "sha256": _sha256(archive_payload),
                "sizeBytes": len(archive_payload),
            },
            "files": [
                {
                    "path": item.path,
                    "sha256": item.sha256,
                    "sizeBytes": len(item.payload),
                }
                for item in inputs
            ],
            "notebookSha256": _sha256(notebook_raw),
            "schemaVersion": EXPORT_INPUTS_RECEIPT_SCHEMA_VERSION,
        }
    )


def _build_export_inputs(
    repo_root: str | Path,
    notebook_path: str | Path,
    bundle_path: str | Path,
    ledger: list[_OwnedDescriptor],
    stages: list[_OwnedStage],
) -> ExportInputsResult:
    root = _normalized_root(repo_root)
    _, notebook_relative = _normalize_repo_path(
        root,
        notebook_path,
        "invalid_notebook_policy",
    )
    bundle_absolute, bundle_relative = _normalize_repo_path(
        root,
        bundle_path,
        "unsafe_export_output",
    )
    bundle_pure = PurePosixPath(bundle_relative)
    if bundle_absolute.name != _BUNDLE_NAME:
        raise ExportInputsError("unsafe_export_output")

    root_walk = _open_absolute_directory(root, "unsafe_export_root", ledger)
    notebook_raw = _read_relative_file(
        root_walk.descriptor,
        notebook_relative,
        maximum_size=_MAX_NOTEBOOK_BYTES,
        error_code="invalid_notebook_policy",
        ledger=ledger,
    )
    notebook = _decode_notebook(notebook_raw)
    policy = _validate_policy(_literal_policy(notebook))
    if (
        notebook_relative in {_ARCHIVE_NAME, _RECEIPT_NAME, bundle_relative}
        or bundle_relative in policy
    ):
        raise ExportInputsError("unsafe_export_output")
    inputs = _verified_inputs(root_walk.descriptor, policy, ledger)
    root_walk.verify("unsafe_export_root")

    archive_payload = _archive_bytes(inputs)
    receipt_payload = _receipt_bytes(archive_payload, notebook_raw, inputs)
    result = ExportInputsResult(
        archive_sha256=_sha256(archive_payload),
        archive_size=len(archive_payload),
        item_count=len(inputs),
    )

    output_walk = _open_relative_directory(
        root_walk.descriptor,
        tuple(bundle_pure.parent.parts),
        "unsafe_export_output",
        ledger,
    )
    parent_details = os.fstat(output_walk.descriptor)
    if (
        parent_details.st_uid != os.getuid()
        or stat.S_IMODE(parent_details.st_mode) & 0o022
    ):
        raise ExportInputsError("unsafe_export_output")

    legacy = tuple(
        artifact
        for artifact in (
            _inspect_legacy_artifact(
                output_walk.descriptor,
                _ARCHIVE_NAME,
                archive_payload,
                _KNOWN_LEGACY_ARCHIVE_SHA256,
                ledger,
            ),
            _inspect_legacy_artifact(
                output_walk.descriptor,
                _RECEIPT_NAME,
                receipt_payload,
                _KNOWN_LEGACY_RECEIPT_SHA256,
                ledger,
            ),
        )
        if artifact is not None
    )

    existing = _validate_bundle(
        output_walk.descriptor,
        bundle_pure.name,
        archive_payload,
        receipt_payload,
        ledger,
    )
    if existing is not None:
        _remove_exact_legacy_artifacts(output_walk.descriptor, legacy)
        os.fsync(output_walk.descriptor)
        output_walk.verify("unsafe_export_output")
        root_walk.verify("unsafe_export_root")
        return result

    stage = _create_stage(output_walk.descriptor, ledger, stages)
    _create_stage_file(stage, _ARCHIVE_NAME, archive_payload, ledger)
    _create_stage_file(stage, _RECEIPT_NAME, receipt_payload, ledger)
    _seal_stage(stage)
    if stage.identity is None:
        raise ExportInputsError("export_inputs_write_failed")

    try:
        _rename_directory_exclusive(
            output_walk.descriptor,
            stage.name,
            output_walk.descriptor,
            bundle_pure.name,
        )
    except OSError:
        final = _validate_bundle(
            output_walk.descriptor,
            bundle_pure.name,
            archive_payload,
            receipt_payload,
            ledger,
        )
        if final is None:
            raise ExportInputsError("export_bundle_publish_failed") from None
        if _same_owned_inode(final, stage.identity):
            stage.active = False
            try:
                os.fsync(output_walk.descriptor)
            except OSError:
                pass
            raise ExportInputsError("export_bundle_durability_uncertain") from None
        # Another exact, immutable publisher won the exclusive rename.
    else:
        stage.active = False
        try:
            os.fsync(output_walk.descriptor)
        except OSError:
            raise ExportInputsError("export_bundle_durability_uncertain") from None
        final = _validate_bundle(
            output_walk.descriptor,
            bundle_pure.name,
            archive_payload,
            receipt_payload,
            ledger,
        )
        if final is None or not _same_owned_inode(final, stage.identity):
            raise ExportInputsError("published_export_bundle_invalid")

    _remove_exact_legacy_artifacts(output_walk.descriptor, legacy)
    output_walk.verify("unsafe_export_output")
    root_walk.verify("unsafe_export_root")
    return result


def build_export_inputs(
    repo_root: str | Path,
    *,
    notebook_path: str | Path = "notebooks/export_wayline_gguf_colab.ipynb",
    bundle_path: str | Path = (
        "data/wayline/runtime/wayline_export_inputs_v1.bundle"
    ),
) -> ExportInputsResult:
    """Verify notebook-pinned inputs and publish one immutable bundle."""

    ledger: list[_OwnedDescriptor] = []
    stages: list[_OwnedStage] = []
    result: ExportInputsResult | None = None
    primary: BaseException | None = None
    primary_traceback = None
    try:
        result = _build_export_inputs(
            repo_root,
            notebook_path,
            bundle_path,
            ledger,
            stages,
        )
    except ExportInputsError as failure:
        primary = failure
        primary_traceback = failure.__traceback__
    except Exception:
        primary = ExportInputsError("export_inputs_build_failed")
    except BaseException as failure:
        primary = failure
        primary_traceback = failure.__traceback__

    cleanup = _CleanupOutcome()
    try:
        _cleanup_owned_stages_bounded(stages, outcome=cleanup)
    except BaseException as failure:
        cleanup.record(failure)
        cleanup.ordinary_failure = True
    try:
        _cleanup_owned_descriptors_bounded(ledger, outcome=cleanup)
    except BaseException as failure:
        cleanup.record(failure)
        cleanup.ordinary_failure = True

    if primary is not None:
        failure_with_traceback = primary.with_traceback(primary_traceback)
        if cleanup.ordinary_failure or cleanup.special_failure is not None:
            raise failure_with_traceback from ExportInputsError(
                "export_inputs_cleanup_failed"
            )
        raise failure_with_traceback from None
    if cleanup.special_failure is not None:
        raise cleanup.special_failure from ExportInputsError(
            "export_inputs_cleanup_failed"
        )
    if cleanup.ordinary_failure:
        raise ExportInputsError("export_inputs_cleanup_failed") from None
    if result is None:
        raise ExportInputsError("export_inputs_build_failed")
    return result


__all__ = [
    "EXPORT_INPUTS_RECEIPT_SCHEMA_VERSION",
    "ExportInputsError",
    "ExportInputsResult",
    "build_export_inputs",
]
