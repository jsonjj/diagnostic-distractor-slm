import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks/export_wayline_gguf_colab.ipynb"
RUNBOOK = ROOT / "docs/wayline/WAYLINE_MODEL_EXPORT_RUNBOOK.md"


class WaylineModelExportNotebookTests(unittest.TestCase):
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
        cls.tags = [
            tag
            for cell in cls.cells
            for tag in cell.get("metadata", {}).get("tags", [])
        ]

    def test_is_a_clean_gpu_colab_notebook_with_ordered_stages(self):
        self.assertEqual(self.notebook["nbformat"], 4)
        self.assertEqual(self.notebook["metadata"]["accelerator"], "GPU")
        self.assertEqual(
            self.notebook["metadata"]["kernelspec"]["name"], "python3"
        )
        for cell in self.code_cells:
            self.assertIsNone(cell["execution_count"])
            self.assertEqual(cell["outputs"], [])

        expected = [
            "install",
            "configuration",
            "verified-inputs",
            "pinned-snapshots",
            "reference-corpus",
            "original-inference",
            "merge",
            "llama-cpp",
            "conversion",
            "gguf-inference",
            "parity-gate",
            "manifest",
            "package",
        ]
        positions = [self.tags.index(tag) for tag in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(positions), len(set(positions)))

    def test_pins_adapter_base_and_dependencies_and_requires_llama_sha(self):
        required = (
            'ADAPTER_ID = "j2ampn/qwen3-4b-distractor-lora-v7"',
            'ADAPTER_REVISION = "dd30dcea2755b7a2659faa908714e31335349408"',
            'EXPECTED_BASE_ID = "unsloth/Qwen3-4B-bnb-4bit"',
            'EXPECTED_BASE_REVISION = "cad0bedfdd862093a12af478cb974ab2addd0e0a"',
            '"unsloth==2026.7.1"',
            'QUANTIZATION = "Q4_K_M"',
            'LLAMA_CPP_REVISION = input(',
            "require_exact_commit(LLAMA_CPP_REVISION",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)
        self.assertNotRegex(
            self.code,
            r"(?:revision|REVISION)\s*=\s*[\"'](?:main|master|latest)[\"']",
        )

    def test_verifies_adapter_config_and_both_hub_commits_before_merge(self):
        for marker in (
            "model_info(",
            "snapshot_download(",
            'adapter_config["base_model_name_or_path"]',
            "adapter base_model_name_or_path disagrees",
            "resolved Hub commit disagrees",
            "EXPECTED_BASE_REVISION",
            "ADAPTER_REVISION",
            "save_pretrained_merged",
            'save_method="merged_16bit"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)
        for training_marker in (
            "SFTTrainer(",
            "Trainer(",
            ".train(",
            "get_peft_model(",
        ):
            self.assertNotIn(training_marker, self.code)

    def test_authenticates_exact_sixty_plus_six_parity_corpus(self):
        for marker in (
            "REFERENCE_PROMPT_COUNT = 60",
            "LEGACY_APPROVED_COUNT = 6",
            '"data/wayline/runtime/reference_prompts_v1.jsonl"',
            '"data/game/work/review_decisions_owner_v1.jsonl"',
            '"data/game/work/reviewed_v1.jsonl"',
            "verify_legacy_owner_approval(",
            "compiler.compile(",
            "DistractorVerifier(",
            "if len(reference_rows) != REFERENCE_PROMPT_COUNT",
            "if len(approved_cases) != LEGACY_APPROVED_COUNT",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)
        self.assertNotIn("src.buggy_procedures", self.code)
        self.assertNotIn("buggy_procedures.py", self.code)

    def test_converts_with_hashed_argument_arrays_and_q4_k_m(self):
        for marker in (
            '"convert_hf_to_gguf.py"',
            '"llama-quantize"',
            '"llama-server"',
            "subprocess.run(",
            '"Q4_K_M"',
            "commandSha256",
            "tokenizerAssetSha256",
            "licenseFilesSha256",
            "mergedModelTreeSha256",
            "ggufF16Sha256",
            "ggufSha256",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)
        self.assertNotIn("shell=True", self.code)

    def test_runs_deterministically_and_fails_closed_on_parity(self):
        for marker in (
            '"temperature": 0',
            '"seed": 0',
            '"enable_thinking": False',
            'MAX_GATE_REGRESSION = 0.05',
            '"exactlyThree"',
            '"distinctAnswers"',
            '"keySafe"',
            '"productVerifierPass"',
            '"acceptedProcedureIds"',
            '"approvedMapping"',
            "approved mapping changed",
            "gate regression exceeds 5 percentage points",
            '"passed": True',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)
        self.assertIn("raise RuntimeError", self.code)

    def test_emits_production_manifest_only_after_gate_and_packages_receipts(self):
        parity_position = self.tags.index("parity-gate")
        manifest_position = self.tags.index("manifest")
        package_position = self.tags.index("package")
        self.assertLess(parity_position, manifest_position)
        self.assertLess(manifest_position, package_position)

        manifest_cell = next(
            "".join(cell["source"])
            for cell in self.cells
            if "manifest" in cell.get("metadata", {}).get("tags", [])
        )
        self.assertIn('if parity_gate["passed"] is not True', manifest_cell)
        self.assertLess(
            manifest_cell.index('if parity_gate["passed"] is not True'),
            manifest_cell.index('"model_manifest_v1.json"'),
        )
        for marker in (
            '"schemaVersion": "wayline.model-manifest.v1"',
            '"platform": "macos-arm64"',
            '"quantization": QUANTIZATION',
            '"wayline_export_receipt_v1.json"',
            '"wayline_parity_report_v1.json"',
            '"THIRD_PARTY_NOTICES.md"',
            '"SHA256SUMS"',
            '"wayline_live_forge_q4_k_m.zip"',
            "files.download(",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)

    def test_never_embeds_or_prints_a_secret(self):
        self.assertIn("getpass(", self.code)
        self.assertNotRegex(self.code, r"hf_[A-Za-z0-9]{8,}")
        self.assertNotRegex(
            self.code,
            r"(?:HF_TOKEN|API_KEY|TOKEN)\s*=\s*[\"'][^\"']+[\"']",
        )
        self.assertNotIn("print(HF_TOKEN", self.code)
        self.assertNotIn("os.environ[\"HF_TOKEN\"]", self.code)

    def test_runbook_is_stepwise_free_tier_resumable_and_fail_closed(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for marker in (
            "T4 GPU",
            "free Colab",
            "wayline_export_inputs_v1.zip",
            "40-character llama.cpp commit SHA",
            "masked prompt",
            "Google Drive",
            "Resume after a disconnect",
            "60 Wayline reference prompts",
            "six owner-approved legacy encounters",
            "five percentage points",
            "model_manifest_v1.json does not exist unless every gate passes",
            "Do not commit",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        numbered_steps = re.findall(r"^\d+\. ", text, flags=re.MULTILINE)
        self.assertGreaterEqual(len(numbered_steps), 8)


if __name__ == "__main__":
    unittest.main()
