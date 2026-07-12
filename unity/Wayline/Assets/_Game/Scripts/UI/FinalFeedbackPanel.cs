using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;
using UnityEngine.UI;
using Wayline.Learning.Contracts;

namespace Wayline.UI
{
    public sealed class FinalFeedbackPanel : MonoBehaviour
    {
        private readonly List<string> _visibleSectionLabels = new List<string>();
        private readonly List<RectTransform> _sectionSurfaces = new List<RectTransform>();
        private readonly List<Text> _sectionHeadings = new List<Text>();
        private readonly List<Text> _sectionValues = new List<Text>();
        private readonly List<Button> _scrollButtons = new List<Button>();
        private readonly List<Text> _scrollLabels = new List<Text>();
        private Text _title;
        private ScrollRect _scrollRect;
        private RectTransform _content;
        private RectTransform _resultPattern;

        public IReadOnlyList<string> VisibleSectionLabels => _visibleSectionLabels;

        public IReadOnlyList<Button> ScrollButtons => _scrollButtons;

        internal void Initialize()
        {
            var rect = (RectTransform)transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;

            _title = AtlasUiFactory.Text(
                transform,
                "Feedback title",
                "TRUSTED ROUTE METHOD",
                34,
                AtlasPalette.MeridianGold,
                TextAnchor.MiddleCenter,
                FontStyle.Bold);
            SetRect(_title, new Vector2(0f, 432f), new Vector2(1400f, 64f));

            var viewport = AtlasUiFactory.Rect(
                transform,
                "Feedback viewport",
                new Vector2(0.08f, 0.16f),
                new Vector2(0.92f, 0.83f),
                Vector2.zero,
                Vector2.zero);
            viewport.gameObject.AddComponent<RectMask2D>();
            _scrollRect = viewport.gameObject.AddComponent<ScrollRect>();
            _scrollRect.horizontal = false;
            _scrollRect.vertical = true;
            _scrollRect.inertia = false;
            _scrollRect.movementType = ScrollRect.MovementType.Clamped;
            _scrollRect.scrollSensitivity = 58f;
            _scrollRect.viewport = viewport;

            _content = AtlasUiFactory.Rect(
                viewport,
                "Feedback content",
                new Vector2(0f, 1f),
                Vector2.one,
                Vector2.zero,
                Vector2.zero);
            _content.pivot = new Vector2(0.5f, 1f);
            _content.anchoredPosition = Vector2.zero;
            _content.sizeDelta = new Vector2(0f, 700f);
            _scrollRect.content = _content;

            for (var index = 0; index < 5; index++)
                BuildSection(index);

            BuildScrollButton("Scroll methods up", "▲  Scroll", -96f, -0.22f);
            BuildScrollButton("Scroll methods down", "▼  Scroll", 96f, 0.22f);
        }

        internal void Bind(
            FinalQuizItemResult result,
            PublicQuizItem item,
            int resultIndex,
            int resultCount,
            float textScale)
        {
            if (result == null)
                throw new ArgumentNullException(nameof(result));
            if (item == null)
                throw new ArgumentNullException(nameof(item));

            _title.text = $"TRUSTED ROUTE METHOD  •  {resultIndex + 1} OF {resultCount}";
            var unchanged =
                result.FirstSelection.OptionId == result.FinalSelection.OptionId &&
                result.FirstSelection.Confidence == result.FinalSelection.Confidence;
            var sections = new List<Section>
            {
                new Section(
                    "First choice",
                    DescribeSelection(item, result.FirstSelection)),
                unchanged
                    ? new Section("Not changed", DescribeSelection(item, result.FinalSelection))
                    : new Section("Review choice", DescribeSelection(item, result.FinalSelection)),
                new Section(
                    result.FinalSelection.IsCorrect
                        ? "Result: Correct"
                        : "Result: Incorrect",
                    result.FinalSelection.IsCorrect
                        ? $"◆  Trusted answer: {result.CorrectAnswer}"
                        : $"◇  Trusted answer: {result.CorrectAnswer}")
            };
            if (!string.IsNullOrWhiteSpace(result.PossibleError))
            {
                sections.Add(new Section(
                    "This answer can come from...",
                    result.PossibleError));
            }
            sections.Add(new Section("Reliable method", BuildReliableMethod(result)));

            _visibleSectionLabels.Clear();
            _visibleSectionLabels.AddRange(sections.Select(section => section.Label));
            for (var index = 0; index < _sectionHeadings.Count; index++)
            {
                var visible = index < sections.Count;
                _sectionSurfaces[index].gameObject.SetActive(visible);
                if (!visible)
                    continue;
                _sectionHeadings[index].text = sections[index].Label;
                _sectionValues[index].text = sections[index].Value;
                _sectionHeadings[index].fontSize = Mathf.RoundToInt(28f * textScale);
                _sectionValues[index].fontSize = Mathf.RoundToInt(28f * textScale);
            }
            _title.fontSize = Mathf.RoundToInt(34f * textScale);
            ApplyResultPattern(result.FinalSelection.IsCorrect);
            LayoutSections(textScale);
        }

