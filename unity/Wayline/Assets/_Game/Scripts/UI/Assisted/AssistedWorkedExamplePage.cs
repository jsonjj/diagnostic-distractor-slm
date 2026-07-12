using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;
using UnityEngine.UI;
using Wayline.Learning.Contracts;

namespace Wayline.UI.Assisted
{
    public sealed class AssistedWorkedExamplePage : MonoBehaviour
    {
        private readonly List<Section> _sections = new List<Section>();
        private AssistedWorkedExample _example;
        private AssistedScrollSurface _scroll;
        private float _textScale = 1f;

        public Text PromptText { get; private set; }

        public Text TrustedAnswerText { get; private set; }

        public Text StepsText { get; private set; }

        public Text MethodText { get; private set; }

        internal void Initialize()
        {
            var rect = (RectTransform)transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;

            _scroll = AssistedUiFactory.ScrollSurface(
                transform,
                "Worked example atlas",
                new Vector2(0f, 18f),
                new Vector2(1500f, 720f));
            PromptText = AddSection("WORKED EXAMPLE", 36, 108f).Value;
            TrustedAnswerText = AddSection("TRUSTED ANSWER", 32, 72f).Value;
            StepsText = AddSection("WORKED STEPS", 28, 90f).Value;
            MethodText = AddSection("RELIABLE METHOD", 28, 72f).Value;
        }

        internal void Bind(AssistedWorkedExample example)
        {
            _example = example ?? throw new ArgumentNullException(nameof(example));
            PromptText.text = example.Prompt;
            TrustedAnswerText.text = example.CorrectAnswer;
            StepsText.text = string.Join(
                "\n",
                example.TrustedSteps.Select((step, index) => $"{index + 1}. {step}"));
            MethodText.text = example.ReliableMethod;
            ApplyTextScale(_textScale);
            _scroll.ScrollRect.verticalNormalizedPosition = 1f;
        }

        public void ApplyTextScale(float scale)
        {
            _textScale = Mathf.Clamp(scale, 1f, 1.5f);
            foreach (var section in _sections)
            {
                section.Heading.fontSize = Mathf.RoundToInt(24f * _textScale);
                section.Value.fontSize = Mathf.RoundToInt(section.BaseFontSize * _textScale);
            }
            Relayout();
        }

        internal string BuildSpeechText()
        {
            if (_example == null)
                return string.Empty;
            var speech = new StringBuilder();
            speech.Append("Worked example. ");
            speech.Append(_example.Prompt);
            speech.Append(". Trusted answer: ");
            speech.Append(_example.CorrectAnswer);
            for (var index = 0; index < _example.TrustedSteps.Count; index++)
            {
                speech.Append(". Step ");
                speech.Append(index + 1);
                speech.Append(": ");
                speech.Append(_example.TrustedSteps[index]);
            }
            speech.Append(". Reliable method: ");
            speech.Append(_example.ReliableMethod);
            return speech.ToString();
        }

        private Section AddSection(string headingText, int valueFont, float minimumValueHeight)
        {
            var root = new GameObject(
                headingText,
                typeof(RectTransform),
                typeof(AsymmetricAtlasGraphic));
            root.transform.SetParent(_scroll.Content, false);
            var rootRect = (RectTransform)root.transform;
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
                rootRect,
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
            var gap = 16f * _textScale;
            foreach (var section in _sections)
            {
                var padding = 24f * _textScale;
                var headingHeight = Mathf.Max(
                    34f * _textScale,
                    section.Heading.preferredHeight);
                var valueHeight = Mathf.Max(
                    section.MinimumValueHeight * _textScale,
                    section.Value.preferredHeight);
                var sectionHeight = padding + headingHeight + gap + valueHeight + padding;

                section.Root.anchorMin = new Vector2(0f, 1f);
                section.Root.anchorMax = Vector2.one;
                section.Root.pivot = new Vector2(0.5f, 1f);
                section.Root.anchoredPosition = new Vector2(0f, -cursor);
                section.Root.sizeDelta = new Vector2(0f, sectionHeight);

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

                cursor += sectionHeight + gap;
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
