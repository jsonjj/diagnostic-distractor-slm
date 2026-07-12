using System;
using System.Linq;
using NUnit.Framework;
using Wayline.Flow;
using Wayline.Flow.Authority;
using Wayline.Learning.Contracts;

namespace Wayline.Tests.Flow
{
    public sealed class AuthoritativeProgressionMapperTests
    {
        private readonly FlowBattle _normalBattle =
            new FlowBattle("valuehold", "valuehold_route_1");
        private readonly FlowBattle _bossBattle =
            new FlowBattle("valuehold", "valuehold_boss");

        [Test]
        public void NormalAndClearedBossMapToRewardWhileMissedBossMapsToSeal()
        {
            var normal = AuthoritativeProgressionMapper.FromBattle(
                _normalBattle,
                "batch-normal-001",
                BattleCommand("complete-normal-001"),
                BattleResponse(
                    "complete-normal-001",
                    _normalBattle,
                    "batch-normal-001",
                    boss: false,
                    worldCleared: false,
                    sealRequired: false));
            var clearedBoss = AuthoritativeProgressionMapper.FromBattle(
                _bossBattle,
                "batch-boss-clear-001",
                BattleCommand("complete-boss-clear-001"),
                BattleResponse(
                    "complete-boss-clear-001",
                    _bossBattle,
                    "batch-boss-clear-001",
                    boss: true,
                    worldCleared: true,
                    sealRequired: false));
            var missedBoss = AuthoritativeProgressionMapper.FromBattle(
                _bossBattle,
                "batch-boss-miss-001",
                BattleCommand("complete-boss-miss-001"),
                BattleResponse(
                    "complete-boss-miss-001",
                    _bossBattle,
                    "batch-boss-miss-001",
                    boss: true,
                    worldCleared: false,
                    sealRequired: true));

            Assert.That(normal.Stage, Is.EqualTo(FlowTrialStage.Normal));
            Assert.That(normal.NextStep, Is.EqualTo(AuthoritativeNextStep.Reward));
            Assert.That(clearedBoss.NextStep, Is.EqualTo(AuthoritativeNextStep.Reward));
            Assert.That(missedBoss.NextStep, Is.EqualTo(AuthoritativeNextStep.SealTrial));
        }

