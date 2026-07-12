namespace Wayline.Combat.Data
{
    public static class GrayboxCombatCatalog
    {
        public static readonly ActionDefinition SplitstaffLight = new ActionDefinition(
            "splitstaff.light.opener",
            25,
            new TickRange(0, 5),
            new TickRange(6, 8),
            new TickRange(9, 10),
            new TickRange(11, 15),
            new TickRange(16, 24),
            10,
            20,
            2400,
            2,
            new TickRange(0, -1));

        public static readonly ActionDefinition SplitstaffHeavy = new ActionDefinition(
            "splitstaff.heavy",
            47,
            new TickRange(0, 14),
            new TickRange(15, 19),
            new TickRange(20, 22),
            new TickRange(23, 31),
            new TickRange(32, 46),
            22,
            45,
            2800,
            4,
            new TickRange(0, -1));

        public static readonly ActionDefinition Parry = new ActionDefinition(
            "shared.parry",
            22,
            new TickRange(0, 2),
            new TickRange(3, 4),
            new TickRange(5, 10),
            new TickRange(11, 14),
            new TickRange(15, 21),
            0,
            0,
            0,
            0,
            new TickRange(0, -1));

        public static readonly ActionDefinition Dodge = new ActionDefinition(
            "shared.dodge",
            27,
            new TickRange(0, 3),
            new TickRange(4, 15),
            new TickRange(0, -1),
            new TickRange(16, 18),
            new TickRange(19, 26),
            0,
            0,
            0,
            0,
            new TickRange(4, 15));
    }
}
