using System;
using NUnit.Framework;
using Wayline.Flow;
using Wayline.Flow.Unity;
using Wayline.Learning.Contracts;

namespace Wayline.Tests.Flow
{
    public sealed class AuthoritativeFlowMapperTests
    {
        private readonly FlowBattle _battle =
            new FlowBattle("valuehold", "valuehold-boss");

        [Test]
        public void BattleAuthorityAloneChoosesRewardOrSeal()
        {
            var cleared = BattleResult(worldCleared: true, sealRequired: false);
            var sealedRoute = BattleResult(worldCleared: false, sealRequired: true);

            Assert.That(
                AuthoritativeFlowMapper.FromBattle(cleared, _battle).NextStep,
                Is.EqualTo(AuthoritativeNextStep.Reward));
            Assert.That(
                AuthoritativeFlowMapper.FromBattle(sealedRoute, _battle).NextStep,
                Is.EqualTo(AuthoritativeNextStep.SealTrial));
        }

        [Test]
        public void SealAuthorityAloneChoosesRetryAssistanceOrReward()
        {
            Assert.That(
                AuthoritativeFlowMapper.FromSeal(
                    SealResult(1, passed: false, cleared: false, assisted: false),
                    _battle).NextStep,
                Is.EqualTo(AuthoritativeNextStep.SealTrial));
            Assert.That(
                AuthoritativeFlowMapper.FromSeal(
                    SealResult(2, passed: false, cleared: false, assisted: true),
                    _battle).NextStep,
                Is.EqualTo(AuthoritativeNextStep.AssistedRoute));
            Assert.That(
                AuthoritativeFlowMapper.FromSeal(
                    SealResult(2, passed: true, cleared: true, assisted: false),
                    _battle).NextStep,
                Is.EqualTo(AuthoritativeNextStep.Reward));
        }

        [Test]
        public void AssistedAuthorityAlwaysRoutesItsCompletedWorldToReward()
        {
            var result = new AssistedRouteCompleted(
                "wayline.v1",
                "complete-assisted-001",
                "valuehold",
                "assisted-aaaaaaaaaaaaaaaaaaaaaaaa",
                1,
                2,
                2,
                true,
                new[]
                {
                    AssistedItem("item-supported-001", "opt-supported-001-a"),
                    AssistedItem("item-supported-002", "opt-supported-002-a")
                });

            var mapped = AuthoritativeFlowMapper.FromAssisted(result, _battle);

            Assert.That(mapped.Stage, Is.EqualTo(FlowTrialStage.Assisted));
            Assert.That(mapped.NextStep, Is.EqualTo(AuthoritativeNextStep.Reward));
        }

        private BattleCompleted BattleResult(bool worldCleared, bool sealRequired)
        {
            return new BattleCompleted(
                "wayline.v1",
                "complete-battle-001",
                _battle.WorldId,
                _battle.BattleId,
                "batch-001",
                worldCleared ? 8 : 5,
                8,
                true,
                worldCleared,
                sealRequired);
        }

        private static SealTrialCompleted SealResult(
            int attempt,
            bool passed,
            bool cleared,
            bool assisted)
        {
            return new SealTrialCompleted(
                "wayline.v1",
                "complete-seal-00" + attempt,
                "valuehold",
                attempt,
                "seal-batch-00" + attempt,
                passed ? 3 : 1,
                3,
                passed,
                cleared,
                assisted);
        }

        private static AssistedItemResult AssistedItem(string itemId, string optionId)
        {
            const string method = "Name the digit's place, then write its full value.";
            const string step = "Keep every placeholder zero required by the place.";
            return new AssistedItemResult(
                itemId,
                optionId,
                "600",
                Confidence.Certain,
                optionId,
                "600",
                true,
                null,
                method,
                new[] { step },
                new[] { method, step });
        }
    }
}
