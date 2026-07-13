using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;
using UnityEngine.UI;
using Wayline.Learning.Contracts;
using Wayline.Learning.Quiz;

namespace Wayline.UI
{
    public sealed class QuestionPage : MonoBehaviour
    {
        private readonly List<Button> _optionButtons = new List<Button>();
        private readonly List<Text> _optionLabels = new List<Text>();
        private readonly List<AtlasSelectableVisual> _optionVisuals =
            new List<AtlasSelectableVisual>();
        private readonly List<RectTransform> _optionRects = new List<RectTransform>();
        private readonly List<Selectable> _focusOrder = new List<Selectable>();
        private Action<int> _optionSelected;
        private PublicQuizItem _item;
        private QuizAnswerState _answer;
        private Text _routeLabel;
        private Text _noTimerText;
        private Text _inputHintText;
        private CanvasGroup _surfaceGroup;
        private Image _readingLine;
        private ScrollRect _scrollRect;
        private RectTransform _scrollContent;
        private float _textScale = 1f;
        private bool _reviewing;
        private bool _scrollToTopOnLayout;

        public Text PromptText { get; private set; }

        public Text ProgressText { get; private set; }

        public Text FirstChoiceText { get; private set; }

        public ConfidenceControl ConfidenceControl { get; private set; }

        public IReadOnlyList<Button> OptionButtons => _optionButtons;

        public IReadOnlyList<RectTransform> OptionRects => _optionRects;

        public IReadOnlyList<Selectable> FocusOrder => _focusOrder;

        public bool ResultMarkersVisible => false;

        public bool SingleColumn { get; private set; }

        public bool HasClippedText =>
            IsVerticallyClipped(PromptText) ||
            IsVerticallyClipped(FirstChoiceText) ||
            _optionLabels.Any(IsVerticallyClipped) ||
            ConfidenceControl.HasClippedText;

