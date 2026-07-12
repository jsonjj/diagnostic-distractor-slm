"""Validated, cache-safe seam between distractor verification and quiz delivery.

The raw model response is accepted only transiently by :meth:`from_verified` so
its digest and receipts can be checked.  It is never retained by the returned
bundle or written by the private serializer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from fractions import Fraction
import hashlib
import json
import re
import secrets
from typing import Any
import unicodedata

from .curriculum import HoldoutReceipt
from .distractor_verifier import (
    VerifiedDistractor,
    VerifiedDistractorSet,
    VerifiedOption,
)
from .providers.distractor import PinnedSlmManifest, RawSlmGeneration
from .question_kernel import (
    CanonicalAnswer,
    CompileRequest,
    QuestionBlueprint,
    QuestionCompiler,
)
from .slm_prompt import build_slm_request


VERIFIED_QUESTION_SCHEMA_VERSION = "wayline.verified-question.v1"
SEMANTIC_CONTENT_SCHEMA_VERSION = "wayline.semantic-question.v1"
VERIFIER_VERSION = "wayline.distractor-verifier.v1"
_VERIFIER_RECEIPT_PAYLOAD = {
    "acceptanceContract": "wayline.runtime.acceptance-algorithm.v1",
    "schemaVersion": "wayline.verifier-receipt.v1",
    "verifierVersion": VERIFIER_VERSION,
}

_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_OPTION_ID = re.compile(r"opt_[0-9a-f]{24}", re.ASCII)
_ITEM_INSTANCE_ID = re.compile(r"item_[0-9a-f]{32}", re.ASCII)
_MAX_PRIVATE_BYTES = 512 * 1024


class _CanonicalJsonError(ValueError):
    pass


def _canonical_json(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _CanonicalJsonError("value is not canonical UTF-8 JSON") from exc
    return encoded


def _sha256_json(value: Any) -> str:
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _CanonicalJsonError("value cannot be canonically hashed") from exc
    return hashlib.sha256(encoded).hexdigest()


VERIFIER_RECEIPT_SHA256 = _sha256_json(_VERIFIER_RECEIPT_PAYLOAD)


class VerifiedQuestionError(ValueError):
    """Fail-closed error with a stable, non-sensitive reason code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _is_nonempty_safe_text(value: object, *, maximum: int = 1024) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value) <= maximum
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 48:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


@dataclass(frozen=True, slots=True)
class QuestionProvenance:
    """Explicit receipts retained after the untrusted response is discarded."""

    model_id: str
    model_sha256: str
    adapter_identity_receipt_sha256: str
    gguf_sha256: str
    generator_identity_receipt_sha256: str
    prompt_sha256: str
    prompt_template_sha256: str
    registry_id: str
    generated_at_utc: str
    generation_sha256: str
    generation_receipt_sha256: str
    verifier_version: str
    verifier_receipt_sha256: str

    def __post_init__(self) -> None:
        if not _is_nonempty_safe_text(self.model_id, maximum=256):
            raise ValueError("model_id is invalid")
        if not _is_nonempty_safe_text(self.registry_id, maximum=128):
            raise ValueError("registry_id is invalid")
        for name in (
            "model_sha256",
            "adapter_identity_receipt_sha256",
            "gguf_sha256",
            "generator_identity_receipt_sha256",
            "prompt_sha256",
            "prompt_template_sha256",
            "generation_sha256",
            "generation_receipt_sha256",
            "verifier_receipt_sha256",
        ):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} is not a canonical SHA-256")
        if not _is_utc_timestamp(self.generated_at_utc):
            raise ValueError("generated_at_utc is not UTC")
        if self.verifier_version != VERIFIER_VERSION:
            raise ValueError("verifier version is not supported")
        if self.verifier_receipt_sha256 != VERIFIER_RECEIPT_SHA256:
            raise ValueError("verifier receipt is not supported")


def mint_item_instance_id() -> str:
    """Mint a 128-bit opaque ID for one item placement in one quiz batch."""

    return "item_" + secrets.token_hex(16)


@dataclass(frozen=True, slots=True)
class PlacedOption:
    """One per-placement public option with no stable source identifier."""

    option_id: str
    display_text: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"opt_[0-9a-f]{32}", self.option_id, re.ASCII):
            raise ValueError("placed option ID is invalid")
        if not _is_nonempty_safe_text(self.display_text, maximum=64):
            raise ValueError("placed option display is invalid")


@dataclass(frozen=True, slots=True)
class PlacedOptionBinding:
    """Server-sealed mapping from an instance choice back to verified truth."""

    instance_option_id: str
    source_option_id: str
    procedure_id: str | None

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"opt_[0-9a-f]{32}", self.instance_option_id, re.ASCII
        ):
            raise ValueError("binding instance option ID is invalid")
        if not _OPTION_ID.fullmatch(self.source_option_id):
            raise ValueError("binding source option ID is invalid")
        if self.procedure_id is not None and not _is_nonempty_safe_text(
            self.procedure_id, maximum=128
        ):
            raise ValueError("binding procedure ID is invalid")


