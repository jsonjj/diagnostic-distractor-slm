using System;
using Wayline.Combat.Data;

namespace Wayline.Combat.Presentation
{
    public readonly struct ProceduralActionPose
    {
        public ProceduralActionPose(
            ActionPhase phase,
            int bodyTravelMillimeters,
            int torsoLeanMillidegrees,
            int weaponProgressPermille,
            int compressionPermille,
            int primarySettlePermille,
            int secondarySettlePermille)
        {
            Phase = phase;
            BodyTravelMillimeters = bodyTravelMillimeters;
            TorsoLeanMillidegrees = torsoLeanMillidegrees;
            WeaponProgressPermille = weaponProgressPermille;
            CompressionPermille = compressionPermille;
            PrimarySettlePermille = primarySettlePermille;
            SecondarySettlePermille = secondarySettlePermille;
        }

        public ActionPhase Phase { get; }

        public int BodyTravelMillimeters { get; }

        public int TorsoLeanMillidegrees { get; }

        public int WeaponProgressPermille { get; }

        public int CompressionPermille { get; }

        public int PrimarySettlePermille { get; }

        public int SecondarySettlePermille { get; }
    }

    public static class ProceduralActionPoseEvaluator
    {
        public static ProceduralActionPose Evaluate(
            ActionDefinition action,
            int actionTick,
            int facing)
        {
            if (action == null)
                throw new ArgumentNullException(nameof(action));
            if (facing != -1 && facing != 1)
                throw new ArgumentOutOfRangeException(nameof(facing));

            var phase = action.PhaseAt(actionTick);
            switch (phase)
            {
                case ActionPhase.Anticipation:
                {
                    var progress = Progress(action.Anticipation, actionTick);
                    return Pose(
                        phase,
                        facing,
                        Lerp(0, -100, progress),
                        Lerp(0, -6000, progress),
                        Lerp(0, 120, progress),
                        Lerp(0, 50, progress),
                        0,
                        0);
                }
                case ActionPhase.Commitment:
                {
                    var progress = Progress(action.Commitment, actionTick);
                    return Pose(
                        phase,
                        facing,
                        Lerp(-100, 180, progress),
                        Lerp(-6000, 9000, progress),
                        Lerp(120, 650, progress),
                        Lerp(50, 30, progress),
                        progress,
                        progress / 5);
                }
                case ActionPhase.Contact:
                {
                    var progress = Progress(action.Contact, actionTick);
                    return Pose(
                        phase,
                        facing,
                        Lerp(180, 220, progress),
                        Lerp(9000, 10000, progress),
                        Lerp(650, 800, progress),
                        Lerp(250, 180, progress),
                        1000,
                        Lerp(200, 400, progress));
                }
                case ActionPhase.FollowThrough:
                {
                    var progress = Progress(action.FollowThrough, actionTick);
                    return Pose(
                        phase,
                        facing,
                        Lerp(220, 260, progress),
                        Lerp(10000, 5000, progress),
                        Lerp(800, 1000, progress),
                        Lerp(160, 0, progress),
                        1000,
                        Lerp(400, 1000, progress));
                }
                case ActionPhase.Recovery:
                {
                    var progress = Progress(action.Recovery, actionTick);
                    var primaryResidual = 1000 - progress;
                    var secondaryResidual = 1000 - progress * progress / 1000;
                    return Pose(
                        phase,
                        facing,
                        260 * primaryResidual / 1000,
                        (int)(5000L * primaryResidual * primaryResidual / 1000000L),
                        1000,
                        0,
                        primaryResidual,
                        secondaryResidual);
                }
                default:
                    throw new InvalidOperationException("Rest is not part of an active action.");
            }
        }

        private static ProceduralActionPose Pose(
            ActionPhase phase,
            int facing,
            int bodyTravelMillimeters,
            int torsoLeanMillidegrees,
            int weaponProgressPermille,
            int compressionPermille,
            int primarySettlePermille,
            int secondarySettlePermille)
        {
            return new ProceduralActionPose(
                phase,
                bodyTravelMillimeters * facing,
                torsoLeanMillidegrees * facing,
                weaponProgressPermille,
                compressionPermille,
                primarySettlePermille,
                secondarySettlePermille);
        }

        private static int Progress(TickRange range, int tick)
        {
            if (range.IsEmpty || !range.Contains(tick))
                throw new ArgumentOutOfRangeException(nameof(tick));
            if (range.Start == range.End)
                return 1000;
            return (tick - range.Start) * 1000 / (range.End - range.Start);
        }

        private static int Lerp(int start, int end, int progressPermille)
        {
            return start + (end - start) * progressPermille / 1000;
        }
    }
}