        internal string BuildSpeechText()
        {
            var speech = new StringBuilder();
            for (var index = 0; index < _sectionHeadings.Count; index++)
            {
                if (!_sectionSurfaces[index].gameObject.activeSelf)
                    continue;
                if (speech.Length > 0)
                    speech.Append(". ");
                speech.Append(_sectionHeadings[index].text);
                speech.Append(": ");
                speech.Append(_sectionValues[index].text);
            }
            return speech.ToString();
        }

        internal void ShowComplete(float textScale)
        {
            _title.text = "ROUTE TRIAL COMPLETE";
            _title.fontSize = Mathf.RoundToInt(42f * textScale);
            _visibleSectionLabels.Clear();
            for (var index = 0; index < _sectionSurfaces.Count; index++)
                _sectionSurfaces[index].gameObject.SetActive(false);
            for (var index = 0; index < _scrollButtons.Count; index++)
                _scrollButtons[index].gameObject.SetActive(false);
        }

        private void BuildSection(int index)
        {
            var surface = new GameObject(
                $"Feedback section {index + 1}",
                typeof(RectTransform),
                typeof(CanvasRenderer),
                typeof(AsymmetricAtlasGraphic));
            surface.transform.SetParent(_content, false);
            var surfaceRect = (RectTransform)surface.transform;
            var graphic = surface.GetComponent<AsymmetricAtlasGraphic>();
            graphic.color = index == 2
                ? new Color(AtlasPalette.StormTeal.r, AtlasPalette.StormTeal.g,
                    AtlasPalette.StormTeal.b, 0.58f)
                : new Color(AtlasPalette.Lapis.r, AtlasPalette.Lapis.g,
                    AtlasPalette.Lapis.b, 0.84f);
            graphic.raycastTarget = false;

            var heading = AtlasUiFactory.Text(
                surface.transform,
                "Heading",
                string.Empty,
                28,
                AtlasPalette.MeridianGold,
                TextAnchor.UpperLeft,
                FontStyle.Bold);
            heading.verticalOverflow = VerticalWrapMode.Overflow;
            var value = AtlasUiFactory.Text(
                surface.transform,
                "Value",
                string.Empty,
                28,
                AtlasPalette.Limestone,
                TextAnchor.UpperLeft);
            value.verticalOverflow = VerticalWrapMode.Overflow;

            if (index == 2)
            {
                _resultPattern = AtlasUiFactory.Rect(
                    surface.transform,
                    "Non-color result border pattern",
                    new Vector2(0f, 0f),
                    new Vector2(0f, 1f),
                    new Vector2(7f, 10f),
                    new Vector2(19f, -10f));
                for (var tick = 0; tick < 7; tick++)
                {
                    var marker = AtlasUiFactory.Image(
                        _resultPattern,
                        $"Pattern mark {tick + 1}",
                        AtlasPalette.MeridianGold);
                    var markerRect = (RectTransform)marker.transform;
                    markerRect.anchorMin = markerRect.anchorMax =
                        new Vector2(0.5f, tick / 6f);
                    markerRect.sizeDelta = new Vector2(8f, 14f);
                }
            }

            _sectionSurfaces.Add(surfaceRect);
            _sectionHeadings.Add(heading);
            _sectionValues.Add(value);
        }

        private void BuildScrollButton(
            string name,
            string labelValue,
            float x,
            float normalizedDelta)
        {
            var button = AtlasUiFactory.AtlasButton(
                transform,
                name,
                labelValue,
                out var label,
                out _);
            var rect = (RectTransform)button.transform;
            rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = new Vector2(x, 72f);
            rect.sizeDelta = new Vector2(174f, 58f);
            label.alignment = TextAnchor.MiddleCenter;
            label.fontSize = 28;
            var labelRect = (RectTransform)label.transform;
            labelRect.offsetMin = new Vector2(12f, 6f);
            labelRect.offsetMax = new Vector2(-12f, -6f);
            button.onClick.AddListener(() => ScrollBy(normalizedDelta));
            _scrollButtons.Add(button);
            _scrollLabels.Add(label);
        }