@dataclass(frozen=True, slots=True)
class PlacedVerifiedQuestion:
    """Per-batch rekeyed and shuffled view plus server-only scoring truth."""

    item_instance_id: str
    prompt: str
    options: tuple[PlacedOption, ...]
    correct_option_id: str
    bindings: tuple[PlacedOptionBinding, ...]
    source_bundle_sha256: str
    source_cache_content_sha256: str
    source_semantic_content_sha256: str
    placement_sha256: str

    def __post_init__(self) -> None:
        if not _ITEM_INSTANCE_ID.fullmatch(self.item_instance_id):
            raise ValueError("placement item ID is invalid")
        if not _is_nonempty_safe_text(self.prompt, maximum=1024):
            raise ValueError("placement prompt is invalid")
        if (
            not _SHA256.fullmatch(self.source_bundle_sha256)
            or not _SHA256.fullmatch(self.source_cache_content_sha256)
            or not _SHA256.fullmatch(self.source_semantic_content_sha256)
        ):
            raise ValueError("placement source receipt is invalid")
        if not isinstance(self.options, tuple) or len(self.options) != 4:
            raise ValueError("placement must contain four options")
        if any(not isinstance(option, PlacedOption) for option in self.options):
            raise ValueError("placement option type is invalid")
        if not isinstance(self.bindings, tuple) or len(self.bindings) != 4:
            raise ValueError("placement must contain four bindings")
        if any(
            not isinstance(binding, PlacedOptionBinding)
            for binding in self.bindings
        ):
            raise ValueError("placement binding type is invalid")
        option_ids = tuple(option.option_id for option in self.options)
        binding_ids = tuple(binding.instance_option_id for binding in self.bindings)
        if (
            len(set(option_ids)) != 4
            or len({option.display_text for option in self.options}) != 4
            or binding_ids != option_ids
            or len({binding.source_option_id for binding in self.bindings}) != 4
            or self.correct_option_id not in set(option_ids)
        ):
            raise ValueError("placement options and bindings are inconsistent")
        correct_bindings = tuple(
            binding for binding in self.bindings if binding.procedure_id is None
        )
        if (
            len(correct_bindings) != 1
            or correct_bindings[0].instance_option_id != self.correct_option_id
        ):
            raise ValueError("placement answer key is inconsistent")
        expected_hash = _sha256_json(_placement_unsigned_payload(self))
        if self.placement_sha256 != expected_hash:
            raise ValueError("placement receipt is inconsistent")

    def binding_for(self, instance_option_id: str) -> PlacedOptionBinding:
        for binding in self.bindings:
            if binding.instance_option_id == instance_option_id:
                return binding
        raise VerifiedQuestionError("invalid_instance_option_id")

    def is_correct(self, instance_option_id: str) -> bool:
        self.binding_for(instance_option_id)
        return instance_option_id == self.correct_option_id

    def public_payload(self) -> dict[str, Any]:
        return {
            "itemId": self.item_instance_id,
            "prompt": self.prompt,
            "options": [
                {
                    "optionId": option.option_id,
                    "displayText": option.display_text,
                }
                for option in self.options
            ],
        }


def _placement_unsigned_payload(value: PlacedVerifiedQuestion) -> dict[str, Any]:
    return {
        "itemInstanceId": value.item_instance_id,
        "prompt": value.prompt,
        "options": [
            {"optionId": option.option_id, "displayText": option.display_text}
            for option in value.options
        ],
        "correctOptionId": value.correct_option_id,
        "bindings": [
            {
                "instanceOptionId": binding.instance_option_id,
                "sourceOptionId": binding.source_option_id,
                "procedureId": binding.procedure_id,
            }
            for binding in value.bindings
        ],
        "sourceBundleSha256": value.source_bundle_sha256,
        "sourceCacheContentSha256": value.source_cache_content_sha256,
        "sourceSemanticContentSha256": value.source_semantic_content_sha256,
        "schemaVersion": "wayline.question-placement.v1",
    }


