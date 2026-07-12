using System;
using System.Collections.Generic;
using System.IO;
using Wayline.Combat.Data;

namespace Wayline.Combat.Simulation
{
    public sealed class CombatWorld
    {
        private readonly FighterRuntimeState _player;
        private readonly FighterRuntimeState _enemy;
        private readonly List<CombatEvent> _events = new List<CombatEvent>(4);
        private long _tick;
        private CombatResult _result;

        private CombatWorld(
            int playerHealth,
            int enemyHealth,
            int playerXMillimeters,
            int enemyXMillimeters)
        {
            if (playerHealth < 1 || playerHealth > 100)
                throw new ArgumentOutOfRangeException(nameof(playerHealth));
            if (enemyHealth < 1 || enemyHealth > 100)
                throw new ArgumentOutOfRangeException(nameof(enemyHealth));
            var clampedPlayerX = KinematicMotor.Clamp(playerXMillimeters);
            var clampedEnemyX = KinematicMotor.Clamp(enemyXMillimeters);
            if (clampedEnemyX - clampedPlayerX <
                PushboxResolver.MinimumSeparationMillimeters)
            {
                throw new ArgumentException("Fighters must begin with separated pushboxes.");
            }

            _player = new FighterRuntimeState(
                FighterSide.Player,
                clampedPlayerX,
                playerHealth,
                1);
            _enemy = new FighterRuntimeState(
                FighterSide.Enemy,
                clampedEnemyX,
                enemyHealth,
                -1);
            _result = CombatResult.InProgress;
        }

        public CombatWorldState State => new CombatWorldState(_tick, _player, _enemy, _result);

        public static CombatWorld CreateGraybox(
            int playerHealth,
            int enemyHealth,
            int playerXMillimeters,
            int enemyXMillimeters)
        {
            return new CombatWorld(
                playerHealth,
                enemyHealth,
                playerXMillimeters,
                enemyXMillimeters);
        }

        public IReadOnlyList<CombatEvent> Step(
            CombatCommand playerCommand,
            CombatCommand enemyCommand)
        {
            if (_result != CombatResult.InProgress)
                return Array.Empty<CombatEvent>();

            _events.Clear();
            _tick++;

            PrepareFighter(_player, playerCommand);
            PrepareFighter(_enemy, enemyCommand);
            PushboxResolver.Resolve(_player, _enemy);

            _result = HitResolver.Resolve(
                _tick,
                _player,
                _enemy,
                _events,
                _result);
            _result = HitResolver.Resolve(
                _tick,
                _enemy,
                _player,
                _events,
                _result);

            AdvanceAction(_player);
            AdvanceAction(_enemy);
            return _events.ToArray();
        }

        public byte[] SerializeSnapshot()
        {
            using (var stream = new MemoryStream(80))
            using (var writer = new BinaryWriter(stream))
            {
                writer.Write(_tick);
                writer.Write((int)_result);
                WriteFighter(writer, _player);
                WriteFighter(writer, _enemy);
                writer.Flush();
                return stream.ToArray();
            }
        }

        private static void PrepareFighter(
            FighterRuntimeState fighter,
            CombatCommand command)
        {
            fighter.GuardActive = false;
            if (fighter.IsKnockedOut)
                return;
            if (fighter.StunTicksRemaining > 0)
            {
                fighter.StunTicksRemaining--;
                return;
            }

            if (fighter.CurrentDefinition == null)
            {
                if (command.Action != CombatAction.None)
                {
                    fighter.StartAction(
                        command.Action,
                        DefinitionFor(command.Action),
                        command.RelativeDodgeDirection);
                }
                else if (command.IsGuarding && fighter.Guard > 0)
                {
                    fighter.GuardActive = true;
                }
                else
                {
                    KinematicMotor.Walk(fighter, command.MoveAxis);
                }
            }

            if (fighter.CurrentActionKind == CombatAction.Dodge &&
                fighter.CurrentDefinition.PhaseAt(fighter.ActionTick) == ActionPhase.Commitment)
            {
                KinematicMotor.Dodge(fighter);
            }
        }

        private static ActionDefinition DefinitionFor(CombatAction action)
        {
            switch (action)
            {
                case CombatAction.LightAttack:
                    return GrayboxCombatCatalog.SplitstaffLight;
                case CombatAction.HeavyAttack:
                    return GrayboxCombatCatalog.SplitstaffHeavy;
                case CombatAction.Parry:
                    return GrayboxCombatCatalog.Parry;
                case CombatAction.Dodge:
                    return GrayboxCombatCatalog.Dodge;
                default:
                    throw new ArgumentOutOfRangeException(nameof(action));
            }
        }

        private static void AdvanceAction(FighterRuntimeState fighter)
        {
            if (fighter.CurrentDefinition == null)
                return;
            fighter.ActionTick++;
            if (fighter.ActionTick >= fighter.CurrentDefinition.TotalTicks)
                fighter.CancelAction();
        }

        private static void WriteFighter(BinaryWriter writer, FighterRuntimeState fighter)
        {
            writer.Write((int)fighter.Side);
            writer.Write(fighter.XMillimeters);
            writer.Write(fighter.Health);
            writer.Write(fighter.Guard);
            writer.Write(fighter.Facing);
            writer.Write((int)fighter.CurrentActionKind);
            writer.Write(fighter.ActionTick);
            writer.Write(fighter.HasConnected);
            writer.Write(fighter.GuardActive);
            writer.Write(fighter.StunTicksRemaining);
            writer.Write(fighter.RelativeDodgeDirection);
        }
    }
}
