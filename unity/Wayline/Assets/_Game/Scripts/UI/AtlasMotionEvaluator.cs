using System;

namespace Wayline.UI
{
    public readonly struct AtlasOpeningMotionState
    {
        public AtlasOpeningMotionState(
            float lineProgress,
            float lineOpacity,
            float surfaceOpacity,
            bool interactive)
        {
            LineProgress = lineProgress;
            LineOpacity = lineOpacity;
            SurfaceOpacity = surfaceOpacity;
            Interactive = interactive;
        }

        public float LineProgress { get; }

        public float LineOpacity { get; }

        public float SurfaceOpacity { get; }

        public bool Interactive { get; }
    }

    public readonly struct AtlasWrongCountMotionState
    {
        public AtlasWrongCountMotionState(float scale, float opacity)
        {
            Scale = scale;
            Opacity = opacity;
        }

        public float Scale { get; }

        public float Opacity { get; }
    }

    public static class AtlasMotionEvaluator
    {
        public const float StandardOpeningDurationSeconds = 0.66f;
        public const float ReducedOpeningDurationSeconds = 0.18f;
        public const float WrongCountDurationSeconds = 0.12f;

        private const float ArenaQuietDurationSeconds = 0.18f;
        private const float LineTraceDurationSeconds = 0.42f;
        private const float SurfaceResolveStartSeconds = 0.42f;
        private const float SurfaceResolveDurationSeconds = 0.24f;
        private const float WrongCountInitialScale = 0.96f;

        public static AtlasOpeningMotionState EvaluateOpening(
            float elapsedSeconds,
            bool reducedMotion)
        {
            elapsedSeconds = ValidateElapsedSeconds(elapsedSeconds);

            if (reducedMotion)
            {
                var opacity = SmoothStep(
                    Saturate(elapsedSeconds / ReducedOpeningDurationSeconds));
                return new AtlasOpeningMotionState(
                    1f,
                    opacity,
                    opacity,
                    elapsedSeconds >= ReducedOpeningDurationSeconds);
            }

            var lineProgress = SmoothStep(Saturate(
                (elapsedSeconds - ArenaQuietDurationSeconds) /
                LineTraceDurationSeconds));
            var surfaceOpacity = SmoothStep(Saturate(
                (elapsedSeconds - SurfaceResolveStartSeconds) /
                SurfaceResolveDurationSeconds));

            return new AtlasOpeningMotionState(
                lineProgress,
                1f,
                surfaceOpacity,
                elapsedSeconds >= StandardOpeningDurationSeconds);
        }

        public static AtlasWrongCountMotionState EvaluateWrongCount(
            float elapsedSeconds,
            bool reducedMotion)
        {
            elapsedSeconds = ValidateElapsedSeconds(elapsedSeconds);
            var progress = SmoothStep(Saturate(
                elapsedSeconds / WrongCountDurationSeconds));

            if (reducedMotion)
                return new AtlasWrongCountMotionState(1f, progress);

            return new AtlasWrongCountMotionState(
                WrongCountInitialScale +
                ((1f - WrongCountInitialScale) * progress),
                1f);
        }

        private static float ValidateElapsedSeconds(float elapsedSeconds)
        {
            if (float.IsNaN(elapsedSeconds) || float.IsInfinity(elapsedSeconds))
                throw new ArgumentOutOfRangeException(
                    nameof(elapsedSeconds),
                    "Elapsed time must be finite.");

            return elapsedSeconds < 0f ? 0f : elapsedSeconds;
        }

        private static float Saturate(float value)
        {
            if (value < 0f)
                return 0f;
            return value > 1f ? 1f : value;
        }

        private static float SmoothStep(float progress)
        {
            return progress * progress * (3f - (2f * progress));
        }
    }
}