        private void LayoutSections(float textScale)
        {
            Canvas.ForceUpdateCanvases();
            var viewport = _scrollRect.viewport.rect;
            var width = Mathf.Max(1060f, viewport.width - 44f);
            var top = 10f;
            for (var index = 0; index < _sectionSurfaces.Count; index++)
            {
                if (!_sectionSurfaces[index].gameObject.activeSelf)
                    continue;
                var headingHeight = Mathf.Max(38f * textScale,
                    Measure(_sectionHeadings[index], width - 72f));
                var valueHeight = Mathf.Max(42f * textScale,
                    Measure(_sectionValues[index], width - 72f));
                var height = headingHeight + valueHeight + 44f;
                var surface = _sectionSurfaces[index];
                surface.anchorMin = surface.anchorMax = new Vector2(0.5f, 1f);
                surface.pivot = new Vector2(0.5f, 1f);
                surface.sizeDelta = new Vector2(width, height);
                surface.anchoredPosition = new Vector2(0f, -top);

                var headingRect = (RectTransform)_sectionHeadings[index].transform;
                headingRect.anchorMin = headingRect.anchorMax = new Vector2(0.5f, 1f);
                headingRect.pivot = new Vector2(0.5f, 1f);
                headingRect.sizeDelta = new Vector2(width - 68f, headingHeight);
                headingRect.anchoredPosition = new Vector2(0f, -16f);

                var valueRect = (RectTransform)_sectionValues[index].transform;
                valueRect.anchorMin = valueRect.anchorMax = new Vector2(0.5f, 1f);
                valueRect.pivot = new Vector2(0.5f, 1f);
                valueRect.sizeDelta = new Vector2(width - 68f, valueHeight);
                valueRect.anchoredPosition = new Vector2(
                    0f,
                    -headingHeight - 24f);
                top += height + 18f;
            }
            _content.sizeDelta = new Vector2(0f, Mathf.Max(top, viewport.height));
            _content.anchoredPosition = Vector2.zero;
            Canvas.ForceUpdateCanvases();
            var needsScroll = _content.rect.height > viewport.height + 1f;
            for (var index = 0; index < _scrollButtons.Count; index++)
            {
                _scrollButtons[index].gameObject.SetActive(needsScroll);
                _scrollLabels[index].fontSize = Mathf.RoundToInt(28f * textScale);
            }
        }

        private static float Measure(Text text, float width)
        {
            var rect = (RectTransform)text.transform;
            rect.sizeDelta = new Vector2(width, 40f);
            Canvas.ForceUpdateCanvases();
            return text.preferredHeight;
        }

        private void ApplyResultPattern(bool correct)
        {
            var resultSurface = _sectionSurfaces[2].GetComponent<AsymmetricAtlasGraphic>();
            resultSurface.color = correct
                ? new Color(AtlasPalette.StormTeal.r, AtlasPalette.StormTeal.g,
                    AtlasPalette.StormTeal.b, 0.66f)
                : new Color(AtlasPalette.Lapis.r, AtlasPalette.Lapis.g,
                    AtlasPalette.Lapis.b, 0.92f);
            for (var index = 0; index < _resultPattern.childCount; index++)
            {
                var marker = _resultPattern.GetChild(index).gameObject;
                marker.SetActive(correct || index % 2 == 0);
            }
        }

        private void ScrollBy(float delta)
        {
            _scrollRect.verticalNormalizedPosition = Mathf.Clamp01(
                _scrollRect.verticalNormalizedPosition - delta);
        }

        private static string DescribeSelection(
            PublicQuizItem item,
            RevealedSelection selection)
        {
            for (var index = 0; index < item.Options.Count; index++)
            {
                if (item.Options[index].OptionId == selection.OptionId)
                {
                    return $"{(char)('A' + index)} — {item.Options[index].DisplayText}  " +
                           $"({selection.Confidence})";
                }
            }
            return $"Recorded answer ({selection.Confidence})";
        }

        private static string BuildReliableMethod(FinalQuizItemResult result)
        {
            if (result.TrustedSteps == null || result.TrustedSteps.Count == 0)
                return result.ReliableMethod;
            return string.Join("  ", result.TrustedSteps) + "  " + result.ReliableMethod;
        }

        private static void SetRect(Text text, Vector2 position, Vector2 size)
        {
            var rect = (RectTransform)text.transform;
            rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
        }

        private readonly struct Section
        {
            public Section(string label, string value)
            {
                Label = label;
                Value = value;
            }

            public string Label { get; }

            public string Value { get; }
        }
    }
}
