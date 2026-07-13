using System;

namespace Wayline.UI
{
    public enum AtlasTrialPurpose
    {
        RouteProgression = 0,
        DefeatRecovery = 1
    }

    public sealed class AtlasTrialSettings
    {
        public const float MinimumTextScale = 1f;
        public const float MaximumTextScale = 1.5f;

        public AtlasTrialSettings(
            string worldLabel,
            float textScale,
            bool reducedMotion,
            AtlasTrialPurpose purpose = AtlasTrialPurpose.RouteProgression)
        {
            if (string.IsNullOrWhiteSpace(worldLabel))
                throw new ArgumentException("World label is required.", nameof(worldLabel));
            if (float.IsNaN(textScale) || float.IsInfinity(textScale))
                throw new ArgumentOutOfRangeException(
                    nameof(textScale),
                    "Text scale must be finite.");
            if (!Enum.IsDefined(typeof(AtlasTrialPurpose), purpose))
                throw new ArgumentOutOfRangeException(nameof(purpose), purpose, null);

            WorldLabel = worldLabel.Trim();
            TextScale = Clamp(textScale, MinimumTextScale, MaximumTextScale);
            ReducedMotion = reducedMotion;
            Purpose = purpose;
        }

        public string WorldLabel { get; }

        public float TextScale { get; }

        public bool ReducedMotion { get; }

        public AtlasTrialPurpose Purpose { get; }

        public bool RequiresCompletionBeforeMap =>
            Purpose == AtlasTrialPurpose.DefeatRecovery;

        public string QuestionHeader =>
            RequiresCompletionBeforeMap
                ? WorldLabel + " / NEXT-TRY QUESTIONS"
                : WorldLabel + " / ROUTE TRIAL";

        public string LoadingMessage =>
            RequiresCompletionBeforeMap
                ? "PREPARING NEXT-TRY QUESTIONS\n\n" +
                  "That battle did not go your way. Answer all three questions and " +
                  "check the trusted methods before returning to the map.\n\n" +
                  "Every answer choice is checked before it appears."
                : "GENERATING VERIFIED ROUTE TRIAL\n\n" +
                  "Local Qwen is creating three diagnostic questions.\n" +
                  "Every distractor is checked before it appears.\n\n" +
                  "This can take up to one minute.";

        public string UnavailableMessage =>
            RequiresCompletionBeforeMap
                ? "The next-try questions are not ready yet.\n" +
                  "That battle is not counted as a win, and no route rewards were added.\n" +
                  "Try again to continue."
                : "The route trial is unavailable. Your combat result is safe.";

        public string CompletionTitle =>
            RequiresCompletionBeforeMap
                ? "QUESTIONS COMPLETE — READY FOR ANOTHER TRY"
                : "ROUTE TRIAL COMPLETE";

        private static float Clamp(float value, float minimum, float maximum)
        {
            if (value < minimum)
                return minimum;
            return value > maximum ? maximum : value;
        }
    }
}
