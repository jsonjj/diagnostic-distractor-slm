using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;
using UnityEngine.UI;
using Wayline.Learning.Assisted;
using Wayline.Learning.Contracts;

namespace Wayline.UI.Assisted
{
    public sealed class AssistedQuestionPage : MonoBehaviour
    {
        private readonly List<Button> _optionButtons = new List<Button>();
        private readonly List<Text> _optionLabels = new List<Text>();
        private readonly List<AssistedSelectableVisual> _optionVisuals =
            new List<AssistedSelectableVisual>();
        private readonly List<RectTransform> _optionRects = new List<RectTransform>();
        private Action<int> _optionSelected;
        private AssistedSupportedItem _item;
        private AssistedAnswerState _answer;
        private AssistedScrollSurface _scroll;
        private RectTransform _promptSurface;
        private float _textScale = 1f;

        public Text PromptText { get; private set; }

        public Text ProgressText { get; private set; }

        public AssistedConfidenceControl ConfidenceControl { get; private set; }

        public IReadOnlyList<Button> OptionButtons => _optionButtons;

        public IReadOnlyList<RectTransform> OptionRects => _optionRects;

        public int SelectedOptionCount => _optionVisuals.Count(visual => visual.Chosen);

        public bool SingleColumn { get; private set; }

        internal void Initialize(
            Action<int> optionSelected,
            Action<Confidence> confidenceSelected)
        {
            _optionSelected = optionSelected ??
                throw new ArgumentNullException(nameof(optionSelected));
            var rect = (RectTransform)transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;

            ProgressText = AssistedUiFactory.Text(
                transform,
                "Supported progress",
                "Supported question 1 of 2",
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
                "Supported question atlas",
                new Vector2(0f, 8f),
                new Vector2(1500f, 720f));

            var promptObject = new GameObject(
                "Supported prompt field",
                typeof(RectTransform),
                typeof(AsymmetricAtlasGraphic));
            promptObject.transform.SetParent(_scroll.Content, false);
            _promptSurface = (RectTransform)promptObject.transform;
            var promptGraphic = promptObject.GetComponent<AsymmetricAtlasGraphic>();
            promptGraphic.color = new Color(
                AssistedPalette.StormTeal.r,
                AssistedPalette.StormTeal.g,
                AssistedPalette.StormTeal.b,
                0.32f);
            promptGraphic.CutSize = 22f;
            promptGraphic.raycastTarget = false;
            PromptText = AssistedUiFactory.Text(
                promptObject.transform,
                "Supported prompt",
                string.Empty,
                36,
                AssistedPalette.Limestone,
                TextAnchor.MiddleCenter,
                FontStyle.Bold);

            for (var index = 0; index < 4; index++)
            {
                var button = AssistedUiFactory.AtlasButton(
                    _scroll.Content,
                    $"Supported option {(char)('A' + index)}",
                    string.Empty,
                    out var label,
                    out var visual);
                var captured = index;
                button.onClick.AddListener(() => _optionSelected(captured));
                _optionButtons.Add(button);
                _optionLabels.Add(label);
                _optionVisuals.Add(visual);
                _optionRects.Add((RectTransform)button.transform);
            }

            var confidenceObject = new GameObject(
                "Supported confidence",
                typeof(RectTransform),
                typeof(AssistedConfidenceControl));
            confidenceObject.transform.SetParent(_scroll.Content, false);
            ConfidenceControl = confidenceObject.GetComponent<AssistedConfidenceControl>();
            ConfidenceControl.Initialize(confidenceSelected);
            ApplyTextScale(1f);
        }

        internal void Bind(
            AssistedSupportedItem item,
            AssistedAnswerState answer,
            int itemIndex,
            int itemCount)
        {
            _item = item ?? throw new ArgumentNullException(nameof(item));
            _answer = answer ?? throw new ArgumentNullException(nameof(answer));
            PromptText.text = item.Prompt;
            ProgressText.text = $"Supported question {itemIndex + 1} of {itemCount}";
            for (var index = 0; index < _optionButtons.Count; index++)
            {
                var option = item.Options[index];
                _optionLabels[index].text =
                    $"{(char)('A' + index)}   {option.DisplayText}";
                _optionVisuals[index].SetChosen(
                    option.OptionId == answer.SelectedOptionId);
            }
            ConfidenceControl.Bind(answer.SelectedConfidence);
            ApplyTextScale(_textScale);
            _scroll.ScrollRect.verticalNormalizedPosition = 1f;
        }

