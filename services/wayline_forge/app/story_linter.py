"""Fail-closed validation for symbolic, child-safe Wayline story sentences."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import re

from .providers.narrative import (
    DemoUnit,
    MAX_STORY_DISPLAY_CHARS,
    REQUIRED_PLACEHOLDER_NAMES,
    PlaceholderName,
    RenderedStory,
    StoryRenderValues,
    StorySkin,
    StorySkinRequest,
    StoryStructureError,
    StorySettingId,
    validate_rendered_story_structure,
    validate_symbolic_story_structure,
)


_PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}", re.ASCII)
_SYMBOLIC_CLAUSE = "{A} and {B} {UNIT}"
_SINGLE_SENTENCE = re.compile(r"[A-Z][A-Za-z {},]*\.", re.ASCII)
_ANSWER_LEAKAGE = (
    re.compile(r"\b(?:correct|incorrect|answer|solution|equals?|option|choice)\b"),
    re.compile(r"\bright\s+(?:answer|option|choice)\b"),
    re.compile(r"\bresult\s+(?:is|was|will\s+be)\b"),
)
_BLAME_OR_DEFICIT = (
    re.compile(
        r"\b(?:careless|lazy|stupid|dumb|weak|deficit|failure|failed|"
        r"wrong|mistake|error|confused|confusion|behind)\b"
    ),
    re.compile(r"\bbad\s+at\b"),
    re.compile(r"\b(?:can(?:no|')t|cannot|do(?:es)?n't)\s+understand\b"),
    re.compile(r"\bnot\s+smart\b"),
)
_SECOND_PERSON_DIAGNOSIS = (
    re.compile(r"\b(?:this|that)\s+(?:shows|means|proves)\s+(?:that\s+)?you\b"),
    re.compile(
        r"\byou(?:'re|\s+are|\s+seem|\s+struggle|\s+misunderstood|"
        r"\s+forgot|\s+failed|\s+guessed|\s+need\s+to)\b"
    ),
    re.compile(r"\byour\s+(?:mistake|error|weakness|problem|failure|confusion)\b"),
)
_UNSAFE_LANGUAGE = re.compile(
    r"\b(?:kill|killed|killing|blood|bloody|gore|dead|death|die|dying|"
    r"murder|suicide|self\s+harm|hurt|idiot|hate)\b"
)
_OPERATION_OR_QUANTITY_LANGUAGE = re.compile(
    r"\b(?:add|added|adds|adding|subtract|subtracted|subtracts|multiply|"
    r"multiplied|multiplies|divide|divided|divides|sum|difference|product|"
    r"quotient|total|increase|decrease|greater|fewer|less|more|double|twice|"
    r"triple|half|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"all|none|both|several|many)\b"
)
_ALLOWED_VOCABULARY = frozenset(
    """
    a above across again and arch arches arena at awaits basin beacon beneath
    beside between beyond brass bridge bronze bound calibrated calm carries
    carry careful causeway channels cloud coordinate crossing dry floating for
    fresh gathers gate glass gold guide guides holds in inlay island is isles
    joins lapis light limestone lines look marker masonry method misty monument
    near notice pale pass pause platform quiet ready reflective relay reliable
    restored review route sea seams seal setting signal sluices spans stable stay
    steady steps stone storm sunlit survey take teal terrace test the then through
    tide toward trace turquoise which
    """.split()
)


class StoryLintError(ValueError):
    """A stable rejection that never repeats untrusted provider text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StoryRenderError(ValueError):
    """A stable final-render rejection with no unsafe value in its message."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def lint_story_text(
    text: str,
    expected_placeholders: tuple[PlaceholderName, ...],
) -> str:
    """Validate one display string without filling or rewriting any slot."""

    try:
        validate_symbolic_story_structure(text)
    except StoryStructureError as exc:
        raise StoryLintError(exc.code) from exc
    if not _SINGLE_SENTENCE.fullmatch(text):
        raise StoryLintError("unsupported_story_grammar")

    normalized = text.casefold()
    if any(pattern.search(normalized) for pattern in _ANSWER_LEAKAGE):
        raise StoryLintError("answer_leakage")
    if any(pattern.search(normalized) for pattern in _BLAME_OR_DEFICIT):
        raise StoryLintError("blame_or_deficit_language")
    if any(pattern.search(normalized) for pattern in _SECOND_PERSON_DIAGNOSIS):
        raise StoryLintError("second_person_diagnosis")
    if _UNSAFE_LANGUAGE.search(normalized):
        raise StoryLintError("unsafe_language")
    if _OPERATION_OR_QUANTITY_LANGUAGE.search(normalized):
        raise StoryLintError("operation_or_quantity_language")

    matches = list(_PLACEHOLDER.finditer(text))
    without_recognized = _PLACEHOLDER.sub("", text)
    if "{" in without_recognized or "}" in without_recognized:
        raise StoryLintError("malformed_placeholder")

    found = tuple(match.group(1) for match in matches)
    allowed_names = {item.value for item in PlaceholderName}
    expected_names = tuple(item.value for item in expected_placeholders)
    if any(name not in allowed_names or name not in expected_names for name in found):
        raise StoryLintError("unknown_placeholder")

    counts = Counter(found)
    if any(count > 1 for count in counts.values()):
        raise StoryLintError("duplicate_placeholder")
    if any(counts[name] == 0 for name in expected_names):
        raise StoryLintError("missing_placeholder")
    if len(found) != len(expected_names):
        raise StoryLintError("placeholder_mismatch")

    if expected_placeholders:
        if expected_placeholders != REQUIRED_PLACEHOLDER_NAMES:
            raise StoryLintError("unsupported_placeholder_shape")
        if text.count(_SYMBOLIC_CLAUSE) != 1:
            raise StoryLintError("placeholder_clause_mismatch")
        clause_start = text.index(_SYMBOLIC_CLAUSE)
        clause_end = clause_start + len(_SYMBOLIC_CLAUSE)
        if clause_start == 0 or text[clause_start - 1] != " ":
            raise StoryLintError("placeholder_clause_not_standalone")
        if clause_end < len(text) and text[clause_end] not in {" ", ",", "."}:
            raise StoryLintError("placeholder_clause_not_standalone")
    elif found:
        raise StoryLintError("unexpected_placeholder")

    prose = _PLACEHOLDER.sub("", text)
    words = {word.casefold() for word in re.findall(r"[A-Za-z]+", prose, re.ASCII)}
    if not words.issubset(_ALLOWED_VOCABULARY):
        raise StoryLintError("unknown_story_vocabulary")
    return text


def lint_story_skin(
    payload: Mapping[str, object] | StorySkin,
    request: StorySkinRequest,
) -> StorySkin:
    """Accept only ``displayText`` and preserve all requested symbols exactly."""

    if type(request) is not StorySkinRequest:
        raise TypeError("request must be a StorySkinRequest")
    if isinstance(payload, StorySkin):
        fields: Mapping[str, object] = {"displayText": payload.display_text}
    elif isinstance(payload, Mapping):
        fields = payload
    else:
        raise StoryLintError("response_must_be_an_object")
    if set(fields) != {"displayText"}:
        raise StoryLintError("unsupported_response_fields")
    display_text = fields["displayText"]
    if type(display_text) is not str:
        raise StoryLintError("invalid_display_text")
    return StorySkin(
        display_text=lint_story_text(display_text, request.placeholder_names)
    )


_UNIT_FOR_SETTING = {
    StorySettingId.VALUEHOLD_REACH: DemoUnit.SURVEY_MARKS,
    StorySettingId.DECIMARA_BASIN: DemoUnit.TIDE_MEASURES,
    StorySettingId.FRACTURE_ISLES: DemoUnit.BRIDGE_SPANS,
}


def render_story_skin(
    skin: StorySkin,
    request: StorySkinRequest,
    values: StoryRenderValues,
) -> RenderedStory:
    """Re-lint, fill the frozen clause once, then validate final display text."""

    if type(skin) is not StorySkin:
        raise TypeError("skin must be a StorySkin")
    if type(request) is not StorySkinRequest:
        raise TypeError("request must be a StorySkinRequest")
    if type(values) is not StoryRenderValues:
        raise TypeError("values must be StoryRenderValues")
    try:
        linted = lint_story_skin(skin, request)
    except (StoryLintError, ValueError, TypeError) as exc:
        raise StoryRenderError("unsafe_symbolic_skin") from exc
    if _UNIT_FOR_SETTING[request.setting_id] is not values.unit:
        raise StoryRenderError("unit_does_not_match_setting")
    replacement = f"{values.a.value} and {values.b.value} {values.unit.value}"
    rendered = linted.display_text.replace(_SYMBOLIC_CLAUSE, replacement, 1)
    if "{" in rendered or "}" in rendered:
        raise StoryRenderError("unfilled_placeholder")
    try:
        validate_rendered_story_structure(rendered)
        return RenderedStory(rendered)
    except StoryStructureError as exc:
        raise StoryRenderError(exc.code) from exc