        [Test]
        public void SealPassRetryAndAssistedUnlockMapOnlyFromServerFlags()
        {
            var passed = AuthoritativeProgressionMapper.FromSeal(
                _bossBattle,
                1,
                "seal-batch-pass-001",
                SealCommand("complete-seal-pass-001"),
                SealResponse(
                    "complete-seal-pass-001",
                    1,
                    "seal-batch-pass-001",
                    finalCorrect: 2,
                    assisted: false));
            var retry = AuthoritativeProgressionMapper.FromSeal(
                _bossBattle,
                1,
                "seal-batch-retry-001",
                SealCommand("complete-seal-retry-001"),
                SealResponse(
                    "complete-seal-retry-001",
                    1,
                    "seal-batch-retry-001",
                    finalCorrect: 1,
                    assisted: false));
            var assisted = AuthoritativeProgressionMapper.FromSeal(
                _bossBattle,
                2,
                "seal-batch-assisted-001",
                SealCommand("complete-seal-assisted-001"),
                SealResponse(
                    "complete-seal-assisted-001",
                    2,
                    "seal-batch-assisted-001",
                    finalCorrect: 1,
                    assisted: true));

            Assert.That(passed.Stage, Is.EqualTo(FlowTrialStage.Seal));
            Assert.That(passed.NextStep, Is.EqualTo(AuthoritativeNextStep.Reward));
            Assert.That(retry.NextStep, Is.EqualTo(AuthoritativeNextStep.SealTrial));
            Assert.That(assisted.NextStep, Is.EqualTo(AuthoritativeNextStep.AssistedRoute));
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        public void AssistedCompletionMapsEveryScoreToReward(int finalCorrect)
        {
            var command = AssistedCommand("complete-assisted-001", finalCorrect);
            var response = AssistedResponse("complete-assisted-001", finalCorrect);

            var completion = AuthoritativeProgressionMapper.FromAssisted(
                _bossBattle,
                "assisted-route-001",
                command,
                response);

            Assert.That(completion.Stage, Is.EqualTo(FlowTrialStage.Assisted));
            Assert.That(completion.NextStep, Is.EqualTo(AuthoritativeNextStep.Reward));
        }

        [TestCase("request")]
        [TestCase("world")]
        [TestCase("battle")]
        [TestCase("batch")]
        public void BattleIdentityMismatchIsRejected(string mismatch)
        {
            var command = BattleCommand("complete-battle-001");
            var response = BattleResponse(
                mismatch == "request" ? "different-request-001" : "complete-battle-001",
                new FlowBattle(
                    mismatch == "world" ? "decimara" : _normalBattle.WorldId,
                    mismatch == "battle" ? "valuehold_route_2" : _normalBattle.BattleId),
                mismatch == "batch" ? "different-batch-001" : "batch-001",
                boss: false,
                worldCleared: false,
                sealRequired: false);

            Assert.That(
                () => AuthoritativeProgressionMapper.FromBattle(
                    _normalBattle,
                    "batch-001",
                    command,
                    response),
                Throws.ArgumentException);
        }

        [TestCase("request")]
        [TestCase("world")]
        [TestCase("batch")]
        [TestCase("attempt")]
        public void SealIdentityMismatchIsRejected(string mismatch)
        {
            var command = SealCommand("complete-seal-001");
            var response = new SealTrialCompleted(
                "wayline.v1",
                mismatch == "request" ? "different-request-001" : "complete-seal-001",
                mismatch == "world" ? "decimara" : "valuehold",
                mismatch == "attempt" ? 2 : 1,
                mismatch == "batch" ? "different-batch-001" : "seal-batch-001",
                1,
                3,
                false,
                false,
                mismatch == "attempt");

            Assert.That(
                () => AuthoritativeProgressionMapper.FromSeal(
                    _bossBattle,
                    1,
                    "seal-batch-001",
                    command,
                    response),
                Throws.ArgumentException);
        }

        [TestCase("request")]
        [TestCase("world")]
        [TestCase("route")]
        public void AssistedIdentityMismatchIsRejected(string mismatch)
        {
            var command = AssistedCommand("complete-assisted-001", 1);
            var response = AssistedResponse(
                mismatch == "request" ? "different-request-001" : "complete-assisted-001",
                1,
                mismatch == "world" ? "decimara" : "valuehold",
                mismatch == "route" ? "assisted-route-002" : "assisted-route-001");

            Assert.That(
                () => AuthoritativeProgressionMapper.FromAssisted(
                    _bossBattle,
                    "assisted-route-001",
                    command,
                    response),
                Throws.ArgumentException);
        }

        [Test]
        public void AssistedSelectionEchoMismatchIsRejected()
        {
            var command = AssistedCommand("complete-assisted-001", 1);
            var response = AssistedResponse("complete-assisted-001", 1);
            var changed = new AssistedRouteComplete(
                command.SchemaVersion,
                command.RequestId,
                command.SessionId,
                new[]
                {
                    command.Selections[0],
                    new AssistedSelection(
                        command.Selections[1].ItemId,
                        "different-option-001",
                        command.Selections[1].Confidence)
                });

            Assert.That(
                () => AuthoritativeProgressionMapper.FromAssisted(
                    _bossBattle,
                    "assisted-route-001",
                    changed,
                    response),
                Throws.ArgumentException);
        }

        [Test]
        public void ItemCountAndImpossibleFlagCombinationsAreRejected()
        {
            var command = BattleCommand("complete-battle-001");
            var invalidCount = new BattleCompleted(
                "wayline.v1",
                command.RequestId,
                _normalBattle.WorldId,
                _normalBattle.BattleId,
                "batch-001",
                2,
                2,
                false,
                false,
                false);
            var impossibleBoss = BattleResponse(
                command.RequestId,
                _bossBattle,
                "batch-001",
                boss: true,
                worldCleared: false,
                sealRequired: false);

            Assert.That(
                () => AuthoritativeProgressionMapper.FromBattle(
                    _normalBattle,
                    "batch-001",
                    command,
                    invalidCount),
                Throws.Exception);
            Assert.That(
                () => AuthoritativeProgressionMapper.FromBattle(
                    _bossBattle,
                    "batch-001",
                    command,
                    impossibleBoss),
                Throws.ArgumentException);
        }

        [Test]
        public void NonVictoryCommandCannotEnterPostCombatProgression()
        {
            var command = new BattleComplete(
                "wayline.v1",
                "complete-battle-001",
                "session-001",
                false);
            var response = BattleResponse(
                command.RequestId,
                _normalBattle,
                "batch-001",
                boss: false,
                worldCleared: false,
                sealRequired: false);

            Assert.That(
                () => AuthoritativeProgressionMapper.FromBattle(
                    _normalBattle,
                    "batch-001",
                    command,
                    response),
                Throws.ArgumentException);
        }

        [Test]
        public void ReceiptIsStableLowercaseAndChangesWithAnyMaterialResponseChange()
        {
            var command = BattleCommand("complete-battle-001");
            var firstResponse = BattleResponse(
                command.RequestId,
                _normalBattle,
                "batch-001",
                boss: false,
                worldCleared: false,
                sealRequired: false,
                finalCorrect: 2);
            var changedResponse = BattleResponse(
                command.RequestId,
                _normalBattle,
                "batch-001",
                boss: false,
                worldCleared: false,
                sealRequired: false,
                finalCorrect: 3);

            var first = AuthoritativeProgressionMapper.FromBattle(
                _normalBattle,
                "batch-001",
                command,
                firstResponse);
            var replay = AuthoritativeProgressionMapper.FromBattle(
                _normalBattle,
                "batch-001",
                command,
                firstResponse);
            var changed = AuthoritativeProgressionMapper.FromBattle(
                _normalBattle,
                "batch-001",
                command,
                changedResponse);

            Assert.That(first.CompletionId, Is.EqualTo(command.RequestId));
            Assert.That(first.AuthorityReceiptId, Is.EqualTo(replay.AuthorityReceiptId));
            Assert.That(first.AuthorityReceiptId,
                Does.Match("^wayline\\.progression\\.v1:[0-9a-f]{64}$"));
            Assert.That(changed.AuthorityReceiptId, Is.Not.EqualTo(first.AuthorityReceiptId));
        }

        private static BattleComplete BattleCommand(string requestId)
        {
            return new BattleComplete("wayline.v1", requestId, "session-001", true);
        }

        private static BattleCompleted BattleResponse(
            string requestId,
            FlowBattle battle,
            string batchId,
            bool boss,
            bool worldCleared,
            bool sealRequired,
            int finalCorrect = 3)
        {
            var itemCount = boss ? 8 : 3;
            return new BattleCompleted(
                "wayline.v1",
                requestId,
                battle.WorldId,
                battle.BattleId,
                batchId,
                finalCorrect,
                itemCount,
                boss,
                worldCleared,
                sealRequired);
        }

        private static SealTrialComplete SealCommand(string requestId)
        {
            return new SealTrialComplete("wayline.v1", requestId, "session-001");
        }

        private static SealTrialCompleted SealResponse(
            string requestId,
            int attempt,
            string batchId,
            int finalCorrect,
            bool assisted)
        {
            var passed = finalCorrect >= 2;
            return new SealTrialCompleted(
                "wayline.v1",
                requestId,
                "valuehold",
                attempt,
                batchId,
                finalCorrect,
                3,
                passed,
                passed,
                assisted);
        }

        private static AssistedRouteComplete AssistedCommand(
            string requestId,
            int correctCount)
        {
            var response = AssistedResponse(requestId, correctCount);
            return new AssistedRouteComplete(
                "wayline.v1",
                requestId,
                "session-001",
                response.Items.Select(item => new AssistedSelection(
                    item.ItemId,
                    item.SelectedOptionId,
                    item.Confidence)).ToArray());
        }

        private static AssistedRouteCompleted AssistedResponse(
            string requestId,
            int correctCount,
            string worldId = "valuehold",
            string routeId = "assisted-route-001")
        {
            return new AssistedRouteCompleted(
                "wayline.v1",
                requestId,
                worldId,
                routeId,
                1,
                2,
                correctCount,
                true,
                new[]
                {
                    AssistedItem(1, correctCount >= 1),
                    AssistedItem(2, correctCount >= 2)
                });
        }

        private static AssistedItemResult AssistedItem(int index, bool correct)
        {
            var itemId = "supported-item-00" + index;
            var selectedOption = "selected-option-00" + index;
            var correctOption = correct ? selectedOption : "correct-option-00" + index;
            var selectedAnswer = correct ? "600" : "60";
            const string correctAnswer = "600";
            const string possibleError =
                "This answer can come from reading the tens place instead of the hundreds place.";
            const string reliableMethod =
                "Name the digit's place, then write its full value.";
            var steps = new[] { "The digit is in the hundreds place." };
            var feedback = correct
                ? new[] { reliableMethod, steps[0] }
                : new[] { possibleError, reliableMethod, steps[0] };
            return new AssistedItemResult(
                itemId,
                selectedOption,
                selectedAnswer,
                index == 1 ? Confidence.Certain : Confidence.Leaning,
                correctOption,
                correctAnswer,
                correct,
                correct ? null : possibleError,
                reliableMethod,
                steps,
                feedback);
        }
    }
}
