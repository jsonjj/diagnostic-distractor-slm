import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.benchmark_v8 import primary_metrics
from src.v8_data import jsonl_sha256
from src.v8_finalize import (
    CANONICAL_DOWNLOADS,
    copy_canonical_artifacts,
    validate_downloads,
)


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class V8FinalizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.downloads = base / "downloads"
        (self.root / "data" / "processed").mkdir(parents=True)
        self.downloads.mkdir()

        self.gold = [
            {
                "id": "q1",
                "question": "What is 6 / 2?",
                "correct": "3",
                "topic": "Division",
            },
            {
                "id": "q2",
                "question": "What is 2 + 3?",
                "correct": "5",
                "topic": "Addition",
            },
        ]
        self.train = [{"id": "train-1", "value": "kept separate"}]
        self.model_only = self._predictions("model_only")
        self.best_of_n = self._predictions(
            "verifier_guided_best_of_n",
            best_of_n=4,
        )
        _write_jsonl(
            self.root / "data" / "processed" / "eval_v8_frozen.jsonl",
            self.gold,
        )
        _write_jsonl(
            self.root / "data" / "processed" / "train_v8.jsonl",
            self.train,
        )
        _write_json(
            self.root / "data" / "processed" / "v8_manifest.json",
            {
                "artifacts": {
                    "frozen_benchmark": {
                        "rows": len(self.gold),
                        "sha256": jsonl_sha256(self.gold),
                    },
                    "train": {
                        "rows": len(self.train),
                        "sha256": jsonl_sha256(self.train),
                    },
                }
            },
        )
        _write_jsonl(
            self.downloads / "predictions_v8_model_only.jsonl",
            self.model_only,
        )
        _write_jsonl(
            self.downloads / "predictions_v8_best_of_n.jsonl",
            self.best_of_n,
        )
        _write_json(
            self.downloads / "local_metrics_v8_model_only.json",
            primary_metrics(self.gold, self.model_only),
        )
        _write_json(
            self.downloads / "local_metrics_v8_best_of_n.json",
            primary_metrics(self.gold, self.best_of_n),
        )
        _write_json(
            self.downloads / "v8_training_receipt.json",
            {
                "schema_version": "diagnostic-distractor-v8-training-v1",
                "base_model": "unsloth/Qwen3-8B-bnb-4bit",
                "base_revision": (
                    "1deaf68f694c40dbce295da300851729d759b21a"
                ),
                "best_checkpoint": "outputs_v8/checkpoint-7",
                "best_eval_loss": 0.1,
                "frozen_sha256": jsonl_sha256(self.gold),
                "train_sha256": jsonl_sha256(self.train),
                "seed": 42,
            },
        )

    def _predictions(self, track, *, best_of_n=None):
        rows = []
        for item in self.gold:
            row = {
                "id": item["id"],
                "generator_model": "j2ampn/qwen3-8b-distractor-lora-v8",
                "inference_track": track,
                "distractors": [
                    {
                        "misconception": f"mistake {index}",
                        "computation": f"{index + 1} + 1 = {index + 2}",
                        "answer": str(index + 2),
                    }
                    for index in range(3)
                ],
            }
            if best_of_n is not None:
                row["best_of_n"] = best_of_n
            rows.append(row)
        return rows

    def test_validates_complete_downloads_and_copies_only_canonical_files(self):
        report = validate_downloads(self.downloads, self.root)

        self.assertTrue(report["ok"], report["failures"])
        self.assertTrue(report["tracks"]["best_of_n"]["complete"])
        self.assertEqual(
            report["adapter"]["hf_repo_id"],
            "j2ampn/qwen3-8b-distractor-lora-v8",
        )

        copied = copy_canonical_artifacts(self.downloads, self.root)
        self.assertEqual(set(copied), set(CANONICAL_DOWNLOADS))
        for name in CANONICAL_DOWNLOADS:
            self.assertEqual(
                (self.downloads / name).read_bytes(),
                (self.root / name).read_bytes(),
            )

    def test_rejects_partial_best_of_n_artifact(self):
        _write_jsonl(
            self.downloads / "predictions_v8_best_of_n.jsonl",
            self.best_of_n[:-1],
        )

        report = validate_downloads(self.downloads, self.root)

        self.assertFalse(report["ok"])
        self.assertFalse(report["tracks"]["best_of_n"]["complete"])
        self.assertIn("best_of_n_ids_do_not_match_frozen", report["failures"])

    def test_validates_and_canonicalizes_adapter_zip(self):
        adapter = self.downloads / "qwen3-8b-distractor-lora-v8 (1).zip"
        with zipfile.ZipFile(adapter, "w") as archive:
            archive.writestr("adapter_config.json", "{}")
            archive.writestr("adapter_model.safetensors", b"weights")
            archive.writestr("tokenizer_config.json", "{}")

        report = validate_downloads(self.downloads, self.root)

        self.assertTrue(report["adapter"]["zip_integrity"])
        self.assertTrue(report["adapter"]["required_entries_present"])
        self.assertGreater(report["adapter"]["local_zip_evidence"]["bytes"], 0)

        copied = copy_canonical_artifacts(self.downloads, self.root)
        self.assertIn("qwen3-8b-distractor-lora-v8.zip", copied)
        self.assertEqual(
            report["adapter"]["local_zip_evidence"]["sha256"],
            copied["qwen3-8b-distractor-lora-v8.zip"]["sha256"],
        )


if __name__ == "__main__":
    unittest.main()
