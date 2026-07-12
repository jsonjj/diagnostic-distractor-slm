"""Immutable validation for exported Wayline GGUF model manifests."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Revision = Annotated[
    str,
    Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$"),
]
Sha256 = Annotated[
    str,
    Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]


class DuplicateManifestKeyError(ValueError):
    """Raised when raw manifest JSON repeats an object key."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"duplicate model manifest key: {key}")


class ModelManifest(BaseModel):
    """Pinned receipt for exactly one exported Wayline GGUF artifact."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        frozen=True,
    )

    schema_version: Literal["wayline.model-manifest.v1"] = Field(
        alias="schemaVersion"
    )
    base_model_id: Literal["unsloth/Qwen3-4B-bnb-4bit"] = Field(
        alias="baseModelId"
    )
    base_model_revision: Revision = Field(alias="baseModelRevision")
    adapter_id: Literal["j2ampn/qwen3-4b-distractor-lora-v7"] = Field(
        alias="adapterId"
    )
    adapter_revision: Revision = Field(alias="adapterRevision")
    llama_cpp_revision: Revision = Field(alias="llamaCppRevision")
    quantization: Literal["Q4_K_M"]
    gguf_file_name: Annotated[
        str,
        Field(
            alias="ggufFileName",
            min_length=6,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.gguf$",
        ),
    ]
    gguf_sha256: Sha256 = Field(alias="ggufSha256")
    prompt_sha256: Sha256 = Field(alias="promptSha256")
    tokenizer_sha256: Sha256 = Field(alias="tokenizerSha256")
    context_size: int = Field(alias="contextSize", ge=512, le=8192)
    thread_count: int = Field(alias="threadCount", ge=1, le=32)
    platform: Literal["macos-arm64"]

    @field_validator("gguf_file_name")
    @classmethod
    def file_name_is_local_and_nonsecret(cls, value: str) -> str:
        lowered = value.casefold()
        forbidden = ("latest", "secret", "hf_", "api_key", "apikey")
        if any(marker in lowered for marker in forbidden):
            raise ValueError("ggufFileName contains a forbidden mutable or secret marker")
        return value


def parse_model_manifest(payload: str | bytes | bytearray) -> ModelManifest:
    """Decode duplicate-free standard JSON and validate an immutable manifest."""

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        decoded: dict[str, object] = {}
        for key, value in pairs:
            if key in decoded:
                raise DuplicateManifestKeyError(key)
            decoded[key] = value
        return decoded

    def reject_nonstandard_number(value: str) -> object:
        raise ValueError(f"non-standard JSON numeric constant: {value}")

    decoded = json.loads(
        payload,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_nonstandard_number,
    )
    return ModelManifest.model_validate(decoded)