        internal void Initialize(
            string trialLabel,
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

            var surface = AtlasUiFactory.Rect(
                transform,
                "Atlas surface",
                Vector2.zero,
                Vector2.one,
                Vector2.zero,
                Vector2.zero);
            _surfaceGroup = surface.gameObject.AddComponent<CanvasGroup>();
            _surfaceGroup.alpha = 0f;

            _routeLabel = AtlasUiFactory.Text(
                surface,
                "Route label",
                trialLabel,
                30,
                AtlasPalette.MeridianGold,
                TextAnchor.MiddleLeft,
                FontStyle.Bold);
            SetRect(_routeLabel, new Vector2(0f, 1f), new Vector2(0f, 1f),
                new Vector2(74f, -62f), new Vector2(830f, 56f), new Vector2(0f, 0.5f));

            ProgressText = AtlasUiFactory.Text(
                surface,
                "Written progress",
                "Item 1 of 3",
                28,
                AtlasPalette.Limestone,
                TextAnchor.MiddleRight,
                FontStyle.Bold);
            SetRect(ProgressText, Vector2.one, Vector2.one,
                new Vector2(-390f, -62f), new Vector2(300f, 56f), new Vector2(1f, 0.5f));

            var viewport = AtlasUiFactory.Rect(
                surface,
                "Scrollable question viewport",
                new Vector2(0.055f, 0.14f),
                new Vector2(0.945f, 0.84f),
                Vector2.zero,
                Vector2.zero);
            viewport.gameObject.AddComponent<RectMask2D>();
            _scrollRect = viewport.gameObject.AddComponent<ScrollRect>();
            _scrollRect.horizontal = false;
            _scrollRect.vertical = true;
            _scrollRect.inertia = false;
            _scrollRect.movementType = ScrollRect.MovementType.Clamped;
            _scrollRect.scrollSensitivity = 52f;
            _scrollRect.viewport = viewport;

            _scrollContent = AtlasUiFactory.Rect(
                viewport,
                "Question content",
                new Vector2(0f, 1f),
                Vector2.one,
                Vector2.zero,
                Vector2.zero);
            _scrollContent.pivot = new Vector2(0.5f, 1f);
            _scrollContent.anchoredPosition = Vector2.zero;
            _scrollContent.sizeDelta = new Vector2(0f, 720f);
            _scrollRect.content = _scrollContent;

            PromptText = AtlasUiFactory.Text(
                _scrollContent,
                "Question prompt",
                string.Empty,
                36,
                AtlasPalette.Limestone,
                TextAnchor.MiddleCenter,
                FontStyle.Bold);
            PromptText.verticalOverflow = VerticalWrapMode.Overflow;

            FirstChoiceText = AtlasUiFactory.Text(
                _scrollContent,
                "First choice",
                string.Empty,
                28,
                AtlasPalette.Limestone,
                TextAnchor.MiddleCenter);
            FirstChoiceText.verticalOverflow = VerticalWrapMode.Overflow;
            FirstChoiceText.gameObject.SetActive(false);

            for (var index = 0; index < 4; index++)
            {
                var button = AtlasUiFactory.AtlasButton(
                    _scrollContent,
                    $"Option {(char)('A' + index)}",
                    string.Empty,
                    out var label,
                    out var visual);
                label.verticalOverflow = VerticalWrapMode.Overflow;
                var optionIndex = index;
                button.onClick.AddListener(() => _optionSelected(optionIndex));
                visual.SetScrollRect(_scrollRect);
                _optionButtons.Add(button);
                _optionLabels.Add(label);
                _optionVisuals.Add(visual);
                _optionRects.Add((RectTransform)button.transform);
            }

            var confidenceObject = new GameObject(
                "Confidence control",
                typeof(RectTransform),
                typeof(ConfidenceControl));
            confidenceObject.transform.SetParent(_scrollContent, false);
            ConfidenceControl = confidenceObject.GetComponent<ConfidenceControl>();
            ConfidenceControl.Initialize(confidenceSelected);
            ConfidenceControl.SetScrollRect(_scrollRect);

            _noTimerText = AtlasUiFactory.Text(
                surface,
                "No timer",
                "No timer",
                28,
                AtlasPalette.Limestone,
                TextAnchor.MiddleLeft);
            SetRect(_noTimerText, Vector2.zero, Vector2.zero,
                new Vector2(74f, 54f), new Vector2(220f, 52f), new Vector2(0f, 0.5f));

            _inputHintText = AtlasUiFactory.Text(
                surface,
                "Input hint",
                "Arrows / stick: navigate  •  Enter / A: choose",
                28,
                AtlasPalette.Limestone,
                TextAnchor.MiddleLeft);
            SetRect(_inputHintText, Vector2.zero, Vector2.zero,
                new Vector2(330f, 54f), new Vector2(950f, 52f), new Vector2(0f, 0.5f));

            _readingLine = AtlasUiFactory.Image(
                transform,
                "Meridian reading line",
                AtlasPalette.MeridianGold);
            _readingLine.type = Image.Type.Filled;
            _readingLine.fillMethod = Image.FillMethod.Horizontal;
            _readingLine.fillOrigin = 0;
            _readingLine.fillAmount = 0f;
            var readingRect = (RectTransform)_readingLine.transform;
            readingRect.anchorMin = new Vector2(0.06f, 1f);
            readingRect.anchorMax = new Vector2(0.94f, 1f);
            readingRect.pivot = new Vector2(0f, 0.5f);
            readingRect.sizeDelta = new Vector2(0f, 3f);
            readingRect.anchoredPosition = new Vector2(0f, -122f);

            ApplyTextScale(1f);
        }

        internal void Bind(
            PublicQuizItem item,
            QuizAnswerState answer,
            int itemIndex,
            int itemCount,
            bool reviewing)
        {
            var pageChanged = _item == null || _item.ItemId != item?.ItemId ||
                _reviewing != reviewing;
            _item = item ?? throw new ArgumentNullException(nameof(item));
            _answer = answer ?? throw new ArgumentNullException(nameof(answer));
            _reviewing = reviewing;
            _scrollToTopOnLayout |= pageChanged;
            PromptText.text = item.Prompt;
            ProgressText.text = $"Item {itemIndex + 1} of {itemCount}";

            for (var index = 0; index < _optionButtons.Count; index++)
            {
                var exists = index < item.Options.Count;
                _optionButtons[index].gameObject.SetActive(exists);
                if (!exists)
                    continue;
                var option = item.Options[index];
                _optionLabels[index].text = $"{(char)('A' + index)}   {option.DisplayText}";
                _optionVisuals[index].SetChosen(option.OptionId == answer.SelectedOptionId);
            }
            ConfidenceControl.Bind(answer.SelectedConfidence);

            FirstChoiceText.gameObject.SetActive(reviewing);
            FirstChoiceText.text = reviewing
                ? "First choice: " + DescribeOption(item, answer.FirstOptionId)
                : string.Empty;
            ApplyTextScale(_textScale);
        }

