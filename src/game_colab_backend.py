"""Pinned Colab-only model loading and deterministic completion decoding.

All optional GPU/runtime imports stay inside ``load_pinned_colab_backend`` so the
offline content pipeline and its tests never require Torch, PEFT, or Unsloth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping


BACKEND_VERSION = "glitch-rally-unsloth-backend-v1"
BACKEND_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
LOCKED_GENERATION_PARAMETERS = {
    "do_sample": False,
    "max_new_tokens": 512,
    "enable_thinking": False,
}

_IMMUTABLE_REVISION = re.compile(r"[0-9a-f]{40}")


class DeterministicUnslothBackend:
    """Callable adapter that preserves the exact decoded completion text."""

    def __init__(self, model: Any, tokenizer: Any, torch_module: Any) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self._torch = torch_module

    def __call__(
        self,
        system_prompt: str,
        user_prompt: str,
        generation_parameters: Mapping[str, Any],
    ) -> str:
        if dict(generation_parameters) != LOCKED_GENERATION_PARAMETERS:
            raise ValueError(
                "backend requires the locked deterministic parameters: "
                f"{LOCKED_GENERATION_PARAMETERS}"
            )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        input_ids = self.tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=generation_parameters["enable_thinking"],
            return_tensors="pt",
        ).to(next(self.model.parameters()).device)
        prompt_length = input_ids.shape[-1]
        with self._torch.inference_mode():
            output_ids = self.model.generate(
                input_ids=input_ids,
                do_sample=generation_parameters["do_sample"],
                max_new_tokens=generation_parameters["max_new_tokens"],
                pad_token_id=self.tokenizer.eos_token_id,
            )
        return self.tokenizer.decode(
            output_ids[0, prompt_length:],
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )


_PRODUCTION_RECEIPT_SEAL = object()
_TEST_RECEIPT_SEAL = object()


@dataclass(frozen=True)
class LoadedColabBackend:
    """Sealed backend plus the exact source and model identities it carries."""

    backend: Any
    model_id: str
    model_revision: str
    adapter_id: str
    adapter_revision: str
    backend_source_sha256: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal not in {_PRODUCTION_RECEIPT_SEAL, _TEST_RECEIPT_SEAL}:
            raise ValueError(
                "LoadedColabBackend receipts must come from the pinned loader or "
                "the explicit test factory"
            )
        if not callable(self.backend):
            raise ValueError("backend receipt must contain a callable backend")
        if not self.model_id or not self.adapter_id:
            raise ValueError("backend receipt model IDs must be nonempty")
        for name, revision in (
            ("model_revision", self.model_revision),
            ("adapter_revision", self.adapter_revision),
        ):
            if _IMMUTABLE_REVISION.fullmatch(revision) is None:
                raise ValueError(f"{name} must be an immutable 40-character commit")
        if re.fullmatch(r"[0-9a-f]{64}", self.backend_source_sha256) is None:
            raise ValueError("backend_source_sha256 must be a lowercase SHA-256")

    @property
    def is_test(self) -> bool:
        return self._seal is _TEST_RECEIPT_SEAL


def make_test_backend_receipt(
    *,
    backend: Any,
    model_id: str,
    model_revision: str,
    adapter_id: str,
    adapter_revision: str,
    backend_source_sha256: str,
) -> LoadedColabBackend:
    """Bind an injected fake backend without granting it production provenance."""

    if backend_source_sha256 == BACKEND_SOURCE_SHA256:
        raise ValueError("a test backend receipt cannot claim production source")
    return LoadedColabBackend(
        backend=backend,
        model_id=model_id,
        model_revision=model_revision,
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        backend_source_sha256=backend_source_sha256,
        _seal=_TEST_RECEIPT_SEAL,
    )


def _resolved_revision(model_info, repo_id: str, requested_revision: str) -> str:
    revision = model_info(repo_id, revision=requested_revision).sha
    if not isinstance(revision, str) or not _IMMUTABLE_REVISION.fullmatch(revision):
        raise RuntimeError(
            f"could not resolve an immutable 40-character commit for {repo_id}: "
            f"{revision!r}"
        )
    return revision


def load_pinned_colab_backend(
    *,
    model_id: str,
    model_requested_revision: str,
    adapter_id: str,
    adapter_requested_revision: str,
    max_seq_length: int = 2048,
    seed: int = 0,
) -> LoadedColabBackend:
    """Resolve, load once, and prepare the final base plus adapter for inference."""

    import random

    import numpy as np
    import torch
    from huggingface_hub import model_info
    from unsloth import FastLanguageModel
    from peft import PeftModel

    model_revision = _resolved_revision(
        model_info,
        model_id,
        model_requested_revision,
    )
    adapter_revision = _resolved_revision(
        model_info,
        adapter_id,
        adapter_requested_revision,
    )

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_id,
        revision=model_revision,
        max_seq_length=max_seq_length,
        dtype=None,
        load_in_4bit=True,
    )
    model = PeftModel.from_pretrained(
        model,
        adapter_id,
        revision=adapter_revision,
        is_trainable=False,
    )
    FastLanguageModel.for_inference(model)
    return LoadedColabBackend(
        backend=DeterministicUnslothBackend(model, tokenizer, torch),
        model_id=model_id,
        model_revision=model_revision,
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        backend_source_sha256=BACKEND_SOURCE_SHA256,
        _seal=_PRODUCTION_RECEIPT_SEAL,
    )
