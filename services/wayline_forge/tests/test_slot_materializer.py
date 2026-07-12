from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace
from fractions import Fraction
import hashlib
import json
from types import SimpleNamespace
import unittest

from services.wayline_forge.app.adaptive_planner import SlotIntent
from services.wayline_forge.app.batch_material import SelectionExclusions
from services.wayline_forge.app.curriculum import HoldoutReceipt
from services.wayline_forge.app.procedure_registry import RegistryError
from services.wayline_forge.app.question_kernel import (
    CanonicalAnswer,
    CompilationError,
    CompileRequest,
    QuestionBlueprint,
    QuestionCompiler,
)
from services.wayline_forge.app import slot_materializer as slot_materializer_module
from services.wayline_forge.app.slm_prompt import build_slm_request
from services.wayline_forge.app.slot_materializer import (
    MAX_SEED_ATTEMPTS,
    MaterializedSlot,
    SlotMaterializationError,
    materialize_slots,
)


TIER_LENGTHS = {
    "route_1": 3,
    "route_2": 4,
    "route_3": 4,
    "elite": 5,
    "world_boss": 8,
    "campaign_finale": 10,
    "seal_trial": 3,
}

TIER_SCHEDULES = {
    "route_1": (1,),
    "route_2": (1, 2),
    "route_3": (2,),
    "elite": (2, 3),
    "world_boss": (2, 3),
    "campaign_finale": (3,),
    "seal_trial": (1,),
}

REGULAR_SLOT_KINDS = (
    "fragile_skill_transfer",
    "under_sampled_core_skill",
    "spaced_prior_world_transfer",
    "novel_current_skill",
)


@dataclass(frozen=True)
class FixtureFamily:
    family_id: str
    world_id: str
    skill_id: str
    templates: tuple[object, ...]


class FixtureRegistry:
    registry_id = "fixture-registry-v1"

    def __init__(self, procedures: dict[str, str]):
        self._procedures = dict(procedures)

    def entry(self, procedure_id: str) -> object:
        try:
            family_id = self._procedures[procedure_id]
        except KeyError as exc:
            raise RegistryError(f"unknown procedure: {procedure_id}") from exc
        return SimpleNamespace(family_id=family_id)


class FixtureCompiler:
    """Small deterministic compiler double; it still returns real blueprints."""

    def __init__(
        self,
        families: tuple[FixtureFamily, ...],
        procedures: dict[str, str],
        *,
        builder=None,
    ):
        self.curriculum = SimpleNamespace(
            curriculum_id="fixture-curriculum-v1",
            families={family.family_id: family for family in families},
        )
        self.registry = FixtureRegistry(procedures)
        self.requests: list[CompileRequest] = []
        self.compile_results: list[QuestionBlueprint] = []
        self._builder = builder

    def compile(self, request: CompileRequest) -> QuestionBlueprint:
        call_index = len(self.requests)
        self.requests.append(request)
        if self._builder is not None:
            result = self._builder(self, request, call_index)
        else:
            result = fixture_blueprint(self, request, label=f"call-{call_index}")
        self.compile_results.append(result)
        return result


def fixture_family(
    family_id: str = "family-a",
    *,
    world_id: str = "world-a",
    skill_id: str = "skill-a",
    procedure_ids: tuple[str, ...] = ("route-a", "route-b", "route-c"),
) -> FixtureFamily:
    template_contexts = (
        ("fixture-template-a", "context-a"),
        ("fixture-template-b", "context-b"),
        ("fixture-template-c", "context-c"),
        ("blocked-template", "context-blocked"),
        ("same-template", "context-same"),
        ("template-a", "context-record-a"),
        ("template-b", "context-record-b"),
        ("template-c", "context-record-c"),
        ("template-d", "context-record-d"),
        ("template-e", "context-record-e"),
    )
    return FixtureFamily(
        family_id=family_id,
        world_id=world_id,
        skill_id=skill_id,
        templates=tuple(
            SimpleNamespace(
                template_id=template_id,
                context_id=context_id,
                procedure_ids=procedure_ids,
            )
            for template_id, context_id in template_contexts
        ),
    )


def fixture_blueprint(
    compiler: FixtureCompiler,
    request: CompileRequest,
    *,
    label: str,
    allowed_procedure_ids: tuple[str, ...] | None = None,
    question_id: str | None = None,
    template_id: str | None = None,
    operand: str | None = None,
    content_sha256: str | None = None,
    holdout_excluded: bool = False,
) -> QuestionBlueprint:
    family = compiler.curriculum.families[request.family_id]
    declared = tuple(
        dict.fromkeys(
            procedure_id
            for template in family.templates
            for procedure_id in template.procedure_ids
        )
    )
    operand_value = operand or str(len(compiler.requests) + 10)
    content = content_sha256 or hashlib.sha256(
        f"{request.family_id}|{request.seed}|{label}".encode("ascii")
    ).hexdigest()
    selected_template_id = template_id or family.templates[
        (len(compiler.requests) - 1) % 3
    ].template_id
    return QuestionBlueprint(
        schema_version="wayline-question-blueprint-v1",
        question_id=question_id or f"question-{label}",
        world_id=request.world_id,
        skill_id=request.skill_id,
        family_id=request.family_id,
        topic="Fixture Topic",
        template_id=selected_template_id,
        template_revision=1,
        operand_names=("a",),
        operands=(operand_value,),
        solver_spec="fixture",
        prompt=f"Fixture prompt {label}",
        canonical_answer=CanonicalAnswer(Fraction(1), "1"),
        trusted_steps=("Use the fixture method.",),
        allowed_procedure_ids=(
            declared if allowed_procedure_ids is None else allowed_procedure_ids
        ),
        difficulty=request.difficulty,
        seed=request.seed,
        content_sha256=content,
        holdout_receipt=HoldoutReceipt(
            boundary_version="fixture-holdout-v1",
            record_count=140,
            source_sha256="a" * 64,
            canonical_sha256="b" * 64,
            question_fingerprint="c" * 64,
            maximum_similarity_bits=12,
            similarity_threshold_bits=58,
            excluded=holdout_excluded,
        ),
    )