        internal void ConfigureFocusOrder(Button readAloud, Button primary)
        {
            _focusOrder.Clear();
            _focusOrder.AddRange(_optionButtons);
            _focusOrder.AddRange(ConfidenceControl.Buttons);
            _focusOrder.Add(readAloud);
            _focusOrder.Add(primary);

            for (var index = 0; index < _focusOrder.Count; index++)
            {
                var navigation = new Navigation
                {
                    mode = Navigation.Mode.Explicit,
                    selectOnUp = _focusOrder[(index - 1 + _focusOrder.Count) % _focusOrder.Count],
                    selectOnDown = _focusOrder[(index + 1) % _focusOrder.Count]
                };
                if (index < 4)
                {
                    navigation.selectOnLeft = _optionButtons[(index + 3) % 4];
                    navigation.selectOnRight = _optionButtons[(index + 1) % 4];
                }
                _focusOrder[index].navigation = navigation;
            }
        }

        public void ApplyTextScale(float scale)
        {
            _textScale = Mathf.Clamp(scale, 1f, 1.5f);
            PromptText.fontSize = Mathf.RoundToInt(36f * _textScale);
            ProgressText.fontSize = Mathf.RoundToInt(28f * _textScale);
            _routeLabel.fontSize = Mathf.RoundToInt(30f * _textScale);
            FirstChoiceText.fontSize = Mathf.RoundToInt(28f * _textScale);
            _noTimerText.fontSize = Mathf.RoundToInt(28f * _textScale);
            _inputHintText.fontSize = Mathf.RoundToInt(28f * _textScale);
            for (var index = 0; index < _optionLabels.Count; index++)
            {
                _optionLabels[index].fontSize = Mathf.RoundToInt(32f * _textScale);
                _optionVisuals[index].SetTextScale(_textScale);
            }
            ConfidenceControl.ApplyTextScale(_textScale);

            SingleColumn = _textScale >= 1.5f;
            LayoutContent();
        }

        internal void ApplyOpeningMotion(AtlasOpeningMotionState state)
        {
            _surfaceGroup.alpha = state.SurfaceOpacity;
            _readingLine.fillAmount = state.LineProgress;
            var color = AtlasPalette.MeridianGold;
            color.a = state.LineOpacity;
            _readingLine.color = color;
        }

        internal void SetInteractive(bool interactive)
        {
            for (var index = 0; index < _optionButtons.Count; index++)
                AtlasUiFactory.SetInteractable(_optionButtons[index], interactive);
            ConfidenceControl.SetInteractive(interactive);
        }

        internal string BuildSpeechText()
        {
            if (_item == null)
                return string.Empty;
            var text = new StringBuilder();
            text.Append(_item.Prompt);
            for (var index = 0; index < _item.Options.Count; index++)
            {
                text.Append(". Option ");
                text.Append((char)('A' + index));
                text.Append(": ");
                text.Append(_item.Options[index].DisplayText);
            }
            text.Append(". Confidence: ");
            text.Append(_answer?.SelectedConfidence.ToString() ?? "not selected");
            if (_answer != null && _answer.SelectedOptionId != null)
            {
                text.Append(". Selected answer: ");
                text.Append(DescribeOption(_item, _answer.SelectedOptionId));
            }
            return text.ToString();
        }

