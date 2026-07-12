using System;
using Wayline.UI;

namespace Wayline.UI.Assisted
{
    public readonly struct AssistedRouteMotionState
    {
        public AssistedRouteMotionState(
            float routeProgress,
            float lineOpacity,
            float surfaceOpacity,
            bool interactive)
        {
            RouteProgress = routeProgress;
            LineOpacity = lineOpacity;
            SurfaceOpacity = surfaceOpacity;
            Interactive = interactive;
        }

        public float RouteProgress { get; }

        public float LineOpacity { get; }

        public float SurfaceOpacity { get; }

        public bool Interactive { get; }
    }

    public static class AssistedRouteMotionEvaluator
    {
        public const float StandardAdvanceDurationSeconds = 0.24f;
        public const float ReducedAdvanceDurationSeconds = 0.18f;

        public static AssistedRouteMotionState EvaluateOpening(
            float elapsedSeconds,
            bool reducedMotion)
        {
            var opening = AtlasMotionEvaluator.EvaluateOpening(
                elapsedSeconds,
                reducedMotion);
            return new AssistedRouteMotionState(
                opening.LineProgress,
                opening.LineOpacity,
                opening.SurfaceOpacity,
                opening.Interactive);
        }

        public static AssistedRouteMotionState EvaluateAdvance(
            float elapsedSeconds,
            bool reducedMotion)
        {
            elapsedSeconds = ValidateElapsed(elapsedSeconds);
            var duration = reducedMotion
                ? ReducedAdvanceDurationSeconds
                : StandardAdvanceDurationSeconds;
            var progress = SmoothStep(Saturate(elapsedSeconds / duration));

            if (reducedMotion)
            {
                return new AssistedRouteMotionState(
                    1f,
                    progress,
                    progress,
                    elapsedSeconds >= duration);
            }

            var surfaceProgress = SmoothStep(Saturate(
                (elapsedSeconds - 0.06f) / (duration - 0.06f)));
            return new AssistedRouteMotionState(
                progress,
                1f,
                0.86f + (0.14f * surfaceProgress),
                elapsedSeconds >= duration);
        }

        private static float ValidateElapsed(float elapsedSeconds)
        {
            if (float.IsNaN(elapsedSeconds) || float.IsInfinity(elapsedSeconds))
                throw new ArgumentOutOfRangeException(nameof(elapsedSeconds));
            return elapsedSeconds < 0f ? 0f : elapsedSeconds;
        }

        private static float Saturate(float value)
        {
            if (value < 0f)
                return 0f;
            return value > 1f ? 1f : value;
        }

        private static float SmoothStep(float value)
        {
            return value * value * (3f - (2f * value));
        }
    }
}
