"""Build and verify the minimal source bundle used by the free Colab generator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

from .config import ROOT
from .game_colab_backend import BACKEND_SOURCE_SHA256
from .game_candidate_generation import (
    FROZEN_HOLDOUT_RECORD_COUNT,
    FROZEN_HOLDOUT_SHA256,
    GENERATOR_SOURCE_SHA256,
    load_validated_question_batch,
    stable_json_sha256,
)


BUNDLE_SCHEMA_VERSION = "glitch-rally-colab-bundle-v1"
BUNDLE_MANIFEST_PATH = "glitch_rally_bundle_manifest.json"
BUNDLE_FILES = (
    "data/game/questions_v1.jsonl",
    "data/processed/eval_heldout.jsonl",
    "src/__init__.py",
    "src/buggy_procedures.py",
    "src/config.py",
    "src/consistency.py",
    "src/game_colab_backend.py",
    "src/game_candidate_generation.py",
    "src/game_content.py",
    "src/prompts.py",
    "src/text_utils.py",
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MAX_MEMBER_BYTES = 10 * 1024 * 1024
_MAX_BUNDLE_BYTES = 25 * 1024 * 1024


class ColabBundleError(ValueError):
    """Raised when a Colab source bundle is incomplete, unsafe, or tampered."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _reject_duplicate_object_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ColabBundleError(f"duplicate JSON key in bundle manifest: {key}")
        result[key] = value
    return result


def _manifest_core(files):
    generator = next(
        item
        for item in files
        if item["path"] == "src/game_candidate_generation.py"
    )
    backend = next(
        item for item in files if item["path"] == "src/game_colab_backend.py"
    )
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "files": files,
        "frozen_holdout_count": FROZEN_HOLDOUT_RECORD_COUNT,
        "frozen_holdout_sha256": FROZEN_HOLDOUT_SHA256,
        "generator_source_sha256": generator["sha256"],
        "backend_source_sha256": backend["sha256"],
    }