        private void LayoutContent()
        {
            var previousScroll = _scrollContent.anchoredPosition.y;
            var contentWidth = Mathf.Max(1440f, ((RectTransform)_scrollRect.transform).rect.width);
            var top = 30f;

            var promptWidth = Mathf.Min(1540f, contentWidth - 80f);
            var promptHeight = MeasuredHeight(PromptText, promptWidth, 92f * _textScale, 24f);
            PlaceFromTop((RectTransform)PromptText.transform, 0f, top, promptWidth, promptHeight);
            top += promptHeight + 22f;

            if (_reviewing)
            {
                var firstHeight = MeasuredHeight(
                    FirstChoiceText,
                    Mathf.Min(1480f, contentWidth - 100f),
                    52f * _textScale,
                    18f);
                PlaceFromTop(
                    (RectTransform)FirstChoiceText.transform,
                    0f,
                    top,
                    Mathf.Min(1480f, contentWidth - 100f),
                    firstHeight);
                top += firstHeight + 22f;
            }

            if (SingleColumn)
            {
                var width = Mathf.Min(1400f, contentWidth - 100f);
                for (var index = 0; index < _optionRects.Count; index++)
                {
                    var height = MeasuredOptionHeight(index, width);
                    PlaceFromTop(_optionRects[index], 0f, top, width, height);
                    top += height + 22f;
                }
            }
            else
            {
                var width = Mathf.Min(740f, (contentWidth - 120f) * 0.5f);
                var x = (width + 34f) * 0.5f;
                for (var row = 0; row < 2; row++)
                {
                    var first = row * 2;
                    var height = Mathf.Max(
                        MeasuredOptionHeight(first, width),
                        MeasuredOptionHeight(first + 1, width));
                    PlaceFromTop(_optionRects[first], -x, top, width, height);
                    PlaceFromTop(_optionRects[first + 1], x, top, width, height);
                    top += height + 26f;
                }
            }

            var confidenceRect = (RectTransform)ConfidenceControl.transform;
            var confidenceHeight = ConfidenceControl.PreferredHeight;
            PlaceFromTop(
                confidenceRect,
                0f,
                top + 4f,
                Mathf.Min(1240f, contentWidth - 80f),
                confidenceHeight);
            top += confidenceHeight + 48f;

            _scrollContent.sizeDelta = new Vector2(0f, Mathf.Max(
                top,
                ((RectTransform)_scrollRect.transform).rect.height));
            Canvas.ForceUpdateCanvases();
            var maximumScroll = Mathf.Max(
                0f,
                _scrollContent.rect.height - ((RectTransform)_scrollRect.transform).rect.height);
            _scrollContent.anchoredPosition = new Vector2(
                0f,
                _scrollToTopOnLayout ? 0f : Mathf.Clamp(previousScroll, 0f, maximumScroll));
            _scrollToTopOnLayout = false;
        }

        private float MeasuredOptionHeight(int index, float width)
        {
            var rect = _optionRects[index];
            rect.sizeDelta = new Vector2(width, 90f);
            Canvas.ForceUpdateCanvases();
            return Mathf.Max(96f * _textScale, _optionLabels[index].preferredHeight + 34f);
        }

        private static float MeasuredHeight(
            Text text,
            float width,
            float minimum,
            float padding)
        {
            var rect = (RectTransform)text.transform;
            rect.sizeDelta = new Vector2(width, minimum);
            Canvas.ForceUpdateCanvases();
            return Mathf.Max(minimum, text.preferredHeight + padding);
        }

        private static void PlaceFromTop(
            RectTransform rect,
            float centerX,
            float top,
            float width,
            float height)
        {
            rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 1f);
            rect.pivot = new Vector2(0.5f, 1f);
            rect.sizeDelta = new Vector2(width, height);
            rect.anchoredPosition = new Vector2(centerX, -top);
        }

        private static bool IsVerticallyClipped(Text text)
        {
            if (text == null || !text.gameObject.activeInHierarchy)
                return false;
            var rect = (RectTransform)text.transform;
            return text.preferredHeight > rect.rect.height + 0.5f;
        }

        private static string DescribeOption(PublicQuizItem item, string optionId)
        {
            if (item == null || optionId == null)
                return "not recorded";
            for (var index = 0; index < item.Options.Count; index++)
            {
                if (item.Options[index].OptionId == optionId)
                    return $"{(char)('A' + index)} — {item.Options[index].DisplayText}";
            }
            return "recorded answer";
        }

        private static void SetRect(
            Text text,
            Vector2 anchorMin,
            Vector2 anchorMax,
            Vector2 position,
            Vector2 size,
            Vector2 pivot)
        {
            var rect = (RectTransform)text.transform;
            rect.anchorMin = anchorMin;
            rect.anchorMax = anchorMax;
            rect.pivot = pivot;
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
        }
    }
}
