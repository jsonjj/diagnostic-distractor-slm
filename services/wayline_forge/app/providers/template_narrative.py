"""Deterministic authored narrative provider used by every Wayline build."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from .narrative import (
    FeedbackToneId,
    PlaceholderName,
    REQUIRED_PLACEHOLDER_NAMES,
    ReadingLevelId,
    StoryFrameId,
    StorySettingId,
    StorySkin,
    StorySkinRequest,
    StoryStyleId,
)
from ..story_linter import StoryLintError, lint_story_skin, lint_story_text


class StoryTemplateError(ValueError):
    """Raised when authored narrative data is incomplete or untrusted."""


STORY_TEMPLATES_V1_SHA256 = (
    "466333dd1fc78bab34a8a614deba7312997f23f2355615f68ac8139831b652d9"
)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StoryTemplateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_string(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise StoryTemplateError(f"{field_name} must be text")
    return value


@dataclass(frozen=True, slots=True)
class StoryTemplate:
    template_id: str
    style_id: StoryStyleId
    setting_id: StorySettingId
    reading_level_id: ReadingLevelId
    story_frame_id: StoryFrameId
    placeholder_names: tuple[PlaceholderName, ...]
    display_text: str


@dataclass(frozen=True, slots=True)
class FeedbackTone:
    feedback_tone_id: FeedbackToneId
    display_text: str


_STYLE_FOR_SETTING: Mapping[StorySettingId, StoryStyleId] = MappingProxyType(
    {
        StorySettingId.VALUEHOLD_REACH: StoryStyleId.MEASURED_SURVEYOR,
        StorySettingId.DECIMARA_BASIN: StoryStyleId.FLOWING_TIDEKEEPER,
        StorySettingId.FRACTURE_ISLES: StoryStyleId.STEADFAST_WARDEN,
    }
)


@dataclass(frozen=True, slots=True)
class StoryTemplateCatalog:
    template_set_id: str
    templates: tuple[StoryTemplate, ...]
    feedback_tones: tuple[FeedbackTone, ...]

    def __post_init__(self) -> None:
        if (
            type(self.template_set_id) is not str
            or self.template_set_id != "wayline-demo-story-templates-v1"
        ):
            raise StoryTemplateError("unexpected story template set ID")
        if type(self.templates) is not tuple or type(self.feedback_tones) is not tuple:
            raise StoryTemplateError("catalog collections must be immutable tuples")

        expected_combinations = {
            (setting_id, frame_id)
            for setting_id in StorySettingId
            for frame_id in StoryFrameId
        }
        combinations: set[tuple[StorySettingId, StoryFrameId]] = set()
        template_ids: set[str] = set()
        for template in self.templates:
            if type(template) is not StoryTemplate:
                raise StoryTemplateError("catalog contains an invalid template")
            if (
                type(template.template_id) is not str
                or not re.fullmatch(r"[a-z]+(?:_[a-z]+)*", template.template_id)
                or template.template_id in template_ids
            ):
                raise StoryTemplateError("invalid or duplicate story template ID")
            if type(template.style_id) is not StoryStyleId:
                raise StoryTemplateError("invalid authored style ID")
            if type(template.setting_id) is not StorySettingId:
                raise StoryTemplateError("invalid authored setting ID")
            if type(template.reading_level_id) is not ReadingLevelId:
                raise StoryTemplateError("invalid authored reading-level ID")
            if type(template.story_frame_id) is not StoryFrameId:
                raise StoryTemplateError("invalid authored frame ID")
            if template.placeholder_names != REQUIRED_PLACEHOLDER_NAMES:
                raise StoryTemplateError("invalid authored placeholder shape")
            if _STYLE_FOR_SETTING[template.setting_id] is not template.style_id:
                raise StoryTemplateError("style does not belong to setting")
            combination = (template.setting_id, template.story_frame_id)
            if combination in combinations:
                raise StoryTemplateError("duplicate setting and frame")
            try:
                lint_story_skin(
                    {"displayText": template.display_text},
                    StorySkinRequest(
                        template.style_id,
                        template.setting_id,
                        template.reading_level_id,
                        template.story_frame_id,
                        template.placeholder_names,
                    ),
                )
            except (StoryLintError, TypeError, ValueError) as exc:
                raise StoryTemplateError("authored story template failed lint") from exc
            combinations.add(combination)
            template_ids.add(template.template_id)
        if combinations != expected_combinations or len(self.templates) != 18:
            raise StoryTemplateError("demo catalog must contain every authored frame")

        seen_tones: set[FeedbackToneId] = set()
        for tone in self.feedback_tones:
            if type(tone) is not FeedbackTone:
                raise StoryTemplateError("catalog contains an invalid feedback tone")
            if (
                type(tone.feedback_tone_id) is not FeedbackToneId
                or tone.feedback_tone_id in seen_tones
            ):
                raise StoryTemplateError("invalid or duplicate feedback tone")
            try:
                lint_story_text(tone.display_text, ())
            except (StoryLintError, TypeError, ValueError) as exc:
                raise StoryTemplateError("authored feedback tone failed lint") from exc
            seen_tones.add(tone.feedback_tone_id)
        if seen_tones != set(FeedbackToneId) or len(self.feedback_tones) != 4:
            raise StoryTemplateError("catalog must contain every feedback tone")

    @classmethod
    def load(cls, path: Path) -> "StoryTemplateCatalog":
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise StoryTemplateError("cannot load story template resource") from exc
        return cls._from_bytes(raw)

    @classmethod
    def _from_bytes(cls, raw: bytes) -> "StoryTemplateCatalog":
        if type(raw) is not bytes:
            raise TypeError("raw story template resource must be bytes")
        try:
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda unused: (_ for _ in ()).throw(
                    StoryTemplateError("non-standard JSON number")
                ),
            )
        except StoryTemplateError:
            raise
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise StoryTemplateError("cannot load story template resource") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "template_set_id",
            "templates",
            "feedback_tones",
        }:
            raise StoryTemplateError("story template root fields do not match v1")
        if value["schema_version"] != "wayline.story-templates.v1":
            raise StoryTemplateError("unsupported story template schema")

        raw_templates = value["templates"]
        if not isinstance(raw_templates, list):
            raise StoryTemplateError("templates must be a list")
        templates: list[StoryTemplate] = []
        template_ids: set[str] = set()
        combinations: set[tuple[StorySettingId, StoryFrameId]] = set()
        template_fields = {
            "template_id",
            "style_id",
            "setting_id",
            "reading_level_id",
            "story_frame_id",
            "placeholder_names",
            "display_text",
        }
        for raw in raw_templates:
            if not isinstance(raw, dict) or set(raw) != template_fields:
                raise StoryTemplateError("story template fields do not match v1")
            template_id = _strict_string(raw["template_id"], "template_id")
            if not re.fullmatch(r"[a-z]+(?:_[a-z]+)*", template_id):
                raise StoryTemplateError("invalid story template ID")
            if template_id in template_ids:
                raise StoryTemplateError("duplicate story template ID")
            try:
                style_id = StoryStyleId(_strict_string(raw["style_id"], "style_id"))
                setting_id = StorySettingId(
                    _strict_string(raw["setting_id"], "setting_id")
                )
                reading_level_id = ReadingLevelId(
                    _strict_string(raw["reading_level_id"], "reading_level_id")
                )
                story_frame_id = StoryFrameId(
                    _strict_string(raw["story_frame_id"], "story_frame_id")
                )
            except ValueError as exc:
                raise StoryTemplateError("unknown authored narrative ID") from exc
            if _STYLE_FOR_SETTING[setting_id] is not style_id:
                raise StoryTemplateError("style does not belong to setting")
            combination = (setting_id, story_frame_id)
            if combination in combinations:
                raise StoryTemplateError("duplicate setting and frame")

            raw_placeholders = raw["placeholder_names"]
            if not isinstance(raw_placeholders, list):
                raise StoryTemplateError("placeholder_names must be a list")
            try:
                placeholder_names = tuple(
                    PlaceholderName(_strict_string(item, "placeholder name"))
                    for item in raw_placeholders
                )
            except ValueError as exc:
                raise StoryTemplateError("unknown authored placeholder") from exc
            display_text = _strict_string(raw["display_text"], "display_text")
            request = StorySkinRequest(
                style_id=style_id,
                setting_id=setting_id,
                reading_level_id=reading_level_id,
                story_frame_id=story_frame_id,
                placeholder_names=placeholder_names,
            )
            try:
                lint_story_skin({"displayText": display_text}, request)
            except StoryLintError as exc:
                raise StoryTemplateError("authored story template failed lint") from exc
            templates.append(
                StoryTemplate(
                    template_id=template_id,
                    style_id=style_id,
                    setting_id=setting_id,
                    reading_level_id=reading_level_id,
                    story_frame_id=story_frame_id,
                    placeholder_names=placeholder_names,
                    display_text=display_text,
                )
            )
            template_ids.add(template_id)
            combinations.add(combination)

        expected_combinations = {
            (setting_id, frame_id)
            for setting_id in StorySettingId
            for frame_id in StoryFrameId
        }
        if combinations != expected_combinations or len(templates) != 18:
            raise StoryTemplateError("demo catalog must contain every authored frame")

        raw_tones = value["feedback_tones"]
        if not isinstance(raw_tones, list):
            raise StoryTemplateError("feedback_tones must be a list")
        tones: list[FeedbackTone] = []
        seen_tones: set[FeedbackToneId] = set()
        for raw in raw_tones:
            if not isinstance(raw, dict) or set(raw) != {
                "feedback_tone_id",
                "display_text",
            }:
                raise StoryTemplateError("feedback tone fields do not match v1")
            try:
                tone_id = FeedbackToneId(
                    _strict_string(raw["feedback_tone_id"], "feedback_tone_id")
                )
            except ValueError as exc:
                raise StoryTemplateError("unknown feedback tone") from exc
            if tone_id in seen_tones:
                raise StoryTemplateError("duplicate feedback tone")
            display_text = _strict_string(raw["display_text"], "display_text")
            try:
                lint_story_text(display_text, ())
            except StoryLintError as exc:
                raise StoryTemplateError("authored feedback tone failed lint") from exc
            tones.append(FeedbackTone(tone_id, display_text))
            seen_tones.add(tone_id)
        if seen_tones != set(FeedbackToneId) or len(tones) != 4:
            raise StoryTemplateError("catalog must contain every feedback tone")

        template_set_id = _strict_string(value["template_set_id"], "template_set_id")
        if template_set_id != "wayline-demo-story-templates-v1":
            raise StoryTemplateError("unexpected story template set ID")
        return cls(template_set_id, tuple(templates), tuple(tones))

    @classmethod
    def packaged_v1(
        cls,
        *,
        resource_path: Path | None = None,
    ) -> "StoryTemplateCatalog":
        path = resource_path or (
            Path(__file__).resolve().parents[2] / "resources/story_templates_v1.json"
        )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise StoryTemplateError("cannot read packaged story templates") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if digest != STORY_TEMPLATES_V1_SHA256:
            raise StoryTemplateError("packaged story template digest mismatch")
        return cls._from_bytes(raw)

    def template_for(self, request: StorySkinRequest) -> StoryTemplate:
        if type(request) is not StorySkinRequest:
            raise TypeError("request must be a StorySkinRequest")
        matching = tuple(
            template
            for template in self.templates
            if template.style_id is request.style_id
            and template.setting_id is request.setting_id
            and template.reading_level_id is request.reading_level_id
            and template.story_frame_id is request.story_frame_id
        )
        if len(matching) != 1:
            raise StoryTemplateError("no unique authored template for request")
        return matching[0]

    def feedback_for(self, tone_id: FeedbackToneId) -> FeedbackTone:
        if type(tone_id) is not FeedbackToneId:
            raise TypeError("tone_id must be an enumerated ID")
        matching = tuple(
            tone for tone in self.feedback_tones if tone.feedback_tone_id is tone_id
        )
        if len(matching) != 1:
            raise StoryTemplateError("no unique authored feedback tone")
        return matching[0]


class TemplateNarrativeProvider:
    """Offline, deterministic production default for optional narrative text."""

    def __init__(self, catalog: StoryTemplateCatalog | None = None) -> None:
        if catalog is not None and type(catalog) is not StoryTemplateCatalog:
            raise TypeError("catalog must be a StoryTemplateCatalog")
        self._catalog = catalog or StoryTemplateCatalog.packaged_v1()

    def skin(self, request: StorySkinRequest) -> StorySkin:
        template = self._catalog.template_for(request)
        try:
            return lint_story_skin({"displayText": template.display_text}, request)
        except StoryLintError as exc:
            raise StoryTemplateError("authored template failed at use time") from exc

    def feedback_tone(self, tone_id: FeedbackToneId) -> str:
        tone = self._catalog.feedback_for(tone_id)
        try:
            return lint_story_text(tone.display_text, ())
        except (StoryLintError, TypeError, ValueError) as exc:
            raise StoryTemplateError("authored feedback failed at use time") from exc


class SafeNarrativeProvider:
    """Mandatory validation and fallback boundary for any optional provider."""

    __slots__ = ("_primary", "_fallback")

    def __init__(
        self,
        primary: object | None = None,
        fallback: TemplateNarrativeProvider | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or TemplateNarrativeProvider()

    def skin(self, request: StorySkinRequest) -> StorySkin:
        if type(request) is not StorySkinRequest:
            raise TypeError("request must be a StorySkinRequest")
        if self._primary is None:
            return self._fallback.skin(request)
        try:
            raw_skin = self._primary.skin(request)  # type: ignore[attr-defined]
            return lint_story_skin(raw_skin, request)
        except Exception:
            return self._fallback.skin(request)
