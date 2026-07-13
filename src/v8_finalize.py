"""Validate and safely copy one-shot v8 Colab artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Sequence

from .benchmark_v8 import primary_metrics
from .v8_data import jsonl_sha256


CANONICAL_DOWNLOADS = (
    "predictions_v8_model_only.jsonl",
    "predictions_v8_best_of_n.jsonl",
    "local_metrics_v8_model_only.json",
    "local_metrics_v8_best_of_n.json",
    "v8_training_receipt.json",
)
BASE_MODEL = "unsloth/Qwen3-8B-bnb-4bit"
BASE_REVISION = "1deaf68f694c40dbce295da300851729d759b21a"
RECEIPT_SCHEMA = "diagnostic-distractor-v8-training-v1"
ADAPTER_BASENAME = "qwen3-8b-distractor-lora-v8"


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSON objects: {path}")
    return rows


def _file_evidence(path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _prediction_schema_ok(rows: Sequence[dict]) -> bool:
    return all(
        isinstance(row.get("distractors"), list)
        and len(row["distractors"]) == 3
        and all(
            isinstance(distractor, dict)
            and all(
                isinstance(distractor.get(field), str)
                and bool(distractor[field].strip())
                for field in ("misconception", "computation", "answer")
            )
            for distractor in row["distractors"]
        )
        for row in rows
    )


def _track_report(
    rows: Sequence[dict],
    gold_ids: Sequence[str],
    *,
    track: str,
    best_of_n: int | None = None,
) -> dict:
    ids = [str(row.get("id", "")) for row in rows]
    ids_match = ids == list(gold_ids) and len(set(ids)) == len(ids)
    track_ok = all(row.get("inference_track") == track for row in rows)
    best_of_n_ok = (
        True
        if best_of_n is None
        else all(row.get("best_of_n") == best_of_n for row in rows)
    )
    schema_ok = _prediction_schema_ok(rows)
    return {
        "rows": len(rows),
        "unique_ids": len(set(ids)),
        "ids_match_frozen_in_order": ids_match,
        "schema_exactly_three": schema_ok,
        "inference_track_valid": track_ok,
        "best_of_n": best_of_n,
        "best_of_n_metadata_valid": best_of_n_ok,
        "complete": ids_match and schema_ok and track_ok and best_of_n_ok,
    }


def validate_downloads(downloads: str | Path, repo_root: str | Path) -> dict:
    """Return fail-closed evidence for the downloaded frozen-evaluation artifacts."""
    downloads = Path(downloads)
    repo_root = Path(repo_root)
    failures: list[str] = []
    missing = [
        name for name in CANONICAL_DOWNLOADS if not (downloads / name).is_file()
    ]
    if missing:
        return {
            "ok": False,
            "failures": [f"missing:{name}" for name in missing],
            "files": {},
            "tracks": {},
            "adapter": {},
        }

    files = {
        name: _file_evidence(downloads / name)
        for name in CANONICAL_DOWNLOADS
    }
    if any(evidence["bytes"] <= 0 for evidence in files.values()):
        failures.append("empty_artifact")

    gold = _load_jsonl(
        repo_root / "data" / "processed" / "eval_v8_frozen.jsonl"
    )
    train = _load_jsonl(repo_root / "data" / "processed" / "train_v8.jsonl")
    manifest = _load_json(
        repo_root / "data" / "processed" / "v8_manifest.json"
    )
    receipt = _load_json(downloads / "v8_training_receipt.json")
    model_only = _load_jsonl(
        downloads / "predictions_v8_model_only.jsonl"
    )
    best = _load_jsonl(downloads / "predictions_v8_best_of_n.jsonl")
    gold_ids = [str(row.get("id", "")) for row in gold]
    tracks = {
        "model_only": _track_report(
            model_only,
            gold_ids,
            track="model_only",
        ),
        "best_of_n": _track_report(
            best,
            gold_ids,
            track="verifier_guided_best_of_n",
            best_of_n=4,
        ),
    }
    if not tracks["model_only"]["complete"]:
        failures.append("model_only_ids_or_schema_do_not_match_frozen")
    if not tracks["best_of_n"]["ids_match_frozen_in_order"]:
        failures.append("best_of_n_ids_do_not_match_frozen")
    if not tracks["best_of_n"]["schema_exactly_three"]:
        failures.append("best_of_n_schema_incomplete")
    if not tracks["best_of_n"]["inference_track_valid"]:
        failures.append("best_of_n_track_invalid")
    if not tracks["best_of_n"]["best_of_n_metadata_valid"]:
        failures.append("best_of_n_metadata_invalid")

    artifact_manifest = manifest.get("artifacts", {})
    frozen_hash = jsonl_sha256(gold)
    train_hash = jsonl_sha256(train)
    receipt_checks = {
        "schema": receipt.get("schema_version") == RECEIPT_SCHEMA,
        "base_model": receipt.get("base_model") == BASE_MODEL,
        "base_revision": receipt.get("base_revision") == BASE_REVISION,
        "seed": receipt.get("seed") == 42,
        "checkpoint_present": bool(
            str(receipt.get("best_checkpoint", "")).strip()
        ),
        "eval_loss_present": isinstance(
            receipt.get("best_eval_loss"),
            (int, float),
        ),
        "frozen_hash": receipt.get("frozen_sha256") == frozen_hash,
        "train_hash": receipt.get("train_sha256") == train_hash,
        "manifest_frozen_hash": (
            artifact_manifest.get("frozen_benchmark", {}).get("sha256")
            == frozen_hash
        ),
        "manifest_train_hash": (
            artifact_manifest.get("train", {}).get("sha256") == train_hash
        ),
        "manifest_frozen_rows": (
            artifact_manifest.get("frozen_benchmark", {}).get("rows")
            == len(gold)
        ),
        "manifest_train_rows": (
            artifact_manifest.get("train", {}).get("rows") == len(train)
        ),
    }
    failures.extend(
        f"receipt_or_manifest:{name}"
        for name, passed in receipt_checks.items()
        if not passed
    )

    metric_checks = {
        "model_only": (
            _load_json(downloads / "local_metrics_v8_model_only.json")
            == primary_metrics(gold, model_only)
        ),
        "best_of_n": (
            _load_json(downloads / "local_metrics_v8_best_of_n.json")
            == primary_metrics(gold, best)
        ),
    }
    failures.extend(
        f"local_metrics_mismatch:{name}"
        for name, passed in metric_checks.items()
        if not passed
    )

    model_ids = {
        str(row.get("generator_model", "")).strip()
        for row in [*model_only, *best]
        if str(row.get("generator_model", "")).strip()
    }
    hf_repo_id = next(iter(model_ids)) if len(model_ids) == 1 else None
    adapter_zips = sorted(
        downloads.glob(f"{ADAPTER_BASENAME}*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    adapter_dirs = sorted(
        path
        for path in downloads.glob(f"{ADAPTER_BASENAME}*")
        if path.is_dir()
    )
    local_zip_evidence = (
        _file_evidence(adapter_zips[0]) if adapter_zips else None
    )
    zip_integrity = None
    required_entries_present = None
    if adapter_zips:
        try:
            with zipfile.ZipFile(adapter_zips[0]) as archive:
                zip_integrity = archive.testzip() is None
                names = {
                    Path(name).name
                    for name in archive.namelist()
                    if not name.endswith("/")
                }
            required_entries_present = (
                "adapter_config.json" in names
                and "adapter_model.safetensors" in names
                and "tokenizer_config.json" in names
            )
        except (OSError, zipfile.BadZipFile):
            zip_integrity = False
            required_entries_present = False
        if not zip_integrity:
            failures.append("adapter_zip_corrupt")
        if not required_entries_present:
            failures.append("adapter_zip_required_entries_missing")
    adapter = {
        "local_zip": str(adapter_zips[0]) if adapter_zips else None,
        "local_zip_evidence": local_zip_evidence,
        "zip_integrity": zip_integrity,
        "required_entries_present": required_entries_present,
        "local_folder": str(adapter_dirs[0]) if adapter_dirs else None,
        "hf_repo_id": hf_repo_id,
        "hf_url": (
            f"https://huggingface.co/{hf_repo_id}" if hf_repo_id else None
        ),
        "remote_verified": False,
    }
    if not (adapter_zips or adapter_dirs or hf_repo_id):
        failures.append("adapter_artifact_or_hf_reference_missing")

    return {
        "ok": not failures,
        "failures": failures,
        "files": files,
        "frozen": {
            "rows": len(gold),
            "unique_ids": len(set(gold_ids)),
            "canonical_sha256": frozen_hash,
        },
        "train": {
            "rows": len(train),
            "canonical_sha256": train_hash,
        },
        "tracks": tracks,
        "receipt": {
            "checks": receipt_checks,
            "best_checkpoint": receipt.get("best_checkpoint"),
            "best_eval_loss": receipt.get("best_eval_loss"),
        },
        "local_metrics_reproduced": metric_checks,
        "adapter": adapter,
    }


def copy_canonical_artifacts(
    downloads: str | Path,
    repo_root: str | Path,
) -> dict[str, dict]:
    """Copy only final v8 artifacts, preserving the source downloads."""
    downloads = Path(downloads)
    repo_root = Path(repo_root)
    copied: dict[str, dict] = {}
    for name in CANONICAL_DOWNLOADS:
        source = downloads / name
        target = repo_root / name
        shutil.copy2(source, target)
        copied[name] = _file_evidence(target)
    adapter_zips = sorted(
        downloads.glob(f"{ADAPTER_BASENAME}*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if adapter_zips:
        target = repo_root / f"{ADAPTER_BASENAME}.zip"
        shutil.copy2(adapter_zips[0], target)
        copied[target.name] = _file_evidence(target)
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--downloads", default=str(Path.home() / "Downloads"))
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--out",
        default="data/eval_out/v8_artifact_validation.json",
    )
    parser.add_argument("--copy", action="store_true")
    args = parser.parse_args()

    report = validate_downloads(args.downloads, args.repo_root)
    if report["ok"] and args.copy:
        report["copied"] = copy_canonical_artifacts(
            args.downloads,
            args.repo_root,
        )
        report["copies_match_downloads"] = all(
            evidence["sha256"] == report["files"][name]["sha256"]
            for name, evidence in report["copied"].items()
            if name in report["files"]
        )
    output = Path(args.repo_root) / args.out
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if not report["ok"]:
        raise SystemExit("v8 artifact validation failed")


if __name__ == "__main__":
    main()
