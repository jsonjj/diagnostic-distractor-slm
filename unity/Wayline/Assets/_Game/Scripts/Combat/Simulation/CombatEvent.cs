namespace Wayline.Combat.Simulation
{
    public enum FighterSide
    {
        Player = 0,
        Enemy = 1
    }

    public enum CombatEventKind
    {
        Evaded = 0,
        Parried = 1,
        Guarded = 2,
        GuardBreak = 3,
        Hit = 4,
        Knockout = 5,
        HitStop = 6
    }

    public readonly struct CombatEvent
    {
        public CombatEvent(
            long tick,
            CombatEventKind kind,
            FighterSide source,
            FighterSide target,
            int amount)
        {
            Tick = tick;
            Kind = kind;
            Source = source;
            Target = target;
            Amount = amount;
        }

        public long Tick { get; }

        public CombatEventKind Kind { get; }

        public FighterSide Source { get; }

        public FighterSide Target { get; }

        public int Amount { get; }
    }
}
