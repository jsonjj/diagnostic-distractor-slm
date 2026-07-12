"""Immutable contracts at the local distractor-model boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol


_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)


class ProviderError(RuntimeError):
    """Typed provider failure that intentionally omits sensitive details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SlmRequest:
    question_id: str
    question: str
    correct_answer: str
    topic: str
    prompt_sha256: str


@dataclass(frozen=True, slots=True)
class RawSlmGeneration:
    text: str
    model_sha256: str
    prompt_sha256: str
    generated_at_utc: str
    adapter_identity_receipt_sha256: str = ""
    gguf_sha256: str = ""
    generator_identity_receipt_sha256: str = ""
    registry_id: str = ""
    prompt_template_sha256: str = ""


@dataclass(frozen=True, slots=True)
class PinnedSlmManifest:
    """Pinned runtime provenance.

    The GGUF fields are artifact digests. The adapter and generator fields are
    deterministic identity-receipt hashes, not hashes of adapter weights or a
    llama.cpp binary, because those artifact digests are absent from
    ``ModelManifest``.
    """

    model_id: str
    model_sha256: str
    adapter_identity_receipt_sha256: str
    gguf_sha256: str
    generator_identity_receipt_sha256: str
    registry_id: str
    prompt_template_sha256: str
    max_response_bytes: int = 16_384
    max_tokens: int = 768

    def __post_init__(self) -> None:
        if not self.model_id or not self.registry_id:
            raise ValueError("manifest identifiers cannot be empty")
        for field_name in (
            "model_sha256",
            "adapter_identity_receipt_sha256",
            "gguf_sha256",
            "generator_identity_receipt_sha256",
            "prompt_template_sha256",
        ):
            if not _SHA256.fullmatch(getattr(self, field_name)):
                raise ValueError(f"manifest {field_name} must be a lowercase SHA-256")
        if not 1_024 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("manifest response bound is invalid")
        if not 64 <= self.max_tokens <= 4_096:
            raise ValueError("manifest token bound is invalid")

    @classmethod
    def for_tests(cls) -> "PinnedSlmManifest":
        from ..slm_prompt import PROMPT_TEMPLATE_SHA256

        return cls(
            model_id="wayline-qwen3-4b-test",
            model_sha256="1" * 64,
            adapter_identity_receipt_sha256="2" * 64,
            gguf_sha256="3" * 64,
            generator_identity_receipt_sha256="4" * 64,
            registry_id="wayline-procedures-v1",
            prompt_template_sha256=PROMPT_TEMPLATE_SHA256,
        )

    @classmethod
    def from_model_manifest(
        cls,
        manifest: object,
        *,
        registry_id: str,
        max_response_bytes: int,
        max_tokens: int,
    ) -> "PinnedSlmManifest":
        """Combine an export manifest with explicit runtime-owned settings.

        ``ModelManifest`` owns the GGUF artifact digest and pinned identities.
        Registry identity and inference bounds are explicit here because the
        export manifest does not contain them.
        """

        from ..model_manifest import ModelManifest
        from ..slm_prompt import PROMPT_TEMPLATE_SHA256

        if not isinstance(manifest, ModelManifest):
            raise TypeError("manifest must be a validated ModelManifest")
        if manifest.prompt_sha256 != PROMPT_TEMPLATE_SHA256:
            raise ValueError("model manifest prompt receipt does not match runtime")
        adapter_receipt = _identity_receipt_sha256(
            kind="adapter",
            identity_id=manifest.adapter_id,
            revision=manifest.adapter_revision,
        )
        generator_receipt = _identity_receipt_sha256(
            kind="generator",
            identity_id="llama.cpp",
            revision=manifest.llama_cpp_revision,
        )
        return cls(
            model_id=manifest.gguf_file_name,
            model_sha256=manifest.gguf_sha256,
            adapter_identity_receipt_sha256=adapter_receipt,
            gguf_sha256=manifest.gguf_sha256,
            generator_identity_receipt_sha256=generator_receipt,
            registry_id=registry_id,
            prompt_template_sha256=manifest.prompt_sha256,
            max_response_bytes=max_response_bytes,
            max_tokens=max_tokens,
        )


class DistractorProvider(Protocol):
    async def generate(self, request: SlmRequest) -> RawSlmGeneration: ...


def _identity_receipt_sha256(*, kind: str, identity_id: str, revision: str) -> str:
    """Hash an exact, domain-separated identity receipt (not artifact bytes)."""

    payload = {
        "id": identity_id,
        "kind": kind,
        "revision": revision,
        "schema": "wayline.identity-receipt.v1",
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
