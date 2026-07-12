using System.Linq;
using NUnit.Framework;
using Wayline.Combat.Simulation;

namespace Wayline.Tests.Combat
{
    public sealed class HitResolverTests
    {
        [Test]
        public void GuardPreventsHealthDamageAndConsumesGuard()
        {
            var world = CombatFixtures.NewSplitstaffVsLance();
            var events = RunSynchronized(world, CombatCommand.LightAttack, CombatCommand.Guard);

            Assert.That(events.Any(combatEvent => combatEvent.Kind == CombatEventKind.Guarded), Is.True);
            Assert.That(world.State.Enemy.Health, Is.EqualTo(100));
            Assert.That(world.State.Enemy.Guard, Is.EqualTo(80));
        }

        [Test]
        public void ParryResolvesBeforeGuardOrDamage()
        {
            var world = CombatFixtures.NewSplitstaffVsLance();
            var events = RunSynchronized(world, CombatCommand.LightAttack, CombatCommand.Parry);

            Assert.That(events.Any(combatEvent => combatEvent.Kind == CombatEventKind.Parried), Is.True);
            Assert.That(events.Any(combatEvent => combatEvent.Kind == CombatEventKind.Hit), Is.False);
            Assert.That(world.State.Enemy.Health, Is.EqualTo(100));
        }

        [Test]
        public void DodgeInvulnerabilityResolvesBeforeParryGuardOrDamage()
        {
            var world = CombatFixtures.NewSplitstaffVsLance();
            var events = RunSynchronized(
                world,
                CombatCommand.LightAttack,
                CombatCommand.DodgeBackward);

            Assert.That(events.Any(combatEvent => combatEvent.Kind == CombatEventKind.Evaded), Is.True);
            Assert.That(world.State.Enemy.Health, Is.EqualTo(100));
        }

        [Test]
        public void ZeroHealthEmitsKnockoutAndLocksTheResult()
        {
            var world = CombatFixtures.NewSplitstaffVsLance(enemyHealth: 10);
            var events = RunSynchronized(world, CombatCommand.LightAttack, CombatCommand.None);

            Assert.That(events.Any(combatEvent => combatEvent.Kind == CombatEventKind.Knockout), Is.True);
            Assert.That(world.State.Enemy.Health, Is.Zero);
            Assert.That(world.State.Result, Is.EqualTo(CombatResult.PlayerWon));

            world.Step(CombatCommand.HeavyAttack, CombatCommand.HeavyAttack);
            Assert.That(world.State.Enemy.Health, Is.Zero);
        }

        [Test]
        public void DepletedGuardEmitsGuardBreakInsteadOfAnotherGuardedHit()
        {
            var world = CombatFixtures.NewSplitstaffVsLance();
            var events = new System.Collections.Generic.List<CombatEvent>();

            for (var attack = 0; attack < 5; attack++)
            {
                for (var tick = 0; tick < 30; tick++)
                {
                    events.AddRange(world.Step(
                        tick == 0 ? CombatCommand.LightAttack : CombatCommand.None,
                        CombatCommand.Guard));
                }
            }

            Assert.That(world.State.Enemy.Guard, Is.Zero);
            Assert.That(
                events.Count(combatEvent => combatEvent.Kind == CombatEventKind.GuardBreak),
                Is.EqualTo(1));
            Assert.That(
                events.Last(combatEvent =>
                    combatEvent.Kind == CombatEventKind.Guarded ||
                    combatEvent.Kind == CombatEventKind.GuardBreak).Kind,
                Is.EqualTo(CombatEventKind.GuardBreak));
        }

        [Test]
        public void HitEmitsSemanticPresentationHitStopFromActionData()
        {
            var world = CombatFixtures.NewSplitstaffVsLance();
            var events = RunSynchronized(
                world,
                CombatCommand.LightAttack,
                CombatCommand.None);

            Assert.That(
                events.Single(combatEvent => combatEvent.Kind == CombatEventKind.HitStop).Amount,
                Is.EqualTo(2));
        }

        [Test]
        public void BrokenGuardCannotAbsorbOrBreakAgainWithoutRecovery()
        {
            var world = CombatFixtures.NewSplitstaffVsLance();
            var events = new System.Collections.Generic.List<CombatEvent>();

            for (var attack = 0; attack < 6; attack++)
            {
                for (var tick = 0; tick < 30; tick++)
                {
                    events.AddRange(world.Step(
                        tick == 0 ? CombatCommand.LightAttack : CombatCommand.None,
                        CombatCommand.Guard));
                }
            }

            Assert.That(
                events.Count(combatEvent => combatEvent.Kind == CombatEventKind.GuardBreak),
                Is.EqualTo(1));
            Assert.That(
                events.Count(combatEvent => combatEvent.Kind == CombatEventKind.Hit),
                Is.EqualTo(1));
            Assert.That(world.State.Enemy.Health, Is.EqualTo(90));
        }

        private static System.Collections.Generic.List<CombatEvent> RunSynchronized(
            CombatWorld world,
            CombatCommand playerStart,
            CombatCommand enemyHeld)
        {
            var events = new System.Collections.Generic.List<CombatEvent>();
            for (var tick = 0; tick < 30; tick++)
            {
                events.AddRange(world.Step(
                    tick == 0 ? playerStart : CombatCommand.None,
                    enemyHeld));
            }

            return events;
        }
    }
}
