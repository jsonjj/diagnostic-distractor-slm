import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest

import src.game_colab_backend as backend_module
from src.game_colab_backend import (
    BACKEND_SOURCE_SHA256,
    DeterministicUnslothBackend,
    LoadedColabBackend,
    make_test_backend_receipt,
)


class FakeInputs:
    shape = (1, 4)

    def __init__(self):
        self.device = None

    def to(self, device):
        self.device = device
        return self


class FakeOutputIds:
    def __getitem__(self, key):
        if key != (0, slice(4, None, None)):
            raise AssertionError(f"unexpected generated-token slice: {key!r}")
        return "completion-token-ids"


class FakeTokenizer:
    eos_token_id = 99

    def __init__(self):
        self.chat_call = None
        self.decode_call = None

    def apply_chat_template(self, messages, **kwargs):
        self.chat_call = (messages, kwargs)
        return FakeInputs()

    def decode(self, token_ids, **kwargs):
        self.decode_call = (token_ids, kwargs)
        return " exact decoded completion \n"


class FakeModel:
    def __init__(self):
        self.generate_call = None

    def parameters(self):
        return iter([SimpleNamespace(device="cuda:0")])

    def generate(self, **kwargs):
        self.generate_call = kwargs
        return FakeOutputIds()


class FakeInferenceMode:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeTorch:
    @staticmethod
    def inference_mode():
        return FakeInferenceMode()


class ColabBackendTests(unittest.TestCase):
    def test_fake_backend_requires_an_explicit_nonproduction_receipt(self):
        fake = lambda system, user, parameters: "raw"

        receipt = make_test_backend_receipt(
            backend=fake,
            model_id="test/base",
            model_revision="1" * 40,
            adapter_id="test/adapter",
            adapter_revision="2" * 40,
            backend_source_sha256="f" * 64,
        )

        self.assertIsInstance(receipt, LoadedColabBackend)
        self.assertIs(receipt.backend, fake)
        self.assertTrue(receipt.is_test)
        self.assertEqual(receipt.backend_source_sha256, "f" * 64)

    def test_test_receipt_cannot_claim_the_reviewed_production_backend_hash(self):
        with self.assertRaisesRegex(ValueError, "cannot claim production"):
            make_test_backend_receipt(
                backend=lambda system, user, parameters: "raw",
                model_id="test/base",
                model_revision="1" * 40,
                adapter_id="test/adapter",
                adapter_revision="2" * 40,
                backend_source_sha256=BACKEND_SOURCE_SHA256,
            )

    def test_source_hash_matches_the_exact_backend_module_bytes(self):
        expected = hashlib.sha256(
            Path(backend_module.__file__).read_bytes()
        ).hexdigest()

        self.assertEqual(BACKEND_SOURCE_SHA256, expected)

    def test_generates_with_locked_chat_and_decode_semantics_without_trimming(self):
        model = FakeModel()
        tokenizer = FakeTokenizer()
        backend = DeterministicUnslothBackend(model, tokenizer, FakeTorch)

        result = backend(
            "system prompt",
            "user prompt",
            {
                "do_sample": False,
                "max_new_tokens": 512,
                "enable_thinking": False,
            },
        )

        self.assertEqual(result, " exact decoded completion \n")
        messages, chat_kwargs = tokenizer.chat_call
        self.assertEqual(
            messages,
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user prompt"},
            ],
        )
        self.assertEqual(
            chat_kwargs,
            {
                "tokenize": True,
                "add_generation_prompt": True,
                "enable_thinking": False,
                "return_tensors": "pt",
            },
        )
        self.assertEqual(
            model.generate_call,
            {
                "input_ids": model.generate_call["input_ids"],
                "do_sample": False,
                "max_new_tokens": 512,
                "pad_token_id": 99,
            },
        )
        self.assertEqual(model.generate_call["input_ids"].device, "cuda:0")
        self.assertEqual(
            tokenizer.decode_call,
            (
                "completion-token-ids",
                {
                    "skip_special_tokens": True,
                    "clean_up_tokenization_spaces": False,
                },
            ),
        )

    def test_refuses_any_generation_parameter_drift(self):
        model = FakeModel()
        backend = DeterministicUnslothBackend(model, FakeTokenizer(), FakeTorch)

        with self.assertRaisesRegex(ValueError, "locked deterministic parameters"):
            backend(
                "system",
                "user",
                {
                    "do_sample": True,
                    "max_new_tokens": 512,
                    "enable_thinking": False,
                },
            )

        self.assertIsNone(model.generate_call)

    def test_optional_gpu_dependencies_are_imported_only_by_the_loader(self):
        source = Path(backend_module.__file__).read_text(encoding="utf-8")
        loader_start = source.index("def load_pinned_colab_backend")
        module_prefix = source[:loader_start]

        self.assertNotIn("from unsloth import", module_prefix)
        self.assertNotIn("from peft import", module_prefix)
        self.assertNotIn("import torch", module_prefix)
        self.assertEqual(source.count("FastLanguageModel.from_pretrained("), 1)
        self.assertEqual(source.count("PeftModel.from_pretrained("), 1)


if __name__ == "__main__":
    unittest.main()
