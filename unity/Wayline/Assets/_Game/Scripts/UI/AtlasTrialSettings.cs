using System;

namespace Wayline.UI
{
    public sealed class AtlasTrialSettings
    {
        public const float MinimumTextScale = 1f;
        public const float MaximumTextScale = 1.5f;

        public AtlasTrialSettings(
            string worldLabel,
            float textScale,
            bool reducedMotion)
        {
            if (string.IsNullOrWhiteSpace(worldLabel))
                throw new ArgumentException("World label is required.", nameof(worldLabel));
            if (float.IsNaN(textScale) || float.IsInfinity(textScale))
                throw new ArgumentOutOfRangeException(
                    nameof(textScale),
                    "Text scale must be finite.");

            WorldLabel = worldLabel.Trim();
            TextScale = Clamp(textScale, MinimumTextScale, MaximumTextScale);
            ReducedMotion = reducedMotion;
        }

        public string WorldLabel { get; }

        public float TextScale { get; }

        public bool ReducedMotion { get; }

        private static float Clamp(float value, float minimum, float maximum)
        {
            if (value < minimum)
                return minimum;
            return value > maximum ? maximum : value;
        }
    }
}
