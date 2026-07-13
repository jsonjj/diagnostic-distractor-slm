using System;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Wayline.Characters;
using Wayline.Flow;
using Wayline.Flow.Runtime;
using Wayline.Save;

namespace Wayline.Tests.Flow
{
    public sealed class RuntimeSessionStoreTests
    {
        private string _directory;
        private string _path;

        [SetUp]
        public void SetUp()
        {
            _directory = Path.Combine(
                Path.GetTempPath(),
                "wayline-runtime-session-tests-" + Guid.NewGuid().ToString("N"));
            _path = Path.Combine(_directory, "session.json");
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_directory))
                Directory.Delete(_directory, recursive: true);
        }

        [TestCase(FlowState.Title)]
        [TestCase(FlowState.Map)]
        [TestCase(FlowState.NormalTrial)]
        [TestCase(FlowState.LossTrial)]
        [TestCase(FlowState.SealTrial)]
        [TestCase(FlowState.AssistedRoute)]
        [TestCase(FlowState.Reward)]
        public void SaveAndLoadRoundTripEveryPersistedPresentation(FlowState state)
        {
            var store = new RuntimeSessionStore(_path);
            var profile = CreateProfile();
            profile.RecordCombatVictory("valuehold-scout");
            var expected = Checkpoint(state);

            store.Save(profile, expected);
            var loaded = store.Load();

            AssertProfileEqual(profile, loaded.Profile);
            AssertCheckpointEqual(expected, loaded.Checkpoint);
            Assert.That(File.Exists(store.TemporaryPath), Is.False);
        }

        [Test]
        public void CorruptPrimaryFallsBackToTheLastValidBackup()
        {
            var store = new RuntimeSessionStore(_path);
            var profile = CreateProfile();
            var backupCheckpoint = Checkpoint(FlowState.NormalTrial);
            store.Save(profile, backupCheckpoint);
            profile.ApplyReward(routeMarks: 2, focus: 1);
            store.Save(profile, Checkpoint(FlowState.Map));
            File.WriteAllText(store.PrimaryPath, "{ definitely-not-json");

            var loaded = store.Load();

            Assert.That(loaded.Profile.RouteMarks, Is.EqualTo(0));
            AssertCheckpointEqual(backupCheckpoint, loaded.Checkpoint);
        }

        [Test]
        public void SemanticallyInvalidPrimaryFallsBackToTheLastValidBackup()
        {
            var store = new RuntimeSessionStore(_path);
            var profile = CreateProfile();
            var backupCheckpoint = Checkpoint(FlowState.NormalTrial);
            store.Save(profile, backupCheckpoint);
            profile.ApplyReward(routeMarks: 4, focus: 1);
            store.Save(profile, Checkpoint(FlowState.Map));
            var primary = JObject.Parse(File.ReadAllText(store.PrimaryPath));
            ((JObject)primary["checkpoint"]).Add("unknownCheckpointData", true);
            File.WriteAllText(store.PrimaryPath, primary.ToString());

            var loaded = store.Load();

            Assert.That(loaded.Profile.RouteMarks, Is.EqualTo(0));
            AssertCheckpointEqual(backupCheckpoint, loaded.Checkpoint);
        }

        [Test]
        public void OversizedPrimaryIsRejectedBeforeParsingAndFallsBackToBackup()
        {
            var store = new RuntimeSessionStore(_path);
            var profile = CreateProfile();
            var backupCheckpoint = Checkpoint(FlowState.NormalTrial);
            store.Save(profile, backupCheckpoint);
            profile.ApplyReward(routeMarks: 4, focus: 1);
            store.Save(profile, Checkpoint(FlowState.Map));
            File.AppendAllText(
                store.PrimaryPath,
                new string(' ', RuntimeSessionStore.MaximumFileBytes + 1));

            var loaded = store.Load();

            Assert.That(loaded.Profile.RouteMarks, Is.EqualTo(0));
            AssertCheckpointEqual(backupCheckpoint, loaded.Checkpoint);
        }

        [Test]
        public void CandidateValidatorRejectsPrimaryAndTriesBackup()
        {
            var store = new RuntimeSessionStore(_path);
            store.Save(CreateProfile("profile-accepted-backup"), Checkpoint(FlowState.Title));
            store.Save(CreateProfile("profile-rejected-primary"), Checkpoint(FlowState.Title));

            var loaded = store.Load(snapshot =>
                snapshot.Profile.SidecarProfileId == "profile-accepted-backup");

            Assert.That(
                loaded.Profile.SidecarProfileId,
                Is.EqualTo("profile-accepted-backup"));
        }

        [Test]
        public void CandidateValidatorExceptionsPropagateWithoutTryingBackup()
        {
            var store = new RuntimeSessionStore(_path);
            store.Save(CreateProfile("profile-valid-backup"), Checkpoint(FlowState.Title));
            store.Save(CreateProfile("profile-valid-primary"), Checkpoint(FlowState.Title));
            var expected = new ApplicationException("catalog_validation_failed");

            var actual = Assert.Throws<ApplicationException>(() =>
                store.Load(_ => throw expected));

            Assert.That(actual, Is.SameAs(expected));
        }

        [Test]
        public void SaveAfterRejectedPrimaryPreservesKnownValidBackup()
        {
            var store = new RuntimeSessionStore(_path);
            store.Save(CreateProfile("profile-known-valid-backup"), Checkpoint(FlowState.Title));
            store.Save(CreateProfile("profile-rejected-primary"), Checkpoint(FlowState.Title));

            var recovered = store.Load(snapshot =>
                snapshot.Profile.SidecarProfileId == "profile-known-valid-backup");
            store.Save(CreateProfile("profile-new-valid-primary"), Checkpoint(FlowState.Map));
            File.WriteAllText(store.PrimaryPath, "{ corrupt-new-primary");

            var recoveredAgain = store.Load();

            Assert.That(
                recovered.Profile.SidecarProfileId,
                Is.EqualTo("profile-known-valid-backup"));
            Assert.That(
                recoveredAgain.Profile.SidecarProfileId,
                Is.EqualTo("profile-known-valid-backup"));
        }

        [Test]
        public void LoadRejectsDuplicateAndUnknownMembers()
        {
            AssertRejected(json => json.Replace(
                "\"schemaVersion\":\"wayline.runtime-session.v1\"",
                "\"schemaVersion\":\"wayline.runtime-session.v1\"," +
                "\"schemaVersion\":\"wayline.runtime-session.v1\""));
            AssertRejected(json => AddProperty(json, "unexpected", 1));
            AssertRejected(json =>
            {
                var checkpoint = (JObject)json["checkpoint"];
                checkpoint.Add("unexpected", 1);
            });
            AssertRejected(json =>
            {
                var profile = (JObject)json["profile"];
                profile.Add("unexpected", 1);
            });
        }

        [Test]
        public void LoadRevalidatesCheckpointInvariants()
        {
            AssertRejected(json => json["checkpoint"]["stableState"] = "Unavailable");
            AssertRejected(json =>
            {
                json["checkpoint"]["stableState"] = "Map";
                json["checkpoint"]["combatVictoryPreserved"] = false;
            });
            AssertRejected(json =>
                json["checkpoint"]["committedRewardIds"] =
                    new JArray("orphan-completion"));
        }

        [TestCase("answerHistory")]
        [TestCase("confidence")]
        [TestCase("misconception")]
        [TestCase("correctAnswer")]
        [TestCase("rawResponse")]
        [TestCase("apiKey")]
        [TestCase("credential")]
        public void SessionSchemaRejectsLearningEvidenceAndSecrets(string forbiddenName)
        {
            AssertRejected(json =>
            {
                var checkpoint = (JObject)json["checkpoint"];
                checkpoint.Add(forbiddenName, "must-not-persist");
            });
        }

        [Test]
        public void SnapshotExposesOnlyProfileAndCheckpoint()
        {
            var publicProperties = typeof(RuntimeSessionSnapshot)
                .GetProperties(BindingFlags.Public | BindingFlags.Instance)
                .Select(property => property.Name)
                .OrderBy(name => name, StringComparer.Ordinal)
                .ToArray();

            Assert.That(publicProperties, Is.EqualTo(new[] { "Checkpoint", "Profile" }));
        }

        [Test]
        public void SerializedSessionContainsNoLearningEvidenceOrCredentials()
        {
            var store = new RuntimeSessionStore(_path);
            store.Save(CreateProfile(), Checkpoint(FlowState.NormalTrial));

            var json = File.ReadAllText(store.PrimaryPath);

            foreach (var forbidden in new[]
            {
                "answerHistory",
                "confidence",
                "misconception",
                "correctAnswer",
                "rawResponse",
                "apiKey",
                "credential",
                "authorization"
            })
            {
                Assert.That(json, Does.Not.Contain(forbidden));
            }
        }

        private void AssertRejected(Action<JObject> mutate)
        {
            var store = new RuntimeSessionStore(_path);
            store.Save(CreateProfile(), Checkpoint(FlowState.NormalTrial));
            var json = JObject.Parse(File.ReadAllText(store.PrimaryPath));
            mutate(json);
            File.WriteAllText(store.PrimaryPath, json.ToString());

            Assert.Throws<InvalidDataException>(() => store.Load());
        }

        private void AssertRejected(Func<string, string> mutate)
        {
            var store = new RuntimeSessionStore(_path);
            store.Save(CreateProfile(), Checkpoint(FlowState.NormalTrial));
            var json = File.ReadAllText(store.PrimaryPath);
            File.WriteAllText(store.PrimaryPath, mutate(json));

            Assert.Throws<InvalidDataException>(() => store.Load());
        }

        private static void AddProperty(JObject json, string name, JToken value)
        {
            json.Add(name, value);
        }

        private static FlowCheckpoint Checkpoint(FlowState state)
        {
            var hasBattle = state != FlowState.Title && state != FlowState.Map;
            var isReward = state == FlowState.Reward;
            var hasPreservedVictory = state == FlowState.NormalTrial ||
                                      state == FlowState.SealTrial ||
                                      state == FlowState.AssistedRoute ||
                                      isReward;
            return new FlowCheckpoint(
                state,
                hasBattle ? new FlowBattle("valuehold", "valuehold-scout") : null,
                combatVictoryPreserved: hasPreservedVictory,
                committedTrialIds: isReward
                    ? new[] { "completion-001", "completion-002" }
                    : new[] { "completion-001" },
                committedRewardIds: isReward
                    ? new[] { "completion-001" }
                    : Array.Empty<string>(),
                rewardSourceCompletionId: isReward ? "completion-002" : null,
                rewardAuthorityReceiptId: isReward ? "receipt-002" : null);
        }

        private static ProfileDataV1 CreateProfile(
            string sidecarProfileId = "profile-session-test")
        {
            return ProfileDataV1.CreateNew(
                sidecarProfileId,
                "valuehold",
                "splitstaff",
                new HeroAppearanceSelection(
                    "routekeeper-amber",
                    "hair-braid",
                    "mantle-scout",
                    "dye-lapis",
                    "dye-oxide",
                    "inlay-gold"));
        }

        private static void AssertProfileEqual(ProfileDataV1 expected, ProfileDataV1 actual)
        {
            Assert.That(actual.SchemaVersion, Is.EqualTo(expected.SchemaVersion));
            Assert.That(actual.SidecarProfileId, Is.EqualTo(expected.SidecarProfileId));
            Assert.That(actual.ActiveWorldId, Is.EqualTo(expected.ActiveWorldId));
            Assert.That(actual.RouteMarks, Is.EqualTo(expected.RouteMarks));
            Assert.That(actual.Focus, Is.EqualTo(expected.Focus));
            Assert.That(actual.CombatVictoryBattleIds,
                Is.EqualTo(expected.CombatVictoryBattleIds));
            Assert.That(actual.CompletedBattleIds,
                Is.EqualTo(expected.CompletedBattleIds));
            Assert.That(actual.RewardedBattleIds,
                Is.EqualTo(expected.RewardedBattleIds));
        }

        private static void AssertCheckpointEqual(
            FlowCheckpoint expected,
            FlowCheckpoint actual)
        {
            Assert.That(actual.StableState, Is.EqualTo(expected.StableState));
            Assert.That(actual.Battle, Is.EqualTo(expected.Battle));
            Assert.That(actual.CombatVictoryPreserved,
                Is.EqualTo(expected.CombatVictoryPreserved));
            Assert.That(actual.CommittedTrialIds,
                Is.EqualTo(expected.CommittedTrialIds));
            Assert.That(actual.CommittedRewardIds,
                Is.EqualTo(expected.CommittedRewardIds));
            Assert.That(actual.RewardSourceCompletionId,
                Is.EqualTo(expected.RewardSourceCompletionId));
            Assert.That(actual.RewardAuthorityReceiptId,
                Is.EqualTo(expected.RewardAuthorityReceiptId));
        }
    }
}
