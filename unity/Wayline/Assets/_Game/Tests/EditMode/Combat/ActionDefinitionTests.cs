using System;
using NUnit.Framework;
using Wayline.Combat.Data;

namespace Wayline.Tests.Combat
{
    public sealed class ActionDefinitionTests
    {
        [Test]
        public void LightAttackExposesReadableSemanticPhases()
        {
            var light = GrayboxCombatCatalog.SplitstaffLight;

            Assert.That(light.TotalTicks, Is.EqualTo(25));
            Assert.That(light.PhaseAt(0), Is.EqualTo(ActionPhase.Anticipation));
            Assert.That(light.PhaseAt(6), Is.EqualTo(ActionPhase.Commitment));
            Assert.That(light.PhaseAt(9), Is.EqualTo(ActionPhase.Contact));
            Assert.That(light.PhaseAt(11), Is.EqualTo(ActionPhase.FollowThrough));
            Assert.That(light.PhaseAt(16), Is.EqualTo(ActionPhase.Recovery));
        }

        [Test]
        public void ContactBeforeCommitmentIsRejected()
        {
            Assert.Throws<ArgumentException>(() => new ActionDefinition(
                "invalid.contact-order",
                10,
                new TickRange(0, 1),
                new TickRange(2, 4),
                new TickRange(1, 2),
                new TickRange(5, 6),
                new TickRange(7, 9),
                10,
                10,
                1000,
                2,
                new TickRange(0, -1)));
        }

        [Test]
        public void GapBetweenCommitmentAndContactIsRejected()
        {
            Assert.Throws<ArgumentException>(() => new ActionDefinition(
                "invalid.phase-gap",
                11,
                new TickRange(0, 1),
                new TickRange(2, 4),
                new TickRange(6, 7),
                new TickRange(8, 9),
                new TickRange(10, 10),
                10,
                10,
                1000,
                2,
                new TickRange(0, -1)));
        }
    }
}