def operand_signature(
    family_id: str,
    operand_names: tuple[str, ...],
    operands: tuple[str, ...],
) -> str:
    payload = {
        "familyId": family_id,
        "operandNames": list(operand_names),
        "operands": list(operands),
        "schemaVersion": "wayline.operand-signature.v1",
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def intent(
    *,
    kind: str = "novel_current_skill",
    campaign_world_id: str = "world-a",
    content_world_id: str = "world-a",
    skill_id: str = "skill-a",
    procedure_ids: tuple[str, ...] = (),
    excluded_item_ids: tuple[str, ...] = (),
    excluded_question_ids: tuple[str, ...] = (),
    excluded_template_ids: tuple[str, ...] = (),
    excluded_operand_signatures: tuple[str, ...] = (),
    excluded_context_ids: tuple[str, ...] | None = None,
) -> SlotIntent:
    if excluded_context_ids is None:
        excluded_context_ids = (
            ("context-prior",) if kind == "fragile_skill_transfer" else ()
        )
    return SlotIntent(
        kind=kind,
        campaign_world_id=campaign_world_id,
        content_world_id=content_world_id,
        skill_id=skill_id,
        procedure_ids=procedure_ids,
        excluded_item_ids=excluded_item_ids,
        excluded_question_ids=excluded_question_ids,
        excluded_template_ids=excluded_template_ids,
        excluded_operand_signatures=excluded_operand_signatures,
        excluded_context_ids=excluded_context_ids,
    )


def fixture_compiler(*, builder=None) -> FixtureCompiler:
    family = fixture_family()
    return FixtureCompiler(
        (family,),
        {procedure_id: family.family_id for procedure_id in family.templates[0].procedure_ids},
        builder=builder,
    )


def selection_exclusions(**overrides: tuple[str, ...]) -> SelectionExclusions:
    values = {
        "item_ids": (),
        "question_ids": (),
        "question_semantic_sha256s": (),
        "adjacent_template_ids": (),
        "adjacent_operand_signatures": (),
        "content_ids": (),
        "context_ids": (),
    }
    values.update(overrides)
    return SelectionExclusions(**values)


class SlotMaterializerTests(unittest.TestCase):
    def test_assisted_route_materializes_exact_fresh_difficulty_schedule(self):
        compiler = fixture_compiler()
        prior_operands = ("a" * 64, "b" * 64)
        intents = (
            intent(
                kind="assisted_worked_example",
                excluded_question_ids=("question-old-001", "question-old-002"),
                excluded_operand_signatures=prior_operands,
            ),
            intent(
                kind="assisted_supported_mcq",
                excluded_question_ids=("question-old-001", "question-old-002"),
                excluded_operand_signatures=prior_operands,
            ),
            intent(
                kind="assisted_supported_mcq",
                excluded_question_ids=("question-old-001", "question-old-002"),
                excluded_operand_signatures=prior_operands,
            ),
        )

        slots = materialize_slots(
            intents,
            "assisted_route",
            20260712,
            compiler,
        )

        self.assertEqual(tuple(slot.difficulty for slot in slots), (2, 1, 1))
        self.assertEqual(len({slot.question_semantic_sha256 for slot in slots}), 3)
        self.assertEqual(len({slot.operand_signature for slot in slots}), 3)
        self.assertTrue(all(
            slot.blueprint.question_id not in {"question-old-001", "question-old-002"}
            for slot in slots
        ))
        self.assertTrue(all(
            slot.operand_signature not in set(prior_operands)
            for slot in slots
        ))

    def test_materialized_slot_is_immutable_and_ready_for_exact_cache_lookup(self):
        compiler = fixture_compiler()
        old_content = "d" * 64
        source = intent(
            kind="active_misconception_probe",
            campaign_world_id="campaign-world",
            procedure_ids=("route-a",),
            excluded_item_ids=(old_content,),
            excluded_question_ids=("old-question",),
            excluded_template_ids=("old-template",),
            excluded_operand_signatures=("e" * 64,),
        )

        result = materialize_slots(
            (source, intent(), intent()),
            "route_1",
            83,
            compiler,
        )

        self.assertEqual(len(result), 3)
        slot = result[0]
        self.assertIsInstance(slot, MaterializedSlot)
        self.assertEqual(slot.slot_index, 0)
        self.assertEqual(slot.index, 0)
        self.assertEqual(slot.kind, source.kind)
        self.assertEqual(slot.campaign_world_id, "campaign-world")
        self.assertIs(slot.blueprint, compiler.compile_results[0])
        self.assertEqual(slot.request, compiler.requests[0])
        self.assertEqual(slot.compile_request, slot.request)
        self.assertEqual(slot.difficulty, 1)
        self.assertEqual(slot.required_procedure_ids, ("route-a",))
        self.assertIn("route-a", slot.blueprint.allowed_procedure_ids)
        self.assertEqual(slot.excluded_item_ids, (old_content,))
        self.assertEqual(slot.excluded_content_ids, ())
        self.assertEqual(slot.excluded_question_ids, ("old-question",))
        self.assertEqual(slot.excluded_template_ids, ("old-template",))
        self.assertEqual(slot.excluded_operand_signatures, ("e" * 64,))
        self.assertEqual(slot.excluded_context_ids, ())
        self.assertEqual(slot.cache_key.world_id, "world-a")
        self.assertEqual(slot.cache_key.skill_id, "skill-a")
        self.assertEqual(slot.cache_key.family_id, slot.request.family_id)
        self.assertEqual(slot.cache_key.difficulty, 1)
        self.assertEqual(slot.cache_key.required_procedure_ids, ("route-a",))
        self.assertEqual(slot.cache_key.registry_id, compiler.registry.registry_id)
        self.assertEqual(
            slot.cache_key.curriculum_id,
            compiler.curriculum.curriculum_id,
        )
        self.assertEqual(slot.cache_key.selection_seed, slot.selection_seed)
        self.assertEqual(slot.cache_key.excluded_content_ids, ())
        self.assertEqual(slot.cache_key.excluded_question_semantic_sha256s, ())
        self.assertEqual(slot.cache_key.excluded_context_ids, ())
        self.assertGreaterEqual(slot.selection_seed, 0)
        self.assertLess(slot.selection_seed, 2**63)
        with self.assertRaises(FrozenInstanceError):
            slot.difficulty = 3

    def test_all_tier_schedules_cycle_without_changing_intent_length_or_order(self):
        for tier, length in TIER_LENGTHS.items():
            with self.subTest(tier=tier):
                compiler = fixture_compiler()
                intents = tuple(
                    intent(kind=REGULAR_SLOT_KINDS[index % len(REGULAR_SLOT_KINDS)])
                    for index in range(length)
                )
                result = materialize_slots(intents, tier, 191, compiler)
                schedule = TIER_SCHEDULES[tier]
                self.assertEqual(len(result), length)
                self.assertEqual(
                    tuple(slot.kind for slot in result),
                    tuple(item.kind for item in intents),
                )
                self.assertEqual(
                    tuple(slot.difficulty for slot in result),
                    tuple(schedule[index % len(schedule)] for index in range(length)),
                )

    def test_diagnostic_probe_kinds_are_capped_at_difficulty_one(self):
        compiler = fixture_compiler()
        intents = (
            intent(kind="novel_current_skill"),
            intent(kind="active_misconception_probe"),
            intent(kind="fragile_skill_transfer"),
            intent(kind="misconception_discrimination"),
            intent(kind="under_sampled_core_skill"),
        )

        result = materialize_slots(intents, "elite", 211, compiler)

        self.assertEqual(tuple(slot.difficulty for slot in result), (2, 1, 2, 1, 2))

    def test_fragile_transfer_requires_and_enforces_a_prior_context_baseline(self):
        def builder(compiler, request, call_index):
            template_ids = (
                "fixture-template-a",
                "fixture-template-b",
                "fixture-template-c",
            )
            return fixture_blueprint(
                compiler,
                request,
                label=f"context-{call_index}",
                template_id=template_ids[call_index % len(template_ids)],
            )

        compiler = fixture_compiler(builder=builder)
        source = intent(
            kind="fragile_skill_transfer",
            excluded_context_ids=("context-a",),
        )

        slot = materialize_slots(
            (source, intent(), intent()),
            "route_1",
            217,
            compiler,
        )[0]

        self.assertEqual(len(compiler.requests), 4)
        self.assertEqual(slot.blueprint.template_id, "fixture-template-b")
        self.assertEqual(slot.excluded_context_ids, ("context-a",))
        self.assertEqual(slot.cache_key.excluded_context_ids, ("context-a",))

        empty = fixture_compiler()
        with self.assertRaisesRegex(
            SlotMaterializationError,
            "prior context baseline",
        ):
            materialize_slots(
                (
                    intent(
                        kind="fragile_skill_transfer",
                        excluded_context_ids=(),
                    ),
                    intent(),
                    intent(),
                ),
                "route_1",
                219,
                empty,
            )
        self.assertEqual(empty.requests, [])

    def test_only_the_eight_supported_slot_kinds_are_accepted_before_compilation(self):
        self.assertEqual(
            slot_materializer_module.SUPPORTED_SLOT_KINDS,
            frozenset({
                "active_misconception_probe",
                "assisted_supported_mcq",
                "assisted_worked_example",
                "misconception_discrimination",
                "fragile_skill_transfer",
                "under_sampled_core_skill",
                "spaced_prior_world_transfer",
                "novel_current_skill",
            }),
        )
        for invalid_kind in ("", "kind-0", "boss_probe", None, 7):
            with self.subTest(invalid_kind=invalid_kind):
                compiler = fixture_compiler()
                invalid = intent(kind=invalid_kind)
                with self.assertRaisesRegex(
                    SlotMaterializationError,
                    "unsupported slot kind",
                ):
                    materialize_slots(
                        (intent(), invalid, intent()),
                        "route_1",
                        223,
                        compiler,
                    )
                self.assertEqual(compiler.requests, [])

    def test_repeatability_is_independent_of_mapping_insertion_order(self):
        family_a = fixture_family("family-a")
        family_b = fixture_family("family-b")
        procedures = {
            **{route: "family-a" for route in family_a.templates[0].procedure_ids},
            "route-d": "family-b",
        }
        family_b = fixture_family("family-b", procedure_ids=("route-d",))
        intents = tuple(intent() for _ in range(4))
        forward = FixtureCompiler((family_a, family_b), procedures)
        reverse = FixtureCompiler((family_b, family_a), procedures)

        first = materialize_slots(intents, "route_2", 0, forward)
        replay = materialize_slots(intents, "route_2", 0, reverse)

        first_families = tuple(slot.request.family_id for slot in first)
        self.assertEqual(first, replay)
        self.assertEqual(first_families, tuple(slot.request.family_id for slot in replay))
        self.assertIn(
            first_families,
            {
                ("family-a", "family-b", "family-a", "family-b"),
                ("family-b", "family-a", "family-b", "family-a"),
            },
        )
        changed = materialize_slots(intents, "route_2", 1, FixtureCompiler(
            (family_a, family_b), procedures
        ))
        self.assertNotEqual(
            tuple(slot.request.seed for slot in first),
            tuple(slot.request.seed for slot in changed),
        )
        self.assertNotEqual(
            tuple(slot.selection_seed for slot in first),
            tuple(slot.selection_seed for slot in changed),
        )

    def test_required_routes_filter_family_and_cross_family_pairs_fail_closed(self):
        family_a = fixture_family("family-a", procedure_ids=("route-a",))
        family_b = fixture_family("family-b", procedure_ids=("route-b",))
        compiler = FixtureCompiler(
            (family_a, family_b),
            {"route-a": "family-a", "route-b": "family-b"},
        )

        selected = materialize_slots(
            (
                intent(procedure_ids=("route-b",)),
                intent(),
                intent(),
            ),
            "route_1",
            307,
            compiler,
        )[0]
        self.assertEqual(selected.request.family_id, "family-b")
        self.assertEqual(selected.required_procedure_ids, ("route-b",))
        self.assertIn("route-b", selected.blueprint.allowed_procedure_ids)

        impossible = FixtureCompiler(
            (family_a, family_b),
            {"route-a": "family-a", "route-b": "family-b"},
        )
        with self.assertRaisesRegex(SlotMaterializationError, "compatible family"):
            materialize_slots(
                (
                    intent(procedure_ids=("route-a", "route-b")),
                    intent(),
                    intent(),
                ),
                "route_1",
                307,
                impossible,
            )
        self.assertEqual(impossible.requests, [])

    def test_unknown_or_crosswired_world_skill_and_procedure_fail_before_compile(self):
        cases = (
            intent(content_world_id="other-world"),
            intent(skill_id="other-skill"),
            intent(procedure_ids=("unknown-route",)),
            intent(procedure_ids=("route-a", "route-a")),
        )
        for source in cases:
            with self.subTest(source=source):
                compiler = fixture_compiler()
                with self.assertRaises(SlotMaterializationError):
                    materialize_slots(
                        (source, intent(), intent()),
                        "route_1",
                        401,
                        compiler,
                    )
                self.assertEqual(compiler.requests, [])

    def test_question_template_operand_and_content_exclusions_are_all_enforced(self):
        blocked_operand = operand_signature("family-a", ("a",), ("77",))

        def builder(compiler, request, call_index):
            if call_index == 0:
                return fixture_blueprint(
                    compiler,
                    request,
                    label="blocked-question",
                    question_id="blocked-question",
                )
            if call_index == 1:
                return fixture_blueprint(
                    compiler,
                    request,
                    label="blocked-template",
                    template_id="blocked-template",
                )
            if call_index == 2:
                return fixture_blueprint(
                    compiler,
                    request,
                    label="blocked-operand",
                    operand="77",
                )
            return fixture_blueprint(
                compiler,
                request,
                label=f"accepted-{call_index}",
            )

        compiler = fixture_compiler(builder=builder)
        source = intent(
            excluded_item_ids=("prior-public-item-id",),
            excluded_question_ids=("blocked-question",),
            excluded_template_ids=("blocked-template",),
            excluded_operand_signatures=(blocked_operand,),
        )

        slot = materialize_slots(
            (source, intent(), intent()),
            "route_1",
            509,
            compiler,
            max_attempts=5,
        )[0]

        self.assertEqual(len(compiler.requests), 6)
        self.assertEqual(slot.blueprint.question_id, "question-accepted-3")
        self.assertEqual(slot.cache_key.excluded_question_ids, ("blocked-question",))
        self.assertEqual(slot.cache_key.excluded_template_ids, ("blocked-template",))
        self.assertEqual(slot.cache_key.excluded_operand_signatures, (blocked_operand,))
        self.assertEqual(slot.excluded_item_ids, ("prior-public-item-id",))
        self.assertEqual(slot.cache_key.excluded_content_ids, ())

    def test_immediately_prior_materialized_blueprint_is_excluded_from_next_slot(self):
        def builder(compiler, request, call_index):
            if call_index in (0, 1):
                return fixture_blueprint(
                    compiler,
                    request,
                    label="same",
                    question_id="same-question",
                    template_id="same-template",
                    operand="44",
                    content_sha256="1" * 64,
                )
            return fixture_blueprint(
                compiler,
                request,
                label=f"different-{call_index}",
                operand=str(43 + call_index),
                content_sha256=hashlib.sha256(
                    f"different-content-{call_index}".encode("ascii")
                ).hexdigest(),
            )

        compiler = fixture_compiler(builder=builder)

        first, second, third = materialize_slots(
            (intent(), intent(), intent()),
            "route_1",
            601,
            compiler,
        )

        self.assertEqual(len(compiler.requests), 4)
        self.assertNotEqual(first.blueprint.question_id, second.blueprint.question_id)
        self.assertIn(first.blueprint.question_id, second.excluded_question_ids)
        self.assertIn(first.blueprint.template_id, second.excluded_template_ids)
        self.assertIn(first.operand_signature, second.excluded_operand_signatures)
        self.assertIn(first.blueprint.content_sha256, second.excluded_content_ids)
        self.assertEqual(
            second.cache_key.excluded_content_ids,
            second.excluded_content_ids,
        )
        self.assertEqual(
            second.cache_key.excluded_question_semantic_sha256s,
            (first.question_semantic_sha256,),
        )
        self.assertNotEqual(second.blueprint.question_id, third.blueprint.question_id)

    def test_question_and_content_reuse_from_two_slots_back_is_rejected(self):
        def builder(compiler, request, call_index):
            records = (
                ("a", "question-a", "template-a", "41", "1" * 64),
                ("b", "question-b", "template-b", "42", "2" * 64),
                ("question-reused", "question-a", "template-c", "43", "3" * 64),
                ("content-reused", "question-d", "template-d", "44", "1" * 64),
                ("c", "question-c", "template-e", "45", "4" * 64),
            )
            label, question, template, operand, content = records[call_index]
            return fixture_blueprint(
                compiler,
                request,
                label=label,
                question_id=question,
                template_id=template,
                operand=operand,
                content_sha256=content,
            )

        compiler = fixture_compiler(builder=builder)

        first, second, third = materialize_slots(
            (intent(), intent(), intent()),
            "route_1",
            659,
            compiler,
        )

        self.assertEqual(len(compiler.requests), 5)
        self.assertEqual(third.blueprint.question_id, "question-c")
        self.assertEqual(
            third.excluded_question_ids,
            (first.blueprint.question_id, second.blueprint.question_id),
        )
        self.assertEqual(
            third.excluded_content_ids,
            (first.blueprint.content_sha256, second.blueprint.content_sha256),
        )
        self.assertEqual(third.excluded_template_ids, (second.blueprint.template_id,))
        self.assertEqual(third.excluded_operand_signatures, (second.operand_signature,))

    def test_public_semantic_hash_excludes_provenance_but_binds_question_meaning(self):
        compiler = fixture_compiler()
        slots = materialize_slots(
            (intent(), intent(), intent()),
            "route_1",
            683,
            compiler,
        )
        blueprint = slots[0].blueprint

        semantic_hash = slot_materializer_module.question_semantic_sha256(blueprint)

        self.assertEqual(slots[0].question_semantic_sha256, semantic_hash)
        self.assertRegex(semantic_hash, r"^[0-9a-f]{64}$")
        provenance_only = replace(
            blueprint,
            question_id="different-question-id",
            seed=(blueprint.seed + 1) % (2**63),
            content_sha256="f" * 64,
        )
        self.assertEqual(
            slot_materializer_module.question_semantic_sha256(provenance_only),
            semantic_hash,
        )
        meaning_changes = (
            replace(blueprint, family_id="different-family"),
            replace(blueprint, prompt=blueprint.prompt + " Changed meaning."),
            replace(
                blueprint,
                canonical_answer=CanonicalAnswer(Fraction(2), "2"),
            ),
            replace(
                blueprint,
                trusted_steps=blueprint.trusted_steps + ("One more exact step.",),
            ),
            replace(blueprint, operands=(str(int(blueprint.operands[0]) + 1),)),
        )
        for changed in meaning_changes:
            with self.subTest(changed=changed):
                self.assertNotEqual(
                    slot_materializer_module.question_semantic_sha256(changed),
                    semantic_hash,
                )

    def test_real_seed_649_retries_seed_distinct_but_semantically_identical_questions(self):
        compiler = QuestionCompiler.for_tests()
        intents = tuple(
            intent(
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
            )
            for _ in range(3)
        )

        slots = materialize_slots(intents, "route_1", 649, compiler)

        self.assertEqual(len(slots), 3)
        self.assertEqual(len({slot.question_semantic_sha256 for slot in slots}), 3)
        self.assertNotEqual(
            slots[0].question_semantic_sha256,
            slots[2].question_semantic_sha256,
        )

    def test_absent_required_route_and_holdout_match_exhaust_only_the_bound(self):
        for case in ("missing-route", "holdout"):
            with self.subTest(case=case):
                def builder(compiler, request, call_index):
                    return fixture_blueprint(
                        compiler,
                        request,
                        label=f"{case}-{call_index}",
                        allowed_procedure_ids=(
                            ("route-b", "route-c")
                            if case == "missing-route"
                            else ("route-a", "route-b", "route-c")
                        ),
                        holdout_excluded=case == "holdout",
                    )

                compiler = fixture_compiler(builder=builder)
                with self.assertRaisesRegex(
                    SlotMaterializationError,
                    "could not materialize slot 0",
                ):
                    materialize_slots(
                        (
                            intent(procedure_ids=("route-a",)),
                            intent(),
                            intent(),
                        ),
                        "route_1",
                        701,
                        compiler,
                        max_attempts=3,
                    )
                self.assertEqual(len(compiler.requests), 3)

    def test_compile_failures_are_retried_but_unexpected_errors_are_not_swallowed(self):
        def bounded_failure(compiler, request, call_index):
            raise CompilationError("fixture cannot compile")

        compiler = fixture_compiler(builder=bounded_failure)
        with self.assertRaises(SlotMaterializationError):
            materialize_slots(
                (intent(), intent(), intent()),
                "route_1",
                809,
                compiler,
                max_attempts=2,
            )
        self.assertEqual(len(compiler.requests), 2)

        def programming_error(compiler, request, call_index):
            raise RuntimeError("do not hide this")

        compiler = fixture_compiler(builder=programming_error)
        with self.assertRaisesRegex(RuntimeError, "do not hide this"):
            materialize_slots(
                (intent(), intent(), intent()),
                "route_1",
                809,
                compiler,
            )
        self.assertEqual(len(compiler.requests), 1)

    def test_tier_intent_count_mismatch_fails_before_any_compilation(self):
        for tier, expected in TIER_LENGTHS.items():
            for actual in (expected - 1, expected + 1):
                with self.subTest(tier=tier, actual=actual):
                    compiler = fixture_compiler()
                    with self.assertRaisesRegex(
                        SlotMaterializationError,
                        "requires exactly",
                    ):
                        materialize_slots(
                            tuple(intent() for _ in range(actual)),
                            tier,
                            877,
                            compiler,
                        )
                    self.assertEqual(compiler.requests, [])

    def test_seed_tier_attempt_and_input_contracts_reject_invalid_values(self):
        for batch_seed in (-1, 2**63, True, 1.5, "7"):
            with self.subTest(batch_seed=batch_seed), self.assertRaises(ValueError):
                materialize_slots((intent(),), "route_1", batch_seed, fixture_compiler())
        for max_attempts in (0, MAX_SEED_ATTEMPTS + 1, True, 1.5):
            with self.subTest(max_attempts=max_attempts), self.assertRaises(ValueError):
                materialize_slots(
                    (intent(),),
                    "route_1",
                    7,
                    fixture_compiler(),
                    max_attempts=max_attempts,
                )
        for tier in ("unknown", "", None, 1):
            with self.subTest(tier=tier), self.assertRaises(ValueError):
                materialize_slots((intent(),), tier, 7, fixture_compiler())
        with self.assertRaises(TypeError):
            materialize_slots((object(),), "route_1", 7, fixture_compiler())
        with self.assertRaises(TypeError):
            materialize_slots((intent(),), "route_1", 7, object())

    def test_real_packaged_compiler_produces_exact_holdout_safe_targeted_blueprints(self):
        compiler = QuestionCompiler.for_tests()
        intents = tuple(
            intent(
                kind="active_misconception_probe" if index == 0 else "novel_current_skill",
                campaign_world_id="valuehold",
                content_world_id="valuehold",
                skill_id="place_value",
                procedure_ids=("pv_face_value",) if index == 0 else (),
            )
            for index in range(3)
        )

        result = materialize_slots(intents, "route_1", 911, compiler)

        self.assertEqual(len(result), 3)
        for slot in result:
            self.assertEqual(slot.request.world_id, "valuehold")
            self.assertEqual(slot.request.skill_id, "place_value")
            self.assertEqual(slot.request.family_id, "place_value")
            self.assertEqual(slot.blueprint, compiler.compile(slot.request))
            self.assertFalse(slot.blueprint.holdout_receipt.excluded)
            self.assertTrue(
                set(slot.required_procedure_ids).issubset(
                    slot.blueprint.allowed_procedure_ids
                )
            )


class LiveSlotCandidateTests(unittest.TestCase):
    def planned_slot(
        self,
        *,
        source: SlotIntent | None = None,
    ) -> MaterializedSlot:
        compiler = fixture_compiler()
        return materialize_slots(
            (source or intent(), intent(), intent()),
            "route_1",
            1201,
            compiler,
        )[0]

    def test_attempt_one_reuses_an_allowed_plan_and_attempt_two_is_a_deterministic_new_prompt(self):
        planned = self.planned_slot()
        exclusions = selection_exclusions()
        first_compiler = fixture_compiler()

        first = slot_materializer_module.materialize_live_candidate(
            planned,
            batch_seed=1301,
            live_attempt=1,
            compiler=first_compiler,
            exclusions=exclusions,
        )

        self.assertIsInstance(first, slot_materializer_module.LiveSlotCandidate)
        self.assertIs(first.blueprint, planned.blueprint)
        self.assertEqual(first.request, planned.request)
        self.assertEqual(first.slot_index, planned.slot_index)
        self.assertEqual(first.operand_signature, planned.operand_signature)
        self.assertEqual(
            first.question_semantic_sha256,
            planned.question_semantic_sha256,
        )
        self.assertEqual(first_compiler.requests, [])

        second_compiler = fixture_compiler()
        second = slot_materializer_module.materialize_live_candidate(
            planned,
            batch_seed=1301,
            live_attempt=2,
            compiler=second_compiler,
            exclusions=exclusions,
            attempted_semantic_sha256s=(first.question_semantic_sha256,),
        )
        replay_compiler = fixture_compiler()
        replay = slot_materializer_module.materialize_live_candidate(
            planned,
            batch_seed=1301,
            live_attempt=2,
            compiler=replay_compiler,
            exclusions=exclusions,
            attempted_semantic_sha256s=(first.question_semantic_sha256,),
        )

        self.assertEqual(second, replay)
        self.assertGreaterEqual(len(second_compiler.requests), 1)
        self.assertEqual(second_compiler.requests, replay_compiler.requests)
        self.assertNotEqual(second.request.seed, planned.request.seed)
        self.assertNotEqual(
            second.question_semantic_sha256,
            first.question_semantic_sha256,
        )
        self.assertNotEqual(
            build_slm_request(second.blueprint).prompt_sha256,
            build_slm_request(first.blueprint).prompt_sha256,
        )
        changed_seed = slot_materializer_module.materialize_live_candidate(
            planned,
            batch_seed=1302,
            live_attempt=2,
            compiler=fixture_compiler(),
            exclusions=exclusions,
            attempted_semantic_sha256s=(first.question_semantic_sha256,),
        )
        self.assertNotEqual(second.request.seed, changed_seed.request.seed)

    def test_actual_selection_exclusions_each_block_the_planned_or_compiled_candidate(self):
        planned = self.planned_slot()
        cases = {
            "question": (
                {"question_ids": (planned.blueprint.question_id,)},
                lambda candidate: candidate.blueprint.question_id,
            ),
            "semantic": (
                {
                    "question_semantic_sha256s": (
                        planned.question_semantic_sha256,
                    )
                },
                lambda candidate: candidate.question_semantic_sha256,
            ),
            "template": (
                {"adjacent_template_ids": (planned.blueprint.template_id,)},
                lambda candidate: candidate.blueprint.template_id,
            ),
            "operand": (
                {"adjacent_operand_signatures": (planned.operand_signature,)},
                lambda candidate: candidate.operand_signature,
            ),
            "content": (
                {"content_ids": (planned.blueprint.content_sha256,)},
                lambda candidate: candidate.blueprint.content_sha256,
            ),
            "context": (
                {"context_ids": ("context-a",)},
                lambda candidate: slot_materializer_module._blueprint_context_id(
                    fixture_compiler(),
                    candidate.blueprint,
                ),
            ),
        }
        for name, (fields, selected_value) in cases.items():
            with self.subTest(name=name):
                compiler = fixture_compiler()
                exclusions = selection_exclusions(**fields)

                candidate = slot_materializer_module.materialize_live_candidate(
                    planned,
                    batch_seed=1401,
                    live_attempt=1,
                    compiler=compiler,
                    exclusions=exclusions,
                )

                self.assertGreaterEqual(len(compiler.requests), 1)
                excluded_values = next(iter(fields.values()))
                self.assertNotIn(selected_value(candidate), excluded_values)

    def test_fragile_transfer_honors_the_actual_prior_context_baseline(self):
        planned = self.planned_slot(
            source=intent(
                kind="fragile_skill_transfer",
                excluded_context_ids=("context-prior",),
            )
        )
        compiler = fixture_compiler()

        candidate = slot_materializer_module.materialize_live_candidate(
            planned,
            batch_seed=1451,
            live_attempt=1,
            compiler=compiler,
            exclusions=selection_exclusions(context_ids=("context-a",)),
        )

        self.assertEqual(candidate.slot_index, planned.slot_index)
        self.assertNotEqual(candidate.blueprint.template_id, "fixture-template-a")
        self.assertEqual(
            slot_materializer_module._blueprint_context_id(
                compiler,
                candidate.blueprint,
            ),
            "context-b",
        )

    def test_attempted_semantics_are_excluded_from_later_compiler_retries(self):
        planned = self.planned_slot()
        first_compiler = fixture_compiler()
        first = slot_materializer_module.materialize_live_candidate(
            planned,
            batch_seed=1501,
            live_attempt=1,
            compiler=first_compiler,
            exclusions=selection_exclusions(
                question_ids=(planned.blueprint.question_id,),
            ),
        )

        def builder(compiler, request, call_index):
            if call_index == 0:
                return replace(
                    first.blueprint,
                    question_id="attempted-semantic-with-new-provenance",
                    seed=request.seed,
                    content_sha256="9" * 64,
                )
            return fixture_blueprint(
                compiler,
                request,
                label=f"fresh-{call_index}",
            )

        second_compiler = fixture_compiler(builder=builder)
        second = slot_materializer_module.materialize_live_candidate(
            planned,
            batch_seed=1501,
            live_attempt=2,
            compiler=second_compiler,
            exclusions=selection_exclusions(),
            attempted_semantic_sha256s=(first.question_semantic_sha256,),
        )

        self.assertEqual(len(second_compiler.requests), 2)
        self.assertNotEqual(
            second.question_semantic_sha256,
            first.question_semantic_sha256,
        )

    def test_live_request_identity_is_locked_and_required_routes_are_rechecked(self):
        planned = self.planned_slot(
            source=intent(
                kind="active_misconception_probe",
                campaign_world_id="campaign-world",
                procedure_ids=("route-a",),
            )
        )

        def builder(compiler, request, call_index):
            return fixture_blueprint(
                compiler,
                request,
                label=f"route-{call_index}",
                allowed_procedure_ids=(
                    ("route-b", "route-c")
                    if call_index == 0
                    else ("route-a", "route-b", "route-c")
                ),
            )

        compiler = fixture_compiler(builder=builder)
        candidate = slot_materializer_module.materialize_live_candidate(
            planned,
            batch_seed=1601,
            live_attempt=2,
            compiler=compiler,
            exclusions=selection_exclusions(),
            attempted_semantic_sha256s=(planned.question_semantic_sha256,),
        )

        self.assertEqual(len(compiler.requests), 2)
        self.assertEqual(candidate.slot_index, planned.slot_index)
        self.assertEqual(candidate.request.world_id, planned.content_world_id)
        self.assertEqual(candidate.request.skill_id, planned.skill_id)
        self.assertEqual(candidate.request.family_id, planned.request.family_id)
        self.assertEqual(candidate.request.difficulty, planned.difficulty)
        self.assertEqual(planned.kind, "active_misconception_probe")
        self.assertEqual(planned.campaign_world_id, "campaign-world")
        self.assertIn("route-a", candidate.blueprint.allowed_procedure_ids)

    def test_mismatched_compiler_blueprints_exhaust_without_escaping_the_plan(self):
        planned = self.planned_slot()
        mutations = {
            "world": lambda value: replace(value, world_id="other-world"),
            "skill": lambda value: replace(value, skill_id="other-skill"),
            "family": lambda value: replace(value, family_id="other-family"),
            "difficulty": lambda value: replace(value, difficulty=3),
            "seed": lambda value: replace(value, seed=(value.seed + 1) % (2**63)),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                def builder(compiler, request, call_index):
                    return mutate(fixture_blueprint(
                        compiler,
                        request,
                        label=f"mismatch-{call_index}",
                    ))

                compiler = fixture_compiler(builder=builder)
                with self.assertRaisesRegex(
                    SlotMaterializationError,
                    r"live candidate.*slot 0.*attempt 2",
                ):
                    slot_materializer_module.materialize_live_candidate(
                        planned,
                        batch_seed=1701,
                        live_attempt=2,
                        compiler=compiler,
                        exclusions=selection_exclusions(),
                        attempted_semantic_sha256s=(
                            planned.question_semantic_sha256,
                        ),
                        max_compile_attempts=2,
                    )
                self.assertEqual(len(compiler.requests), 2)

    def test_exhausted_live_candidates_raise_one_stable_fail_closed_error(self):
        planned = self.planned_slot()

        def builder(compiler, request, call_index):
            return replace(
                planned.blueprint,
                question_id=f"new-provenance-{call_index}",
                seed=request.seed,
                content_sha256=hashlib.sha256(
                    f"new-provenance-{call_index}".encode("ascii")
                ).hexdigest(),
            )

        compiler = fixture_compiler(builder=builder)
        with self.assertRaisesRegex(
            SlotMaterializationError,
            r"^could not materialize live candidate for slot 0 attempt 2 "
            r"in 3 compiler attempts$",
        ):
            slot_materializer_module.materialize_live_candidate(
                planned,
                batch_seed=1801,
                live_attempt=2,
                compiler=compiler,
                exclusions=selection_exclusions(),
                attempted_semantic_sha256s=(
                    planned.question_semantic_sha256,
                ),
                max_compile_attempts=3,
            )
        self.assertEqual(len(compiler.requests), 3)

    def test_live_candidate_rejects_invalid_attempt_seed_compiler_and_bounds_before_compile(self):
        planned = self.planned_slot()
        for live_attempt in (0, 3, True, 1.5, "1"):
            with self.subTest(live_attempt=live_attempt), self.assertRaises(ValueError):
                slot_materializer_module.materialize_live_candidate(
                    planned,
                    batch_seed=1901,
                    live_attempt=live_attempt,
                    compiler=fixture_compiler(),
                    exclusions=selection_exclusions(),
                )
        for batch_seed in (-1, 2**63, True, 1.5, "7"):
            with self.subTest(batch_seed=batch_seed), self.assertRaises(ValueError):
                slot_materializer_module.materialize_live_candidate(
                    planned,
                    batch_seed=batch_seed,
                    live_attempt=1,
                    compiler=fixture_compiler(),
                    exclusions=selection_exclusions(),
                )
        for bound in (0, MAX_SEED_ATTEMPTS + 1, True, 1.5):
            with self.subTest(bound=bound), self.assertRaises(ValueError):
                slot_materializer_module.materialize_live_candidate(
                    planned,
                    batch_seed=1901,
                    live_attempt=1,
                    compiler=fixture_compiler(),
                    exclusions=selection_exclusions(),
                    max_compile_attempts=bound,
                )
        with self.assertRaises(TypeError):
            slot_materializer_module.materialize_live_candidate(
                planned,
                batch_seed=1901,
                live_attempt=1,
                compiler=object(),
                exclusions=selection_exclusions(),
            )
        with self.assertRaises(TypeError):
            slot_materializer_module.materialize_live_candidate(
                object(),
                batch_seed=1901,
                live_attempt=1,
                compiler=fixture_compiler(),
                exclusions=selection_exclusions(),
            )
        with self.assertRaises(TypeError):
            slot_materializer_module.materialize_live_candidate(
                planned,
                batch_seed=1901,
                live_attempt=1,
                compiler=fixture_compiler(),
                exclusions=object(),
            )
        for attempted_semantics in (
            "not-a-tuple",
            ("not-a-sha256",),
            ("a" * 64, "a" * 64),
            ([],),
        ):
            with (
                self.subTest(attempted_semantics=attempted_semantics),
                self.assertRaises(ValueError),
            ):
                slot_materializer_module.materialize_live_candidate(
                    planned,
                    batch_seed=1901,
                    live_attempt=1,
                    compiler=fixture_compiler(),
                    exclusions=selection_exclusions(),
                    attempted_semantic_sha256s=attempted_semantics,
                )

    def test_second_attempt_requires_the_first_attempt_semantic_receipt(self):
        planned = self.planned_slot()
        compiler = fixture_compiler()

        with self.assertRaisesRegex(
            ValueError,
            "attempt two requires at least one attempted semantic",
        ):
            slot_materializer_module.materialize_live_candidate(
                planned,
                batch_seed=1903,
                live_attempt=2,
                compiler=compiler,
                exclusions=selection_exclusions(),
            )

        self.assertEqual(compiler.requests, [])


if __name__ == "__main__":
    unittest.main()
