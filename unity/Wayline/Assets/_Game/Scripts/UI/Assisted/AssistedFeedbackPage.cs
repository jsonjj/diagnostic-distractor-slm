using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;
using UnityEngine.UI;
using Wayline.Learning.Contracts;

namespace Wayline.UI.Assisted
{
    public sealed class AssistedFeedbackPage : MonoBehaviour
    {
        private readonly List<Section> _sections = new List<Section>();
        private AssistedItemResult _result;
        private AssistedScrollSurface _scroll;
        private Section _selectedSection;
        private Section _resultSection;
        private Section _trustedSection;
        private Section _errorSection;
        private Section _methodSection;
        private Section _stepsSection;
        private float _textScale = 1f;

        public Text ProgressText { get; private set; }

        public Text SelectedAnswerText => _selectedSection.Value;

        public Text ResultText => _resultSection.Value;

        public Text TrustedAnswerText => _trustedSection.Value;

        public Text PossibleErrorText => _errorSection.Value;

        public Text MethodText => _methodSection.Value;

        public Text StepsText => _stepsSection.Value;

        internal void Initialize()
        {
            var rect = (RectTransform)transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;

            ProgressText = AssistedUiFactory.Text(
                transform,
                "Supported method progress",
                "Supported method 1 of 2",
                28,
                AssistedPalette.Limestone,
                TextAnchor.MiddleRight,
                FontStyle.Bold);
            AssistedUiFactory.SetFixedRect(
                (RectTransform)ProgressText.transform,
                Vector2.one,
                new Vector2(1f, 0.5f),
                new Vector2(-390f, -60f),
                new Vector2(590f, 60f));

            _scroll = AssistedUiFactory.ScrollSurface(
                transform,
                "Supported feedback atlas",
                new Vector2(0f, 8f),
                new Vector2(1500f, 720f));
            _selectedSection = AddSection("YOUR ANSWER", 28, 64f);
            _resultSection = AddSection("RESULT", 32, 64f);
            _trustedSection = AddSection("TRUSTED ANSWER", 28, 64f);
            _errorSection = AddSection("POSSIBLE ERROR ROUTE", 28, 72f);
            _methodSection = AddSection("RELIABLE METHOD", 28, 72f);
            _stepsSection = AddSection("TRUSTED STEPS", 28, 90f);
        }

        internal void Bind(AssistedItemResult result, int itemIndex, int itemCount)
        {
            _result = result ?? throw new ArgumentNullException(nameof(result));
            ProgressText.text = $"Supported method {itemIndex + 1} of {itemCount}";
            SelectedAnswerText.text =
                $"{result.SelectedAnswer}  •  Confidence: {result.Confidence}";
            ResultText.text = result.IsCorrect
                ? "Result: Correct"
                : "Result: Incorrect";
            TrustedAnswerText.text = result.CorrectAnswer;
            _errorSection.Root.gameObject.SetActive(
                !string.IsNullOrWhiteSpace(result.PossibleError));
            PossibleErrorText.text = result.PossibleError ?? string.Empty;
            MethodText.text = result.ReliableMethod;
            StepsText.text = string.Join(
                "\n",
                result.TrustedSteps.Select((step, index) => $"{index + 1}. {step}"));
            ApplyTextScale(_textScale);
            _scroll.ScrollRect.verticalNormalizedPosition = 1f;
        }

        public void ApplyTextScale(float scale)
        {
            _textScale = Mathf.Clamp(scale, 1f, 1.5f);
            ProgressText.fontSize = Mathf.RoundToInt(28f * _textScale);
            var progressRect = (RectTransform)ProgressText.transform;
            progressRect.sizeDelta = new Vector2(590f, progressRect.sizeDelta.y);
            Canvas.ForceUpdateCanvases();
            progressRect.sizeDelta = new Vector2(
                590f,
                Mathf.Max(60f, ProgressText.preferredHeight));
            foreach (var section in _sections)
            {
                section.Heading.fontSize = Mathf.RoundToInt(24f * _textScale);
                section.Value.fontSize = Mathf.RoundToInt(section.BaseFontSize * _textScale);
            }
            Relayout();
        }