def _manifest(files):
    core = _manifest_core(files)
    return {
        **core,
        "bundle_id": f"bundle:v1:{stable_json_sha256(core)}",
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build_colab_bundle(
    repo_root: str | Path,
    output_path: str | Path,
) -> dict:
    """Write a deterministic, minimal, manifest-bound Colab source archive."""

    root = Path(repo_root).resolve()
    output = Path(output_path)
    if output.exists():
        raise ColabBundleError(f"bundle output already exists: {output}")

    batch = load_validated_question_batch(
        root / "data/game/questions_v1.jsonl",
        root / "data/processed/eval_heldout.jsonl",
    )
    if (
        batch.holdout_count != FROZEN_HOLDOUT_RECORD_COUNT
        or batch.holdout_sha256 != FROZEN_HOLDOUT_SHA256
    ):
        raise ColabBundleError("question batch does not carry the frozen holdout receipt")

    payloads = {}
    files = []
    for relative in BUNDLE_FILES:
        source = root / relative
        if not source.is_file():
            raise ColabBundleError(f"required bundle file is missing: {relative}")
        payload = source.read_bytes()
        payloads[relative] = payload
        files.append(
            {
                "path": relative,
                "size": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )

    manifest = _manifest(files)
    if manifest["generator_source_sha256"] != GENERATOR_SOURCE_SHA256:
        raise ColabBundleError(
            "loaded generator source does not match the file being bundled; "
            "restart Python and retry"
        )
    if manifest["backend_source_sha256"] != BACKEND_SOURCE_SHA256:
        raise ColabBundleError(
            "loaded Colab backend source does not match the file being bundled; "
            "restart Python and retry"
        )
    manifest_payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for relative in BUNDLE_FILES:
                archive.writestr(_zip_info(relative), payloads[relative])
            archive.writestr(_zip_info(BUNDLE_MANIFEST_PATH), manifest_payload)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        if output.exists():
            raise ColabBundleError(f"bundle output already exists: {output}")
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return manifest


def _validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ColabBundleError(f"unsafe bundle member path: {name!r}")


def _read_jsonl_payload(payload: bytes, label: str):
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ColabBundleError(f"{label} is not UTF-8") from exc
    records = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, object_pairs_hook=_reject_duplicate_object_keys)
        except (json.JSONDecodeError, ColabBundleError) as exc:
            raise ColabBundleError(
                f"{label} line {line_number} is invalid: {exc}"
            ) from exc
        if not isinstance(record, dict):
            raise ColabBundleError(f"{label} line {line_number} must be an object")
        records.append(record)
    return records


def verify_colab_bundle(bundle_path: str | Path) -> dict:
    """Verify exact members, hashes, manifest identity, and holdout receipt."""

    bundle = Path(bundle_path)
    if not bundle.is_file():
        raise ColabBundleError(f"bundle does not exist: {bundle}")

    try:
        archive = zipfile.ZipFile(bundle)
    except zipfile.BadZipFile as exc:
        raise ColabBundleError("bundle is not a valid zip archive") from exc

    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        for name in names:
            _validate_member_name(name)
        if len(names) != len(set(names)):
            raise ColabBundleError("bundle contains duplicate member names")
        expected_names = set(BUNDLE_FILES) | {BUNDLE_MANIFEST_PATH}
        if set(names) != expected_names:
            unexpected = sorted(set(names) - expected_names)
            missing = sorted(expected_names - set(names))
            raise ColabBundleError(
                f"bundle has unexpected members {unexpected} or missing members {missing}"
            )
        if any(info.is_dir() for info in infos):
            raise ColabBundleError("bundle must contain files only")
        if any(stat.S_ISLNK(info.external_attr >> 16) for info in infos):
            raise ColabBundleError("bundle cannot contain symbolic links")
        if any(info.flag_bits & 0x1 for info in infos):
            raise ColabBundleError("bundle cannot contain encrypted members")
        if any(info.file_size > _MAX_MEMBER_BYTES for info in infos):
            raise ColabBundleError("bundle member exceeds the size limit")
        if sum(info.file_size for info in infos) > _MAX_BUNDLE_BYTES:
            raise ColabBundleError("bundle exceeds the total size limit")

        try:
            manifest = json.loads(
                archive.read(BUNDLE_MANIFEST_PATH),
                object_pairs_hook=_reject_duplicate_object_keys,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ColabBundleError) as exc:
            raise ColabBundleError(f"bundle manifest is invalid: {exc}") from exc
        required_manifest_fields = {
            "schema_version",
            "bundle_id",
            "files",
            "frozen_holdout_count",
            "frozen_holdout_sha256",
            "generator_source_sha256",
            "backend_source_sha256",
        }
        if not isinstance(manifest, dict) or set(manifest) != required_manifest_fields:
            raise ColabBundleError("bundle manifest has an unsupported schema")
        if manifest["schema_version"] != BUNDLE_SCHEMA_VERSION:
            raise ColabBundleError("bundle manifest version is not supported")
        if not isinstance(manifest["files"], list):
            raise ColabBundleError("bundle manifest files must be a list")

        listed_paths = []
        for item in manifest["files"]:
            if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
                raise ColabBundleError("bundle manifest file entry is invalid")
            relative = item["path"]
            if relative not in BUNDLE_FILES:
                raise ColabBundleError(f"unexpected manifest path: {relative!r}")
            if relative in listed_paths:
                raise ColabBundleError(f"duplicate manifest path: {relative}")
            listed_paths.append(relative)
            payload = archive.read(relative)
            if item["size"] != len(payload):
                raise ColabBundleError(f"size mismatch for {relative}")
            if not _SHA256.fullmatch(str(item["sha256"])):
                raise ColabBundleError(f"invalid hash for {relative}")
            if item["sha256"] != _sha256_bytes(payload):
                raise ColabBundleError(f"hash mismatch for {relative}")
        if tuple(listed_paths) != BUNDLE_FILES:
            raise ColabBundleError("bundle manifest file order or coverage is invalid")

        core = {key: value for key, value in manifest.items() if key != "bundle_id"}
        expected_bundle_id = f"bundle:v1:{stable_json_sha256(core)}"
        if manifest["bundle_id"] != expected_bundle_id:
            raise ColabBundleError("bundle manifest identity hash mismatch")

        holdout_payload = archive.read("data/processed/eval_heldout.jsonl")
        holdout = _read_jsonl_payload(holdout_payload, "frozen holdout")
        if (
            manifest["frozen_holdout_count"] != FROZEN_HOLDOUT_RECORD_COUNT
            or len(holdout) != FROZEN_HOLDOUT_RECORD_COUNT
            or manifest["frozen_holdout_sha256"] != FROZEN_HOLDOUT_SHA256
            or stable_json_sha256(holdout) != FROZEN_HOLDOUT_SHA256
        ):
            raise ColabBundleError("frozen holdout receipt mismatch")

        generator_payload = archive.read("src/game_candidate_generation.py")
        if manifest["generator_source_sha256"] != _sha256_bytes(generator_payload):
            raise ColabBundleError("generator source hash mismatch")
        backend_payload = archive.read("src/game_colab_backend.py")
        if manifest["backend_source_sha256"] != _sha256_bytes(backend_payload):
            raise ColabBundleError("Colab backend source hash mismatch")

    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the verified upload bundle for Glitch Rally Colab generation."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "glitch_rally_colab_bundle.zip",
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    manifest = build_colab_bundle(args.repo_root, args.output)
    verify_colab_bundle(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "bundle_id": manifest["bundle_id"],
                "files": len(manifest["files"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
