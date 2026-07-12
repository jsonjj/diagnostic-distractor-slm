using System;

namespace Wayline.Combat.Simulation
{
    public enum CombatAction
    {
        None = 0,
        LightAttack = 1,
        HeavyAttack = 2,
        Parry = 3,
        Dodge = 4
    }

    public readonly struct CombatCommand
    {
        public CombatCommand(
            int moveAxis,
            bool isGuarding,
            CombatAction action,
            int relativeDodgeDirection = -1)
        {
            if (moveAxis < -1 || moveAxis > 1)
                throw new ArgumentOutOfRangeException(nameof(moveAxis));
            if (relativeDodgeDirection < -1 || relativeDodgeDirection > 1)
                throw new ArgumentOutOfRangeException(nameof(relativeDodgeDirection));

            MoveAxis = moveAxis;
            IsGuarding = isGuarding;
            Action = action;
            RelativeDodgeDirection = relativeDodgeDirection;
        }

        public int MoveAxis { get; }

        public bool IsGuarding { get; }

        public CombatAction Action { get; }

        public int RelativeDodgeDirection { get; }

        public static CombatCommand None => new CombatCommand(0, false, CombatAction.None);

        public static CombatCommand MoveLeft => new CombatCommand(-1, false, CombatAction.None);

        public static CombatCommand MoveRight => new CombatCommand(1, false, CombatAction.None);

        public static CombatCommand Guard => new CombatCommand(0, true, CombatAction.None);

        public static CombatCommand LightAttack => new CombatCommand(0, false, CombatAction.LightAttack);

        public static CombatCommand HeavyAttack => new CombatCommand(0, false, CombatAction.HeavyAttack);

        public static CombatCommand Parry => new CombatCommand(0, false, CombatAction.Parry);

        public static CombatCommand DodgeBackward => new CombatCommand(0, false, CombatAction.Dodge, -1);
    }
}
