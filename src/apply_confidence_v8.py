"""Attach versioned, scope-calibrated binding confidence to prediction JSONL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .confidence import apply_binding_calibration


def _load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions")
    parser.add_argument("--verdicts", required=True)
    parser.add_argument("--calibration", required=True)
    parser.add_argument(
        "--nonnumeric-calibration",
        default="data/eval_out/opus_nonnumeric_binding_calibration_v8.json",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    artifact = json.loads(
        Path(args.calibration).read_text(encoding="utf-8")
    )
    if artifact.get("accepted") is not True:
        raise SystemExit("calibration artifact is not accepted")
    nonnumeric_artifact = json.loads(
        Path(args.nonnumeric_calibration).read_text(encoding="utf-8")
    )
    if nonnumeric_artifact.get("accepted") is not True:
        raise SystemExit("nonnumeric calibration artifact is not accepted")
    enriched = apply_binding_calibration(
        _load_jsonl(args.predictions),
        _load_jsonl(args.verdicts),
        artifact,
        nonnumeric_artifact=nonnumeric_artifact,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in enriched:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"attached {artifact['calibration_id']} + "
        f"{nonnumeric_artifact['calibration_id']} confidence to "
        f"{len(enriched)} rows -> {output}"
    )


if __name__ == "__main__":
    main()
