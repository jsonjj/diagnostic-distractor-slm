using System;
using System.Collections.Generic;
using Wayline.Combat.Data;

namespace Wayline.Combat.Simulation
{
    internal static class HitResolver
    {
        public static CombatResult Resolve(
            long tick,
            FighterRuntimeState attacker,
            FighterRuntimeState defender,
            List<CombatEvent> events,
            CombatResult currentResult)
        {
            if (currentResult != CombatResult.InProgress ||
                attacker.CurrentDefinition == null ||
                attacker.HasConnected ||
                (attacker.CurrentActionKind != CombatAction.LightAttack &&
                 attacker.CurrentActionKind != CombatAction.HeavyAttack) ||
                attacker.CurrentDefinition.PhaseAt(attacker.ActionTick) != ActionPhase.Contact)
            {
                return currentResult;
            }

            var distance = Math.Abs(attacker.XMillimeters - defender.XMillimeters);
            if (distance > attacker.CurrentDefinition.ReachMillimeters)
                return currentResult;

            attacker.HasConnected = true;
            if (IsInvulnerable(defender))
            {
                events.Add(new CombatEvent(
                    tick,
                    CombatEventKind.Evaded,
                    defender.Side,
                    attacker.Side,
                    0));
                return currentResult;
            }

            if (IsParrying(defender))
            {
                events.Add(new CombatEvent(
                    tick,
                    CombatEventKind.Parried,
                    defender.Side,
                    attacker.Side,
                    0));
                attacker.CancelAction();
                attacker.StunTicksRemaining = 12;
                return currentResult;
            }

            if (defender.GuardActive)
            {
                defender.Guard = Math.Max(
                    0,
                    defender.Guard - attacker.CurrentDefinition.GuardDamage);
                if (defender.Guard == 0)
                {
                    defender.StunTicksRemaining = 20;
                    events.Add(new CombatEvent(
                        tick,
                        CombatEventKind.GuardBreak,
                        attacker.Side,
                        defender.Side,
                        attacker.CurrentDefinition.GuardDamage));
                }
                else
                {
                    events.Add(new CombatEvent(
                        tick,
                        CombatEventKind.Guarded,
                        attacker.Side,
                        defender.Side,
                        attacker.CurrentDefinition.GuardDamage));
                }

                return currentResult;
            }

            var damage = attacker.CurrentDefinition.Damage;
            defender.Health = Math.Max(0, defender.Health - damage);
            defender.CancelAction();
            defender.StunTicksRemaining = defender.Health == 0
                ? 0
                : attacker.CurrentActionKind == CombatAction.HeavyAttack ? 20 : 12;
            events.Add(new CombatEvent(
                tick,
                CombatEventKind.Hit,
                attacker.Side,
                defender.Side,
                damage));
            if (attacker.CurrentDefinition.HitStopTicks > 0)
            {
                events.Add(new CombatEvent(
                    tick,
                    CombatEventKind.HitStop,
                    attacker.Side,
                    defender.Side,
                    attacker.CurrentDefinition.HitStopTicks));
            }

            if (defender.Health > 0)
                return currentResult;

            events.Add(new CombatEvent(
                tick,
                CombatEventKind.Knockout,
                attacker.Side,
                defender.Side,
                0));
            return attacker.Side == FighterSide.Player
                ? CombatResult.PlayerWon
                : CombatResult.EnemyWon;
        }

        private static bool IsInvulnerable(FighterRuntimeState fighter)
        {
            return fighter.CurrentDefinition != null &&
                   fighter.CurrentDefinition.Invulnerability.Contains(fighter.ActionTick);
        }

        private static bool IsParrying(FighterRuntimeState fighter)
        {
            return fighter.CurrentActionKind == CombatAction.Parry &&
                   fighter.CurrentDefinition != null &&
                   fighter.CurrentDefinition.PhaseAt(fighter.ActionTick) == ActionPhase.Contact;
        }
    }
}
