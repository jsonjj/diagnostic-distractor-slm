from fractions import Fraction
from collections.abc import Mapping
import json
from pathlib import Path
import sys
import tempfile
import unittest

from services.wayline_forge.app.procedure_registry import (
    ProcedureRegistry,
    RegistryError,
)
from services.wayline_forge.app.question_kernel import CompileRequest, QuestionCompiler
from services.wayline_forge.app.safe_numeric import parse_exact_value


class ProcedureRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = ProcedureRegistry.for_tests()

    def test_registry_contains_the_fifty_nine_nonduplicate_launch_routes(self):
        self.assertEqual(len(self.registry.entries), 59)
        self.assertEqual(len({entry.procedure_id for entry in self.registry.entries}), 59)
        self.assertEqual(len({entry.family_id for entry in self.registry.entries}), 15)
        self.assertNotIn("frac_div_den_by_int", {entry.procedure_id for entry in self.registry.entries})

    def test_every_route_matches_its_frozen_audit_fixture(self):
        for entry in self.registry.entries:
            with self.subTest(procedure_id=entry.procedure_id):
                fixture = entry.audit_fixture
                expected = parse_exact_value(
                    fixture.expected,
                    allow_percent=entry.family_id == "decimal_to_percent",
                )
                actual = self.registry.evaluate_operands(
                    entry.procedure_id,
                    entry.family_id,
                    fixture.operands,
                )
                self.assertEqual(actual.value, expected.value)

    def test_every_route_has_audited_rendering_and_child_safe_feedback(self):
        for entry in self.registry.entries:
            with self.subTest(procedure_id=entry.procedure_id):
                self.assertTrue(entry.aliases)
                self.assertIn(entry.canonical_label, entry.aliases)
                self.assertTrue(entry.formula_name)
                self.assertIsInstance(entry.applicability, Mapping)
                self.assertTrue(entry.computation_template)
                self.assertTrue(entry.can_come_from.startswith("This answer can come from "))
                self.assertTrue(entry.reliable_method.startswith("A reliable method is "))
                rendered = self.registry.render_computation_from_operands(
                    entry.procedure_id,
                    entry.family_id,
                    entry.audit_fixture.operands,
                )
                self.assertIn(" = ", rendered)
                self.assertNotIn("{", rendered)

    def test_decimal_alignment_route_is_exact_and_canonical(self):
        blueprint = QuestionCompiler.for_tests().compile(
            CompileRequest("decimara", "decimal_add_sub", "decimal_add", 2, 731)
        )
        o = blueprint.operand_map
        value = self.registry.evaluate("da_align_wrong", blueprint)
        self.assertEqual(value.value, Fraction(o["a"] + o["b"], 10))
        self.assertEqual(
            self.registry.canonical_label("da_align_wrong"),
            "Does not align place values and adds both as tenths",
        )
        self.assertTrue(
            self.registry.matches_alias(
                "da_align_wrong",
                "  Does not align place values and adds both as tenths. ",
            )
        )
        self.assertFalse(self.registry.matches_alias("da_align_wrong", "Adds carelessly"))
        self.assertEqual(
            self.registry.canonical_computation("da_align_wrong", blueprint),
            self.registry.render_computation("da_align_wrong", blueprint),
        )

    def test_divide_instead_really_divides_the_decimal_by_one_hundred(self):
        operands = {"a": 3, "b": 6}
        value = self.registry.evaluate_operands(
            "cp_divide_instead", "decimal_to_percent", operands
        )
        self.assertEqual(value.value, Fraction(36, 10_000))
        self.assertEqual(value.display, "0.0036%")
        self.assertEqual(
            self.registry.render_computation_from_operands(
                "cp_divide_instead", "decimal_to_percent", operands
            ),
            "0.36 ÷ 100 = 0.0036%",
        )

    def test_percent_computations_end_with_the_exact_option_display(self):
        blueprint = QuestionCompiler.for_tests().compile(
            CompileRequest(
                "hundredfold",
                "decimal_percent_conversion",
                "decimal_to_percent",
                2,
                40202,
            )
        )
        for procedure_id in blueprint.allowed_procedure_ids:
            with self.subTest(procedure_id=procedure_id):
                display = self.registry.evaluate(procedure_id, blueprint).display
                computation = self.registry.canonical_computation(procedure_id, blueprint)
                self.assertTrue(computation.endswith(f" = {display}"))

    def test_fraction_division_templates_have_no_permanent_duplicate_route(self):
        compiler = QuestionCompiler.for_tests()
        family = compiler.curriculum.families["fraction_divide_integer"]
        for template in family.templates:
            with self.subTest(template_id=template.template_id):
                self.assertNotIn("frac_div_den_by_int", template.procedure_ids)
                self.assertEqual(len(template.procedure_ids), 4)
        blueprint = compiler.compile(
            CompileRequest(
                "reciprocal_deep", "fraction_divide", "fraction_divide_integer", 2, 60202
            )
        )
        outputs = [
            self.registry.evaluate(procedure_id, blueprint).value
            for procedure_id in blueprint.allowed_procedure_ids
        ]
        self.assertEqual(len(outputs), 4)
        self.assertEqual(len(outputs), len(set(outputs)))

    def test_modified_packaged_registry_fails_its_code_owned_digest(self):
        packaged = Path(__file__).resolve().parents[1] / "resources/procedure_registry_v1.json"
        with tempfile.TemporaryDirectory() as directory:
            modified = Path(directory) / "procedure_registry_v1.json"
            payload = json.loads(packaged.read_text(encoding="utf-8"))
            payload["registry_id"] = "tampered-procedures"
            modified.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RegistryError):
                ProcedureRegistry.packaged_v1(resource_path=modified)

    def test_unknown_or_cross_family_routes_fail_closed(self):
        with self.assertRaises(RegistryError):
            self.registry.evaluate_operands("missing", "decimal_add", {"a": 2, "b": 3})
        with self.assertRaises(RegistryError):
            self.registry.evaluate_operands(
                "da_align_wrong",
                "mental_add",
                {"a": 40, "b": 20},
            )
        with self.assertRaises(RegistryError):
            self.registry.evaluate_operands(
                "frac_div_den_by_int",
                "fraction_divide_integer",
                {"a": 1, "b": 5, "n": 2},
            )

    def test_product_registry_never_loads_the_training_engine(self):
        self.assertNotIn("src.buggy_procedures", sys.modules)


if __name__ == "__main__":
    unittest.main()
