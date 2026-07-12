using UnityEngine;
using UnityEngine.UI;

namespace Wayline.UI
{
    public sealed class WrongCountPanel : MonoBehaviour
    {
        private CanvasGroup _canvasGroup;
        private float _shownAt;
        private bool _reducedMotion;

        public Text ExactCountText { get; private set; }

        public Text SupportingText { get; private set; }

        internal void Initialize()
        {
            var rect = (RectTransform)transform;
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
            _canvasGroup = gameObject.AddComponent<CanvasGroup>();

            var routeLine = AtlasUiFactory.Image(
                transform,
                "Count meridian",
                AtlasPalette.MeridianGold);
            var routeRect = (RectTransform)routeLine.transform;
            routeRect.anchorMin = new Vector2(0.23f, 0.5f);
            routeRect.anchorMax = new Vector2(0.77f, 0.5f);
            routeRect.sizeDelta = new Vector2(0f, 3f);
            routeRect.anchoredPosition = new Vector2(0f, 126f);

            ExactCountText = AtlasUiFactory.Text(
                transform,
                "Exact wrong count",
                string.Empty,
                68,
                AtlasPalette.Limestone,
                TextAnchor.MiddleCenter,
                FontStyle.Bold);
            SetRect(ExactCountText, new Vector2(0f, 18f), new Vector2(1180f, 190f));

            SupportingText = AtlasUiFactory.Text(
                transform,
                "Review explanation",
                string.Empty,
                30,
                AtlasPalette.Limestone,
                TextAnchor.MiddleCenter);
            SetRect(SupportingText, new Vector2(0f, -112f), new Vector2(1320f, 90f));
        }

        internal void Bind(int wrongCount, int itemCount, float textScale, bool reducedMotion)
        {
            ExactCountText.text = $"{wrongCount} of {itemCount}\nanswers are incorrect";
            SupportingText.text = wrongCount == 0
                ? "The route is secure. Review the trusted methods."
                : "You have one review pass. We won't mark which ones yet.";
            ExactCountText.fontSize = Mathf.RoundToInt(68f * textScale);
            SupportingText.fontSize = Mathf.RoundToInt(30f * textScale);
            _reducedMotion = reducedMotion;
            _shownAt = Time.unscaledTime;
            ApplyMotion(0f);
        }

        private void Update()
        {
            if (gameObject.activeInHierarchy)
                ApplyMotion(Time.unscaledTime - _shownAt);
        }

        private void ApplyMotion(float elapsed)
        {
            if (_canvasGroup == null)
                return;
            var state = AtlasMotionEvaluator.EvaluateWrongCount(elapsed, _reducedMotion);
            transform.localScale = new Vector3(state.Scale, state.Scale, 1f);
            _canvasGroup.alpha = state.Opacity;
        }

        private static void SetRect(Text text, Vector2 position, Vector2 size)
        {
            var rect = (RectTransform)text.transform;
            rect.anchorMin = rect.anchorMax = new Vector2(0.5f, 0.5f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
        }
    }
}
