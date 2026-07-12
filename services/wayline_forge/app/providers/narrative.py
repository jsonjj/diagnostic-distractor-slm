"""Immutable, nonpersonal contracts for optional Wayline story wording."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
import string
from typing import Protocol, runtime_checkable


class StoryStyleId(str, Enum):
    """Authored NPC voice cards; values carry no learner information."""

    MEASURED_SURVEYOR = "measured_surveyor"
    FLOWING_TIDEKEEPER = "flowing_tidekeeper"
    STEADFAST_WARDEN = "steadfast_warden"


class StorySettingId(str, Enum):
    """The three settings in the public Wayline demo."""

    VALUEHOLD_REACH = "valuehold_reach"
    DECIMARA_BASIN = "decimara_basin"
    FRACTURE_ISLES = "fracture_isles"


class ReadingLevelId(str, Enum):
    """A bounded instruction ID rather than free-form learner metadata."""

    MIDDLE_GRADE = "middle_grade"


class StoryFrameId(str, Enum):
    """Authored, nonmathematical places where a story sentence may appear."""

    ROUTE_ARRIVAL = "route_arrival"
    ARENA_BRIEF = "arena_brief"
    CROSSING_REPAIR = "crossing_repair"
    SUPPLY_RELAY = "supply_relay"
    SEAL_APPROACH = "seal_approach"
    WORLD_RESTORATION = "world_restoration"


class PlaceholderName(str, Enum):
    """Symbolic slots whose mathematical values are inserted after linting."""

    A = "A"
    B = "B"
    UNIT = "UNIT"


REQUIRED_PLACEHOLDER_NAMES = (
    PlaceholderName.A,
    PlaceholderName.B,
    PlaceholderName.UNIT,
)
MAX_STORY_DISPLAY_CHARS = 180
_SYMBOLIC_STORY_CHARACTERS = frozenset(string.ascii_letters + " ,.{}")
_RENDERED_STORY_CHARACTERS = frozenset(
    string.ascii_letters + string.digits + " ,.-/%"
)
_TRUSTED_NUMERIC_DISPLAY = re.compile(
    r"-?(?:(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,4})?%?|"
    r"(?:0|[1-9][0-9]{0,5})/[1-9][0-9]{0,5})",
    re.ASCII,
)


class StoryStructureError(ValueError):
    """A stable structural rejection that never repeats unsafe text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_symbolic_story_structure(text: str) -> str:
    """Enforce the structural minimum even before semantic linting."""

    if type(text) is not str or not text or text != text.strip():
        raise StoryStructureError("invalid_display_text")
    if len(text) > MAX_STORY_DISPLAY_CHARS:
        raise StoryStructureError("display_text_too_long")
    if any(char not in _SYMBOLIC_STORY_CHARACTERS for char in text):
        raise StoryStructureError("unsafe_story_character")
    return text


def validate_rendered_story_structure(text: str) -> str:
    """Validate the final ASCII display after trusted slot substitution."""

    if type(text) is not str or not text or text != text.strip():
        raise StoryStructureError("invalid_rendered_text")
    if len(text) > MAX_STORY_DISPLAY_CHARS:
        raise StoryStructureError("rendered_text_too_long")
    if any(char not in _RENDERED_STORY_CHARACTERS for char in text):
        raise StoryStructureError("unsafe_rendered_character")
    return text


class FeedbackToneId(str, Enum):
    """Authored child-safe feedback cards available without a model."""

    CALM_REVIEW = "calm_review"
    METHOD_FOCUS = "method_focus"
    TRANSFER_READY = "transfer_ready"
    FORWARD_MOTION = "forward_motion"


class DemoUnit(str, Enum):
    """The only unit phrases insertable by the three-world demo renderer."""

    SURVEY_MARKS = "survey marks"
    TIDE_MEASURES = "tide measures"
    BRIDGE_SPANS = "bridge spans"


@dataclass(frozen=True, slots=True)
class TrustedNumericDisplay:
    """A small canonical ASCII numeric value supplied by trusted math code."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or not _TRUSTED_NUMERIC_DISPLAY.fullmatch(
            self.value
        ):
            raise ValueError("invalid trusted numeric display")


@dataclass(frozen=True, slots=True)
class StoryRenderValues:
    """Typed values for the three frozen symbolic slots."""

    a: TrustedNumericDisplay
    b: TrustedNumericDisplay
    unit: DemoUnit

    def __post_init__(self) -> None:
        if type(self.a) is not TrustedNumericDisplay:
            raise TypeError("a must be a TrustedNumericDisplay")
        if type(self.b) is not TrustedNumericDisplay:
            raise TypeError("b must be a TrustedNumericDisplay")
        if type(self.unit) is not DemoUnit:
            raise TypeError("unit must be a DemoUnit")


@dataclass(frozen=True, slots=True)
class RenderedStory:
    """Final learner-facing story text with no symbolic placeholders."""

    display_text: str

    def __post_init__(self) -> None:
        validate_rendered_story_structure(self.display_text)


@dataclass(frozen=True, slots=True)
class StorySkinRequest:
    """The complete allowlisted payload at the optional narrative boundary.

    There is intentionally nowhere to put an identity, response, score,
    answer, operand, evidence record, model output, or credential.
    """

    style_id: StoryStyleId
    setting_id: StorySettingId
    reading_level_id: ReadingLevelId
    story_frame_id: StoryFrameId
    placeholder_names: tuple[PlaceholderName, ...]

    def __post_init__(self) -> None:
        enum_fields = (
            ("style_id", self.style_id, StoryStyleId),
            ("setting_id", self.setting_id, StorySettingId),
            ("reading_level_id", self.reading_level_id, ReadingLevelId),
            ("story_frame_id", self.story_frame_id, StoryFrameId),
        )
        for field_name, value, enum_type in enum_fields:
            if type(value) is not enum_type:
                raise TypeError(f"{field_name} must be an enumerated ID")
        if type(self.placeholder_names) is not tuple:
            raise TypeError("placeholder_names must be an immutable tuple")
        if any(type(item) is not PlaceholderName for item in self.placeholder_names):
            raise TypeError("placeholder_names must contain enumerated symbols")
        if self.placeholder_names != REQUIRED_PLACEHOLDER_NAMES:
            raise ValueError("placeholder_names must match the authored symbolic shape")

    def canonical_outbound_payload(self) -> dict[str, object]:
        """Return the sole payload shape allowed to leave the sidecar.

        Returning a new closed dictionary makes both keys and values easy to
        audit without serializing this dataclass or any surrounding state.
        """

        return {
            "schemaVersion": "wayline.story-skin-request.v1",
            "styleId": self.style_id.value,
            "settingId": self.setting_id.value,
            "readingLevelId": self.reading_level_id.value,
            "storyFrameId": self.story_frame_id.value,
            "placeholders": [item.value for item in self.placeholder_names],
        }

    def canonical_outbound_json(self) -> str:
        """Serialize exactly the audited outbound allowlist, deterministically."""

        return json.dumps(
            self.canonical_outbound_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class StorySkin:
    """A linted sentence whose symbolic slots have not been filled yet."""

    display_text: str

    def __post_init__(self) -> None:
        validate_symbolic_story_structure(self.display_text)


@runtime_checkable
class NarrativeProvider(Protocol):
    """Narrow seam shared by the authored default and optional providers."""

    def skin(self, request: StorySkinRequest) -> StorySkin: ...
