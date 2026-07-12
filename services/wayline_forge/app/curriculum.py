"""Strict launch curriculum and frozen-holdout boundary loading."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata


class CurriculumError(ValueError):
    """Raised when an authored curriculum or holdout receipt is invalid."""


CURRICULUM_V1_SHA256 = "307609968a825a2a4b99dc31d56410f9f7d92ac2e525400162762a4abaaeaab4"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CurriculumError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    try:
        raw = path.read_bytes() if payload is None else payload
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CurriculumError(f"cannot load curriculum resource: {path}") from exc
    if not isinstance(value, dict):
        raise CurriculumError("curriculum resource must be one JSON object")
    return value


_STOP_WORDS = frozenset(
    {"what", "is", "calculate", "find", "give", "the", "value", "answer", "equals"}
)


def normalize_question(text: str) -> str:
    """Canonicalize prompt text for exact fingerprint and similarity checks."""

    if not isinstance(text, str):
        raise CurriculumError("question must be text")
    value = unicodedata.normalize("NFKC", text).lower()
    replacements = (
        (r"\div", " divided by "),
        ("÷", " divided by "),
        (r"\times", " times "),
        (r"\cdot", " times "),
        ("×", " times "),
        ("%", " percent "),
        ("=", " equals "),
    )
    for source, target in replacements:
        value = value.replace(source, target)
    value = re.sub(r"\\(?:left|right|text|mathrm|operatorname)", " ", value)
    tokens = re.findall(r"[a-z]+|[0-9]+", value, flags=re.ASCII)
    tokens = [token for token in tokens if token not in _STOP_WORDS]
    return " ".join(tokens)


def question_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_question(text).encode("utf-8")).hexdigest()


def _similarity_features(normalized: str) -> set[str]:
    tokens = normalized.split()
    if not tokens:
        return {"<empty>"}
    features = {f"u:{token}" for token in tokens}
    features.update(f"b:{left}|{right}" for left, right in zip(tokens, tokens[1:]))
    return features


def question_simhash(text: str) -> str:
    """Return a deterministic 64-bit SimHash over normalized token features."""

    weights = [0] * 64
    for feature in sorted(_similarity_features(normalize_question(text))):
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        bits = int.from_bytes(digest[:8], "big")
        for index in range(64):
            weights[index] += 1 if bits & (1 << index) else -1
    value = 0
    for index, weight in enumerate(weights):
        if weight >= 0:
            value |= 1 << index
    return f"{value:016x}"


def _canonical_holdout_sha(entries: tuple[tuple[str, str], ...]) -> str:
    payload = [{"fingerprint": fingerprint, "simhash": simhash} for fingerprint, simhash in entries]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class HoldoutReceipt:
    boundary_version: str
    record_count: int
    source_sha256: str
    canonical_sha256: str
    question_fingerprint: str
    maximum_similarity_bits: int
    similarity_threshold_bits: int
    excluded: bool


@dataclass(frozen=True, slots=True)
class HoldoutBoundary:
    boundary_version: str
    record_count: int
    source_sha256: str
    canonical_sha256: str
    similarity_threshold_bits: int
    entries: tuple[tuple[str, str], ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HoldoutBoundary":
        required = {
            "boundary_version",
            "record_count",
            "source_sha256",
            "canonical_sha256",
            "similarity_threshold_bits",
            "entries",
        }
        if set(value) != required:
            raise CurriculumError("holdout boundary fields do not match the v1 contract")
        raw_entries = value["entries"]
        if not isinstance(raw_entries, list):
            raise CurriculumError("holdout entries must be a list")
        entries: list[tuple[str, str]] = []
        for raw in raw_entries:
            if not isinstance(raw, dict) or set(raw) != {"fingerprint", "simhash"}:
                raise CurriculumError("holdout entry fields are invalid")
            fingerprint, simhash = raw["fingerprint"], raw["simhash"]
            if not re.fullmatch(r"[0-9a-f]{64}", fingerprint or ""):
                raise CurriculumError("invalid holdout fingerprint")
            if not re.fullmatch(r"[0-9a-f]{16}", simhash or ""):
                raise CurriculumError("invalid holdout simhash")
            entries.append((fingerprint, simhash))
        boundary = cls(
            boundary_version=str(value["boundary_version"]),
            record_count=int(value["record_count"]),
            source_sha256=str(value["source_sha256"]),
            canonical_sha256=str(value["canonical_sha256"]),
            similarity_threshold_bits=int(value["similarity_threshold_bits"]),
            entries=tuple(entries),
        )
        if boundary.record_count != len(boundary.entries) or boundary.record_count != 140:
            raise CurriculumError("frozen holdout must contain exactly 140 entries")
        if not 0 <= boundary.similarity_threshold_bits <= 64:
            raise CurriculumError("invalid holdout similarity threshold")
        if _canonical_holdout_sha(boundary.entries) != boundary.canonical_sha256:
            raise CurriculumError("holdout canonical digest mismatch")
        return boundary

    def receipt_for(self, question: str) -> HoldoutReceipt:
        fingerprint = question_fingerprint(question)
        candidate = int(question_simhash(question), 16)
        maximum = 0
        exact = False
        for stored_fingerprint, stored_simhash in self.entries:
            exact = exact or stored_fingerprint == fingerprint
            similarity = 64 - (candidate ^ int(stored_simhash, 16)).bit_count()
            maximum = max(maximum, similarity)
        return HoldoutReceipt(
            boundary_version=self.boundary_version,
            record_count=self.record_count,
            source_sha256=self.source_sha256,
            canonical_sha256=self.canonical_sha256,
            question_fingerprint=fingerprint,
            maximum_similarity_bits=maximum,
            similarity_threshold_bits=self.similarity_threshold_bits,
            excluded=exact or maximum >= self.similarity_threshold_bits,
        )

    def validate_source(self, path: Path) -> None:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CurriculumError(f"cannot read frozen holdout: {path}") from exc
        if hashlib.sha256(raw).hexdigest() != self.source_sha256:
            raise CurriculumError("frozen holdout source digest mismatch")
        computed: list[tuple[str, str]] = []
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
            try:
                record = json.loads(line, object_pairs_hook=_strict_object)
                question = record["question"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise CurriculumError(f"invalid holdout row {line_number}") from exc
            computed.append((question_fingerprint(question), question_simhash(question)))
        entries = tuple(computed)
        if len(entries) != self.record_count:
            raise CurriculumError("frozen holdout record count mismatch")
        if entries != self.entries or _canonical_holdout_sha(entries) != self.canonical_sha256:
            raise CurriculumError("frozen holdout fingerprints do not match")


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    template_id: str
    revision: int
    context_id: str
    prompt_template: str
    procedure_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FamilyDefinition:
    family_id: str
    training_family_id: str
    world_id: str
    skill_id: str
    topic: str
    solver: str
    operand_names: tuple[str, ...]
    shape: Mapping[str, Any]
    templates: tuple[TemplateDefinition, ...]


@dataclass(frozen=True, slots=True)
class Curriculum:
    schema_version: str
    curriculum_id: str
    families: Mapping[str, FamilyDefinition]
    holdout: HoldoutBoundary

    @classmethod
    def load(cls, path: Path) -> "Curriculum":
        return cls._load_payload(path, None)

    @classmethod
    def _load_payload(
        cls,
        path: Path,
        payload: bytes | None,
    ) -> "Curriculum":
        value = _load_json(path, payload)
        if set(value) != {"schema_version", "curriculum_id", "holdout", "families"}:
            raise CurriculumError("curriculum fields do not match the v1 contract")
        raw_families = value["families"]
        if not isinstance(raw_families, list):
            raise CurriculumError("curriculum families must be a list")
        families: dict[str, FamilyDefinition] = {}
        for raw in raw_families:
            expected = {
                "family_id",
                "training_family_id",
                "world_id",
                "skill_id",
                "topic",
                "solver",
                "operand_names",
                "shape",
                "templates",
            }
            if not isinstance(raw, dict) or set(raw) != expected:
                raise CurriculumError("family fields do not match the v1 contract")
            templates: list[TemplateDefinition] = []
            for template in raw["templates"]:
                if not isinstance(template, dict) or set(template) != {
                    "template_id",
                    "revision",
                    "context_id",
                    "prompt_template",
                    "procedure_ids",
                }:
                    raise CurriculumError("template fields do not match the v1 contract")
                templates.append(
                    TemplateDefinition(
                        template_id=str(template["template_id"]),
                        revision=int(template["revision"]),
                        context_id=str(template["context_id"]),
                        prompt_template=str(template["prompt_template"]),
                        procedure_ids=tuple(str(item) for item in template["procedure_ids"]),
                    )
                )
            family = FamilyDefinition(
                family_id=str(raw["family_id"]),
                training_family_id=str(raw["training_family_id"]),
                world_id=str(raw["world_id"]),
                skill_id=str(raw["skill_id"]),
                topic=str(raw["topic"]),
                solver=str(raw["solver"]),
                operand_names=tuple(str(item) for item in raw["operand_names"]),
                shape=MappingProxyType(dict(raw["shape"])),
                templates=tuple(templates),
            )
            if family.family_id in families:
                raise CurriculumError(f"duplicate family: {family.family_id}")
            if len(family.templates) < 2 or len({t.context_id for t in family.templates}) < 2:
                raise CurriculumError(f"family lacks changed-context templates: {family.family_id}")
            families[family.family_id] = family
        if len(families) != 15:
            raise CurriculumError("launch curriculum must contain exactly 15 families")
        return cls(
            schema_version=str(value["schema_version"]),
            curriculum_id=str(value["curriculum_id"]),
            families=MappingProxyType(families),
            holdout=HoldoutBoundary.from_dict(value["holdout"]),
        )

    @classmethod
    def packaged_v1(cls, *, resource_path: Path | None = None) -> "Curriculum":
        path = resource_path or (
            Path(__file__).resolve().parents[1] / "resources/curriculum_v1.json"
        )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CurriculumError(f"cannot read packaged curriculum: {path}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest != CURRICULUM_V1_SHA256:
            raise CurriculumError("packaged curriculum digest mismatch")
        return cls._load_payload(path, raw)
