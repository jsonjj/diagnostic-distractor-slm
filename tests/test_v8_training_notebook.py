import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks/train_qwen3_distractor_v8.ipynb"
RUNBOOK = ROOT / "docs/V8_RUNBOOK.md"
TABLE_PLAN = ROOT / "TABLE_V8_PLAN.md"
BENCHMARK_PROTOCOL = ROOT / "docs/V8_BENCHMARK_PROTOCOL.md"


class V8TrainingNotebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cls.cells = cls.notebook["cells"]
        cls.code_cells = [
            cell for cell in cls.cells if cell["cell_type"] == "code"
        ]
        cls.code = "\n".join(
            "".join(cell["source"]) for cell in cls.code_cells
        )

    def test_notebook_is_clean_gpu_workflow_with_ordered_gates(self):
        self.assertEqual(self.notebook["nbformat"], 4)
        self.assertEqual(self.notebook["metadata"]["accelerator"], "GPU")
        for cell in self.code_cells:
            self.assertIsNone(cell["execution_count"])
            self.assertEqual(cell["outputs"], [])

        expected = [
            "# STAGE: install",
            "# STAGE: configuration",
            "# STAGE: data-integrity",
            "# STAGE: base-litmus",
            "# STAGE: training",
            "# STAGE: model-only",
            "# STAGE: best-of-n",
            "# STAGE: download",
        ]
        positions = [self.code.index(stage) for stage in expected]
        self.assertEqual(positions, sorted(positions))

    def test_defaults_to_8b_v8_and_keeps_tracks_separate(self):
        for marker in (
            'MODEL_NAME = "unsloth/Qwen3-8B-bnb-4bit"',
            'MODEL_REVISION = "1deaf68f694c40dbce295da300851729d759b21a"',
            'TRAIN_FILE = "data/processed/train_v8.jsonl"',
            '"predictions_v8_model_only.jsonl"',
            '"predictions_v8_best_of_n.jsonl"',
            "ensure_confidence_schema",
            "BEST_OF_N",
            "enable_thinking=False",
            "train_on_responses_only",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)

    def test_verifies_manifest_and_never_fits_on_final_benchmark(self):
        for marker in (
            "v8_manifest.json",
            "eval_v8_frozen.jsonl",
            "verify_manifest",
            "assert_no_leakage",
            "FROZEN_BENCHMARK",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)
        self.assertNotIn("fit_confidence(FROZEN_BENCHMARK", self.code)
        self.assertNotIn("train_dataset=FROZEN_BENCHMARK", self.code)

    def test_notebook_requires_role_separated_teacher_route(self):
        self.assertIn(
            'manifest.get("deterministic_teacher_filter_ready") is not True',
            self.code,
        )
        self.assertIn(
            'manifest.get("opus_judge_ready") is not False',
            self.code,
        )
        self.assertNotIn("generate, judge, filter", self.code)
        self.assertNotIn("python -m src.judge_v8", self.code)
        self.assertNotIn(
            "--calibration data/eval_out/opus_binding_calibration_v8.json",
            self.code,
        )

    def test_notebook_and_runbook_never_embed_gateway_secrets(self):
        self.assertNotRegex(self.code, r"(?:sk-|tfy-)[A-Za-z0-9_-]{12,}")
        self.assertNotRegex(
            self.code,
            r"TFY_API_KEY\s*=\s*[\"'][^\"']+[\"']",
        )

        text = RUNBOOK.read_text(encoding="utf-8")
        for marker in (
            "Rotate the compromised token",
            "anthropic-primary/claude-opus-4-8",
            "40% relative error-rate reduction",
            "model-only",
            "best-of-N",
            "NOT YET RUN",
            "Do not commit",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertGreaterEqual(
            len(re.findall(r"^\d+\. ", text, flags=re.MULTILINE)),
            10,
        )

    def test_runbook_documents_role_separated_teacher_recovery(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")
        table_plan = TABLE_PLAN.read_text(encoding="utf-8")
        for text in (runbook, table_plan):
            for marker in (
                "deterministic_teacher_filter",
                "opus_judge_ready",
                "--deterministic-teacher",
                "--require-deterministic-teacher",
                "32",
                "98",
            ):
                with self.subTest(marker=marker):
                    self.assertIn(marker, text)
            self.assertNotIn("v8_teacher_verdicts_opus.jsonl", text)

        protocol = BENCHMARK_PROTOCOL.read_text(encoding="utf-8")
        self.assertIn("independent calibrated judge", protocol)
        self.assertIn("applied identically", protocol)
        self.assertNotIn("otherwise the Opus judge", protocol)


if __name__ == "__main__":
    unittest.main()
