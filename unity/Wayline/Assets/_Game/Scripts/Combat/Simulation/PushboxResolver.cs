namespace Wayline.Combat.Simulation
{
    internal static class PushboxResolver
    {
        public const int MinimumSeparationMillimeters = 600;

        public static void Resolve(FighterRuntimeState player, FighterRuntimeState enemy)
        {
            var separation = enemy.XMillimeters - player.XMillimeters;
            if (separation >= MinimumSeparationMillimeters)
                return;

            var midpoint = (player.XMillimeters + enemy.XMillimeters) / 2;
            var playerX = midpoint - MinimumSeparationMillimeters / 2;
            var enemyX = playerX + MinimumSeparationMillimeters;

            if (playerX < KinematicMotor.ArenaMinimumMillimeters)
            {
                playerX = KinematicMotor.ArenaMinimumMillimeters;
                enemyX = playerX + MinimumSeparationMillimeters;
            }
            else if (enemyX > KinematicMotor.ArenaMaximumMillimeters)
            {
                enemyX = KinematicMotor.ArenaMaximumMillimeters;
                playerX = enemyX - MinimumSeparationMillimeters;
            }

            player.XMillimeters = playerX;
            enemy.XMillimeters = enemyX;
        }
    }
}
