using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.UI;
using Wayline.Learning.Contracts;

namespace Wayline.UI
{
    public sealed class ConfidenceControl : MonoBehaviour
    {
        private static readonly Confidence[] Values =
        {
            Confidence.Certain,
            Confidence.Leaning,
            Confidence.Guessing
        };

        private readonly List<Button> _buttons = new List<Button>();
        private readonly List<Text> _labels = new List<Text>();
        private readonly List<AtlasSelectableVisual> _visuals =
            new List<AtlasSelectableVisual>();
        private Text _heading;
        private Action<Confidence> _selected;
        private float _textScale = 1f;
        private float _preferredHeight = 92f;

        public IReadOnlyList<Button> Buttons => _buttons;

        public float PreferredHeight => _preferredHeight;

        public bool HasClippedText => _labels.Any(label =>
            label.gameObject.activeInHierarchy &&
            label.preferredHeight > ((RectTransform)label.transform).rect.height + 0.5f);

        internal void Initialize(Action<Confidence> selected)
        {
            _selected = selected ?? throw new ArgumentNullException(nameof(selected));
            var rect = (RectTransform)transform;
            rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.sizeDelta = new Vector2(1180f, 88f);

            _heading = AtlasUiFactory.Text(
                transform,
                "Confidence heading",
                "Confidence",
                28,
                AtlasPalette.Limestone,
                TextAnchor.MiddleLeft,
                FontStyle.Bold);
            var headingRect = (RectTransform)_heading.transform;
            headingRect.anchorMin = new Vector2(0f, 0f);
            headingRect.anchorMax = new Vector2(0f, 1f);
            headingRect.sizeDelta = new Vector2(170f, 0f);
            headingRect.anchoredPosition = new Vector2(85f, 0f);

            for (var index = 0; index < Values.Length; index++)
            {
                var value = Values[index];
                var notch = new string('▮', 3 - index);
                var button = AtlasUiFactory.AtlasButton(
                    transform,
                    value.ToString(),
                    $"{value}  {notch}",
                    out var label,
                    out var visual);
                var buttonRect = (RectTransform)button.transform;
                buttonRect.anchorMin = buttonRect.anchorMax = new Vector2(0f, 0.5f);
                buttonRect.pivot = new Vector2(0f, 0.5f);
                buttonRect.anchoredPosition = new Vector2(190f + index * 350f, 0f);
                buttonRect.sizeDelta = new Vector2(334f, 72f);
                label.verticalOverflow = VerticalWrapMode.Overflow;
                visual.SetCompactSelection(true);
                var captured = value;
                button.onClick.AddListener(() => _selected(captured));
                _buttons.Add(button);
                _labels.Add(label);
                _visuals.Add(visual);
            }
        }

        internal void Bind(Confidence? selected)
        {
            for (var index = 0; index < Values.Length; index++)
                _visuals[index].SetChosen(selected == Values[index]);
        }

        internal void ApplyTextScale(float scale)
        {
            _textScale = Mathf.Clamp(scale, 1f, 1.5f);
            _heading.fontSize = Mathf.RoundToInt(28f * _textScale);
            var buttonHeight = Mathf.Max(72f, 58f * _textScale);
            for (var index = 0; index < _labels.Count; index++)
            {
                _labels[index].fontSize = Mathf.RoundToInt(28f * _textScale);
                _visuals[index].SetTextScale(_textScale);
                ((RectTransform)_buttons[index].transform).sizeDelta = new Vector2(
                    334f,
                    buttonHeight);
            }
            Canvas.ForceUpdateCanvases();
            for (var index = 0; index < _labels.Count; index++)
                buttonHeight = Mathf.Max(buttonHeight, _labels[index].preferredHeight + 28f);
            for (var index = 0; index < _buttons.Count; index++)
                ((RectTransform)_buttons[index].transform).sizeDelta =
                    new Vector2(334f, buttonHeight);
            _preferredHeight = buttonHeight + 20f;
        }

        internal void SetScrollRect(ScrollRect scrollRect)
        {
            for (var index = 0; index < _visuals.Count; index++)
                _visuals[index].SetScrollRect(scrollRect);
        }

        internal void SetInteractive(bool interactive)
        {
            for (var index = 0; index < _buttons.Count; index++)
                AtlasUiFactory.SetInteractable(_buttons[index], interactive);
        }
    }
}
