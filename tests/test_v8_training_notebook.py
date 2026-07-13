import ast
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

    def _stage_source(self, stage):
        return next(
            "".join(cell["source"])
            for cell in self.code_cells
            if stage in "".join(cell["source"])
        )

    def _generation_harness(self):
        class FakeDevice:
            type = "cuda"
            index = 0

        class FakeInputs:
            shape = (1, 2)

            def to(self, _device):
                return self

        class FakeTokenizer:
            def apply_chat_template(self, *_args, **_kwargs):
                return FakeInputs()

            def decode(self, tokens, **_kwargs):
                return ",".join(str(token) for token in tokens)

        class FakeGenerator:
            def __init__(self):
                self.seed = None

            def manual_seed(self, seed):
                self.seed = seed
                return self

        class FakeTorch:
            def __init__(self):
                self.state = 777
                self.manual_seed_calls = []
                self.cuda_seed_calls = []
                self.fork_calls = []
                self.random = self.FakeRandom(self)
                self.cuda = self.FakeCuda(self)

            class FakeRandom:
                def __init__(self, owner):
                    self.owner = owner

                def fork_rng(self, *, devices):
                    owner = self.owner

                    class ForkedRng:
                        def __enter__(self):
                            self.previous_state = owner.state
                            owner.fork_calls.append(tuple(devices))

                        def __exit__(self, _exc_type, _exc, _traceback):
                            owner.state = self.previous_state

                    return ForkedRng()

            class FakeCuda:
                def __init__(self, owner):
                    self.owner = owner

                def is_available(self):
                    return True

                def device_count(self):
                    return 1

                def current_device(self):
                    return 0

                def manual_seed_all(self, seed):
                    self.owner.cuda_seed_calls.append(seed)
                    self.owner.state = seed

            def manual_seed(self, seed):
                self.manual_seed_calls.append(seed)
                self.state = seed

            def Generator(self, **_kwargs):
                return FakeGenerator()

            def device(self, value):
                return value

        fake_torch = FakeTorch()

        class FakeModel:
            device = FakeDevice()

            def __init__(self):
                self.calls = []

            def generate(self, **kwargs):
                self.calls.append(dict(kwargs))
                seed = (
                    kwargs["generator"].seed
                    if "generator" in kwargs
                    else fake_torch.state
                )
                return [[10, 11, seed]]

        source = self._stage_source("# STAGE: base-litmus")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "generate_text"
        )
        namespace = {
            "SYSTEM_PROMPT": "system",
            "build_user": lambda question, correct, topic: (
                f"{question}|{correct}|{topic}"
            ),
            "tokenizer": FakeTokenizer(),
            "torch": fake_torch,
        }
        module = ast.fix_missing_locations(
            ast.Module(body=[function], type_ignores=[])
        )
        exec(compile(module, str(NOTEBOOK), "exec"), namespace)
        return namespace["generate_text"], fake_torch, FakeModel()

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

    def test_sampled_generation_uses_forked_global_rng(self):
        generate_text, fake_torch, model = self._generation_harness()
        question = {
            "question": "What is 2 + 2?",
            "correct": "4",
            "topic": "addition",
        }

        first = generate_text(model, question, sample=True, seed=101)
        repeated = generate_text(model, question, sample=True, seed=101)
        distinct = generate_text(model, question, sample=True, seed=102)

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, distinct)
        self.assertEqual(fake_torch.state, 777)
        self.assertEqual(fake_torch.manual_seed_calls, [101, 101, 102])
        self.assertEqual(fake_torch.cuda_seed_calls, [101, 101, 102])
        self.assertEqual(fake_torch.fork_calls, [(0,), (0,), (0,)])
        for call in model.calls:
            self.assertNotIn("generator", call)
            self.assertTrue(call["do_sample"])
            self.assertEqual(call["temperature"], 0.7)
            self.assertEqual(call["top_p"], 0.9)

        best_of_n = self._stage_source("# STAGE: best-of-n")
        self.assertIn(
            "100000 + item_index * BEST_OF_N + sample_index",
            best_of_n,
        )

    def test_greedy_generation_does_not_touch_rng_or_sampling_kwargs(self):
        generate_text, fake_torch, model = self._generation_harness()
        question = {
            "question": "What is 2 + 2?",
            "correct": "4",
            "topic": "addition",
        }

        generate_text(model, question, sample=False, seed=999)

        self.assertEqual(fake_torch.state, 777)
        self.assertEqual(fake_torch.manual_seed_calls, [])
        self.assertEqual(fake_torch.cuda_seed_calls, [])
        self.assertEqual(fake_torch.fork_calls, [])
        self.assertFalse(model.calls[0]["do_sample"])
        self.assertNotIn("generator", model.calls[0])
        self.assertNotIn("temperature", model.calls[0])
        self.assertNotIn("top_p", model.calls[0])

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