@dataclass(frozen=True, slots=True, init=False)
class VerifiedQuestionBundle:
    """Immutable, fully revalidated question truth suitable for a sealed cache."""

    schema_version: str
    request: CompileRequest
    blueprint: QuestionBlueprint
    options: tuple[VerifiedOption, ...]
    correct_option_id: str
    verified_distractors: tuple[VerifiedDistractor, ...]
    source_bundle_sha256: str
    template_id: str
    context_id: str
    operand_signature: str
    provenance: QuestionProvenance
    semantic_content_sha256: str
    cache_content_sha256: str

    @classmethod
    def from_verified(
        cls,
        *,
        compiler: QuestionCompiler,
        request: CompileRequest,
        blueprint: QuestionBlueprint,
        verified: VerifiedDistractorSet,
        generation: RawSlmGeneration,
        manifest: PinnedSlmManifest,
    ) -> "VerifiedQuestionBundle":
        """Seal a verifier result while discarding the raw generation text."""

        if not isinstance(generation, RawSlmGeneration):
            raise VerifiedQuestionError("provenance_mismatch")
        if not isinstance(manifest, PinnedSlmManifest):
            raise VerifiedQuestionError("provenance_mismatch")
        try:
            generation_sha256 = hashlib.sha256(
                generation.text.encode("utf-8")
            ).hexdigest()
        except (AttributeError, UnicodeEncodeError):
            raise VerifiedQuestionError("provenance_mismatch") from None

        receipt = _generation_receipt_payload(
            model_sha256=generation.model_sha256,
            adapter_identity_receipt_sha256=(
                generation.adapter_identity_receipt_sha256
            ),
            gguf_sha256=generation.gguf_sha256,
            generator_identity_receipt_sha256=(
                generation.generator_identity_receipt_sha256
            ),
            prompt_sha256=generation.prompt_sha256,
            prompt_template_sha256=generation.prompt_template_sha256,
            registry_id=generation.registry_id,
            generated_at_utc=generation.generated_at_utc,
            generation_sha256=generation_sha256,
        )
        try:
            provenance = QuestionProvenance(
                model_id=manifest.model_id,
                model_sha256=generation.model_sha256,
                adapter_identity_receipt_sha256=(
                    generation.adapter_identity_receipt_sha256
                ),
                gguf_sha256=generation.gguf_sha256,
                generator_identity_receipt_sha256=(
                    generation.generator_identity_receipt_sha256
                ),
                prompt_sha256=generation.prompt_sha256,
                prompt_template_sha256=generation.prompt_template_sha256,
                registry_id=generation.registry_id,
                generated_at_utc=generation.generated_at_utc,
                generation_sha256=generation_sha256,
                generation_receipt_sha256=_sha256_json(receipt),
                verifier_version=VERIFIER_VERSION,
                verifier_receipt_sha256=VERIFIER_RECEIPT_SHA256,
            )
        except (TypeError, ValueError):
            raise VerifiedQuestionError("provenance_mismatch") from None

        return cls._build(
            compiler=compiler,
            manifest=manifest,
            request=request,
            blueprint=blueprint,
            verified=verified,
            provenance=provenance,
        )

    @classmethod
    def from_private_json(
        cls,
        payload: str | bytes | bytearray,
        *,
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
    ) -> "VerifiedQuestionBundle":
        """Decode canonical data only, then replay every trust check."""

        try:
            if isinstance(payload, str):
                encoded = payload.encode("utf-8")
                text = payload
            elif isinstance(payload, (bytes, bytearray)):
                encoded = bytes(payload)
                text = encoded.decode("utf-8")
            else:
                raise TypeError("payload must be text or bytes")
            if len(encoded) > _MAX_PRIVATE_BYTES:
                raise ValueError("private payload exceeds size bound")
            decoded = json.loads(
                text,
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_nonstandard_number,
            )
            top = _expect_object(
                decoded,
                {
                    "schemaVersion",
                    "compileRequest",
                    "blueprint",
                    "verifiedSet",
                    "templateId",
                    "contextId",
                    "operandSignature",
                    "semanticContentSha256",
                    "provenance",
                    "cacheContentSha256",
                },
            )
            if top["schemaVersion"] != VERIFIED_QUESTION_SCHEMA_VERSION:
                raise ValueError("unsupported verified-question schema")
            cache_content_sha256 = _expect_sha256(top["cacheContentSha256"])
            semantic_content_sha256 = _expect_sha256(
                top["semanticContentSha256"]
            )
        except (TypeError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
            raise VerifiedQuestionError("invalid_private_payload") from None

        unsigned_input = dict(top)
        unsigned_input.pop("cacheContentSha256")
        try:
            computed_cache_sha256 = _sha256_json(unsigned_input)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise VerifiedQuestionError("invalid_private_payload") from None
        if computed_cache_sha256 != cache_content_sha256:
            raise VerifiedQuestionError("canonical_hash_mismatch")

        try:
            request = _request_from_dict(top["compileRequest"])
            blueprint = _blueprint_from_dict(top["blueprint"])
            verified = _verified_from_dict(top["verifiedSet"], blueprint)
            provenance = _provenance_from_dict(top["provenance"])
            template_id = _expect_text(top["templateId"], maximum=128)
            context_id = _expect_text(top["contextId"], maximum=128)
            operand_signature = _expect_sha256(top["operandSignature"])
        except (TypeError, ValueError, KeyError):
            raise VerifiedQuestionError("invalid_private_payload") from None

        return cls._build(
            compiler=compiler,
            manifest=manifest,
            request=request,
            blueprint=blueprint,
            verified=verified,
            provenance=provenance,
            declared_template_id=template_id,
            declared_context_id=context_id,
            declared_operand_signature=operand_signature,
            declared_semantic_content_sha256=semantic_content_sha256,
            declared_cache_content_sha256=cache_content_sha256,
        )

    @classmethod
    def _build(
        cls,
        *,
        compiler: QuestionCompiler,
        manifest: PinnedSlmManifest,
        request: CompileRequest,
        blueprint: QuestionBlueprint,
        verified: VerifiedDistractorSet,
        provenance: QuestionProvenance,
        declared_template_id: str | None = None,
        declared_context_id: str | None = None,
        declared_operand_signature: str | None = None,
        declared_semantic_content_sha256: str | None = None,
        declared_cache_content_sha256: str | None = None,
    ) -> "VerifiedQuestionBundle":
        template = _validate_blueprint(compiler, request, blueprint)
        _validate_provenance(
            compiler=compiler,
            manifest=manifest,
            blueprint=blueprint,
            verified=verified,
            provenance=provenance,
        )
        _validate_verified_set(compiler, blueprint, verified)

        template_id = blueprint.template_id
        context_id = template.context_id
        operand_signature = _operand_signature(blueprint)
        if (
            declared_template_id is not None
            and declared_template_id != template_id
        ) or (
            declared_context_id is not None
            and declared_context_id != context_id
        ) or (
            declared_operand_signature is not None
            and declared_operand_signature != operand_signature
        ):
            raise VerifiedQuestionError("bundle_metadata_mismatch")

        semantic_content_sha256 = _sha256_json(
            _semantic_content_payload(
                blueprint=blueprint,
                verified=verified,
                context_id=context_id,
            )
        )
        if (
            declared_semantic_content_sha256 is not None
            and declared_semantic_content_sha256 != semantic_content_sha256
        ):
            raise VerifiedQuestionError("semantic_content_mismatch")

        unsigned = _private_unsigned_payload(
            request=request,
            blueprint=blueprint,
            verified=verified,
            template_id=template_id,
            context_id=context_id,
            operand_signature=operand_signature,
            semantic_content_sha256=semantic_content_sha256,
            provenance=provenance,
        )
        cache_content_sha256 = _sha256_json(unsigned)
        if (
            declared_cache_content_sha256 is not None
            and declared_cache_content_sha256 != cache_content_sha256
        ):
            raise VerifiedQuestionError("canonical_hash_mismatch")

        instance = object.__new__(cls)
        values = {
            "schema_version": VERIFIED_QUESTION_SCHEMA_VERSION,
            "request": request,
            "blueprint": blueprint,
            "options": tuple(verified.options),
            "correct_option_id": verified.correct_option_id,
            "verified_distractors": tuple(verified.verified_distractors),
            "source_bundle_sha256": verified.bundle_sha256,
            "template_id": template_id,
            "context_id": context_id,
            "operand_signature": operand_signature,
            "provenance": provenance,
            "semantic_content_sha256": semantic_content_sha256,
            "cache_content_sha256": cache_content_sha256,
        }
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        return instance

    def place(self, item_instance_id: str) -> PlacedVerifiedQuestion:
        """Rekey and shuffle one cache/live source for an idempotent batch placement."""

        if not isinstance(item_instance_id, str) or not _ITEM_INSTANCE_ID.fullmatch(
            item_instance_id
        ):
            raise VerifiedQuestionError("invalid_item_instance_id")
        stable_ids = {
            self.blueprint.question_id,
            self.blueprint.content_sha256,
            self.source_bundle_sha256,
            self.semantic_content_sha256,
            self.cache_content_sha256,
        }
        if item_instance_id in stable_ids:
            raise VerifiedQuestionError("invalid_item_instance_id")

        placement_seed = hashlib.sha256(
            (
                "wayline.question-placement.v1|"
                + self.source_bundle_sha256
                + "|"
                + self.cache_content_sha256
                + "|"
                + self.semantic_content_sha256
                + "|"
                + item_instance_id
            ).encode("ascii")
        ).hexdigest()
        route_by_source_id = {
            route.option_id: route.procedure_id
            for route in self.verified_distractors
        }
        shuffled = sorted(
            self.options,
            key=lambda option: hashlib.sha256(
                f"{placement_seed}|order|{option.option_id}".encode("ascii")
            ).digest(),
        )
        placed_options: list[PlacedOption] = []
        bindings: list[PlacedOptionBinding] = []
        correct_option_id = ""
        for source in shuffled:
            instance_option_id = "opt_" + hashlib.sha256(
                f"{placement_seed}|option|{source.option_id}".encode("ascii")
            ).hexdigest()[:32]
            placed_options.append(
                PlacedOption(
                    option_id=instance_option_id,
                    display_text=source.display_text,
                )
            )
            procedure_id = route_by_source_id.get(source.option_id)
            bindings.append(
                PlacedOptionBinding(
                    instance_option_id=instance_option_id,
                    source_option_id=source.option_id,
                    procedure_id=procedure_id,
                )
            )
            if source.option_id == self.correct_option_id:
                correct_option_id = instance_option_id

        provisional = {
            "item_instance_id": item_instance_id,
            "prompt": self.blueprint.prompt,
            "options": tuple(placed_options),
            "correct_option_id": correct_option_id,
            "bindings": tuple(bindings),
            "source_bundle_sha256": self.source_bundle_sha256,
            "source_cache_content_sha256": self.cache_content_sha256,
            "source_semantic_content_sha256": self.semantic_content_sha256,
        }
        receipt_payload = {
            "itemInstanceId": provisional["item_instance_id"],
            "prompt": provisional["prompt"],
            "options": [
                {"optionId": option.option_id, "displayText": option.display_text}
                for option in provisional["options"]
            ],
            "correctOptionId": provisional["correct_option_id"],
            "bindings": [
                {
                    "instanceOptionId": binding.instance_option_id,
                    "sourceOptionId": binding.source_option_id,
                    "procedureId": binding.procedure_id,
                }
                for binding in provisional["bindings"]
            ],
            "sourceBundleSha256": provisional["source_bundle_sha256"],
            "sourceCacheContentSha256": provisional[
                "source_cache_content_sha256"
            ],
            "sourceSemanticContentSha256": provisional[
                "source_semantic_content_sha256"
            ],
            "schemaVersion": "wayline.question-placement.v1",
        }
        return PlacedVerifiedQuestion(
            **provisional,
            placement_sha256=_sha256_json(receipt_payload),
        )

    def public_payload(self, item_instance_id: str) -> dict[str, Any]:
        """Return a rekeyed child-facing form for a fresh batch-owned item ID."""

        return self.place(item_instance_id).public_payload()

    def to_private_json(self) -> str:
        """Serialize deterministic data-only JSON for a reviewed SQLite row."""

        payload = _private_unsigned_payload(
            request=self.request,
            blueprint=self.blueprint,
            verified=_verified_set_from_bundle(self),
            template_id=self.template_id,
            context_id=self.context_id,
            operand_signature=self.operand_signature,
            semantic_content_sha256=self.semantic_content_sha256,
            provenance=self.provenance,
        )
        payload["cacheContentSha256"] = self.cache_content_sha256
        return _canonical_json(payload)


def _validate_blueprint(
    compiler: QuestionCompiler,
    request: CompileRequest,
    blueprint: QuestionBlueprint,
) -> Any:
    if not isinstance(compiler, QuestionCompiler):
        raise VerifiedQuestionError("blueprint_mismatch")
    if not isinstance(request, CompileRequest) or not isinstance(
        blueprint, QuestionBlueprint
    ):
        raise VerifiedQuestionError("blueprint_mismatch")
    try:
        recomputed = compiler.compile(request)
    except Exception:
        raise VerifiedQuestionError("blueprint_mismatch") from None
    if recomputed != blueprint:
        raise VerifiedQuestionError("blueprint_mismatch")
    try:
        family = compiler.curriculum.families[blueprint.family_id]
        template = next(
            item
            for item in family.templates
            if item.template_id == blueprint.template_id
            and item.revision == blueprint.template_revision
        )
    except (KeyError, StopIteration):
        raise VerifiedQuestionError("blueprint_mismatch") from None
    return template


def _validate_provenance(
    *,
    compiler: QuestionCompiler,
    manifest: PinnedSlmManifest,
    blueprint: QuestionBlueprint,
    verified: VerifiedDistractorSet,
    provenance: QuestionProvenance,
) -> None:
    if not isinstance(manifest, PinnedSlmManifest) or not isinstance(
        provenance, QuestionProvenance
    ):
        raise VerifiedQuestionError("provenance_mismatch")
    expected_prompt = build_slm_request(blueprint).prompt_sha256
    manifest_values = (
        manifest.model_id,
        manifest.model_sha256,
        manifest.adapter_identity_receipt_sha256,
        manifest.gguf_sha256,
        manifest.generator_identity_receipt_sha256,
        expected_prompt,
        manifest.prompt_template_sha256,
        manifest.registry_id,
    )
    actual_values = (
        provenance.model_id,
        provenance.model_sha256,
        provenance.adapter_identity_receipt_sha256,
        provenance.gguf_sha256,
        provenance.generator_identity_receipt_sha256,
        provenance.prompt_sha256,
        provenance.prompt_template_sha256,
        provenance.registry_id,
    )
    if manifest_values != actual_values:
        raise VerifiedQuestionError("provenance_mismatch")
    if manifest.registry_id != compiler.registry.registry_id:
        raise VerifiedQuestionError("provenance_mismatch")
    expected_receipt = _sha256_json(
        _generation_receipt_payload(
            model_sha256=provenance.model_sha256,
            adapter_identity_receipt_sha256=(
                provenance.adapter_identity_receipt_sha256
            ),
            gguf_sha256=provenance.gguf_sha256,
            generator_identity_receipt_sha256=(
                provenance.generator_identity_receipt_sha256
            ),
            prompt_sha256=provenance.prompt_sha256,
            prompt_template_sha256=provenance.prompt_template_sha256,
            registry_id=provenance.registry_id,
            generated_at_utc=provenance.generated_at_utc,
            generation_sha256=provenance.generation_sha256,
        )
    )
    if (
        expected_receipt != provenance.generation_receipt_sha256
        or verified.generation_sha256 != provenance.generation_sha256
        or verified.receipt_sha256 != provenance.generation_receipt_sha256
        or provenance.verifier_version != VERIFIER_VERSION
        or provenance.verifier_receipt_sha256 != VERIFIER_RECEIPT_SHA256
    ):
        raise VerifiedQuestionError("provenance_mismatch")


def _validate_verified_set(
    compiler: QuestionCompiler,
    blueprint: QuestionBlueprint,
    verified: VerifiedDistractorSet,
) -> None:
    if not isinstance(verified, VerifiedDistractorSet):
        raise VerifiedQuestionError("verified_set_mismatch")
    if (
        verified.question_id != blueprint.question_id
        or verified.prompt != blueprint.prompt
        or verified.blueprint_sha256 != blueprint.content_sha256
        or not _SHA256.fullmatch(verified.generation_sha256)
        or not _SHA256.fullmatch(verified.receipt_sha256)
        or not _SHA256.fullmatch(verified.bundle_sha256)
    ):
        raise VerifiedQuestionError("verified_set_mismatch")

    options = verified.options
    if not isinstance(options, tuple) or len(options) != 4:
        raise VerifiedQuestionError("verified_set_mismatch")
    if any(not isinstance(option, VerifiedOption) for option in options):
        raise VerifiedQuestionError("verified_set_mismatch")
    if any(
        not _OPTION_ID.fullmatch(option.option_id)
        or not _is_nonempty_safe_text(option.display_text, maximum=64)
        for option in options
    ):
        raise VerifiedQuestionError("verified_set_mismatch")
    option_by_id = {option.option_id: option for option in options}
    if len(option_by_id) != 4 or len({option.display_text for option in options}) != 4:
        raise VerifiedQuestionError("verified_set_mismatch")
    if verified.correct_option_id not in option_by_id:
        raise VerifiedQuestionError("verified_set_mismatch")
    if (
        option_by_id[verified.correct_option_id].display_text
        != blueprint.canonical_answer.display
    ):
        raise VerifiedQuestionError("verified_set_mismatch")

    routes = verified.verified_distractors
    if not isinstance(routes, tuple) or len(routes) != 3:
        raise VerifiedQuestionError("verified_set_mismatch")
    if any(not isinstance(route, VerifiedDistractor) for route in routes):
        raise VerifiedQuestionError("verified_set_mismatch")
    route_ids = {route.option_id for route in routes}
    expected_route_ids = set(option_by_id) - {verified.correct_option_id}
    procedure_ids = {route.procedure_id for route in routes}
    if (
        len(route_ids) != 3
        or route_ids != expected_route_ids
        or len(procedure_ids) != 3
        or not procedure_ids.issubset(set(blueprint.allowed_procedure_ids))
    ):
        raise VerifiedQuestionError("verified_set_mismatch")

    try:
        for route in routes:
            if not all(
                _is_nonempty_safe_text(value, maximum=512)
                for value in (
                    route.procedure_id,
                    route.canonical_label,
                    route.computation,
                    route.feedback,
                    route.reliable_method,
                )
            ):
                raise ValueError("unsafe route metadata")
            evaluated = compiler.registry.evaluate(route.procedure_id, blueprint)
            expected = (
                evaluated.display,
                compiler.registry.canonical_label(route.procedure_id),
                compiler.registry.canonical_computation(route.procedure_id, blueprint),
                compiler.registry.canonical_feedback(route.procedure_id),
                compiler.registry.reliable_method(route.procedure_id),
            )
            actual = (
                option_by_id[route.option_id].display_text,
                route.canonical_label,
                route.computation,
                route.feedback,
                route.reliable_method,
            )
            if actual != expected:
                raise ValueError("route metadata does not match registry")
    except Exception:
        raise VerifiedQuestionError("verified_set_mismatch") from None

    sealed_payload = {
        "question_id": verified.question_id,
        "options": [asdict(option) for option in options],
        "correct_option_id": verified.correct_option_id,
        "verified_distractors": [asdict(route) for route in routes],
        "blueprint_sha256": verified.blueprint_sha256,
        "generation_sha256": verified.generation_sha256,
        "receipt_sha256": verified.receipt_sha256,
    }
    if _sha256_json(sealed_payload) != verified.bundle_sha256:
        raise VerifiedQuestionError("verified_set_mismatch")


def _operand_signature(blueprint: QuestionBlueprint) -> str:
    return _sha256_json(
        {
            "familyId": blueprint.family_id,
            "operandNames": list(blueprint.operand_names),
            "operands": list(blueprint.operands),
            "schemaVersion": "wayline.operand-signature.v1",
        }
    )


def _semantic_content_payload(
    *,
    blueprint: QuestionBlueprint,
    verified: VerifiedDistractorSet,
    context_id: str,
) -> dict[str, Any]:
    option_display = {
        option.option_id: option.display_text for option in verified.options
    }
    distractors = sorted(
        (
            {
                "procedureId": route.procedure_id,
                "display": option_display[route.option_id],
                "canonicalLabel": route.canonical_label,
                "computation": route.computation,
                "feedback": route.feedback,
                "reliableMethod": route.reliable_method,
            }
            for route in verified.verified_distractors
        ),
        key=lambda route: route["procedureId"],
    )
    return {
        "schemaVersion": SEMANTIC_CONTENT_SCHEMA_VERSION,
        "question": {
            "questionSchemaVersion": blueprint.schema_version,
            "worldId": blueprint.world_id,
            "skillId": blueprint.skill_id,
            "familyId": blueprint.family_id,
            "topic": blueprint.topic,
            "templateId": blueprint.template_id,
            "templateRevision": blueprint.template_revision,
            "contextId": context_id,
            "operandNames": list(blueprint.operand_names),
            "operands": list(blueprint.operands),
            "solverSpec": blueprint.solver_spec,
            "prompt": blueprint.prompt,
            "canonicalAnswer": {
                "numerator": blueprint.canonical_answer.value.numerator,
                "denominator": blueprint.canonical_answer.value.denominator,
                "display": blueprint.canonical_answer.display,
            },
            "trustedSteps": list(blueprint.trusted_steps),
            "allowedProcedureIds": sorted(blueprint.allowed_procedure_ids),
            "difficulty": blueprint.difficulty,
        },
        "distractors": distractors,
    }


def _generation_receipt_payload(
    *,
    model_sha256: str,
    adapter_identity_receipt_sha256: str,
    gguf_sha256: str,
    generator_identity_receipt_sha256: str,
    prompt_sha256: str,
    prompt_template_sha256: str,
    registry_id: str,
    generated_at_utc: str,
    generation_sha256: str,
) -> dict[str, str]:
    return {
        "model_sha256": model_sha256,
        "adapter_identity_receipt_sha256": adapter_identity_receipt_sha256,
        "gguf_sha256": gguf_sha256,
        "generator_identity_receipt_sha256": generator_identity_receipt_sha256,
        "prompt_sha256": prompt_sha256,
        "prompt_template_sha256": prompt_template_sha256,
        "registry_id": registry_id,
        "generated_at_utc": generated_at_utc,
        "generation_sha256": generation_sha256,
    }


def _request_dict(request: CompileRequest) -> dict[str, Any]:
    return {
        "worldId": request.world_id,
        "skillId": request.skill_id,
        "familyId": request.family_id,
        "difficulty": request.difficulty,
        "seed": request.seed,
    }


def _holdout_dict(receipt: HoldoutReceipt) -> dict[str, Any]:
    return {
        "boundaryVersion": receipt.boundary_version,
        "recordCount": receipt.record_count,
        "sourceSha256": receipt.source_sha256,
        "canonicalSha256": receipt.canonical_sha256,
        "questionFingerprint": receipt.question_fingerprint,
        "maximumSimilarityBits": receipt.maximum_similarity_bits,
        "similarityThresholdBits": receipt.similarity_threshold_bits,
        "excluded": receipt.excluded,
    }


def _blueprint_dict(blueprint: QuestionBlueprint) -> dict[str, Any]:
    return {
        "schemaVersion": blueprint.schema_version,
        "questionId": blueprint.question_id,
        "worldId": blueprint.world_id,
        "skillId": blueprint.skill_id,
        "familyId": blueprint.family_id,
        "topic": blueprint.topic,
        "templateId": blueprint.template_id,
        "templateRevision": blueprint.template_revision,
        "operandNames": list(blueprint.operand_names),
        "operands": list(blueprint.operands),
        "solverSpec": blueprint.solver_spec,
        "prompt": blueprint.prompt,
        "canonicalAnswer": {
            "numerator": blueprint.canonical_answer.value.numerator,
            "denominator": blueprint.canonical_answer.value.denominator,
            "display": blueprint.canonical_answer.display,
        },
        "trustedSteps": list(blueprint.trusted_steps),
        "allowedProcedureIds": list(blueprint.allowed_procedure_ids),
        "difficulty": blueprint.difficulty,
        "seed": blueprint.seed,
        "contentSha256": blueprint.content_sha256,
        "holdoutReceipt": _holdout_dict(blueprint.holdout_receipt),
    }


def _verified_dict(verified: VerifiedDistractorSet) -> dict[str, Any]:
    return {
        "options": [
            {"optionId": option.option_id, "displayText": option.display_text}
            for option in verified.options
        ],
        "correctOptionId": verified.correct_option_id,
        "verifiedDistractors": [
            {
                "optionId": route.option_id,
                "procedureId": route.procedure_id,
                "canonicalLabel": route.canonical_label,
                "computation": route.computation,
                "feedback": route.feedback,
                "reliableMethod": route.reliable_method,
            }
            for route in verified.verified_distractors
        ],
        "blueprintSha256": verified.blueprint_sha256,
        "generationSha256": verified.generation_sha256,
        "generationReceiptSha256": verified.receipt_sha256,
        "sourceBundleSha256": verified.bundle_sha256,
    }


def _provenance_dict(provenance: QuestionProvenance) -> dict[str, str]:
    return {
        "modelId": provenance.model_id,
        "modelSha256": provenance.model_sha256,
        "adapterIdentityReceiptSha256": provenance.adapter_identity_receipt_sha256,
        "ggufSha256": provenance.gguf_sha256,
        "generatorIdentityReceiptSha256": (
            provenance.generator_identity_receipt_sha256
        ),
        "promptSha256": provenance.prompt_sha256,
        "promptTemplateSha256": provenance.prompt_template_sha256,
        "registryId": provenance.registry_id,
        "generatedAtUtc": provenance.generated_at_utc,
        "generationSha256": provenance.generation_sha256,
        "generationReceiptSha256": provenance.generation_receipt_sha256,
        "verifierVersion": provenance.verifier_version,
        "verifierReceiptSha256": provenance.verifier_receipt_sha256,
    }


def _private_unsigned_payload(
    *,
    request: CompileRequest,
    blueprint: QuestionBlueprint,
    verified: VerifiedDistractorSet,
    template_id: str,
    context_id: str,
    operand_signature: str,
    semantic_content_sha256: str,
    provenance: QuestionProvenance,
) -> dict[str, Any]:
    return {
        "schemaVersion": VERIFIED_QUESTION_SCHEMA_VERSION,
        "compileRequest": _request_dict(request),
        "blueprint": _blueprint_dict(blueprint),
        "verifiedSet": _verified_dict(verified),
        "templateId": template_id,
        "contextId": context_id,
        "operandSignature": operand_signature,
        "semanticContentSha256": semantic_content_sha256,
        "provenance": _provenance_dict(provenance),
    }


def _verified_set_from_bundle(bundle: VerifiedQuestionBundle) -> VerifiedDistractorSet:
    return VerifiedDistractorSet(
        question_id=bundle.blueprint.question_id,
        prompt=bundle.blueprint.prompt,
        options=bundle.options,
        correct_option_id=bundle.correct_option_id,
        verified_distractors=bundle.verified_distractors,
        blueprint_sha256=bundle.blueprint.content_sha256,
        generation_sha256=bundle.provenance.generation_sha256,
        receipt_sha256=bundle.provenance.generation_receipt_sha256,
        bundle_sha256=bundle.source_bundle_sha256,
    )


class _DuplicateJsonKey(ValueError):
    pass


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for key, value in pairs:
        if key in decoded:
            raise _DuplicateJsonKey(key)
        decoded[key] = value
    return decoded


def _reject_nonstandard_number(value: str) -> object:
    raise ValueError(f"non-standard JSON number: {value}")


def _expect_object(value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("object fields do not match schema")
    return value


def _expect_text(value: object, *, maximum: int = 1024) -> str:
    if not _is_nonempty_safe_text(value, maximum=maximum):
        raise ValueError("text field is invalid")
    return value


def _expect_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("SHA-256 field is invalid")
    return value


def _expect_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("integer field is invalid")
    return value


def _expect_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("boolean field is invalid")
    return value


def _expect_text_list(value: object, *, maximum: int = 1024) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("text tuple is invalid")
    return tuple(_expect_text(item, maximum=maximum) for item in value)


def _request_from_dict(value: object) -> CompileRequest:
    raw = _expect_object(
        value,
        {"worldId", "skillId", "familyId", "difficulty", "seed"},
    )
    return CompileRequest(
        world_id=_expect_text(raw["worldId"], maximum=128),
        skill_id=_expect_text(raw["skillId"], maximum=128),
        family_id=_expect_text(raw["familyId"], maximum=128),
        difficulty=_expect_int(raw["difficulty"]),
        seed=_expect_int(raw["seed"]),
    )


def _holdout_from_dict(value: object) -> HoldoutReceipt:
    raw = _expect_object(
        value,
        {
            "boundaryVersion",
            "recordCount",
            "sourceSha256",
            "canonicalSha256",
            "questionFingerprint",
            "maximumSimilarityBits",
            "similarityThresholdBits",
            "excluded",
        },
    )
    return HoldoutReceipt(
        boundary_version=_expect_text(raw["boundaryVersion"], maximum=128),
        record_count=_expect_int(raw["recordCount"]),
        source_sha256=_expect_sha256(raw["sourceSha256"]),
        canonical_sha256=_expect_sha256(raw["canonicalSha256"]),
        question_fingerprint=_expect_sha256(raw["questionFingerprint"]),
        maximum_similarity_bits=_expect_int(raw["maximumSimilarityBits"]),
        similarity_threshold_bits=_expect_int(raw["similarityThresholdBits"]),
        excluded=_expect_bool(raw["excluded"]),
    )


def _blueprint_from_dict(value: object) -> QuestionBlueprint:
    raw = _expect_object(
        value,
        {
            "schemaVersion",
            "questionId",
            "worldId",
            "skillId",
            "familyId",
            "topic",
            "templateId",
            "templateRevision",
            "operandNames",
            "operands",
            "solverSpec",
            "prompt",
            "canonicalAnswer",
            "trustedSteps",
            "allowedProcedureIds",
            "difficulty",
            "seed",
            "contentSha256",
            "holdoutReceipt",
        },
    )
    answer_raw = _expect_object(
        raw["canonicalAnswer"],
        {"numerator", "denominator", "display"},
    )
    numerator = _expect_int(answer_raw["numerator"])
    denominator = _expect_int(answer_raw["denominator"])
    if denominator <= 0:
        raise ValueError("answer denominator is invalid")
    fraction = Fraction(numerator, denominator)
    if fraction.numerator != numerator or fraction.denominator != denominator:
        raise ValueError("answer fraction is not canonical")
    return QuestionBlueprint(
        schema_version=_expect_text(raw["schemaVersion"], maximum=128),
        question_id=_expect_text(raw["questionId"], maximum=256),
        world_id=_expect_text(raw["worldId"], maximum=128),
        skill_id=_expect_text(raw["skillId"], maximum=128),
        family_id=_expect_text(raw["familyId"], maximum=128),
        topic=_expect_text(raw["topic"], maximum=256),
        template_id=_expect_text(raw["templateId"], maximum=128),
        template_revision=_expect_int(raw["templateRevision"]),
        operand_names=_expect_text_list(raw["operandNames"], maximum=64),
        operands=_expect_text_list(raw["operands"], maximum=64),
        solver_spec=_expect_text(raw["solverSpec"], maximum=128),
        prompt=_expect_text(raw["prompt"], maximum=1024),
        canonical_answer=CanonicalAnswer(
            value=fraction,
            display=_expect_text(answer_raw["display"], maximum=64),
        ),
        trusted_steps=_expect_text_list(raw["trustedSteps"], maximum=512),
        allowed_procedure_ids=_expect_text_list(
            raw["allowedProcedureIds"], maximum=128
        ),
        difficulty=_expect_int(raw["difficulty"]),
        seed=_expect_int(raw["seed"]),
        content_sha256=_expect_sha256(raw["contentSha256"]),
        holdout_receipt=_holdout_from_dict(raw["holdoutReceipt"]),
    )


def _verified_from_dict(
    value: object,
    blueprint: QuestionBlueprint,
) -> VerifiedDistractorSet:
    raw = _expect_object(
        value,
        {
            "options",
            "correctOptionId",
            "verifiedDistractors",
            "blueprintSha256",
            "generationSha256",
            "generationReceiptSha256",
            "sourceBundleSha256",
        },
    )
    if not isinstance(raw["options"], list):
        raise ValueError("options are invalid")
    options: list[VerifiedOption] = []
    for item in raw["options"]:
        option = _expect_object(item, {"optionId", "displayText"})
        options.append(
            VerifiedOption(
                option_id=_expect_text(option["optionId"], maximum=64),
                display_text=_expect_text(option["displayText"], maximum=64),
            )
        )
    if not isinstance(raw["verifiedDistractors"], list):
        raise ValueError("verified distractors are invalid")
    routes: list[VerifiedDistractor] = []
    for item in raw["verifiedDistractors"]:
        route = _expect_object(
            item,
            {
                "optionId",
                "procedureId",
                "canonicalLabel",
                "computation",
                "feedback",
                "reliableMethod",
            },
        )
        routes.append(
            VerifiedDistractor(
                option_id=_expect_text(route["optionId"], maximum=64),
                procedure_id=_expect_text(route["procedureId"], maximum=128),
                canonical_label=_expect_text(route["canonicalLabel"], maximum=512),
                computation=_expect_text(route["computation"], maximum=512),
                feedback=_expect_text(route["feedback"], maximum=512),
                reliable_method=_expect_text(route["reliableMethod"], maximum=512),
            )
        )
    return VerifiedDistractorSet(
        question_id=blueprint.question_id,
        prompt=blueprint.prompt,
        options=tuple(options),
        correct_option_id=_expect_text(raw["correctOptionId"], maximum=64),
        verified_distractors=tuple(routes),
        blueprint_sha256=_expect_sha256(raw["blueprintSha256"]),
        generation_sha256=_expect_sha256(raw["generationSha256"]),
        receipt_sha256=_expect_sha256(raw["generationReceiptSha256"]),
        bundle_sha256=_expect_sha256(raw["sourceBundleSha256"]),
    )


def _provenance_from_dict(value: object) -> QuestionProvenance:
    raw = _expect_object(
        value,
        {
            "modelId",
            "modelSha256",
            "adapterIdentityReceiptSha256",
            "ggufSha256",
            "generatorIdentityReceiptSha256",
            "promptSha256",
            "promptTemplateSha256",
            "registryId",
            "generatedAtUtc",
            "generationSha256",
            "generationReceiptSha256",
            "verifierVersion",
            "verifierReceiptSha256",
        },
    )
    return QuestionProvenance(
        model_id=_expect_text(raw["modelId"], maximum=256),
        model_sha256=_expect_sha256(raw["modelSha256"]),
        adapter_identity_receipt_sha256=_expect_sha256(
            raw["adapterIdentityReceiptSha256"]
        ),
        gguf_sha256=_expect_sha256(raw["ggufSha256"]),
        generator_identity_receipt_sha256=_expect_sha256(
            raw["generatorIdentityReceiptSha256"]
        ),
        prompt_sha256=_expect_sha256(raw["promptSha256"]),
        prompt_template_sha256=_expect_sha256(raw["promptTemplateSha256"]),
        registry_id=_expect_text(raw["registryId"], maximum=128),
        generated_at_utc=_expect_text(raw["generatedAtUtc"], maximum=48),
        generation_sha256=_expect_sha256(raw["generationSha256"]),
        generation_receipt_sha256=_expect_sha256(raw["generationReceiptSha256"]),
        verifier_version=_expect_text(raw["verifierVersion"], maximum=128),
        verifier_receipt_sha256=_expect_sha256(raw["verifierReceiptSha256"]),
    )
