using Wayline.Combat.Data;

namespace Wayline.Combat.Simulation
{
    public sealed class FighterState
    {
        internal FighterState(FighterRuntimeState runtime)
        {
            Side = runtime.Side;
            XMillimeters = runtime.XMillimeters;
            Health = runtime.Health;
            Guard = runtime.Guard;
            Facing = runtime.Facing;
            CurrentAction = runtime.CurrentActionKind;
            ActionTick = runtime.ActionTick;
            Phase = runtime.CurrentDefinition == null
                ? ActionPhase.Rest
                : runtime.CurrentDefinition.PhaseAt(runtime.ActionTick);
            StunTicksRemaining = runtime.StunTicksRemaining;
            GuardActive = runtime.GuardActive;
        }

        public FighterSide Side { get; }

        public int XMillimeters { get; }

        public int YMillimeters => 0;

        public int ZMillimeters => 0;

        public int Health { get; }

        public int Guard { get; }

        public int Facing { get; }

        public CombatAction CurrentAction { get; }

        public int ActionTick { get; }

        public ActionPhase Phase { get; }

        public int StunTicksRemaining { get; }

        public bool GuardActive { get; }
    }

    internal sealed class FighterRuntimeState
    {
        public FighterRuntimeState(FighterSide side, int xMillimeters, int health, int facing)
        {
            Side = side;
            XMillimeters = xMillimeters;
            Health = health;
            Guard = 100;
            Facing = facing;
            CurrentActionKind = CombatAction.None;
        }

        public FighterSide Side { get; }

        public int XMillimeters { get; set; }

        public int Health { get; set; }

        public int Guard { get; set; }

        public int Facing { get; set; }

        public CombatAction CurrentActionKind { get; private set; }

        public ActionDefinition CurrentDefinition { get; private set; }

        public int ActionTick { get; set; }

        public bool HasConnected { get; set; }

        public bool GuardActive { get; set; }

        public int StunTicksRemaining { get; set; }

        public int RelativeDodgeDirection { get; private set; }

        public bool IsKnockedOut => Health == 0;

        public void StartAction(
            CombatAction action,
            ActionDefinition definition,
            int relativeDodgeDirection)
        {
            CurrentActionKind = action;
            CurrentDefinition = definition;
            ActionTick = 0;
            HasConnected = false;
            RelativeDodgeDirection = relativeDodgeDirection;
        }

        public void CancelAction()
        {
            CurrentActionKind = CombatAction.None;
            CurrentDefinition = null;
            ActionTick = 0;
            HasConnected = false;
            RelativeDodgeDirection = 0;
        }
    }
}
