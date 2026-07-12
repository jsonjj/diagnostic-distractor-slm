using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using Wayline.Learning.Contracts;

namespace Wayline.UI.Assisted
{
    public sealed class AssistedConfidenceControl : MonoBehaviour
    {
        private static readonly Confidence[] Values =
        {
            Confidence.Certain,
            Confidence.Leaning,
            Confidence.Guessing
        };

        private readonly List<Button> _buttons = new List<Button>();
        private readonly List<Text> _labels = new List<Text>();
        private readonly List<AssistedSelectableVisual> _visuals =
            new List<AssistedSelectableVisual>();
        private Text _heading;
        private Action<Confidence> _selected;
        private float _textScale = 1f;

        public IReadOnlyList<Button> Buttons => _buttons;

        public int SelectedCount => _visuals.FindAll(visual => visual.Chosen).Count;

        internal void Initialize(Action<Confidence> selected)
        {
            _selected = selected ?? throw new ArgumentNullException(nameof(selected));
            _heading = AssistedUiFactory.Text(
                transform,
                "Confidence heading",
                "Confidence",
                28,
                AssistedPalette.MeridianGold,
                TextAnchor.MiddleLeft,
                FontStyle.Bold);

            for (var index = 0; index < Values.Length; index++)
            {
                var value = Values[index];
                var notches = new string('▮', 3 - index);
                var button = AssistedUiFactory.AtlasButton(
                    transform,
                    value.ToString(),
                    $"{value}  {notches}",
                    out var label,
                    out var visual);
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
            foreach (var label in _labels)
                label.fontSize = Mathf.RoundToInt(26f * _textScale);
        }

        internal float Layout(float top, float width, bool singleColumn)
        {
            var rect = (RectTransform)transform;
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = Vector2.one;
            rect.pivot = new Vector2(0.5f, 1f);
            rect.anchoredPosition = new Vector2(0f, -top);

            var headingHeight = Mathf.Max(
                42f * _textScale,
                _heading.preferredHeight);
            var headingRect = (RectTransform)_heading.transform;
            headingRect.anchorMin = new Vector2(0f, 1f);
            headingRect.anchorMax = Vector2.one;
            headingRect.pivot = new Vector2(0.5f, 1f);
            headingRect.anchoredPosition = Vector2.zero;
            headingRect.sizeDelta = new Vector2(0f, headingHeight);

            var gap = 14f * _textScale;
            var buttonTop = headingHeight + gap;
            if (singleColumn)
            {
                var buttonHeight = 84f * _textScale;
                for (var index = 0; index < _buttons.Count; index++)
                {
                    AssistedUiFactory.SetFixedRect(
                        (RectTransform)_buttons[index].transform,
                        new Vector2(0f, 1f),
                        new Vector2(0f, 1f),
                        new Vector2(0f, -(buttonTop + index * (buttonHeight + gap))),
                        new Vector2(width, buttonHeight));
                }
                var height = buttonTop + _buttons.Count * buttonHeight +
                             (_buttons.Count - 1) * gap;
                rect.sizeDelta = new Vector2(0f, height);
                return height;
            }

            var buttonWidth = (width - (2f * gap)) / 3f;
            var rowHeight = 80f * _textScale;
            for (var index = 0; index < _buttons.Count; index++)
            {
                AssistedUiFactory.SetFixedRect(
                    (RectTransform)_buttons[index].transform,
                    new Vector2(0f, 1f),
                    new Vector2(0f, 1f),
                    new Vector2(index * (buttonWidth + gap), -buttonTop),
                    new Vector2(buttonWidth, rowHeight));
            }
            var total = buttonTop + rowHeight;
            rect.sizeDelta = new Vector2(0f, total);
            return total;
        }

        internal void SetInteractable(bool interactable)
        {
            foreach (var button in _buttons)
                button.interactable = interactable;
        }
    }
}
