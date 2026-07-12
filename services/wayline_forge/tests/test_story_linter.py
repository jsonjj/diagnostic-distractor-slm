import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.wayline_forge.app.providers.narrative import (
    DemoUnit,
    FeedbackToneId,
    PlaceholderName,
    ReadingLevelId,
    RenderedStory,
    StoryRenderValues,
    StoryFrameId,
    StorySettingId,
    StorySkin,
    StorySkinRequest,
    StoryStyleId,
    TrustedNumericDisplay,
)
from services.wayline_forge.app.providers.template_narrative import (
    FeedbackTone,
    STORY_TEMPLATES_V1_SHA256,
    StoryTemplateCatalog,
    StoryTemplateError,
    TemplateNarrativeProvider,
)
from services.wayline_forge.app.story_linter import (
    StoryLintError,
    StoryRenderError,
    lint_story_skin,
    render_story_skin,
)


def request() -> StorySkinRequest:
    return StorySkinRequest(
        style_id=StoryStyleId.MEASURED_SURVEYOR,
        setting_id=StorySettingId.VALUEHOLD_REACH,
        reading_level_id=ReadingLevelId.MIDDLE_GRADE,
        story_frame_id=StoryFrameId.ROUTE_ARRIVAL,
        placeholder_names=(
            PlaceholderName.A,
            PlaceholderName.B,
            PlaceholderName.UNIT,
        ),
    )


