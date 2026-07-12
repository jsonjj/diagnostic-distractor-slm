using System;

namespace Wayline.Combat.Simulation
{
    internal static class KinematicMotor
    {
        public const int ArenaMinimumMillimeters = -8000;
        public const int ArenaMaximumMillimeters = 8000;
        public const int WalkMillimetersPerTick = 80;
        public const int DodgeMillimetersPerTick = 100;

        public static void Walk(FighterRuntimeState fighter, int moveAxis)
        {
            fighter.XMillimeters = Clamp(
                fighter.XMillimeters + moveAxis * WalkMillimetersPerTick);
        }

        public static void Dodge(FighterRuntimeState fighter)
        {
            var absoluteDirection = fighter.Facing * fighter.RelativeDodgeDirection;
            fighter.XMillimeters = Clamp(
                fighter.XMillimeters + absoluteDirection * DodgeMillimetersPerTick);
        }

        public static int Clamp(int xMillimeters)
        {
            return Math.Max(
                ArenaMinimumMillimeters,
                Math.Min(ArenaMaximumMillimeters, xMillimeters));
        }
    }
}
