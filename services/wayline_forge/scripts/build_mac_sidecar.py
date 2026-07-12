"""Build and audit the Apple-Silicon Wayline Forge sidecar folder."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Any, NoReturn, Sequence
import zipfile

from services.wayline_forge.app.model_manifest import (
    ModelManifest,
    parse_model_manifest,
)
from services.wayline_forge.app.macos_worker_runtime import (
    DescriptorBindingReceiptError,
    parse_descriptor_binding_release_receipt,
)
from services.wayline_forge.app.production_spawn import (
    PRODUCTION_SPAWN_ADAPTER_SHA256,
)


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
PACKAGE_MANIFEST_NAME = "package_manifest_v1.json"
PACKAGE_SCHEMA_VERSION = "wayline.package-manifest.v1"
PACKAGE_PLATFORM = "macos-arm64"
EXECUTABLE_PATH = "WaylineForge"
LLAMA_SERVER_PATH = "bin/llama-server"
MODEL_MANIFEST_PATH = "resources/model_manifest_v1.json"
DESCRIPTOR_BINDING_RECEIPT_PATH = (
    "resources/descriptor_binding_release_receipt_v1.json"
)
REVIEWED_CACHE_ROOT = "resources/reviewed_cache_release_v1"
_EXECUTABLE_FILES = frozenset({EXECUTABLE_PATH, LLAMA_SERVER_PATH})
_REQUIRED_FILES = frozenset(
    {
        EXECUTABLE_PATH,
        LLAMA_SERVER_PATH,
        MODEL_MANIFEST_PATH,
        DESCRIPTOR_BINDING_RECEIPT_PATH,
        "resources/campaign_catalog_v1.json",
        "resources/curriculum_v1.json",
        "resources/procedure_registry_v1.json",
        "resources/story_templates_v1.json",
        f"{REVIEWED_CACHE_ROOT}/current.json",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_GENERATION_ID = re.compile(r"generation-[0-9a-f]{64}", re.ASCII)
_FILE_READ_CHUNK_BYTES = 1024 * 1024
_SECRET_NAME_MARKERS = (
    ".env",
    "api_key",
    "apikey",
    "credentials",
    "hf_token",
    "private_key",
    "secret",
)
_SECRET_CONTENT_MARKERS = (
    b"-----begin private key-----",
    b"tfy_api_key=",
    b"hf_token=",
    b"authorization: bearer ",
)
_SECRET_SCAN_OVERLAP_BYTES = max(
    len(marker) for marker in _SECRET_CONTENT_MARKERS
) - 1
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_COMPRESSED_SIGNATURES = (b"\x1f\x8b", b"\xfd7zXZ\x00", b"BZh")
_ALLOWED_ZIP_PATH = "_internal/base_library.zip"
_OPAQUE_ARCHIVE_SUFFIXES = frozenset({".egg", ".pyz", ".whl", ".zip"})
_ALLOWED_ZIP_COMPRESSION = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
)
_MIN_ZIP_MEMBERS = 64
_MAX_ZIP_MEMBERS = 512
_MAX_ZIP_MEMBER_BYTES = 8 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_ZIP_FILE_BYTES = 16 * 1024 * 1024
_MAX_ZIP_CENTRAL_DIRECTORY_BYTES = 256 * 1024
_ZIP_EOCD = struct.Struct("<4s4H2IH")
_PYTHON_SOURCE_SUFFIXES = frozenset(
    {".pxd", ".pxi", ".py", ".pyi", ".pyw", ".pyx"}
)
_RESEARCH_ROOTS = frozenset({"data", "notebooks", "src"})
_PACKAGE_ERROR_CODES = frozenset(
    {
        "build_failed",
        "package_digest_mismatch",
        "package_descriptor_receipt_invalid",
        "package_file_missing",
        "package_hardlink_forbidden",
        "package_manifest_invalid",
        "package_model_mismatch",
        "package_permissions_invalid",
        "package_platform_invalid",
        "package_research_forbidden",
        "package_secret_forbidden",
        "package_source_forbidden",
        "package_symlink_forbidden",
        "package_unexpected_file",
        "package_unsafe_path",
    }
)


class PackageLayoutError(RuntimeError):
    """Stable package-build failure with no source path or content detail."""

    def __init__(self, code: str) -> None:
        if code not in _PACKAGE_ERROR_CODES:
            raise ValueError("unknown package layout error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PackageEntry:
    relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not _safe_relative_path(self.relative_path):
            raise ValueError("package entry path is invalid")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("package entry digest is invalid")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("package entry size is invalid")


@dataclass(frozen=True, slots=True)
class OptionalModel:
    file_name: str
    sha256: str
    bundled: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.file_name, str)
            or Path(self.file_name).name != self.file_name
            or not self.file_name.endswith(".gguf")
        ):
            raise ValueError("optional model file name is invalid")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("optional model digest is invalid")
        if not isinstance(self.bundled, bool):
            raise ValueError("optional model bundled flag is invalid")


@dataclass(frozen=True, slots=True)
class PackageManifest:
    schema_version: str
    platform: str
    executable: str
    llama_server: str
    optional_model: OptionalModel
    entries: tuple[PackageEntry, ...]

    def __post_init__(self) -> None:
        if self.schema_version != PACKAGE_SCHEMA_VERSION:
            raise ValueError("package schema is invalid")
        if self.platform != PACKAGE_PLATFORM:
            raise ValueError("package platform is invalid")
        if self.executable != EXECUTABLE_PATH:
            raise ValueError("package executable path is invalid")
        if self.llama_server != LLAMA_SERVER_PATH:
            raise ValueError("package llama-server path is invalid")
        paths = tuple(entry.relative_path for entry in self.entries)
        if paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
            raise ValueError("package entries must be unique and sorted")

    def to_dict(self) -> dict[str, object]:
        return {
            "entries": [
                {
                    "relativePath": entry.relative_path,
                    "sha256": entry.sha256,
                    "sizeBytes": entry.size_bytes,
                }
                for entry in self.entries
            ],
            "executable": self.executable,
            "llamaServer": self.llama_server,
            "optionalModel": {
                "bundled": self.optional_model.bundled,
                "fileName": self.optional_model.file_name,
                "sha256": self.optional_model.sha256,
            },
            "platform": self.platform,
            "schemaVersion": self.schema_version,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


def _fail(code: str) -> NoReturn:
    raise PackageLayoutError(code)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("package_manifest_invalid")
        value[key] = item
    return value


def _safe_relative_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or ":" in value
    ):
        return False
    path = Path(value)
    return bool(
        not path.is_absolute()
        and value == path.as_posix()
        and ".." not in path.parts
        and "." not in path.parts
    )


def _normalized_root(value: str | Path, *, must_exist: bool = True) -> Path:
    raw = os.fspath(value)
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or not os.path.isabs(raw)
        or raw.startswith("//")
        or os.path.normpath(raw) != raw
    ):
        _fail("package_unsafe_path")
    root = Path(raw)
    if must_exist and (
        root.is_symlink()
        or not root.is_dir()
        or not stat.S_ISDIR(root.lstat().st_mode)
    ):
        _fail("package_unsafe_path")
    return root


def _classify_path(relative_path: str) -> None:
    path = Path(relative_path)
    lowered = relative_path.casefold()
    lowered_parts = tuple(part.casefold() for part in path.parts)
    if any(
        marker in part
        for part in lowered_parts
        for marker in _SECRET_NAME_MARKERS
    ):
        _fail("package_secret_forbidden")
    if path.suffix.casefold() in {".key", ".pem", ".p12"}:
        _fail("package_secret_forbidden")
    if path.parts and path.parts[0].casefold() in _RESEARCH_ROOTS:
        _fail("package_research_forbidden")
    if path.suffix.casefold() == ".jsonl" or any(
        marker in lowered
        for marker in ("eval_heldout", "train_v", "predictions_")
    ):
        _fail("package_research_forbidden")
    if path.suffix.casefold() in _PYTHON_SOURCE_SUFFIXES:
        _fail("package_source_forbidden")


def _file_identity(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_nlink,
        details.st_uid,
        details.st_gid,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _require_single_link_regular(details: os.stat_result) -> None:
    if not stat.S_ISREG(details.st_mode):
        _fail("package_unsafe_path")
    if details.st_nlink != 1:
        _fail("package_hardlink_forbidden")


def _hash_and_scan_file(
    path: Path,
    expected: os.stat_result,
    relative_path: str,
) -> tuple[str, int]:
    """Hash and secret-scan one stable regular inode in one bounded read."""

    _require_single_link_regular(expected)
    if (
        relative_path == _ALLOWED_ZIP_PATH
        and expected.st_size > _MAX_ZIP_FILE_BYTES
    ):
        _fail("package_unexpected_file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("package_unsafe_path")

    try:
        before = os.fstat(descriptor)
        _require_single_link_regular(before)
        if _file_identity(before) != _file_identity(expected):
            _fail("package_unsafe_path")

        digest = hashlib.sha256()
        size = 0
        overlap = b""
        prefix = b""
        binary_overlap = b""
        zip_signature_seen = False
        while True:
            try:
                block = os.read(descriptor, _FILE_READ_CHUNK_BYTES)
            except InterruptedError:
                continue
            if not isinstance(block, bytes):
                _fail("package_unsafe_path")
            if not block:
                break
            digest.update(block)
            size += len(block)
            if len(prefix) < 8:
                prefix = (prefix + block)[:8]
            binary_window = binary_overlap + block
            if any(signature in binary_window for signature in _ZIP_SIGNATURES):
                zip_signature_seen = True
            binary_overlap = binary_window[-5:]
            lowered_window = overlap + block.lower()
            if any(
                marker in lowered_window
                for marker in _SECRET_CONTENT_MARKERS
            ):
                _fail("package_secret_forbidden")
            overlap = lowered_window[-_SECRET_SCAN_OVERLAP_BYTES:]

        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode):
            _fail("package_unsafe_path")
        if (
            _file_identity(after) != _file_identity(before)
            or size != after.st_size
        ):
            _fail("package_unsafe_path")
        rebound = path.lstat()
        _require_single_link_regular(rebound)
        if _file_identity(rebound) != _file_identity(after):
            _fail("package_unsafe_path")
        is_zip = any(prefix.startswith(signature) for signature in _ZIP_SIGNATURES)
        archive_suffix = Path(relative_path).suffix.casefold()
        if any(
            prefix.startswith(signature)
            for signature in _COMPRESSED_SIGNATURES
        ):
            _fail("package_unexpected_file")
        if relative_path == _ALLOWED_ZIP_PATH:
            if not is_zip:
                _fail("package_unexpected_file")
            expected_members = _validate_zip_envelope(descriptor, size)
            _scan_zip_members(descriptor, expected_members=expected_members)
        elif (
            is_zip
            or archive_suffix in _OPAQUE_ARCHIVE_SUFFIXES
            or (zip_signature_seen and _descriptor_is_zip(descriptor))
        ):
            _fail("package_unexpected_file")
    except PackageLayoutError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        _fail("package_unsafe_path")

    try:
        os.close(descriptor)
    except OSError:
        _fail("package_unsafe_path")
    return digest.hexdigest(), size


def _descriptor_is_zip(descriptor: int) -> bool:
    duplicate = os.dup(descriptor)
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as candidate:
            duplicate = -1
            return bool(zipfile.is_zipfile(candidate))
    except OSError:
        _fail("package_unsafe_path")
    finally:
        if duplicate >= 0:
            try:
                os.close(duplicate)
            except OSError:
                _fail("package_unsafe_path")


def _validate_zip_envelope(descriptor: int, size: int) -> int:
    """Require one unprefixed, untrailed, non-ZIP64 canonical ZIP envelope."""

    if size < _ZIP_EOCD.size or size > _MAX_ZIP_FILE_BYTES:
        _fail("package_unexpected_file")
    try:
        prefix = os.pread(descriptor, 4, 0)
        eocd_offset = size - _ZIP_EOCD.size
        eocd = os.pread(descriptor, _ZIP_EOCD.size, eocd_offset)
        if len(eocd) != _ZIP_EOCD.size:
            _fail("package_unexpected_file")
        (
            signature,
            disk_number,
            central_disk,
            disk_entries,
            total_entries,
            central_size,
            central_offset,
            comment_size,
        ) = _ZIP_EOCD.unpack(eocd)
        if (
            prefix != b"PK\x03\x04"
            or signature != b"PK\x05\x06"
            or disk_number != 0
            or central_disk != 0
            or disk_entries != total_entries
            or not 0 < total_entries <= _MAX_ZIP_MEMBERS
            or central_size > _MAX_ZIP_CENTRAL_DIRECTORY_BYTES
            or central_offset + central_size != eocd_offset
            or comment_size != 0
            or os.pread(descriptor, 4, central_offset) != b"PK\x01\x02"
        ):
            _fail("package_unexpected_file")
    except OSError:
        _fail("package_unsafe_path")
    return total_entries


def _scan_zip_members(descriptor: int, *, expected_members: int) -> None:
    """Inspect bounded decompressed members from the already-attested inode."""

    duplicate = os.dup(descriptor)
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as archive_file:
            duplicate = -1
            with zipfile.ZipFile(archive_file, "r") as archive:
                if archive.comment:
                    _fail("package_unexpected_file")
                members = archive.infolist()
                if (
                    not members
                    or len(members) != expected_members
                    or len(members) > _MAX_ZIP_MEMBERS
                ):
                    _fail("package_unexpected_file")
                total_size = 0
                names: set[str] = set()
                for member in members:
                    name = member.filename
                    if (
                        name in names
                        or not _safe_relative_path(name)
                        or member.is_dir()
                        or member.flag_bits & 0x1
                        or member.compress_type not in _ALLOWED_ZIP_COMPRESSION
                        or member.comment
                        or member.extra
                    ):
                        _fail("package_unexpected_file")
                    names.add(name)
                    _classify_path(name)
                    member_mode = member.external_attr >> 16
                    if member_mode and stat.S_ISLNK(member_mode):
                        _fail("package_unsafe_path")
                    if (
                        member.file_size < 0
                        or member.file_size > _MAX_ZIP_MEMBER_BYTES
                    ):
                        _fail("package_unexpected_file")
                    total_size += member.file_size
                    if total_size > _MAX_ZIP_TOTAL_BYTES:
                        _fail("package_unexpected_file")

                    expanded = 0
                    overlap = b""
                    payload_overlap = b""
                    embedded_payload = False
                    with archive.open(member, "r") as member_file:
                        while True:
                            block = member_file.read(_FILE_READ_CHUNK_BYTES)
                            if not block:
                                break
                            expanded += len(block)
                            if expanded > member.file_size:
                                _fail("package_unexpected_file")
                            payload_window = payload_overlap + block
                            if any(
                                signature in payload_window
                                for signature in (
                                    *_ZIP_SIGNATURES,
                                    *_COMPRESSED_SIGNATURES,
                                )
                            ):
                                embedded_payload = True
                            payload_overlap = payload_window[-5:]
                            lowered_window = overlap + block.lower()
                            if any(
                                marker in lowered_window
                                for marker in _SECRET_CONTENT_MARKERS
                            ):
                                _fail("package_secret_forbidden")
                            overlap = lowered_window[
                                -_SECRET_SCAN_OVERLAP_BYTES:
                            ]
                    if expanded != member.file_size:
                        _fail("package_unexpected_file")
                    if (
                        embedded_payload
                        or Path(name).suffix.casefold()
                        in _OPAQUE_ARCHIVE_SUFFIXES
                    ):
                        _fail("package_unexpected_file")
                    if (
                        Path(name).suffix.casefold() != ".pyc"
                        or member.compress_type != zipfile.ZIP_STORED
                    ):
                        _fail("package_unexpected_file")
                if len(members) < _MIN_ZIP_MEMBERS:
                    _fail("package_unexpected_file")
    except PackageLayoutError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        _fail("package_unexpected_file")
    finally:
        if duplicate >= 0:
            try:
                os.close(duplicate)
            except OSError:
                _fail("package_unsafe_path")


def _scan_files(root: Path) -> tuple[PackageEntry, ...]:
    entries: list[PackageEntry] = []
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for directory, directories, files in walker:
            parent = Path(directory)
            for name in directories:
                child = parent / name
                if child.is_symlink():
                    _fail("package_symlink_forbidden")
                if not stat.S_ISDIR(child.lstat().st_mode):
                    _fail("package_unsafe_path")
                _classify_path(child.relative_to(root).as_posix())
            for name in files:
                path = parent / name
                if path.is_symlink():
                    _fail("package_symlink_forbidden")
                details = path.lstat()
                _require_single_link_regular(details)
                relative = path.relative_to(root).as_posix()
                if relative == PACKAGE_MANIFEST_NAME:
                    continue
                _classify_path(relative)
                sha256, size = _hash_and_scan_file(path, details, relative)
                entries.append(PackageEntry(relative, sha256, size))
    except PackageLayoutError:
        raise
    except OSError:
        _fail("package_unsafe_path")
    return tuple(sorted(entries, key=lambda entry: entry.relative_path))


def _require_layout_files(root: Path, entries: tuple[PackageEntry, ...]) -> None:
    paths = {entry.relative_path for entry in entries}
    if not _REQUIRED_FILES.issubset(paths):
        _fail("package_file_missing")
    by_path = {entry.relative_path: entry for entry in entries}
    if any(by_path[path].size_bytes == 0 for path in _REQUIRED_FILES):
        _fail("package_file_missing")
    pointer_path = root / REVIEWED_CACHE_ROOT / "current.json"
    try:
        pointer_raw = pointer_path.read_bytes()
        pointer = json.loads(pointer_raw, object_pairs_hook=_strict_object)
    except PackageLayoutError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("package_manifest_invalid")
    if (
        not isinstance(pointer, dict)
        or set(pointer)
        != {"generationId", "manifestSha256", "schemaVersion"}
        or pointer.get("schemaVersion")
        != "wayline.reviewed-cache-pointer.v1"
        or not isinstance(pointer.get("generationId"), str)
        or _GENERATION_ID.fullmatch(pointer["generationId"]) is None
        or not isinstance(pointer.get("manifestSha256"), str)
        or _SHA256.fullmatch(pointer["manifestSha256"]) is None
    ):
        _fail("package_manifest_invalid")
    if pointer["generationId"] != "generation-" + pointer["manifestSha256"]:
        _fail("package_digest_mismatch")
    generation_root = (
        f"{REVIEWED_CACHE_ROOT}/generations/{pointer['generationId']}"
    )
    cache_manifest_path = f"{generation_root}/reviewed_cache_manifest.json"
    if not {
        cache_manifest_path,
        f"{generation_root}/reviewed_cache.sqlite3",
    }.issubset(paths):
        _fail("package_file_missing")
    if by_path[cache_manifest_path].sha256 != pointer["manifestSha256"]:
        _fail("package_digest_mismatch")
    expected_cache_paths = {
        f"{REVIEWED_CACHE_ROOT}/current.json",
        cache_manifest_path,
        f"{generation_root}/reviewed_cache.sqlite3",
    }
    actual_cache_paths = {
        path
        for path in paths
        if path.startswith(f"{REVIEWED_CACHE_ROOT}/")
    }
    if actual_cache_paths != expected_cache_paths:
        _fail("package_unexpected_file")


def _model_record(root: Path, entries: tuple[PackageEntry, ...]) -> OptionalModel:
    try:
        manifest = parse_model_manifest(
            (root / MODEL_MANIFEST_PATH).read_bytes()
        )
    except Exception:
        _fail("package_manifest_invalid")
    model_path = f"models/{manifest.gguf_file_name}"
    by_path = {entry.relative_path: entry for entry in entries}
    bundled = model_path in by_path
    if not bundled:
        _fail("package_file_missing")
    if bundled and by_path[model_path].sha256 != manifest.gguf_sha256:
        _fail("package_model_mismatch")
    return OptionalModel(
        file_name=manifest.gguf_file_name,
        sha256=manifest.gguf_sha256,
        bundled=bundled,
    )


def _validate_descriptor_binding_receipt(
    root: Path,
    entries: tuple[PackageEntry, ...],
    model: OptionalModel,
) -> None:
    by_path = {entry.relative_path: entry for entry in entries}
    try:
        receipt = parse_descriptor_binding_release_receipt(
            (root / DESCRIPTOR_BINDING_RECEIPT_PATH).read_bytes()
        )
        model_manifest = parse_model_manifest(
            (root / MODEL_MANIFEST_PATH).read_bytes()
        )
    except (OSError, DescriptorBindingReceiptError, TypeError, ValueError):
        _fail("package_descriptor_receipt_invalid")
    if (
        receipt.binary_sha256 != by_path[LLAMA_SERVER_PATH].sha256
        or receipt.model_sha256 != model.sha256
        or receipt.llama_cpp_revision != model_manifest.llama_cpp_revision
        or receipt.spawn_adapter_sha256
        != PRODUCTION_SPAWN_ADAPTER_SHA256
    ):
        _fail("package_descriptor_receipt_invalid")


def _manifest_from_dict(value: object) -> PackageManifest:
    if not isinstance(value, dict) or set(value) != {
        "entries",
        "executable",
        "llamaServer",
        "optionalModel",
        "platform",
        "schemaVersion",
    }:
        _fail("package_manifest_invalid")
    entries = value.get("entries")
    optional = value.get("optionalModel")
    if not isinstance(entries, list) or not isinstance(optional, dict):
        _fail("package_manifest_invalid")
    if set(optional) != {"bundled", "fileName", "sha256"}:
        _fail("package_manifest_invalid")
    parsed_entries: list[PackageEntry] = []
    try:
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "relativePath",
                "sha256",
                "sizeBytes",
            }:
                _fail("package_manifest_invalid")
            parsed_entries.append(
                PackageEntry(
                    relative_path=entry["relativePath"],
                    sha256=entry["sha256"],
                    size_bytes=entry["sizeBytes"],
                )
            )
        return PackageManifest(
            schema_version=value["schemaVersion"],
            platform=value["platform"],
            executable=value["executable"],
            llama_server=value["llamaServer"],
            optional_model=OptionalModel(
                file_name=optional["fileName"],
                sha256=optional["sha256"],
                bundled=optional["bundled"],
            ),
            entries=tuple(parsed_entries),
        )
    except PackageLayoutError:
        raise
    except (KeyError, TypeError, ValueError):
        _fail("package_manifest_invalid")


def _freeze_permissions(root: Path) -> None:
    try:
        paths = sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True)
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                _fail("package_symlink_forbidden")
            if path.is_file():
                _require_single_link_regular(path.lstat())
                path.chmod(0o500 if relative in _EXECUTABLE_FILES else 0o400)
            elif path.is_dir():
                path.chmod(0o500)
        root.chmod(0o500)
    except PackageLayoutError:
        raise
    except OSError:
        _fail("package_permissions_invalid")


def _validate_permissions(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            _fail("package_symlink_forbidden")
        details = path.lstat()
        mode = stat.S_IMODE(details.st_mode)
        if path.is_dir():
            if not stat.S_ISDIR(details.st_mode) or mode != 0o500:
                _fail("package_permissions_invalid")
            continue
        if mode & 0o022:
            _fail("package_permissions_invalid")
        if path.is_file():
            _require_single_link_regular(details)
            relative = path.relative_to(root).as_posix()
            expected = 0o500 if relative in _EXECUTABLE_FILES else 0o400
            if mode != expected:
                _fail("package_permissions_invalid")


def _require_manifest_absent(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        _fail("package_manifest_invalid")
    if stat.S_ISLNK(details.st_mode):
        _fail("package_symlink_forbidden")
    _fail("package_manifest_invalid")


def _write_new_manifest(path: Path, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        offset = 0
        while offset < len(payload):
            try:
                written = os.write(descriptor, payload[offset:])
            except InterruptedError:
                continue
            if written <= 0:
                _fail("package_manifest_invalid")
            offset += written
        details = os.fstat(descriptor)
        _require_single_link_regular(details)
        if details.st_size != len(payload):
            _fail("package_manifest_invalid")
        rebound = path.lstat()
        _require_single_link_regular(rebound)
        if _file_identity(rebound) != _file_identity(details):
            _fail("package_manifest_invalid")
    except PackageLayoutError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = -1
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    except OSError:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = -1
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        _fail("package_manifest_invalid")
    try:
        os.close(descriptor)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        _fail("package_manifest_invalid")


def write_package_manifest(root: str | Path) -> PackageManifest:
    """Audit an assembled package, write its manifest, and freeze it."""

    package_root = _normalized_root(root)
    manifest_path = package_root / PACKAGE_MANIFEST_NAME
    _require_manifest_absent(manifest_path)
    entries = _scan_files(package_root)
    _require_layout_files(package_root, entries)
    model = _model_record(package_root, entries)
    _validate_descriptor_binding_receipt(package_root, entries, model)
    manifest = PackageManifest(
        schema_version=PACKAGE_SCHEMA_VERSION,
        platform=PACKAGE_PLATFORM,
        executable=EXECUTABLE_PATH,
        llama_server=LLAMA_SERVER_PATH,
        optional_model=model,
        entries=entries,
    )
    _write_new_manifest(manifest_path, manifest.to_json().encode("utf-8"))
    _freeze_permissions(package_root)
    return validate_packaged_layout(package_root)


def validate_packaged_layout(root: str | Path) -> PackageManifest:
    """Verify the frozen package has exactly the hashed safe files."""

    package_root = _normalized_root(root)
    manifest_path = package_root / PACKAGE_MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        _fail("package_file_missing")
    try:
        raw = manifest_path.read_bytes()
        decoded = json.loads(raw, object_pairs_hook=_strict_object)
    except PackageLayoutError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("package_manifest_invalid")
    manifest = _manifest_from_dict(decoded)
    if raw != manifest.to_json().encode("utf-8"):
        _fail("package_manifest_invalid")
    entries = _scan_files(package_root)
    _require_layout_files(package_root, entries)
    if entries != manifest.entries:
        actual = {entry.relative_path: entry for entry in entries}
        recorded = {entry.relative_path: entry for entry in manifest.entries}
        if set(actual) != set(recorded):
            _fail("package_unexpected_file")
        _fail("package_digest_mismatch")
    if _model_record(package_root, entries) != manifest.optional_model:
        _fail("package_model_mismatch")
    _validate_descriptor_binding_receipt(
        package_root,
        entries,
        manifest.optional_model,
    )
    _validate_permissions(package_root)
    return manifest


def _require_regular_source(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        _fail("package_unsafe_path")
    if not stat.S_ISREG(path.lstat().st_mode):
        _fail("package_unsafe_path")


def _copy_tree_without_links(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        _fail("package_unsafe_path")
    for path in source.rglob("*"):
        if path.is_symlink():
            _fail("package_symlink_forbidden")
    shutil.copytree(source, destination, dirs_exist_ok=True)


def _copy_pyinstaller_tree(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        _fail("package_unsafe_path")
    try:
        children = {child.name: child for child in source.iterdir()}
    except OSError:
        _fail("package_unsafe_path")
    if set(children) != {EXECUTABLE_PATH, "_internal"}:
        _fail("package_unexpected_file")
    _require_regular_source(children[EXECUTABLE_PATH])
    internal = children["_internal"]
    if internal.is_symlink() or not internal.is_dir():
        _fail("package_unsafe_path")
    _copy_tree_without_links(source, destination)


def _copy_current_reviewed_cache(source: Path, destination: Path) -> None:
    """Stage only the canonical pointer and its exact current generation."""

    if source.is_symlink() or not source.is_dir() or destination.exists():
        _fail("package_unsafe_path")
    pointer_path = source / "current.json"
    _require_regular_source(pointer_path)
    try:
        pointer_raw = pointer_path.read_bytes()
        pointer = json.loads(pointer_raw, object_pairs_hook=_strict_object)
    except PackageLayoutError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("package_manifest_invalid")
    if (
        not isinstance(pointer, dict)
        or set(pointer)
        != {"generationId", "manifestSha256", "schemaVersion"}
        or pointer.get("schemaVersion")
        != "wayline.reviewed-cache-pointer.v1"
        or not isinstance(pointer.get("generationId"), str)
        or _GENERATION_ID.fullmatch(pointer["generationId"]) is None
        or not isinstance(pointer.get("manifestSha256"), str)
        or _SHA256.fullmatch(pointer["manifestSha256"]) is None
        or pointer["generationId"]
        != "generation-" + pointer["manifestSha256"]
        or pointer_raw != _canonical_json(pointer).encode("utf-8")
    ):
        _fail("package_manifest_invalid")
    generations = source / "generations"
    generation = generations / pointer["generationId"]
    for directory in (generations, generation):
        if directory.is_symlink() or not directory.is_dir():
            _fail("package_unsafe_path")
    manifest = generation / "reviewed_cache_manifest.json"
    database = generation / "reviewed_cache.sqlite3"
    _require_regular_source(manifest)
    _require_regular_source(database)
    try:
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != pointer[
            "manifestSha256"
        ]:
            _fail("package_digest_mismatch")
    except OSError:
        _fail("package_unsafe_path")

    staged_generation = destination / "generations" / pointer["generationId"]
    staged_generation.mkdir(parents=True)
    shutil.copyfile(pointer_path, destination / "current.json")
    shutil.copyfile(manifest, staged_generation / manifest.name)
    shutil.copyfile(database, staged_generation / database.name)


def assemble_sidecar(
    *,
    pyinstaller_directory: Path,
    destination: Path,
    llama_server: Path,
    model_manifest: Path,
    descriptor_binding_receipt: Path,
    reviewed_cache_release: Path,
    gguf: Path,
) -> PackageManifest:
    """Assemble audited build inputs into one new release directory."""

    if destination.exists():
        _fail("package_unsafe_path")
    _require_regular_source(llama_server)
    _require_regular_source(model_manifest)
    _require_regular_source(descriptor_binding_receipt)
    _require_regular_source(gguf)
    if pyinstaller_directory.is_symlink() or not pyinstaller_directory.is_dir():
        _fail("package_unsafe_path")
    if reviewed_cache_release.is_symlink() or not reviewed_cache_release.is_dir():
        _fail("package_unsafe_path")

    destination_parent = destination.parent
    destination_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".wayline-sidecar-stage-",
        dir=destination_parent,
    ) as temporary:
        stage = Path(temporary) / destination.name
        _copy_pyinstaller_tree(pyinstaller_directory, stage)
        (stage / "bin").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(llama_server, stage / LLAMA_SERVER_PATH)
        resources = stage / "resources"
        resources.mkdir(parents=True, exist_ok=True)
        for name in (
            "campaign_catalog_v1.json",
            "curriculum_v1.json",
            "procedure_registry_v1.json",
            "story_templates_v1.json",
        ):
            shutil.copyfile(SERVICE_ROOT / "resources" / name, resources / name)
        shutil.copyfile(model_manifest, stage / MODEL_MANIFEST_PATH)
        shutil.copyfile(
            descriptor_binding_receipt,
            stage / DESCRIPTOR_BINDING_RECEIPT_PATH,
        )
        _copy_current_reviewed_cache(
            reviewed_cache_release,
            stage / REVIEWED_CACHE_ROOT,
        )
        parsed_model = parse_model_manifest(model_manifest.read_bytes())
        model_directory = stage / "models"
        model_directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(gguf, model_directory / parsed_model.gguf_file_name)
        manifest = write_package_manifest(stage)
        os.replace(stage, destination)
        return manifest


def build_mac_sidecar(
    *,
    output: Path,
    llama_server: Path,
    model_manifest: Path,
    descriptor_binding_receipt: Path,
    reviewed_cache_release: Path,
    gguf: Path,
) -> PackageManifest:
    """Run PyInstaller, then assemble the immutable runtime folder."""

    if (
        platform.system() != "Darwin"
        or platform.machine() != "arm64"
        or sys.version_info[:2] != (3, 12)
    ):
        _fail("package_platform_invalid")
    spec = SERVICE_ROOT / "WaylineForge.spec"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec),
    ]
    try:
        subprocess.run(command, cwd=SERVICE_ROOT, check=True)
    except (OSError, subprocess.CalledProcessError):
        _fail("build_failed")
    return assemble_sidecar(
        pyinstaller_directory=SERVICE_ROOT / "dist/WaylineForge",
        destination=output,
        llama_server=llama_server,
        model_manifest=model_manifest,
        descriptor_binding_receipt=descriptor_binding_receipt,
        reviewed_cache_release=reviewed_cache_release,
        gguf=gguf,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="build_mac_sidecar")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--llama-server", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument(
        "--descriptor-binding-receipt",
        type=Path,
        required=True,
    )
    parser.add_argument("--reviewed-cache-release", type=Path, required=True)
    parser.add_argument("--gguf", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        manifest = build_mac_sidecar(
            output=arguments.output.absolute(),
            llama_server=arguments.llama_server.absolute(),
            model_manifest=arguments.model_manifest.absolute(),
            descriptor_binding_receipt=(
                arguments.descriptor_binding_receipt.absolute()
            ),
            reviewed_cache_release=arguments.reviewed_cache_release.absolute(),
            gguf=arguments.gguf.absolute(),
        )
    except PackageLayoutError as error:
        print(f"wayline_sidecar_build_failed: {error.code}", file=sys.stderr)
        return 1
    print(hashlib.sha256(manifest.to_json().encode("utf-8")).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "OptionalModel",
    "DESCRIPTOR_BINDING_RECEIPT_PATH",
    "PackageEntry",
    "PackageLayoutError",
    "PackageManifest",
    "assemble_sidecar",
    "build_mac_sidecar",
    "validate_packaged_layout",
    "write_package_manifest",
]
