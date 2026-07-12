"""All-or-nothing verification and sealing for raw distractor generations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
from typing import Any
import unicodedata

from .procedure_registry import ProcedureRegistry, RegistryError
from .providers.distractor import PinnedSlmManifest, RawSlmGeneration
from .question_kernel import CompileRequest, QuestionBlueprint, QuestionCompiler
from .safe_numeric import NumericParseError, parse_exact_value
from .slm_prompt import PROMPT_TEMPLATE_SHA256, build_slm_request


REJECTION_CODES = (
    "receipt_mismatch",
    "response_too_large",
    "invalid_json",
    "duplicate_json_key",
    "invalid_schema",
    "wrong_distractor_count",
    "unsafe_text",
    "invalid_numeric_answer",
    "correct_key_collision",
    "duplicate_answer",
    "unsupported_procedure_mapping",
    "ambiguous_procedure_mapping",
    "duplicate_procedure_mapping",
    "label_procedure_mismatch",
    "blueprint_not_verifiable",
)


@dataclass(frozen=True, slots=True)
class VerifiedOption:
    option_id: str
    display_text: str


@dataclass(frozen=True, slots=True)
class VerifiedDistractor:
    option_id: str
    procedure_id: str
    canonical_label: str
    computation: str
    feedback: str
    reliable_method: str


@dataclass(frozen=True, slots=True)
class VerifiedDistractorSet:
    question_id: str
    prompt: str
    options: tuple[VerifiedOption, ...]
    correct_option_id: str
    verified_distractors: tuple[VerifiedDistractor, ...]
    blueprint_sha256: str
    generation_sha256: str
    receipt_sha256: str
    bundle_sha256: str


@dataclass(frozen=True, slots=True)
class VerificationRejection:
    code: str

    def __post_init__(self) -> None:
        if self.code not in REJECTION_CODES:
            raise ValueError("unknown verification rejection code")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    accepted: bool
    value: VerifiedDistractorSet | None = None
    rejection: VerificationRejection | None = None

    def __post_init__(self) -> None:
        if self.accepted != (self.value is not None and self.rejection is None):
            raise ValueError("verification result must be all-or-nothing")

    @property
    def code(self) -> str | None:
        return None if self.rejection is None else self.rejection.code


@dataclass(frozen=True, slots=True)
class _Candidate:
    label: str
    computation: str
    answer_text: str
    answer: Fraction
    procedure_id: str


class _DuplicateJsonKey(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _is_safe_field(text: str, maximum: int) -> bool:
    if not text or len(text) > maximum or text != text.strip():
        return False
    if "<" in text or ">" in text:
        return False
    return not any(unicodedata.category(char).startswith("C") for char in text)


class DistractorVerifier:
    def __init__(
        self,
        compiler: QuestionCompiler,
        registry: ProcedureRegistry,
        manifest: PinnedSlmManifest,
        *,
        fixture_root: Path | None = None,
    ):
        if manifest.registry_id != registry.registry_id:
            raise ValueError("model manifest and procedure registry do not match")
        if manifest.prompt_template_sha256 != PROMPT_TEMPLATE_SHA256:
            raise ValueError("model manifest and prompt template do not match")
        self.compiler = compiler
        self.registry = registry
        self.manifest = manifest
        self.fixture_root = fixture_root

    @classmethod
    def for_tests(cls) -> "DistractorVerifier":
        return cls(
            compiler=QuestionCompiler.for_tests(),
            registry=ProcedureRegistry.for_tests(),
            manifest=PinnedSlmManifest.for_tests(),
            fixture_root=Path(__file__).resolve().parents[1] / "tests/fixtures/slm",
        )

    def reference_blueprint(self, reference_id: str) -> QuestionBlueprint:
        if reference_id != "decimal-add-731":
            raise ValueError("unknown reference blueprint")
        return self.compiler.compile(
            CompileRequest("decimara", "decimal_add_sub", "decimal_add", 2, 731)
        )

    def fixture_text(self, fixture_name: str) -> str:
        if self.fixture_root is None or Path(fixture_name).name != fixture_name:
            raise ValueError("invalid fixture name")
        path = self.fixture_root / fixture_name
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError("fixture not found") from exc
        if len(raw) > self.manifest.max_response_bytes:
            raise ValueError("fixture exceeds response bound")
        try:
            return raw.decode("utf-8").rstrip("\n")
        except UnicodeDecodeError as exc:
            raise ValueError("fixture is not UTF-8") from exc

    def fixture_generation(
        self,
        blueprint: QuestionBlueprint,
        fixture_name: str,
    ) -> RawSlmGeneration:
        request = build_slm_request(blueprint)
        return RawSlmGeneration(
            text=self.fixture_text(fixture_name),
            model_sha256=self.manifest.model_sha256,
            adapter_identity_receipt_sha256=(
                self.manifest.adapter_identity_receipt_sha256
            ),
            gguf_sha256=self.manifest.gguf_sha256,
            generator_identity_receipt_sha256=(
                self.manifest.generator_identity_receipt_sha256
            ),
            prompt_sha256=request.prompt_sha256,
            prompt_template_sha256=self.manifest.prompt_template_sha256,
            registry_id=self.manifest.registry_id,
            generated_at_utc="2026-07-11T18:00:00Z",
        )

    def verify_fixture(
        self,
        blueprint: QuestionBlueprint,
        fixture_name: str,
    ) -> VerificationResult:
        return self.verify_generation(
            blueprint,
            self.fixture_generation(blueprint, fixture_name),
        )

    def verify_generation(
        self,
        blueprint: QuestionBlueprint,
        generation: RawSlmGeneration,
    ) -> VerificationResult:
        blueprint_error = self._verify_blueprint(blueprint)
        if blueprint_error is not None:
            return self._reject(blueprint_error)
        if not self._receipts_match(blueprint, generation):
            return self._reject("receipt_mismatch")
        if not isinstance(generation.text, str):
            return self._reject("invalid_schema")
        try:
            response_size = len(generation.text.encode("utf-8"))
        except UnicodeEncodeError:
            return self._reject("unsafe_text")
        if response_size > self.manifest.max_response_bytes:
            return self._reject("response_too_large")

        try:
            parsed = json.loads(generation.text, object_pairs_hook=_strict_object)
        except _DuplicateJsonKey:
            return self._reject("duplicate_json_key")
        except (json.JSONDecodeError, RecursionError, UnicodeError):
            return self._reject("invalid_json")

        raw_candidates, schema_error = self._validate_schema(parsed)
        if schema_error is not None:
            return self._reject(schema_error)
        assert raw_candidates is not None

        allow_percent = blueprint.family_id == "decimal_to_percent"
        numeric: list[tuple[str, str, str, Fraction]] = []
        for raw in raw_candidates:
            label = raw["misconception"]
            computation = raw["computation"]
            answer_text = raw["answer"]
            if not (
                _is_safe_field(label, 256)
                and _is_safe_field(computation, 512)
                and _is_safe_field(answer_text, 64)
            ):
                return self._reject("unsafe_text")
            try:
                answer = parse_exact_value(
                    answer_text,
                    allow_percent=allow_percent,
                ).value
            except NumericParseError:
                return self._reject("invalid_numeric_answer")
            numeric.append((label, computation, answer_text, answer))

        answers = tuple(item[3] for item in numeric)
        if blueprint.canonical_answer.value in answers:
            return self._reject("correct_key_collision")
        if len(set(answers)) != len(answers):
            return self._reject("duplicate_answer")

        route_outputs: dict[Fraction, list[str]] = {}
        try:
            for procedure_id in blueprint.allowed_procedure_ids:
                value = self.registry.evaluate(procedure_id, blueprint).value
                route_outputs.setdefault(value, []).append(procedure_id)
        except RegistryError:
            return self._reject("blueprint_not_verifiable")

        candidates: list[_Candidate] = []
        used_routes: set[str] = set()
        for label, computation, answer_text, answer in numeric:
            matches = route_outputs.get(answer, [])
            if not matches:
                return self._reject("unsupported_procedure_mapping")
            if len(matches) != 1:
                return self._reject("ambiguous_procedure_mapping")
            procedure_id = matches[0]
            if procedure_id in used_routes:
                return self._reject("duplicate_procedure_mapping")
            if not self.registry.matches_alias(procedure_id, label):
                return self._reject("label_procedure_mismatch")
            used_routes.add(procedure_id)
            candidates.append(
                _Candidate(
                    label=label,
                    computation=computation,
                    answer_text=answer_text,
                    answer=answer,
                    procedure_id=procedure_id,
                )
            )

        bundle = self._seal(blueprint, generation, tuple(candidates))
        return VerificationResult(accepted=True, value=bundle)

    def _verify_blueprint(self, blueprint: QuestionBlueprint) -> str | None:
        try:
            request = CompileRequest(
                blueprint.world_id,
                blueprint.skill_id,
                blueprint.family_id,
                blueprint.difficulty,
                blueprint.seed,
            )
            authoritative = self.compiler.compile(request)
        except Exception:
            return "blueprint_not_verifiable"
        return None if authoritative == blueprint else "blueprint_not_verifiable"

    def _receipts_match(
        self,
        blueprint: QuestionBlueprint,
        generation: RawSlmGeneration,
    ) -> bool:
        if not isinstance(generation, RawSlmGeneration):
            return False
        expected_prompt = build_slm_request(blueprint).prompt_sha256
        expected = (
            self.manifest.model_sha256,
            self.manifest.adapter_identity_receipt_sha256,
            self.manifest.gguf_sha256,
            self.manifest.generator_identity_receipt_sha256,
            expected_prompt,
            self.manifest.registry_id,
            self.manifest.prompt_template_sha256,
        )
        actual = (
            generation.model_sha256,
            generation.adapter_identity_receipt_sha256,
            generation.gguf_sha256,
            generation.generator_identity_receipt_sha256,
            generation.prompt_sha256,
            generation.registry_id,
            generation.prompt_template_sha256,
        )
        if actual != expected:
            return False
        try:
            timestamp = datetime.fromisoformat(
                generation.generated_at_utc.replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError):
            return False
        return timestamp.tzinfo is not None and timestamp.utcoffset() == timedelta(0)

    @staticmethod
    def _validate_schema(
        parsed: Any,
    ) -> tuple[list[dict[str, str]] | None, str | None]:
        if not isinstance(parsed, dict) or set(parsed) != {"distractors"}:
            return None, "invalid_schema"
        distractors = parsed["distractors"]
        if not isinstance(distractors, list):
            return None, "invalid_schema"
        if len(distractors) != 3:
            return None, "wrong_distractor_count"
        expected = {"misconception", "computation", "answer"}
        for item in distractors:
            if not isinstance(item, dict) or set(item) != expected:
                return None, "invalid_schema"
            if not all(isinstance(item[key], str) for key in expected):
                return None, "invalid_schema"
        return distractors, None

    def _seal(
        self,
        blueprint: QuestionBlueprint,
        generation: RawSlmGeneration,
        candidates: tuple[_Candidate, ...],
    ) -> VerifiedDistractorSet:
        generation_sha256 = hashlib.sha256(generation.text.encode("utf-8")).hexdigest()
        receipt_payload = {
            "model_sha256": generation.model_sha256,
            "adapter_identity_receipt_sha256": (
                generation.adapter_identity_receipt_sha256
            ),
            "gguf_sha256": generation.gguf_sha256,
            "generator_identity_receipt_sha256": (
                generation.generator_identity_receipt_sha256
            ),
            "prompt_sha256": generation.prompt_sha256,
            "prompt_template_sha256": generation.prompt_template_sha256,
            "registry_id": generation.registry_id,
            "generated_at_utc": generation.generated_at_utc,
            "generation_sha256": generation_sha256,
        }
        receipt_sha256 = _sha256_json(receipt_payload)
        option_seed = hashlib.sha256(
            (
                blueprint.content_sha256
                + generation_sha256
                + generation.gguf_sha256
                + generation.prompt_sha256
            ).encode("ascii")
        ).hexdigest()

        option_values: list[tuple[str | None, str]] = [
            (None, blueprint.canonical_answer.display)
        ]
        for candidate in candidates:
            canonical = self.registry.evaluate(candidate.procedure_id, blueprint)
            option_values.append((candidate.procedure_id, canonical.display))
        random.Random(int(option_seed, 16)).shuffle(option_values)

        options: list[VerifiedOption] = []
        route_option_ids: dict[str, str] = {}
        correct_option_id = ""
        for index, (procedure_id, display) in enumerate(option_values):
            option_id = "opt_" + hashlib.sha256(
                f"{option_seed}|{index}|wayline-option-v1".encode("ascii")
            ).hexdigest()[:24]
            options.append(VerifiedOption(option_id=option_id, display_text=display))
            if procedure_id is None:
                correct_option_id = option_id
            else:
                route_option_ids[procedure_id] = option_id

        verified = tuple(
            VerifiedDistractor(
                option_id=route_option_ids[candidate.procedure_id],
                procedure_id=candidate.procedure_id,
                canonical_label=self.registry.canonical_label(candidate.procedure_id),
                computation=self.registry.canonical_computation(
                    candidate.procedure_id, blueprint
                ),
                feedback=self.registry.canonical_feedback(candidate.procedure_id),
                reliable_method=self.registry.reliable_method(candidate.procedure_id),
            )
            for candidate in candidates
        )
        sealed_payload = {
            "question_id": blueprint.question_id,
            "options": [asdict(option) for option in options],
            "correct_option_id": correct_option_id,
            "verified_distractors": [asdict(item) for item in verified],
            "blueprint_sha256": blueprint.content_sha256,
            "generation_sha256": generation_sha256,
            "receipt_sha256": receipt_sha256,
        }
        return VerifiedDistractorSet(
            question_id=blueprint.question_id,
            prompt=blueprint.prompt,
            options=tuple(options),
            correct_option_id=correct_option_id,
            verified_distractors=verified,
            blueprint_sha256=blueprint.content_sha256,
            generation_sha256=generation_sha256,
            receipt_sha256=receipt_sha256,
            bundle_sha256=_sha256_json(sealed_payload),
        )

    @staticmethod
    def _reject(code: str) -> VerificationResult:
        return VerificationResult(
            accepted=False,
            rejection=VerificationRejection(code),
        )
