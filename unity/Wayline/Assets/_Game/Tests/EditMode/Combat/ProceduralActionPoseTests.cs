using NUnit.Framework;
using Wayline.Combat.Data;
using Wayline.Combat.Presentation;

namespace Wayline.Tests.Combat
{
    public sealed class ProceduralActionPoseTests
    {
        [Test]
        public void IdenticalPhaseInputsProduceIdenticalPoseState()
        {
            var first = ProceduralActionPoseEvaluator.Evaluate(
                GrayboxCombatCatalog.SplitstaffHeavy,
                20,
                1);
            var second = ProceduralActionPoseEvaluator.Evaluate(
                GrayboxCombatCatalog.SplitstaffHeavy,
                20,
                1);

            Assert.That(first.Phase, Is.EqualTo(second.Phase));
            Assert.That(first.BodyTravelMillimeters, Is.EqualTo(second.BodyTravelMillimeters));
            Assert.That(first.TorsoLeanMillidegrees, Is.EqualTo(second.TorsoLeanMillidegrees));
            Assert.That(first.WeaponProgressPermille, Is.EqualTo(second.WeaponProgressPermille));
            Assert.That(first.CompressionPermille, Is.EqualTo(second.CompressionPermille));
            Assert.That(first.SecondarySettlePermille, Is.EqualTo(second.SecondarySettlePermille));
        }

        [Test]
        public void AnticipationOpposesCommitmentAndContactCarriesTheStrongestAccent()
        {
            var action = GrayboxCombatCatalog.SplitstaffLight;
            var anticipation = ProceduralActionPoseEvaluator.Evaluate(action, 5, 1);
            var commitment = ProceduralActionPoseEvaluator.Evaluate(action, 8, 1);
            var contact = ProceduralActionPoseEvaluator.Evaluate(action, 9, 1);

            Assert.That(anticipation.BodyTravelMillimeters, Is.LessThan(0));
            Assert.That(anticipation.TorsoLeanMillidegrees, Is.LessThan(0));
            Assert.That(commitment.BodyTravelMillimeters, Is.GreaterThan(0));
            Assert.That(commitment.TorsoLeanMillidegrees, Is.GreaterThan(0));
            Assert.That(contact.CompressionPermille, Is.GreaterThan(commitment.CompressionPermille));
            Assert.That(contact.Phase, Is.EqualTo(ActionPhase.Contact));
        }

        [Test]
        public void SecondaryMotionSettlesLaterWithoutLeadingThePrimaryMass()
        {
            var action = GrayboxCombatCatalog.SplitstaffLight;
            var anticipation = ProceduralActionPoseEvaluator.Evaluate(action, 3, 1);
            var recoveryMiddle = ProceduralActionPoseEvaluator.Evaluate(action, 20, 1);
            var recoveryEnd = ProceduralActionPoseEvaluator.Evaluate(action, 24, 1);

            Assert.That(anticipation.SecondarySettlePermille, Is.Zero);
            Assert.That(
                recoveryMiddle.SecondarySettlePermille,
                Is.GreaterThan(recoveryMiddle.PrimarySettlePermille));
            Assert.That(recoveryEnd.PrimarySettlePermille, Is.Zero);
            Assert.That(recoveryEnd.SecondarySettlePermille, Is.Zero);
        }

        [Test]
        public void RecoveryBeginsAtTheAuthoredFollowThroughLeanWithoutOverflow()
        {
            var recovery = ProceduralActionPoseEvaluator.Evaluate(
                GrayboxCombatCatalog.SplitstaffLight,
                16,
                1);

            Assert.That(recovery.Phase, Is.EqualTo(ActionPhase.Recovery));
            Assert.That(recovery.BodyTravelMillimeters, Is.EqualTo(260));
            Assert.That(recovery.TorsoLeanMillidegrees, Is.EqualTo(5000));
        }
    }
}
