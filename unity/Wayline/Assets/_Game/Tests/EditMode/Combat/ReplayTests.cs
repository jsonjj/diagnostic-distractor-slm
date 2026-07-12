using System;
using System.Security.Cryptography;
using NUnit.Framework;

namespace Wayline.Tests.Combat
{
    public sealed class ReplayTests
    {
        [Test]
        public void TenThousandSeededCommandPairsRemainDeterministicAndBounded()
        {
            var random = new Random(7411);
            var first = CombatFixtures.NewSplitstaffVsLance();
            var second = CombatFixtures.NewSplitstaffVsLance();

            for (var tick = 0; tick < 10000; tick++)
            {
                var player = CombatFixtures.SeededCommand(random);
                var enemy = CombatFixtures.SeededCommand(random);
                first.Step(player, enemy);
                second.Step(player, enemy);

                Assert.That(first.State.Player.Health, Is.InRange(0, 100));
                Assert.That(first.State.Enemy.Health, Is.InRange(0, 100));
                Assert.That(first.State.Player.Guard, Is.InRange(0, 100));
                Assert.That(first.State.Enemy.Guard, Is.InRange(0, 100));
                Assert.That(first.State.Player.YMillimeters, Is.Zero);
                Assert.That(first.State.Player.ZMillimeters, Is.Zero);
                Assert.That(first.State.Enemy.YMillimeters, Is.Zero);
                Assert.That(first.State.Enemy.ZMillimeters, Is.Zero);
                Assert.That(
                    first.State.Enemy.XMillimeters - first.State.Player.XMillimeters,
                    Is.GreaterThanOrEqualTo(600));
            }

            CollectionAssert.AreEqual(first.SerializeSnapshot(), second.SerializeSnapshot());
            using (var sha256 = SHA256.Create())
            {
                var digest = sha256.ComputeHash(first.SerializeSnapshot());
                TestContext.Out.WriteLine(
                    "Wayline replay SHA-256: " +
                    BitConverter.ToString(digest).Replace("-", string.Empty).ToLowerInvariant());
            }
        }
    }
}
