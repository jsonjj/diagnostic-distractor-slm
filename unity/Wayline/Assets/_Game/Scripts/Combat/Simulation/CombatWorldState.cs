namespace Wayline.Combat.Simulation
{
    public enum CombatResult
    {
        InProgress = 0,
        PlayerWon = 1,
        EnemyWon = 2
    }

    public sealed class CombatWorldState
    {
        internal CombatWorldState(
            long tick,
            FighterRuntimeState player,
            FighterRuntimeState enemy,
            CombatResult result)
        {
            Tick = tick;
            Player = new FighterState(player);
            Enemy = new FighterState(enemy);
            Result = result;
        }

        public long Tick { get; }

        public FighterState Player { get; }

        public FighterState Enemy { get; }

        public CombatResult Result { get; }
    }
}
