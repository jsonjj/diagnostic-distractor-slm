from dataclasses import FrozenInstanceError, fields
import json
import unittest

from services.wayline_forge.app.providers.narrative import (
    NarrativeProvider,
    PlaceholderName,
    ReadingLevelId,
    StoryFrameId,
    StorySettingId,
    StorySkin,
    StorySkinRequest,
    StoryStyleId,
)
from services.wayline_forge.app.providers.template_narrative import (
    SafeNarrativeProvider,
    TemplateNarrativeProvider,
)


def request() -> StorySkinRequest:
    return StorySkinRequest(
        style_id=StoryStyleId.FLOWING_TIDEKEEPER,
        setting_id=StorySettingId.DECIMARA_BASIN,
        reading_level_id=ReadingLevelId.MIDDLE_GRADE,
        story_frame_id=StoryFrameId.SEAL_APPROACH,
        placeholder_names=(
            PlaceholderName.A,
            PlaceholderName.B,
            PlaceholderName.UNIT,
        ),
    )


class NarrativePrivacyTests(unittest.TestCase):
    def test_request_contract_has_only_the_five_nonpersonal_symbolic_fields(self):
        self.assertEqual(
            {field.name for field in fields(StorySkinRequest)},
            {
                "style_id",
                "setting_id",
                "reading_level_id",
                "story_frame_id",
                "placeholder_names",
            },
        )
        with self.assertRaises(TypeError):
            StorySkinRequest(
                style_id=StoryStyleId.FLOWING_TIDEKEEPER,
                setting_id=StorySettingId.DECIMARA_BASIN,
                reading_level_id=ReadingLevelId.MIDDLE_GRADE,
                story_frame_id=StoryFrameId.SEAL_APPROACH,
                placeholder_names=(PlaceholderName.A,),
                profile_id="profile-private",  # type: ignore[call-arg]
            )

    def test_request_is_frozen_slotted_and_strictly_enum_backed(self):
        value = request()
        with self.assertRaises(FrozenInstanceError):
            value.setting_id = StorySettingId.FRACTURE_ISLES  # type: ignore[misc]
        with self.assertRaises((AttributeError, TypeError)):
            value.learner_name = "Private"  # type: ignore[attr-defined]
        with self.assertRaises(TypeError):
            StorySkinRequest(
                style_id="flowing_tidekeeper",  # type: ignore[arg-type]
                setting_id=StorySettingId.DECIMARA_BASIN,
                reading_level_id=ReadingLevelId.MIDDLE_GRADE,
                story_frame_id=StoryFrameId.SEAL_APPROACH,
                placeholder_names=(PlaceholderName.A,),
            )
        with self.assertRaises(ValueError):
            StorySkinRequest(
                style_id=StoryStyleId.FLOWING_TIDEKEEPER,
                setting_id=StorySettingId.DECIMARA_BASIN,
                reading_level_id=ReadingLevelId.MIDDLE_GRADE,
                story_frame_id=StoryFrameId.SEAL_APPROACH,
                placeholder_names=(PlaceholderName.A, PlaceholderName.A),
            )

    def test_request_requires_the_single_frozen_authored_placeholder_shape(self):
        invalid_shapes = (
            (PlaceholderName.A,),
            (PlaceholderName.A, PlaceholderName.B),
            (PlaceholderName.B, PlaceholderName.A, PlaceholderName.UNIT),
            (PlaceholderName.A, PlaceholderName.UNIT, PlaceholderName.B),
        )
        for placeholder_names in invalid_shapes:
            with self.subTest(placeholder_names=placeholder_names), self.assertRaises(
                ValueError
            ):
                StorySkinRequest(
                    style_id=StoryStyleId.FLOWING_TIDEKEEPER,
                    setting_id=StorySettingId.DECIMARA_BASIN,
                    reading_level_id=ReadingLevelId.MIDDLE_GRADE,
                    story_frame_id=StoryFrameId.SEAL_APPROACH,
                    placeholder_names=placeholder_names,
                )

    def test_raw_story_skin_enforces_structural_character_and_markup_safety(self):
        invalid = (
            "<b>The route is ready.</b>",
            "The route is\nready.",
            "The route is\u2028ready.",
            "The r\u043eute is ready.",
            "The route is {A} + {B}.",
        )
        for display_text in invalid:
            with self.subTest(display_text=display_text), self.assertRaises(ValueError):
                StorySkin(display_text)

    def test_canonical_outbound_payload_has_an_exact_auditable_allowlist(self):
        value = request()

        payload = value.canonical_outbound_payload()
        serialized = value.canonical_outbound_json()

        self.assertEqual(
            set(payload),
            {
                "schemaVersion",
                "styleId",
                "settingId",
                "readingLevelId",
                "storyFrameId",
                "placeholders",
            },
        )
        self.assertEqual(json.loads(serialized), payload)
        self.assertEqual(serialized, value.canonical_outbound_json())
        self.assertEqual(
            payload["placeholders"],
            ["A", "B", "UNIT"],
        )

        banned_keys = {
            "profileId",
            "sessionId",
            "learnerName",
            "choices",
            "confidence",
            "correctness",
            "evidenceState",
            "operands",
            "numbers",
            "canonicalAnswer",
            "procedureOutput",
            "apiSecret",
            "rawSlmResponse",
        }
        self.assertTrue(banned_keys.isdisjoint(payload))
        self.assertFalse(any(isinstance(item, (int, float)) for item in payload.values()))
        for forbidden_value in (
            "profile-private",
            "session-private",
            "Private",
            "TFY_API_KEY",
            "raw model response",
        ):
            self.assertNotIn(forbidden_value, serialized)

    def test_template_provider_satisfies_the_protocol_without_network_or_credentials(self):
        provider: NarrativeProvider = TemplateNarrativeProvider()

        result = provider.skin(request())

        self.assertTrue(result.display_text)
        self.assertNotIn("TFY_API_KEY", result.display_text)

    def test_safe_facade_relints_provider_output_and_silently_uses_authored_fallback(self):
        class UnsafeProvider:
            def skin(self, unused_request):
                return StorySkin(
                    "Subtract {A} and {B} {UNIT} at the marker."
                )

        expected = TemplateNarrativeProvider().skin(request())
        provider: NarrativeProvider = SafeNarrativeProvider(UnsafeProvider())

        self.assertEqual(provider.skin(request()), expected)

    def test_safe_facade_falls_back_on_provider_exception_or_wrong_response_shape(self):
        class RaisingProvider:
            def skin(self, unused_request):
                raise RuntimeError("untrusted provider detail")

        class ExtraFieldProvider:
            def skin(self, unused_request):
                return {
                    "displayText": "The survey bridge joins {A} and {B} {UNIT} at the marker.",
                    "answer": "private",
                }

        expected = TemplateNarrativeProvider().skin(request())
        for primary in (RaisingProvider(), ExtraFieldProvider()):
            with self.subTest(primary=type(primary).__name__):
                self.assertEqual(
                    SafeNarrativeProvider(primary).skin(request()),
                    expected,
                )

    def test_safe_facade_returns_a_valid_primary_skin_only_after_relinting(self):
        class ValidProvider:
            def skin(self, unused_request):
                return StorySkin(
                    "The survey bridge joins {A} and {B} {UNIT} at the marker."
                )

        provider = SafeNarrativeProvider(ValidProvider())

        self.assertEqual(
            provider.skin(request()).display_text,
            "The survey bridge joins {A} and {B} {UNIT} at the marker.",
        )

    def test_safe_facade_is_the_always_available_default_without_a_primary(self):
        self.assertEqual(
            SafeNarrativeProvider().skin(request()),
            TemplateNarrativeProvider().skin(request()),
        )


if __name__ == "__main__":
    unittest.main()
