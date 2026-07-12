using System.Linq;
using NUnit.Framework;
using Wayline.Combat.Simulation;

namespace Wayline.Tests.Combat
{
    public sealed class CombatWorldTests
    {
        [Test]
        public void SameCommandsProduceByteIdenticalSnapshots()
        {
            var first = CombatFixtures.NewSplitstaffVsLance();
            var second = CombatFixtures.NewSplitstaffVsLance();

            foreach (var pair in CombatFixtures.ThreeSecondExchange())
            {
                first.Step(pair.Player, pair.Enemy);
                second.Step(pair.Player, pair.Enemy);
            }

            CollectionAssert.AreEqual(first.SerializeSnapshot(), second.SerializeSnapshot());
        }

        [Test]
        public void AttackConnectsOnceOnlyDuringItsDeclaredContactPhase()
        {
            var world = CombatFixtures.NewSplitstaffVsLance();
            var hitTicks = new System.Collections.Generic.List<long>();

            for (var tick = 0; tick < 30; tick++)
            {
                var events = world.Step(
                    tick == 0 ? CombatCommand.LightAttack : CombatCommand.None,
                    CombatCommand.None);
                hitTicks.AddRange(
                    events.Where(combatEvent => combatEvent.Kind == CombatEventKind.Hit)
                        .Select(combatEvent => combatEvent.Tick));
            }

            Assert.That(hitTicks, Has.Count.EqualTo(1));
            Assert.That(world.State.Enemy.Health, Is.EqualTo(90));
        }

        [Test]
        public void MovementNeverLeavesArenaOrCombatPlaneOrPushboxesOverlapping()
        {
            var world = CombatFixtures.NewSplitstaffVsLance(separationMillimeters: 800);

            for (var tick = 0; tick < 500; tick++)
                world.Step(CombatCommand.MoveRight, CombatCommand.MoveLeft);

            Assert.That(world.State.Player.XMillimeters, Is.InRange(-8000, 8000));
            Assert.That(world.State.Enemy.XMillimeters, Is.InRange(-8000, 8000));
            Assert.That(world.State.Player.YMillimeters, Is.Zero);
            Assert.That(world.State.Player.ZMillimeters, Is.Zero);
            Assert.That(world.State.Enemy.YMillimeters, Is.Zero);
            Assert.That(world.State.Enemy.ZMillimeters, Is.Zero);
            Assert.That(
                world.State.Enemy.XMillimeters - world.State.Player.XMillimeters,
                Is.GreaterThanOrEqualTo(600));
        }

        [Test]
        public void SpawnThatOverlapsAfterArenaClampingIsRejected()
        {
            Assert.Throws<System.ArgumentException>(() => CombatWorld.CreateGraybox(
                100,
                100,
                -9000,
                -8000));
        }
    }
}