        internal string BuildSpeechText()
        {
            if (_result == null)
                return string.Empty;
            var speech = new StringBuilder();
            speech.Append("Your answer: ");
            speech.Append(_result.SelectedAnswer);
            speech.Append(". Confidence: ");
            speech.Append(_result.Confidence);
            speech.Append(". Result: ");
            speech.Append(_result.IsCorrect ? "Correct" : "Incorrect");
            speech.Append(". Trusted answer: ");
            speech.Append(_result.CorrectAnswer);
            if (!string.IsNullOrWhiteSpace(_result.PossibleError))
            {
                speech.Append(". ");
                speech.Append(_result.PossibleError);
            }
            speech.Append(". Reliable method: ");
            speech.Append(_result.ReliableMethod);
            for (var index = 0; index < _result.TrustedSteps.Count; index++)
            {
                speech.Append(". Step ");
                speech.Append(index + 1);
                speech.Append(": ");
                speech.Append(_result.TrustedSteps[index]);
            }
            return speech.ToString();
        }

        private Section AddSection(string headingText, int valueFont, float minimumValueHeight)
        {
            var root = new GameObject(
                headingText,
                typeof(RectTransform),
                typeof(AsymmetricAtlasGraphic));
            root.transform.SetParent(_scroll.Content, false);
            var graphic = root.GetComponent<AsymmetricAtlasGraphic>();
            graphic.color = new Color(
                AssistedPalette.StormTeal.r,
                AssistedPalette.StormTeal.g,
                AssistedPalette.StormTeal.b,
                0.32f);
            graphic.CutSize = 20f;
            graphic.raycastTarget = false;
            var heading = AssistedUiFactory.Text(
                root.transform,
                "Heading",
                headingText,
                24,
                AssistedPalette.MeridianGold,
                TextAnchor.UpperLeft,
                FontStyle.Bold);
            var value = AssistedUiFactory.Text(
                root.transform,
                "Value",
                string.Empty,
                valueFont,
                AssistedPalette.Limestone,
                TextAnchor.UpperLeft,
                valueFont >= 32 ? FontStyle.Bold : FontStyle.Normal);
            var section = new Section(
                (RectTransform)root.transform,
                heading,
                value,
                valueFont,
                minimumValueHeight);
            _sections.Add(section);
            return section;
        }

        private void Relayout()
        {
            Canvas.ForceUpdateCanvases();
            var cursor = 0f;
            var gap = 14f * _textScale;
            foreach (var section in _sections)
            {
                if (!section.Root.gameObject.activeSelf)
                    continue;
                var padding = 22f * _textScale;
                var headingHeight = Mathf.Max(
                    34f * _textScale,
                    section.Heading.preferredHeight);
                var valueHeight = Mathf.Max(
                    section.MinimumValueHeight * _textScale,
                    section.Value.preferredHeight);
                var height = padding + headingHeight + gap + valueHeight + padding;
                section.Root.anchorMin = new Vector2(0f, 1f);
                section.Root.anchorMax = Vector2.one;
                section.Root.pivot = new Vector2(0.5f, 1f);
                section.Root.anchoredPosition = new Vector2(0f, -cursor);
                section.Root.sizeDelta = new Vector2(0f, height);

                var headingRect = (RectTransform)section.Heading.transform;
                headingRect.anchorMin = new Vector2(0f, 1f);
                headingRect.anchorMax = Vector2.one;
                headingRect.pivot = new Vector2(0.5f, 1f);
                headingRect.anchoredPosition = new Vector2(0f, -padding);
                headingRect.sizeDelta = new Vector2(-2f * padding, headingHeight);

                var valueRect = (RectTransform)section.Value.transform;
                valueRect.anchorMin = new Vector2(0f, 1f);
                valueRect.anchorMax = Vector2.one;
                valueRect.pivot = new Vector2(0.5f, 1f);
                valueRect.anchoredPosition = new Vector2(
                    0f,
                    -(padding + headingHeight + gap));
                valueRect.sizeDelta = new Vector2(-2f * padding, valueHeight);
                cursor += height + gap;
            }
            _scroll.Content.sizeDelta = new Vector2(0f, Mathf.Max(
                cursor,
                _scroll.Viewport.rect.height));
            Canvas.ForceUpdateCanvases();
        }

        private sealed class Section
        {
            public Section(
                RectTransform root,
                Text heading,
                Text value,
                int baseFontSize,
                float minimumValueHeight)
            {
                Root = root;
                Heading = heading;
                Value = value;
                BaseFontSize = baseFontSize;
                MinimumValueHeight = minimumValueHeight;
            }

            public RectTransform Root { get; }

            public Text Heading { get; }

            public Text Value { get; }

            public int BaseFontSize { get; }

            public float MinimumValueHeight { get; }
        }
    }
}
