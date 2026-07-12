"""Audited product-owned executable misconception procedure registry.

This is intentionally independent from ``src.buggy_procedures``.  The research
engine generated training data; this registry is the shipped safety authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping
import unicodedata

from .safe_numeric import ExactValue, MAX_ABSOLUTE, format_fraction


class RegistryError(ValueError):
    """Raised when a procedure or registry record cannot be trusted."""


PROCEDURE_REGISTRY_V1_SHA256 = "93b7d857e1bb063f781cde7783ca00709e34491be67683d47c728dcf94cfd514"


def _formulae() -> dict[str, Callable[[Mapping[str, int]], Fraction]]:
    f = Fraction
    return {
        "frac_add_num_den": lambda o: f(o["a"] + o["c"], o["b"] + o["d"]),
        "frac_add_keep_first_den": lambda o: f(o["a"] + o["c"], o["b"]),
        "frac_add_mul_den": lambda o: f(o["a"] + o["c"], o["b"] * o["d"]),
        "frac_add_keep_second_den": lambda o: f(o["a"] + o["c"], o["d"]),
        "frac_add_multiply_instead": lambda o: f(o["a"] * o["c"], o["b"] * o["d"]),
        "frac_mul_cross": lambda o: f(o["a"] * o["d"], o["b"] * o["c"]),
        "frac_mul_add": lambda o: f(o["a"], o["b"]) + f(o["c"], o["d"]),
        "frac_mul_num_add_den": lambda o: f(o["a"] * o["c"], o["b"] + o["d"]),
        "frac_mul_add_num_mul_den": lambda o: f(o["a"] + o["c"], o["b"] * o["d"]),
        "frac_mul_num_keep_first_den": lambda o: f(o["a"] * o["c"], o["b"]),
        "frac_div_add_int_den": lambda o: f(o["a"], o["b"] + o["n"]),
        "frac_div_num_over_int": lambda o: f(o["a"], o["n"]),
        "frac_div_mul_num_by_int": lambda o: f(o["a"] * o["n"], o["b"]),
        "frac_div_ignore_int": lambda o: f(o["a"], o["b"]),
        "ooo_left_to_right": lambda o: f((o["a"] + o["b"]) * o["c"]),
        "ooo_add_all": lambda o: f(o["a"] + o["b"] + o["c"]),
        "ooo_mul_all": lambda o: f(o["a"] * o["b"] * o["c"]),
        "ooo_mul_first_two": lambda o: f(o["a"] * o["b"] + o["c"]),
        "ooo_add_last_two_first": lambda o: f(o["a"] * (o["b"] + o["c"])),
        "neg_ignore_sign": lambda o: f(o["a"] + o["b"]),
        "neg_both_negative": lambda o: f(-(o["a"] + o["b"])),
        "neg_subtract_wrong_sign": lambda o: f(o["a"] - o["b"]),
        "dec_one_place": lambda o: f(o["p"] * o["q"], 10),
        "dec_no_point": lambda o: f(o["p"] * o["q"]),
        "dec_add": lambda o: f(o["p"] + o["q"], 10),
        "dec_too_many_places": lambda o: f(o["p"] * o["q"], 1000),
        "dec_add_digits_no_point": lambda o: f(o["p"] + o["q"]),
        "pct_no_div_100": lambda o: f(o["amount"] * o["percent"]),
        "pct_divide_by_p": lambda o: f(o["amount"], o["percent"]),
        "pct_subtract": lambda o: f(o["amount"] - o["percent"]),
        "pct_div_by_10": lambda o: f(o["amount"] * o["percent"], 10),
        "pct_add": lambda o: f(o["amount"] + o["percent"]),
        "pv_face_value": lambda o: f(o["d"]),
        "pv_one_place_low": lambda o: f(o["d"] * 10),
        "pv_one_place_high": lambda o: f(o["d"] * 1000),
        "pv_reads_whole": lambda o: f(o["N"]),
        "rd_truncate": lambda o: f(o["whole"] * 10 + o["d1"], 10),
        "rd_round_up_always": lambda o: f(o["whole"] * 10 + o["d1"] + 1, 10),
        "rd_drops_to_whole": lambda o: f(o["whole"]),
        "rd_keeps_all_digits": lambda o: f(o["whole"] * 100 + o["d1"] * 10 + o["d2"], 100),
        "cp_move_one_place": lambda o: f(o["a"] * 10 + o["b"], 10),
        "cp_move_three_places": lambda o: f((o["a"] * 10 + o["b"]) * 10),
        "cp_first_digit_only": lambda o: f(o["a"] * 10),
        "cp_divide_instead": lambda o: f(o["a"] * 10 + o["b"], 10_000),
        "da_align_wrong": lambda o: f(o["a"] + o["b"], 10),
        "da_ignore_point": lambda o: f(o["a"] + o["b"]),
        "da_subtract_instead": lambda o: f(o["a"] * 10 - o["b"], 100),
        "idx_mul_exponents": lambda o: f(o["base"] ** (o["m"] * o["n"])),
        "idx_add_all": lambda o: f(o["base"] + o["m"] + o["n"]),
        "idx_base_times_sum": lambda o: f(o["base"] * (o["m"] + o["n"])),
        "hcf_product": lambda o: f(o["a"] * o["b"]),
        "hcf_sum": lambda o: f(o["a"] + o["b"]),
        "hcf_difference": lambda o: f(abs(o["a"] - o["b"])),
        "ma_subtract_instead": lambda o: f(o["a"] - o["b"]),
        "ma_forgets_ten": lambda o: f(o["a"] + o["b"] - 10),
        "ma_adds_extra_ten": lambda o: f(o["a"] + o["b"] + 10),
        "mm_add_instead": lambda o: f(o["a"] + o["b"]),
        "mm_ignore_tens": lambda o: f((o["a"] - 10) * o["b"]),
        "mm_off_by_one_factor": lambda o: f(o["a"] * (o["b"] - 1)),
    }


_FORMULAE = MappingProxyType(_formulae())


@dataclass(frozen=True, slots=True)
class AuditFixture:
    operands: Mapping[str, int]
    expected: str


@dataclass(frozen=True, slots=True)
class ProcedureEntry:
    procedure_id: str
    topic: str
    family_id: str
    aliases: tuple[str, ...]
    formula_name: str
    applicability: Mapping[str, Any]
    canonical_label: str
    computation_template: str
    can_come_from: str
    reliable_method: str
    audit_fixture: AuditFixture


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _normalize_label(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).lower()
    return " ".join(re.findall(r"[a-z0-9]+", value, flags=re.ASCII))


class ProcedureRegistry:
    def __init__(self, registry_id: str, entries: tuple[ProcedureEntry, ...]):
        self.registry_id = registry_id
        self.entries = entries
        self._by_id = MappingProxyType({entry.procedure_id: entry for entry in entries})
        by_family: dict[str, list[ProcedureEntry]] = {}
        for entry in entries:
            by_family.setdefault(entry.family_id, []).append(entry)
        self._by_family = MappingProxyType(
            {key: tuple(value) for key, value in by_family.items()}
        )

    @classmethod
    def load(cls, path: Path) -> "ProcedureRegistry":
        return cls._load_payload(path, None)

    @classmethod
    def _load_payload(
        cls,
        path: Path,
        payload: bytes | None,
    ) -> "ProcedureRegistry":
        try:
            raw = path.read_bytes() if payload is None else payload
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RegistryError(f"cannot load procedure registry: {path}") from exc
        if not isinstance(value, dict) or set(value) != {"schema_version", "registry_id", "procedures"}:
            raise RegistryError("procedure registry fields do not match the v1 contract")
        raw_entries = value["procedures"]
        if not isinstance(raw_entries, list):
            raise RegistryError("procedures must be a list")
        entries: list[ProcedureEntry] = []
        expected_fields = {
            "procedure_id",
            "topic",
            "family_id",
            "aliases",
            "formula_name",
            "applicability",
            "canonical_label",
            "computation_template",
            "can_come_from",
            "reliable_method",
            "audit_fixture",
        }
        seen: set[str] = set()
        for raw in raw_entries:
            if not isinstance(raw, dict) or set(raw) != expected_fields:
                raise RegistryError("procedure fields do not match the v1 contract")
            procedure_id = str(raw["procedure_id"])
            formula_name = str(raw["formula_name"])
            if procedure_id in seen:
                raise RegistryError(f"duplicate procedure: {procedure_id}")
            if formula_name not in _FORMULAE:
                raise RegistryError(f"unknown executable formula: {formula_name}")
            fixture = raw["audit_fixture"]
            if not isinstance(fixture, dict) or set(fixture) != {"operands", "expected"}:
                raise RegistryError(f"invalid audit fixture: {procedure_id}")
            aliases = tuple(str(alias) for alias in raw["aliases"])
            canonical_label = str(raw["canonical_label"])
            if canonical_label not in aliases:
                raise RegistryError(f"canonical label missing from aliases: {procedure_id}")
            entry = ProcedureEntry(
                procedure_id=procedure_id,
                topic=str(raw["topic"]),
                family_id=str(raw["family_id"]),
                aliases=aliases,
                formula_name=formula_name,
                applicability=MappingProxyType(dict(raw["applicability"])),
                canonical_label=canonical_label,
                computation_template=str(raw["computation_template"]),
                can_come_from=str(raw["can_come_from"]),
                reliable_method=str(raw["reliable_method"]),
                audit_fixture=AuditFixture(
                    operands=MappingProxyType({str(k): int(v) for k, v in fixture["operands"].items()}),
                    expected=str(fixture["expected"]),
                ),
            )
            entries.append(entry)
            seen.add(procedure_id)
        if len(entries) != 59:
            raise RegistryError("launch registry must contain exactly 59 nonduplicate procedures")
        return cls(str(value["registry_id"]), tuple(entries))

    @classmethod
    def for_tests(cls) -> "ProcedureRegistry":
        return cls.packaged_v1()

    @classmethod
    def packaged_v1(cls, *, resource_path: Path | None = None) -> "ProcedureRegistry":
        path = resource_path or (
            Path(__file__).resolve().parents[1] / "resources/procedure_registry_v1.json"
        )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise RegistryError(f"cannot read packaged procedure registry: {path}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest != PROCEDURE_REGISTRY_V1_SHA256:
            raise RegistryError("packaged procedure registry digest mismatch")
        return cls._load_payload(path, raw)

    def entry(self, procedure_id: str) -> ProcedureEntry:
        try:
            return self._by_id[procedure_id]
        except KeyError as exc:
            raise RegistryError(f"unknown procedure: {procedure_id}") from exc

    def procedures_for_family(self, family_id: str) -> tuple[ProcedureEntry, ...]:
        return self._by_family.get(family_id, ())

    def _check_applicability(self, entry: ProcedureEntry, operands: Mapping[str, int]) -> None:
        unknown = set(entry.applicability) - {"requires_b_divisible_by_n"}
        if unknown:
            raise RegistryError(f"unsupported applicability constraint: {entry.procedure_id}")
        if entry.applicability.get("requires_b_divisible_by_n") and operands["b"] % operands["n"]:
            raise RegistryError(f"procedure is not applicable: {entry.procedure_id}")

    def evaluate_operands(
        self,
        procedure_id: str,
        family_id: str,
        operands: Mapping[str, int],
    ) -> ExactValue:
        entry = self.entry(procedure_id)
        if entry.family_id != family_id:
            raise RegistryError(f"procedure {procedure_id} is not allowed for {family_id}")
        normalized = {str(key): int(value) for key, value in operands.items()}
        self._check_applicability(entry, normalized)
        try:
            value = Fraction(_FORMULAE[entry.formula_name](normalized))
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise RegistryError(f"cannot evaluate procedure: {procedure_id}") from exc
        suffix = "%" if family_id == "decimal_to_percent" else ""
        return ExactValue(value=value, display=format_fraction(value) + suffix)

    def evaluate(self, procedure_id: str, blueprint: Any) -> ExactValue:
        return self.evaluate_operands(procedure_id, blueprint.family_id, blueprint.operand_map)

    def distinct_applicable_procedures(
        self,
        family_id: str,
        operands: Mapping[str, int],
        correct: Fraction,
        declared_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        grouped: dict[Fraction, list[str]] = {}
        for procedure_id in declared_ids:
            try:
                value = self.evaluate_operands(procedure_id, family_id, operands).value
            except RegistryError:
                continue
            if value == correct or abs(value) > MAX_ABSOLUTE:
                continue
            grouped.setdefault(value, []).append(procedure_id)
        unique = {ids[0] for ids in grouped.values() if len(ids) == 1}
        return tuple(procedure_id for procedure_id in declared_ids if procedure_id in unique)

    def canonical_label(self, procedure_id: str) -> str:
        return self.entry(procedure_id).canonical_label

    def matches_alias(self, procedure_id: str, proposed_label: str) -> bool:
        if not isinstance(proposed_label, str):
            return False
        normalized = _normalize_label(proposed_label)
        return normalized in {_normalize_label(alias) for alias in self.entry(procedure_id).aliases}

    def render_computation_from_operands(
        self,
        procedure_id: str,
        family_id: str,
        operands: Mapping[str, int],
    ) -> str:
        entry = self.entry(procedure_id)
        if entry.family_id != family_id:
            raise RegistryError(f"procedure {procedure_id} is not allowed for {family_id}")
        context = {str(key): int(value) for key, value in operands.items()}
        if "a" in context and "b" in context:
            context["hi"] = max(context["a"], context["b"])
            context["lo"] = min(context["a"], context["b"])
        try:
            left = entry.computation_template.format(**context)
        except (KeyError, ValueError) as exc:
            raise RegistryError(f"cannot render procedure: {procedure_id}") from exc
        answer = self.evaluate_operands(procedure_id, family_id, operands)
        return f"{left} = {answer.display}"

    def render_computation(self, procedure_id: str, blueprint: Any) -> str:
        return self.render_computation_from_operands(
            procedure_id, blueprint.family_id, blueprint.operand_map
        )

    def canonical_computation(self, procedure_id: str, blueprint: Any) -> str:
        """Render the registry-owned computation; model computation text is discarded."""

        return self.render_computation(procedure_id, blueprint)

    def canonical_feedback(self, procedure_id: str) -> str:
        return self.entry(procedure_id).can_come_from

    def reliable_method(self, procedure_id: str) -> str:
        return self.entry(procedure_id).reliable_method
