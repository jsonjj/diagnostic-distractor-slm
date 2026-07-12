using System;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace Wayline.UI
{
    internal static class AtlasPalette
    {
        public static readonly Color NightInk = Hex("151B26");
        public static readonly Color Lapis = Hex("253B66");
        public static readonly Color StormTeal = Hex("2D7F83");
        public static readonly Color Oxide = Hex("A5432F");
        public static readonly Color MeridianGold = Hex("E6AF3B");
        public static readonly Color Limestone = Hex("D7D1C2");
        public static readonly Color InkText = Hex("18202D");

        private static Color Hex(string value)
        {
            if (!ColorUtility.TryParseHtmlString("#" + value, out var color))
                throw new InvalidOperationException("Invalid atlas color token.");
            return color;
        }
    }

    internal static class AtlasUiFactory
    {
        private static Font _font;

        public static Font Font
        {
            get
            {
                if (_font == null)
                    _font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                return _font;
            }
        }

        public static RectTransform Rect(
            Transform parent,
            string name,
            Vector2 anchorMin,
            Vector2 anchorMax,
            Vector2 offsetMin,
            Vector2 offsetMax)
        {
            var gameObject = new GameObject(name, typeof(RectTransform));
            var rect = (RectTransform)gameObject.transform;
            rect.SetParent(parent, false);
            rect.anchorMin = anchorMin;
            rect.anchorMax = anchorMax;
            rect.offsetMin = offsetMin;
            rect.offsetMax = offsetMax;
            return rect;
        }

        public static RectTransform FixedRect(
            Transform parent,
            string name,
            Vector2 anchor,
            Vector2 position,
            Vector2 size)
        {
            var rect = Rect(parent, name, anchor, anchor, Vector2.zero, Vector2.zero);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
            return rect;
        }

        public static Text Text(
            Transform parent,
            string name,
            string value,
            int fontSize,
            Color color,
            TextAnchor alignment,
            FontStyle style = FontStyle.Normal)
        {
            var rect = Rect(
                parent,
                name,
                Vector2.zero,
                Vector2.one,
                Vector2.zero,
                Vector2.zero);
            var text = rect.gameObject.AddComponent<Text>();
            text.font = Font;
            text.fontSize = fontSize;
            text.fontStyle = style;
            text.color = color;
            text.alignment = alignment;
            text.horizontalOverflow = HorizontalWrapMode.Wrap;
            text.verticalOverflow = VerticalWrapMode.Truncate;
            text.supportRichText = false;
            text.raycastTarget = false;
            text.text = value;
            return text;
        }

        public static Image Image(Transform parent, string name, Color color)
        {
            var rect = Rect(
                parent,
                name,
                Vector2.zero,
                Vector2.one,
                Vector2.zero,
                Vector2.zero);
            var image = rect.gameObject.AddComponent<Image>();
            image.color = color;
            image.raycastTarget = false;
            return image;
        }

        public static Button AtlasButton(
            Transform parent,
            string name,
            string value,
            out Text label,
            out AtlasSelectableVisual visual)
        {
            var root = new GameObject(
                name,
                typeof(RectTransform),
                typeof(CanvasRenderer),
                typeof(CanvasGroup),
                typeof(AsymmetricAtlasGraphic),
                typeof(Outline),
                typeof(Button),
                typeof(AtlasSelectableVisual));
            root.transform.SetParent(parent, false);
            var outer = root.GetComponent<AsymmetricAtlasGraphic>();
            outer.color = AtlasPalette.Lapis;
            outer.raycastTarget = true;
            var focusOutline = root.GetComponent<Outline>();
            focusOutline.effectColor = AtlasPalette.MeridianGold;
            focusOutline.effectDistance = new Vector2(4f, -4f);
            focusOutline.useGraphicAlpha = false;
            focusOutline.enabled = false;

            var inner = Rect(
                root.transform,
                "Limestone field",
                Vector2.zero,
                Vector2.one,
                new Vector2(4f, 4f),
                new Vector2(-4f, -4f));
            inner.gameObject.AddComponent<CanvasRenderer>();
            var innerGraphic = inner.gameObject.AddComponent<AsymmetricAtlasGraphic>();
            innerGraphic.color = AtlasPalette.Limestone;
            innerGraphic.raycastTarget = false;
            innerGraphic.CutSize = 15f;

            label = Text(
                inner,
                "Label",
                value,
                30,
                AtlasPalette.InkText,
                TextAnchor.MiddleLeft,
                FontStyle.Bold);
            var labelRect = (RectTransform)label.transform;
            labelRect.offsetMin = new Vector2(24f, 8f);
            labelRect.offsetMax = new Vector2(-148f, -8f);

            var selected = Text(
                inner,
                "Selection state",
                "◆  Selected",
                22,
                AtlasPalette.InkText,
                TextAnchor.MiddleRight,
                FontStyle.Bold);
            var selectedRect = (RectTransform)selected.transform;
            selectedRect.anchorMin = new Vector2(1f, 0f);
            selectedRect.anchorMax = Vector2.one;
            selectedRect.pivot = new Vector2(1f, 0.5f);
            selectedRect.sizeDelta = new Vector2(142f, 0f);
            selectedRect.anchoredPosition = new Vector2(-18f, 0f);
            selected.gameObject.SetActive(false);

            var button = root.GetComponent<Button>();
            button.targetGraphic = outer;
            button.transition = Selectable.Transition.None;
            visual = root.GetComponent<AtlasSelectableVisual>();
            visual.Initialize(outer, focusOutline, label, selected);
            return button;
        }

        public static void SetInteractable(Button button, bool interactable)
        {
            button.interactable = interactable;
            var group = button.GetComponent<CanvasGroup>();
            if (group != null)
                group.alpha = interactable ? 1f : 0.44f;
        }
    }

    [RequireComponent(typeof(CanvasRenderer))]
    public sealed class AsymmetricAtlasGraphic : MaskableGraphic
    {
        [SerializeField] private float cutSize = 18f;

        public float CutSize
        {
            get => cutSize;
            set
            {
                cutSize = Mathf.Max(0f, value);
                SetVerticesDirty();
            }
        }

        protected override void OnPopulateMesh(VertexHelper vertexHelper)
        {
            vertexHelper.Clear();
            var bounds = GetPixelAdjustedRect();
            var cut = Mathf.Min(cutSize, Mathf.Min(bounds.width, bounds.height) * 0.42f);
            var points = new[]
            {
                new Vector2(bounds.xMin + cut, bounds.yMax),
                new Vector2(bounds.xMax, bounds.yMax),
                new Vector2(bounds.xMax, bounds.yMin + cut * 0.65f),
                new Vector2(bounds.xMax - cut * 0.65f, bounds.yMin),
                new Vector2(bounds.xMin, bounds.yMin),
                new Vector2(bounds.xMin, bounds.yMax - cut)
            };
            for (var index = 0; index < points.Length; index++)
                vertexHelper.AddVert(points[index], color, Vector2.zero);
            for (var index = 1; index < points.Length - 1; index++)
                vertexHelper.AddTriangle(0, index, index + 1);
        }
    }

    internal sealed class AtlasSelectableVisual : MonoBehaviour, ISelectHandler, IDeselectHandler
    {
        private Graphic _border;
        private Outline _focusOutline;
        private Text _contentLabel;
        private Text _selectedLabel;
        private ScrollRect _scrollRect;
        private bool _chosen;
        private bool _focused;
        private bool _compactSelection;

        public void Initialize(
            Graphic border,
            Outline focusOutline,
            Text contentLabel,
            Text selectedLabel)
        {
            _border = border;
            _focusOutline = focusOutline;
            _contentLabel = contentLabel;
            _selectedLabel = selectedLabel;
            SetTextScale(1f);
            Refresh();
        }

        public void SetCompactSelection(bool compact)
        {
            _compactSelection = compact;
            if (_selectedLabel != null)
                _selectedLabel.text = compact ? "◆" : "◆  Selected";
            SetTextScale(1f);
        }

        public void SetTextScale(float scale)
        {
            if (_selectedLabel == null || _contentLabel == null)
                return;
            var selectedRect = (RectTransform)_selectedLabel.transform;
            var gutter = (_compactSelection ? 54f : 148f) * scale;
            selectedRect.sizeDelta = new Vector2(gutter, 0f);
            _selectedLabel.fontSize = Mathf.RoundToInt(28f * scale);
            var contentRect = (RectTransform)_contentLabel.transform;
            contentRect.offsetMax = new Vector2(-gutter - 10f, -8f);
        }

        public void SetScrollRect(ScrollRect scrollRect)
        {
            _scrollRect = scrollRect;
        }

        public void SetChosen(bool chosen)
        {
            _chosen = chosen;
            Refresh();
        }

        public void OnSelect(BaseEventData eventData)
        {
            _focused = true;
            Refresh();
            ScrollIntoView();
        }

        public void OnDeselect(BaseEventData eventData)
        {
            _focused = false;
            Refresh();
        }

        private void Refresh()
        {
            if (_border != null)
                _border.color = _focused || _chosen
                    ? AtlasPalette.MeridianGold
                    : AtlasPalette.Lapis;
            if (_selectedLabel != null)
                _selectedLabel.gameObject.SetActive(_chosen);
            if (_focusOutline != null)
                _focusOutline.enabled = _focused;
        }

        private void ScrollIntoView()
        {
            if (_scrollRect == null || _scrollRect.viewport == null ||
                _scrollRect.content == null)
            {
                return;
            }
            Canvas.ForceUpdateCanvases();
            var target = (RectTransform)transform;
            var corners = new Vector3[4];
            target.GetWorldCorners(corners);
            var viewport = _scrollRect.viewport;
            var bottom = viewport.InverseTransformPoint(corners[0]).y;
            var top = viewport.InverseTransformPoint(corners[1]).y;
            var view = viewport.rect;
            var position = _scrollRect.content.anchoredPosition;
            const float margin = 14f;
            if (bottom < view.yMin + margin)
                position.y += view.yMin + margin - bottom;
            else if (top > view.yMax - margin)
                position.y -= top - (view.yMax - margin);
            var maximum = Mathf.Max(0f, _scrollRect.content.rect.height - view.height);
            position.y = Mathf.Clamp(position.y, 0f, maximum);
            _scrollRect.content.anchoredPosition = position;
        }
    }
}