class StoryLinterTests(unittest.TestCase):
    def test_valid_story_preserves_each_required_placeholder_once(self):
        payload = {
            "displayText": "The survey bridge joins {A} and {B} {UNIT} at the marker."
        }

        skin = lint_story_skin(payload, request())

        self.assertIsInstance(skin, StorySkin)
        self.assertEqual(skin.display_text, payload["displayText"])

    def test_unknown_missing_and_duplicate_placeholders_are_rejected(self):
        invalid = {
            "unknown": "Move {A} {UNIT} past {B} {SECRET} at the marker.",
            "missing": "Move {A} {UNIT} toward the marker.",
            "duplicate": "Move {A} and {A} {UNIT} past {B} at the marker.",
            "malformed": "Move {A} and {B} {UNIT} past {BROKEN at the marker.",
        }
        for case, text in invalid.items():
            with self.subTest(case=case), self.assertRaises(StoryLintError):
                lint_story_skin({"displayText": text}, request())

    def test_numeric_literals_and_answer_leakage_are_rejected(self):
        invalid = (
            "Move {A} and {B} {UNIT} across gate 7.",
            "The correct answer joins {A} and {B} {UNIT} at the marker.",
            "The solution equals {A} and {B} {UNIT} at the marker.",
            "Choose the right option for {A} and {B} {UNIT} at the marker.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(StoryLintError):
                lint_story_skin({"displayText": text}, request())

    def test_review_bypasses_and_unknown_story_language_are_rejected(self):
        invalid = (
            "Subtract {A} and {B} {UNIT} at the marker.",
            "Guide {A}{B} {UNIT} at the marker.",
            "Go die while {A} and {B} {UNIT} wait.",
            "The c\u043erecht \u0430nswer joins {A} and {B} {UNIT}.",
            "Guide {A} and {B} {UNIT}\u2028at the marker.",
            "The survey bridge frobnitzes {A} and {B} {UNIT} at the marker.",
            "The survey bridge joins {A} + {B} {UNIT} at the marker.",
            "The survey bridge joins {A} and {B} {UNIT} = the marker.",
            "Add {A} and {B} {UNIT} at the marker.",
            "Move more than {A} and {B} {UNIT} at the marker.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(StoryLintError):
                lint_story_skin({"displayText": text}, request())

    def test_placeholder_clause_is_exact_standalone_and_occurs_once(self):
        invalid = (
            "The survey bridge joins x{A} and {B} {UNIT} at the marker.",
            "The survey bridge joins {A} and {B} {UNIT}x at the marker.",
            "The survey bridge joins {B} and {A} {UNIT} at the marker.",
            "The survey bridge joins {A} {B} and {UNIT} at the marker.",
            "The survey bridge joins {A} and {B} {UNIT}, then {A} and {B} {UNIT}.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(StoryLintError):
                lint_story_skin({"displayText": text}, request())

    def test_blame_deficit_language_and_second_person_diagnosis_are_rejected(self):
        invalid = (
            "A careless mistake left {A} and {B} {UNIT} at the marker.",
            "Weak learners struggle with {A} and {B} {UNIT} at the marker.",
            "This shows you misunderstood {A} and {B} {UNIT} at the marker.",
            "You are confused by {A} and {B} {UNIT} at the marker.",
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(StoryLintError):
                lint_story_skin({"displayText": text}, request())

    def test_markup_control_characters_unsupported_fields_and_long_text_are_rejected(self):
        invalid_payloads = (
            {"displayText": "Guide <b>{A}</b> and {B} {UNIT} to the marker."},
            {"displayText": "Guide {A} and {B} {UNIT}\nthrough the marker."},
            {
                "displayText": "Guide {A} and {B} {UNIT} to the marker.",
                "answer": "hidden",
            },
            {"text": "Guide {A} and {B} {UNIT} to the marker."},
            {
                "displayText": (
                    "Across the broad Wayline terraces, "
                    + "calm route lights shimmer softly " * 8
                    + "around {A}, {B}, and {UNIT}."
                )
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(StoryLintError):
                lint_story_skin(payload, request())

    def test_authored_catalog_is_closed_complete_and_linted(self):
        catalog = StoryTemplateCatalog.packaged_v1()

        self.assertEqual(len(catalog.templates), 18)
        self.assertEqual(len(catalog.feedback_tones), 4)
        for setting in StorySettingId:
            matching = [item for item in catalog.templates if item.setting_id is setting]
            self.assertEqual(len(matching), 6)
            self.assertEqual(
                {item.story_frame_id for item in matching},
                set(StoryFrameId),
            )
        self.assertEqual(
            {tone.feedback_tone_id for tone in catalog.feedback_tones},
            set(FeedbackToneId),
        )
        self.assertEqual(
            len({template.template_id for template in catalog.templates}),
            len(catalog.templates),
        )
        self.assertRegex(STORY_TEMPLATES_V1_SHA256, r"^[0-9a-f]{64}$")

    def test_authored_provider_is_deterministic_and_passes_the_same_linter(self):
        provider = TemplateNarrativeProvider()

        first = provider.skin(request())
        second = provider.skin(request())

        self.assertEqual(first, second)
        self.assertEqual(first, lint_story_skin(first, request()))
        self.assertEqual(first.display_text.count("{A}"), 1)
        self.assertEqual(first.display_text.count("{B}"), 1)
        self.assertEqual(first.display_text.count("{UNIT}"), 1)

    def test_typed_renderer_substitutes_each_trusted_slot_once(self):
        skin = TemplateNarrativeProvider().skin(request())
        values = StoryRenderValues(
            a=TrustedNumericDisplay("12.5"),
            b=TrustedNumericDisplay("3/4"),
            unit=DemoUnit.SURVEY_MARKS,
        )

        rendered = render_story_skin(skin, request(), values)

        self.assertIsInstance(rendered, RenderedStory)
        self.assertIn("12.5 and 3/4 survey marks", rendered.display_text)
        self.assertNotIn("{", rendered.display_text)
        self.assertNotIn("}", rendered.display_text)
        self.assertTrue(all(ord(char) < 128 for char in rendered.display_text))

    def test_numeric_display_type_rejects_markup_ambiguity_and_noncanonical_values(self):
        for valid in ("0", "-3", "12.5", "3/4", "25%", "-0.25"):
            with self.subTest(valid=valid):
                self.assertEqual(TrustedNumericDisplay(valid).value, valid)

        invalid = (
            "",
            " 3",
            "03",
            "+3",
            "1e3",
            "1,000",
            "1/0",
            "--1",
            "1+2",
            "3 < 4",
            "<size=200>3</size>",
            "\u0663",
            "1234567",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                TrustedNumericDisplay(value)

    def test_renderer_rejects_wrong_unit_bad_symbolic_skin_and_expanded_overflow(self):
        valid_skin = TemplateNarrativeProvider().skin(request())
        wrong_unit = StoryRenderValues(
            TrustedNumericDisplay("3"),
            TrustedNumericDisplay("4"),
            DemoUnit.TIDE_MEASURES,
        )
        with self.assertRaises(StoryRenderError):
            render_story_skin(valid_skin, request(), wrong_unit)

        repeated_skin = StorySkin(
            "The survey bridge joins {A} and {B} {UNIT} beside {A}."
        )
        valid_values = StoryRenderValues(
            TrustedNumericDisplay("3"),
            TrustedNumericDisplay("4"),
            DemoUnit.SURVEY_MARKS,
        )
        with self.assertRaises(StoryRenderError):
            render_story_skin(repeated_skin, request(), valid_values)

        long_skin = StorySkin("Route " * 26 + "{A} and {B} {UNIT}.")
        long_values = StoryRenderValues(
            TrustedNumericDisplay("999999.9999%"),
            TrustedNumericDisplay("-999999.9999%"),
            DemoUnit.SURVEY_MARKS,
        )
        with self.assertRaises(StoryRenderError):
            render_story_skin(long_skin, request(), long_values)

    def test_resource_loader_rejects_duplicate_keys_and_packaged_tampering(self):
        packaged = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "story_templates_v1.json"
        )
        original = packaged.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text(
                original.replace(
                    '"schema_version":',
                    '"schema_version": "duplicate", "schema_version":',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(StoryTemplateError):
                StoryTemplateCatalog.load(duplicate)

            modified = Path(directory) / "modified.json"
            payload = json.loads(original)
            payload["template_set_id"] = "tampered-story-templates"
            modified.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(StoryTemplateError):
                StoryTemplateCatalog.packaged_v1(resource_path=modified)

    def test_packaged_catalog_hashes_and_parses_the_same_single_byte_read(self):
        packaged = (
            Path(__file__).resolve().parents[1]
            / "resources"
            / "story_templates_v1.json"
        )
        original = packaged.read_bytes()
        with patch.object(
            Path,
            "read_bytes",
            autospec=True,
            return_value=original,
        ) as read_bytes, patch.object(
            Path,
            "read_text",
            autospec=True,
            side_effect=AssertionError("a second path read is a TOCTOU gap"),
        ) as read_text:
            catalog = StoryTemplateCatalog.packaged_v1(resource_path=packaged)

        self.assertEqual(len(catalog.templates), 18)
        self.assertEqual(read_bytes.call_count, 1)
        self.assertEqual(read_text.call_count, 0)

    def test_direct_catalog_construction_revalidates_every_invariant(self):
        catalog = StoryTemplateCatalog.packaged_v1()
        bad_template = replace(
            catalog.templates[0],
            display_text="Subtract {A} and {B} {UNIT} at the marker.",
        )
        bad_tone = replace(catalog.feedback_tones[0], display_text="Go die.")
        invalid = (
            (
                catalog.template_set_id,
                (bad_template, *catalog.templates[1:]),
                catalog.feedback_tones,
            ),
            (
                catalog.template_set_id,
                catalog.templates[:-1],
                catalog.feedback_tones,
            ),
            (
                catalog.template_set_id,
                catalog.templates,
                (bad_tone, *catalog.feedback_tones[1:]),
            ),
            (
                "unexpected-catalog",
                catalog.templates,
                catalog.feedback_tones,
            ),
            (
                catalog.template_set_id,
                list(catalog.templates),
                catalog.feedback_tones,
            ),
        )
        for template_set_id, templates, tones in invalid:
            with self.subTest(
                template_set_id=template_set_id,
                template_count=len(templates),
            ), self.assertRaises(StoryTemplateError):
                StoryTemplateCatalog(template_set_id, templates, tones)  # type: ignore[arg-type]

    def test_feedback_tone_is_relinted_at_the_point_of_use(self):
        catalog = StoryTemplateCatalog.packaged_v1()
        bad_tone = FeedbackTone(FeedbackToneId.CALM_REVIEW, "Go die.")
        object.__setattr__(
            catalog,
            "feedback_tones",
            (bad_tone, *catalog.feedback_tones[1:]),
        )
        provider = TemplateNarrativeProvider(catalog)

        with self.assertRaises(StoryTemplateError):
            provider.feedback_tone(FeedbackToneId.CALM_REVIEW)


if __name__ == "__main__":
    unittest.main()