        public void ApplyTextScale(float scale)
        {
            _textScale = Mathf.Clamp(scale, 1f, 1.5f);
            PromptText.fontSize = Mathf.RoundToInt(36f * _textScale);
            ProgressText.fontSize = Mathf.RoundToInt(28f * _textScale);
            var progressRect = (RectTransform)ProgressText.transform;
            progressRect.sizeDelta = new Vector2(590f, progressRect.sizeDelta.y);
            Canvas.ForceUpdateCanvases();
            progressRect.sizeDelta = new Vector2(
                590f,
                Mathf.Max(60f, ProgressText.preferredHeight));
            foreach (var label in _optionLabels)
                label.fontSize = Mathf.RoundToInt(32f * _textScale);
            ConfidenceControl.ApplyTextScale(_textScale);
            SingleColumn = _textScale >= 1.5f;
            Relayout();
        }

        internal void SetInteractable(bool interactable)
        {
            foreach (var button in _optionButtons)
                button.interactable = interactable;
            ConfidenceControl.SetInteractable(interactable);
        }

        internal string BuildSpeechText()
        {
            if (_item == null)
                return string.Empty;
            var speech = new StringBuilder();
            speech.Append(_item.Prompt);
            for (var index = 0; index < _item.Options.Count; index++)
            {
                speech.Append(". Option ");
                speech.Append((char)('A' + index));
                speech.Append(": ");
                speech.Append(_item.Options[index].DisplayText);
            }
            speech.Append(". Confidence: ");
            speech.Append(
                _answer != null && _answer.SelectedConfidence.HasValue
                    ? _answer.SelectedConfidence.Value.ToString()
                    : "not selected");
            if (_answer != null && _answer.SelectedOptionId != null)
            {
                speech.Append(". Selected answer: ");
                speech.Append(DescribeSelection());
            }
            return speech.ToString();
        }

        private string DescribeSelection()
        {
            for (var index = 0; index < _item.Options.Count; index++)
            {
                if (_item.Options[index].OptionId == _answer.SelectedOptionId)
                {
                    return $"{(char)('A' + index)} — " +
                           _item.Options[index].DisplayText;
                }
            }
            return "recorded answer";
        }

        private void Relayout()
        {
            Canvas.ForceUpdateCanvases();
            var width = _scroll.Viewport.rect.width;
            if (width <= 1f)
                width = 1448f;
            var gap = 18f * _textScale;
            var padding = 28f * _textScale;

            _promptSurface.anchorMin = new Vector2(0f, 1f);
            _promptSurface.anchorMax = Vector2.one;
            _promptSurface.pivot = new Vector2(0.5f, 1f);
            _promptSurface.anchoredPosition = Vector2.zero;
            var promptRect = (RectTransform)PromptText.transform;
            promptRect.offsetMin = new Vector2(padding, padding);
            promptRect.offsetMax = new Vector2(-padding, -padding);
            Canvas.ForceUpdateCanvases();
            var promptHeight = Mathf.Max(
                112f * _textScale,
                PromptText.preferredHeight + (2f * padding));
            _promptSurface.sizeDelta = new Vector2(0f, promptHeight);

            var optionTop = promptHeight + gap;
            var columns = SingleColumn ? 1 : 2;
            var optionWidth = (width - ((columns - 1) * gap)) / columns;
            var optionHeights = new float[_optionRects.Count];
            for (var index = 0; index < _optionRects.Count; index++)
            {
                _optionRects[index].sizeDelta = new Vector2(optionWidth, 100f);
            }
            Canvas.ForceUpdateCanvases();
            for (var index = 0; index < _optionRects.Count; index++)
            {
                optionHeights[index] = Mathf.Max(
                    88f * _textScale,
                    _optionLabels[index].preferredHeight + (32f * _textScale));
            }

            var cursor = optionTop;
            for (var row = 0; row < Mathf.CeilToInt(_optionRects.Count / (float)columns); row++)
            {
                var first = row * columns;
                var rowCount = Mathf.Min(columns, _optionRects.Count - first);
                var rowHeight = 0f;
                for (var column = 0; column < rowCount; column++)
                    rowHeight = Mathf.Max(rowHeight, optionHeights[first + column]);
                for (var column = 0; column < rowCount; column++)
                {
                    var index = first + column;
                    AssistedUiFactory.SetFixedRect(
                        _optionRects[index],
                        new Vector2(0f, 1f),
                        new Vector2(0f, 1f),
                        new Vector2(column * (optionWidth + gap), -cursor),
                        new Vector2(optionWidth, rowHeight));
                }
                cursor += rowHeight + gap;
            }

            var confidenceHeight = ConfidenceControl.Layout(
                cursor,
                width,
                SingleColumn);
            cursor += confidenceHeight + gap;
            _scroll.Content.sizeDelta = new Vector2(0f, Mathf.Max(
                cursor,
                _scroll.Viewport.rect.height));
            Canvas.ForceUpdateCanvases();
        }
    }
}
