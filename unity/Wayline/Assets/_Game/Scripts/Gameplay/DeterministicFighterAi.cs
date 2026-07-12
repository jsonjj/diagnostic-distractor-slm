using System;
using Wayline.Combat.Simulation;

namespace Wayline.Gameplay
{
    public sealed class DeterministicFighterAi : ICombatCommandSource
    {
        private readonly int _reactionTicks;
        private uint _randomState;
        private long _nextDecisionTick;
        private CombatCommand _heldCommand;

        public DeterministicFighterAi(int seed, int reactionTicks)
        {
            if (reactionTicks < 1 || reactionTicks > 60)
                throw new ArgumentOutOfRangeException(nameof(reactionTicks));

            _reactionTicks = reactionTicks;
            _randomState = seed == 0 ? 0x9e3779b9u : unchecked((uint)seed);
            _heldCommand = CombatCommand.None;
        }

        public int DecisionsMade { get; private set; }

        public CombatCommand NextCommand(CombatWorldState state, FighterSide side)
        {
            if (state == null)
                throw new ArgumentNullException(nameof(state));
            if (state.Result != CombatResult.InProgress)
                return CombatCommand.None;

            var self = side == FighterSide.Player ? state.Player : state.Enemy;
            if (self.CurrentAction != CombatAction.None || self.StunTicksRemaining > 0)
                return CombatCommand.None;
            if (state.Tick < _nextDecisionTick)
                return _heldCommand;

            _nextDecisionTick = state.Tick + _reactionTicks;
            DecisionsMade++;
            var distance = state.Enemy.XMillimeters - state.Player.XMillimeters;
            if (distance > 2200)
            {
                _heldCommand = side == FighterSide.Player
                    ? CombatCommand.MoveRight
                    : CombatCommand.MoveLeft;
                return _heldCommand;
            }

            switch (NextRandom() % 5u)
            {
                case 0:
                    _heldCommand = CombatCommand.LightAttack;
                    break;
                case 1:
                    _heldCommand = CombatCommand.HeavyAttack;
                    break;
                case 2:
                    _heldCommand = CombatCommand.Guard;
                    break;
                case 3:
                    _heldCommand = CombatCommand.Parry;
                    break;
                default:
                    _heldCommand = CombatCommand.DodgeBackward;
                    break;
            }

            return _heldCommand;
        }

        private uint NextRandom()
        {
            _randomState = unchecked(_randomState * 1664525u + 1013904223u);
            return _randomState;
        }
    }
}
