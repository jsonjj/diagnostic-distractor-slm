using System;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Wayline.Learning.Contracts;

namespace Wayline.Tests.Learning
{
    public sealed class QuizContractTests
    {
        private const string ValidSubmissionJson =
            "{\"schemaVersion\":\"wayline.v1\",\"requestId\":\"request-001\"," +
            "\"batchId\":\"batch-001\",\"itemCount\":3,\"selections\":[" +
            "{\"itemId\":\"item-001\",\"optionId\":\"opt-001-a\",\"confidence\":\"certain\"}," +
            "{\"itemId\":\"item-002\",\"optionId\":\"opt-002-b\",\"confidence\":\"leaning\"}," +
            "{\"itemId\":\"item-003\",\"optionId\":\"opt-003-c\",\"confidence\":\"guessing\"}]}";

        [Test]
        public void ValidBatchHasNoAnswerKeySurface()
        {
            var json = File.ReadAllText(TestPaths.Contract("valid/three-item-batch.json"));
            var batch = JsonConvert.DeserializeObject<PublicQuizBatch>(json);

            StrictQuizValidator.Validate(batch);

            StringAssert.DoesNotContain(
                "correctAnswer",
                JsonConvert.SerializeObject(batch));
        }

        [Test]
        public void MissingConfidenceFailsBeforeTransport()
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/missing-confidence.json"));

            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<InitialSubmission>(json));
        }

        [TestCase("count-mismatch.json")]
        [TestCase("duplicate-batch-item-id.json")]
        [TestCase("duplicate-display-normalization.json")]
        [TestCase("duplicate-option-id.json")]
        [TestCase("leaked-key.json")]
        [TestCase("unknown-field.json")]
        public void InvalidPublicBatchFixtureIsRejected(string fixture)
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/" + fixture));
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<PublicQuizBatch>(json));
        }

        [TestCase("duplicate-selection-item-id.json")]
        [TestCase("missing-confidence.json")]
        [TestCase("selection-count-mismatch.json")]
        public void InvalidSubmissionFixtureIsRejected(string fixture)
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/" + fixture));
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<InitialSubmission>(json));
        }

        [TestCase("revision-required-mismatch.json")]
        [TestCase("wrong-count-exceeds-item-count.json")]
        public void InvalidWrongCountFixtureIsRejected(string fixture)
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/" + fixture));
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<WrongCountResult>(json));
        }

        [TestCase("duplicate-final-item-id.json")]
        [TestCase("final-aggregate-mismatch.json")]
        [TestCase("final-correctness-key-mismatch.json")]
        [TestCase("final-item-count-mismatch.json")]
        [TestCase("first-correctness-key-mismatch.json")]
        [TestCase("first-wrong-count-mismatch.json")]
        [TestCase("no-revision-selection-changed.json")]
        [TestCase("revision-used-mismatch.json")]
        [TestCase("self-corrected-mismatch.json")]
        public void InvalidFinalResultFixtureIsRejected(string fixture)
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/" + fixture));
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<FinalQuizResult>(json));
        }

        [TestCase("initial-final-identity-mismatch.json")]
        [TestCase("initial-nonzero-with-final.json")]
        [TestCase("initial-zero-without-final.json")]
        public void InvalidInitialResultFixtureIsRejected(string fixture)
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/" + fixture));
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<InitialSubmissionResult>(json));
        }

        [TestCase("gate-latest-correct-exceeds-items.json")]
        [TestCase("gate-mismatch.json")]
        [TestCase("gate-ready-subskills-exceeds-total.json")]
        [TestCase("gate-unmet-requirements-mismatch.json")]
        public void InvalidBossGateFixtureIsRejected(string fixture)
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/" + fixture));
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<BossGateResult>(json));
        }

        [Test]
        public void EveryValidResultFixtureIsAccepted()
        {
            Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<WrongCountResult>(
                File.ReadAllText(TestPaths.Contract("valid/two-wrong-result.json"))));
            Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<FinalQuizResult>(
                File.ReadAllText(TestPaths.Contract("valid/final-result.json"))));
            Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<InitialSubmissionResult>(
                File.ReadAllText(TestPaths.Contract("valid/zero-wrong-initial-result.json"))));
        }

        [Test]
        public void InitialAndRevisionSubmissionsShareStrictInvariants()
        {
            Assert.DoesNotThrow(
                () => StrictQuizValidator.Deserialize<InitialSubmission>(ValidSubmissionJson));
            Assert.DoesNotThrow(
                () => StrictQuizValidator.Deserialize<RevisionSubmission>(ValidSubmissionJson));
        }

        [Test]
        public void ValidBossGateIsAccepted()
        {
            const string json =
                "{\"schemaVersion\":\"wayline.v1\",\"worldId\":\"valuehold\"," +
                "\"unlocked\":true,\"leadInWins\":4,\"requiredLeadInWins\":4," +
                "\"validWorldItems\":16,\"requiredValidWorldItems\":16," +
                "\"latestTenItemCount\":10,\"latestTenCorrectCount\":7," +
                "\"requiredLatestTenCorrectCount\":7,\"coreSubskillCount\":2," +
                "\"readyCoreSubskillCount\":2,\"unmetRequirements\":[]}";

            Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<BossGateResult>(json));
        }

        [Test]
        public void DuplicateJsonMemberIsRejectedBeforeDtoCreation()
        {
            var json = ValidSubmissionJson.Replace(
                "\"requestId\":\"request-001\"",
                "\"requestId\":\"request-001\",\"requestId\":\"request-002\"");

            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<InitialSubmission>(json));
        }

        [Test]
        public void NumericStringIsNotCoercedIntoItemCount()
        {
            var json = ValidSubmissionJson.Replace("\"itemCount\":3", "\"itemCount\":\"3\"");

            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<InitialSubmission>(json));
        }

        [Test]
        public void SealTrialRequestAndPreparedResponseMatchSharedFixtures()
        {
            Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<SealTrialPrepare>(
                File.ReadAllText(TestPaths.Contract("valid/seal-trial-prepare.json"))));
            Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<SealTrialPrepared>(
                File.ReadAllText(TestPaths.Contract("valid/seal-trial-prepared.json"))));
        }

        [Test]
        public void SealTrialBattleIdentityMustMatchWorldAndAttempt()
        {
            var json = File.ReadAllText(
                TestPaths.Contract("invalid/seal-trial-battle-id-mismatch.json"));

            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<SealTrialPrepared>(json));
        }

        [Test]
        public void SealTrialPreparedBatchContainsExactlyThreeItems()
        {
            var json = File.ReadAllText(
                TestPaths.Contract("invalid/seal-trial-four-items.json"));

            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<SealTrialPrepared>(json));
        }

        [Test]
        public void EveryStableProgressionFixtureIsAccepted()
        {
            Assert.DoesNotThrow(() => DeserializeFixture<BattleComplete>("battle-complete.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<BattleCompleted>("battle-completed.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<SealTrialComplete>("seal-trial-complete.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<SealTrialCompleted>("seal-trial-completed.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<SecondWindStart>("second-wind-start.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<SecondWindStarted>("second-wind-started.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<SecondWindComplete>("second-wind-complete.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<SecondWindCompleted>("second-wind-completed.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<RevivedCombatComplete>("revived-combat-complete.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<RevivedCombatCompleted>("revived-combat-completed.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<WorldActivate>("world-activate.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<WorldActivated>("world-activated.json"));
        }

        [Test]
        public void EverySharedProgressionSemanticInvalidFixtureIsRejected()
        {
            AssertInvalidFixture<BattleCompleted>("battle-completed-count-mismatch.json");
            AssertInvalidFixture<SealTrialCompleted>("seal-trial-completed-pass-mismatch.json");
            AssertInvalidFixture<SecondWindStarted>("second-wind-started-identity-mismatch.json");
            AssertInvalidFixture<SecondWindCompleted>("second-wind-completed-shield-mismatch.json");
            AssertInvalidFixture<RevivedCombatCompleted>("revived-combat-completed-state-mismatch.json");
            AssertInvalidFixture<WorldActivated>("world-activated-same-world.json");
        }

        [Test]
        public void ProgressionOutcomesMatchServerSemanticRules()
        {
            AssertMutationRejected<BattleCompleted>(
                "battle-completed.json",
                value => value["worldCleared"] = true);
            AssertMutationRejected<BattleCompleted>(
                "battle-completed.json",
                value =>
                {
                    value["bossBattle"] = true;
                    value["worldCleared"] = true;
                    value["sealTrialRequired"] = true;
                });
            AssertMutationRejected<SealTrialCompleted>(
                "seal-trial-completed.json",
                value => value["worldCleared"] = false);
            AssertMutationRejected<SealTrialCompleted>(
                "seal-trial-completed.json",
                value => value["assistedRouteUnlocked"] = true);
            AssertMutationRejected<SecondWindStarted>(
                "second-wind-started.json",
                AddFourthSecondWindItem);
        }

        [Test]
        public void ProgressionSchemaConstantsAndRangesAreEnforced()
        {
            AssertMutationRejected<BattleCompleted>(
                "battle-completed.json",
                value => value["finalCorrect"] = -1);
            AssertMutationRejected<BattleCompleted>(
                "battle-completed.json",
                value => value["itemCount"] = 11);
            AssertMutationRejected<SealTrialCompleted>(
                "seal-trial-completed.json",
                value => value["attemptNumber"] = 0);
            AssertMutationRejected<SealTrialCompleted>(
                "seal-trial-completed.json",
                value => value["itemCount"] = 2);
            AssertMutationRejected<SecondWindCompleted>(
                "second-wind-completed.json",
                value => value["reviveHealthPercent"] = 34);
            AssertMutationRejected<SecondWindCompleted>(
                "second-wind-completed.json",
                value => value["revivedCombatPending"] = false);
            AssertMutationRejected<RevivedCombatCompleted>(
                "revived-combat-completed.json",
                value => value["secondWindClosed"] = false);
            AssertMutationRejected<WorldActivated>(
                "world-activated.json",
                value => value["campaignSequence"] = 1);
            AssertMutationRejected<WorldActivated>(
                "world-activated.json",
                value => value["campaignSequence"] = 10);
        }

        [Test]
        public void ProgressionCommandBodiesRejectServerOwnedIdentity()
        {
            AssertMutationRejected<BattleComplete>(
                "battle-complete.json",
                value => value["worldId"] = "forged-world");
            AssertMutationRejected<SealTrialComplete>(
                "seal-trial-complete.json",
                value => value["batchId"] = "forged-batch");
            AssertMutationRejected<SecondWindStart>(
                "second-wind-start.json",
                value => value["combatAttemptId"] = "forged-attempt");
            AssertMutationRejected<SecondWindComplete>(
                "second-wind-complete.json",
                value => value["secondWindId"] = "forged-wind");
            AssertMutationRejected<RevivedCombatComplete>(
                "revived-combat-complete.json",
                value => value["battleId"] = "forged-battle");
            AssertMutationRejected<WorldActivate>(
                "world-activate.json",
                value => value["completedWorldId"] = "forged-world");
        }

        [Test]
        public void AssistedRouteFixturesMatchTheFrozenWireContract()
        {
            Assert.DoesNotThrow(() => DeserializeFixture<AssistedRoutePrepare>(
                "assisted-route-prepare.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<AssistedRoutePrepared>(
                "assisted-route-prepared.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<AssistedRouteComplete>(
                "assisted-route-complete.json"));
            Assert.DoesNotThrow(() => DeserializeFixture<AssistedRouteCompleted>(
                "assisted-route-completed.json"));
        }

        [TestCase("assisted-route-complete-duplicate-item.json", typeof(AssistedRouteComplete))]
        [TestCase("assisted-route-completed-correctness-mismatch.json", typeof(AssistedRouteCompleted))]
        [TestCase("assisted-route-completed-count-mismatch.json", typeof(AssistedRouteCompleted))]
        [TestCase("assisted-route-prepared-mcq-key-leak.json", typeof(AssistedRoutePrepared))]
        [TestCase("assisted-route-prepared-world-mismatch.json", typeof(AssistedRoutePrepared))]
        public void InvalidAssistedRouteFixtureIsRejected(string fixture, Type contractType)
        {
            var method = typeof(StrictQuizValidator)
                .GetMethod(nameof(StrictQuizValidator.Deserialize))
                .MakeGenericMethod(contractType);
            var error = Assert.Throws<System.Reflection.TargetInvocationException>(() =>
                method.Invoke(null, new object[]
                {
                    File.ReadAllText(TestPaths.Contract("invalid/" + fixture))
                }));
            Assert.That(error.InnerException, Is.TypeOf<JsonSerializationException>());
        }

        [Test]
        public void AssistedSupportedItemsHaveNoAnswerKeyOrDiagnosisSurface()
        {
            var prepared = DeserializeFixture<AssistedRoutePrepared>(
                "assisted-route-prepared.json");
            var json = JsonConvert.SerializeObject(prepared.Batch.Items);

            StringAssert.DoesNotContain("sourceBatchId", json);
            StringAssert.DoesNotContain("correctOptionId", json);
            StringAssert.DoesNotContain("correctAnswer", json);
            StringAssert.DoesNotContain("procedureId", json);
            StringAssert.DoesNotContain("possibleError", json);
            StringAssert.DoesNotContain("reliableMethod", json);
            StringAssert.DoesNotContain("trustedSteps", json);
        }

        [Test]
        public void AssistedRouteCardinalitiesAndCanonicalFeedbackAreStrict()
        {
            AssertMutationRejected<AssistedRoutePrepared>(
                "assisted-route-prepared.json",
                value => ((JArray)value["batch"]["items"]).RemoveAt(1));
            AssertMutationRejected<AssistedRouteComplete>(
                "assisted-route-complete.json",
                value => ((JArray)value["selections"]).RemoveAt(1));
            AssertMutationRejected<AssistedRouteCompleted>(
                "assisted-route-completed.json",
                value =>
                {
                    var feedback = (JArray)value["items"][1]["canonicalFeedback"];
                    var first = feedback[0].Value<string>();
                    feedback[0] = feedback[1].Value<string>();
                    feedback[1] = first;
                });
        }

        [Test]
        public void BattleQuizRequestUsesTheFrozenTierVocabulary()
        {
            const string json =
                "{\"schemaVersion\":\"wayline.v1\",\"requestId\":\"prepare-001\"," +
                "\"sessionId\":\"session-001\",\"battleId\":\"valuehold_route_1\"," +
                "\"worldId\":\"valuehold\",\"battleTier\":\"route_1\"}";

            var request = StrictQuizValidator.Deserialize<BattleQuizRequest>(json);

            Assert.That(request.BattleTier, Is.EqualTo(BattleTier.Route1));
            Assert.Throws<JsonSerializationException>(() =>
                StrictQuizValidator.Deserialize<BattleQuizRequest>(
                    json.Replace("\"route_1\"", "\"unknown\"")));
        }

        [Test]
        public void RevisionOpenSnapshotRestoresOnlyPublicAndSubmittedState()
        {
            var snapshot = new JObject
            {
                ["schemaVersion"] = "wayline.v1",
                ["batchId"] = "batch-001",
                ["quizState"] = "revision_open",
                ["stateVersion"] = 3,
                ["publicBatch"] = JObject.Parse(
                    File.ReadAllText(TestPaths.Contract("valid/three-item-batch.json"))),
                ["initialSubmission"] = JObject.Parse(ValidSubmissionJson),
                ["initialResult"] = NonzeroInitialResultToken(),
                ["revisionSubmission"] = JValue.CreateNull(),
                ["finalResult"] = JValue.CreateNull()
            };

            var restored = StrictQuizValidator.Deserialize<QuizSnapshot>(
                snapshot.ToString(Formatting.None));

            Assert.That(restored.QuizState, Is.EqualTo(QuizSnapshotState.RevisionOpen));
            Assert.That(restored.InitialResult.WrongCount, Is.EqualTo(2));
            Assert.That(restored.FinalResult, Is.Null);
        }

        [Test]
        public void SnapshotStateVersionCannotContradictItsRecords()
        {
            var snapshot = new JObject
            {
                ["schemaVersion"] = "wayline.v1",
                ["batchId"] = "batch-001",
                ["quizState"] = "revision_open",
                ["stateVersion"] = 2,
                ["publicBatch"] = JObject.Parse(
                    File.ReadAllText(TestPaths.Contract("valid/three-item-batch.json"))),
                ["initialSubmission"] = JObject.Parse(ValidSubmissionJson),
                ["initialResult"] = NonzeroInitialResultToken(),
                ["revisionSubmission"] = JValue.CreateNull(),
                ["finalResult"] = JValue.CreateNull()
            };

            Assert.Throws<JsonSerializationException>(() =>
                StrictQuizValidator.Deserialize<QuizSnapshot>(
                    snapshot.ToString(Formatting.None)));
        }

        [Test]
        public void RevealedSnapshotCorrectAnswerMustMatchPublicOptionDisplay()
        {
            var initial = new InitialSubmission(
                "wayline.v1",
                "initial-request-001",
                "batch-001",
                3,
                new[]
                {
                    new SubmissionSelection("item-001", "opt-001-a", Confidence.Certain),
                    new SubmissionSelection("item-002", "opt-002-a", Confidence.Leaning),
                    new SubmissionSelection("item-003", "opt-003-a", Confidence.Guessing)
                });
            var final = RouteTrialTestData.ZeroWrongFinalResult(
                "Contradictory trusted answer");
            var snapshot = new QuizSnapshot(
                "wayline.v1",
                "batch-001",
                QuizSnapshotState.Revealed,
                3,
                RouteTrialTestData.Batch(),
                initial,
                new InitialSubmissionResult(
                    "wayline.v1",
                    "batch-001",
                    3,
                    0,
                    false,
                    final),
                null,
                final);

            Assert.Throws<JsonSerializationException>(() =>
                StrictQuizValidator.Validate(snapshot));
        }

        private static T DeserializeFixture<T>(string fixture)
        {
            var json = File.ReadAllText(TestPaths.Contract("valid/" + fixture));
            return StrictQuizValidator.Deserialize<T>(json);
        }

        private static JObject NonzeroInitialResultToken()
        {
            var result = JObject.Parse(
                File.ReadAllText(TestPaths.Contract("valid/two-wrong-result.json")));
            result["finalResult"] = JValue.CreateNull();
            return result;
        }

        private static void AssertInvalidFixture<T>(string fixture)
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/" + fixture));
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<T>(json));
        }

        private static void AssertMutationRejected<T>(
            string fixture,
            Action<JObject> mutate)
        {
            var value = JObject.Parse(
                File.ReadAllText(TestPaths.Contract("valid/" + fixture)));
            mutate(value);
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<T>(value.ToString(Formatting.None)));
        }

        private static void AddFourthSecondWindItem(JObject value)
        {
            var batch = (JObject)value["batch"];
            var items = (JArray)batch["items"];
            var item = (JObject)items[0].DeepClone();
            item["itemId"] = "item-004";
            items.Add(item);
            batch["itemCount"] = 4;
        }
    }
}
