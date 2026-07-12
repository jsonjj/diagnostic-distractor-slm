using System;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace Wayline.UI.Assisted
{
    internal static class AssistedPalette
    {
        public static readonly Color NightInk = Hex("151B26");
        public static readonly Color Lapis = Hex("253B66");
        public static readonly Color StormTeal = Hex("2D7F83");
        public static readonly Color MeridianGold = Hex("E6AF3B");
        public static readonly Color Limestone = Hex("D7D1C2");
        public static readonly Color InkText = Hex("18202D");

        private static Color Hex(string value)
        {
            if (!ColorUtility.TryParseHtmlString("#" + value, out var color))
                throw new InvalidOperationException("Invalid assisted atlas color token.");
            return color;
        }
    }

    internal readonly struct AssistedScrollSurface
    {
        public AssistedScrollSurface(
            RectTransform root,
            RectTransform viewport,
            RectTransform content,
            ScrollRect scrollRect)
        {
            Root = root;
            Viewport = viewport;
            Content = content;
            ScrollRect = scrollRect;
        }

        public RectTransform Root { get; }

        public RectTransform Viewport { get; }

        public RectTransform Content { get; }

        public ScrollRect ScrollRect { get; }
    }

    internal static class AssistedUiFactory
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
            text.verticalOverflow = VerticalWrapMode.Overflow;
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
            out AssistedSelectableVisual visual)
        {
            var root = new GameObject(
                name,
                typeof(RectTransform),
                typeof(AsymmetricAtlasGraphic),
                typeof(Button),
                typeof(AssistedSelectableVisual));
            root.transform.SetParent(parent, false);
            var border = root.GetComponent<AsymmetricAtlasGraphic>();
            border.color = AssistedPalette.Lapis;
            border.raycastTarget = true;

            var field = Rect(
                root.transform,
                "Limestone field",
                Vector2.zero,
                Vector2.one,
                new Vector2(4f, 4f),
                new Vector2(-4f, -4f));
            var fieldGraphic = field.gameObject.AddComponent<AsymmetricAtlasGraphic>();
            fieldGraphic.color = AssistedPalette.Limestone;
            fieldGraphic.CutSize = 15f;
            fieldGraphic.raycastTarget = false;

            label = Text(
                field,
                "Label",
                value,
                30,
                AssistedPalette.InkText,
                TextAnchor.MiddleLeft,
                FontStyle.Bold);
            var labelRect = (RectTransform)label.transform;
            labelRect.offsetMin = new Vector2(24f, 12f);
            labelRect.offsetMax = new Vector2(-166f, -12f);

            var selected = Text(
                field,
                "Selection state",
                "◆  Selected",
                24,
                AssistedPalette.InkText,
                TextAnchor.MiddleRight,
                FontStyle.Bold);
            var selectedRect = (RectTransform)selected.transform;
            selectedRect.anchorMin = new Vector2(1f, 0f);
            selectedRect.anchorMax = Vector2.one;
            selectedRect.pivot = new Vector2(1f, 0.5f);
            selectedRect.sizeDelta = new Vector2(154f, 0f);
            selectedRect.anchoredPosition = new Vector2(-18f, 0f);
            selected.gameObject.SetActive(false);

            var button = root.GetComponent<Button>();
            button.targetGraphic = border;
            button.transition = Selectable.Transition.None;
            visual = root.GetComponent<AssistedSelectableVisual>();
            visual.Initialize(border, selected);
            return button;
        }

        public static AssistedScrollSurface ScrollSurface(
            Transform parent,
            string name,
            Vector2 position,
            Vector2 size)
        {
            var root = Rect(
                parent,
                name,
                new Vector2(0.5f, 0.5f),
                new Vector2(0.5f, 0.5f),
                Vector2.zero,
                Vector2.zero);
            root.pivot = new Vector2(0.5f, 0.5f);
            root.anchoredPosition = position;
            root.sizeDelta = size;
            var frame = root.gameObject.AddComponent<AsymmetricAtlasGraphic>();
            frame.color = new Color(
                AssistedPalette.Lapis.r,
                AssistedPalette.Lapis.g,
                AssistedPalette.Lapis.b,
                0.72f);
            frame.CutSize = 24f;
            frame.raycastTarget = true;

            var viewport = Rect(
                root,
                "Viewport",
                Vector2.zero,
                Vector2.one,
                new Vector2(26f, 24f),
                new Vector2(-26f, -24f));
            viewport.gameObject.AddComponent<RectMask2D>();

            var content = Rect(
                viewport,
                "Scrollable atlas content",
                new Vector2(0f, 1f),
                Vector2.one,
                Vector2.zero,
                Vector2.zero);
            content.pivot = new Vector2(0.5f, 1f);
            content.anchoredPosition = Vector2.zero;

            var scroll = root.gameObject.AddComponent<ScrollRect>();
            scroll.viewport = viewport;
            scroll.content = content;
            scroll.horizontal = false;
            scroll.vertical = true;
            scroll.movementType = ScrollRect.MovementType.Clamped;
            scroll.scrollSensitivity = 48f;
            return new AssistedScrollSurface(root, viewport, content, scroll);
        }

        public static void SetFixedRect(
            RectTransform rect,
            Vector2 anchor,
            Vector2 pivot,
            Vector2 position,
            Vector2 size)
        {
            rect.anchorMin = rect.anchorMax = anchor;
            rect.pivot = pivot;
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
        }

        public static bool IsVerticallyClipped(Text text)
        {
            if (text == null || !text.gameObject.activeInHierarchy)
                return false;
            var rect = (RectTransform)text.transform;
            return text.preferredHeight > rect.rect.height + 0.5f;
        }
    }

    internal sealed class AssistedSelectableVisual :
        MonoBehaviour,
        ISelectHandler,
        IDeselectHandler
    {
        private Graphic _border;
        private Text _selectedLabel;
        private bool _chosen;
        private bool _focused;

        public bool Chosen => _chosen;

        public void Initialize(Graphic border, Text selectedLabel)
        {
            _border = border;
            _selectedLabel = selectedLabel;
            Refresh();
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
        }

        public void OnDeselect(BaseEventData eventData)
        {
            _focused = false;
            Refresh();
        }

        private void Refresh()
        {
            if (_border != null)
            {
                _border.color = _focused || _chosen
                    ? AssistedPalette.MeridianGold
                    : AssistedPalette.Lapis;
            }
            if (_selectedLabel != null)
                _selectedLabel.gameObject.SetActive(_chosen);
        }
    }
}
