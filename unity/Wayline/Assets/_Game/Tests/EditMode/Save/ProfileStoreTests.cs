using System;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Wayline.Save;
using Wayline.Tests.Campaign;

namespace Wayline.Tests.Save
{
    public sealed class ProfileStoreTests
    {
        private string _root;

        [SetUp]
        public void SetUp()
        {
            _root = Path.Combine(
                Path.GetTempPath(),
                "wayline-profile-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_root))
                Directory.Delete(_root, recursive: true);
        }

        [Test]
        public void SaveIsVersionedAtomicAndFallsBackToOneKnownGoodBackup()
        {
            var store = new AtomicProfileStore(Path.Combine(_root, "profile.json"));
            var profile = CreateProfile();
            profile.ApplyReward(routeMarks: 25, focus: 2);
            store.Save(profile);

            profile.ApplyReward(routeMarks: 10, focus: 1);
            store.Save(profile);

            Assert.That(File.Exists(store.PrimaryPath), Is.True);
            Assert.That(File.Exists(store.BackupPath), Is.True);
            Assert.That(File.Exists(store.TemporaryPath), Is.False);
            File.WriteAllText(store.PrimaryPath, "{not-valid-json");

            var recovered = store.Load();

            Assert.That(recovered.SchemaVersion, Is.EqualTo(ProfileDataV1.CurrentSchemaVersion));
            Assert.That(recovered.RouteMarks, Is.EqualTo(25));
            Assert.That(recovered.Focus, Is.EqualTo(2));
        }

        [Test]
        public void ExportContainsCampaignPresentationStateButNoLearningHistory()
        {
            var store = new AtomicProfileStore(Path.Combine(_root, "profile.json"));
            var profile = CreateProfile();
            profile.ApplyReward(routeMarks: 9, focus: 1);
            store.Save(profile);
            var exportPath = Path.Combine(_root, "exports", "wayline-profile.json");

            store.ExportTo(exportPath);

            var exported = File.ReadAllText(exportPath);
            Assert.That(exported, Does.Contain("sidecarProfileId"));
            Assert.That(exported, Does.Contain("routeMarks"));
            Assert.That(exported, Does.Not.Contain("answerHistory").IgnoreCase);
            Assert.That(exported, Does.Not.Contain("confidence").IgnoreCase);
            Assert.That(exported, Does.Not.Contain("misconception").IgnoreCase);
            Assert.DoesNotThrow(() => JsonConvert.DeserializeObject<ProfileDataV1>(exported));
        }

        [Test]
        public void DeleteRemovesPrimaryBackupAndInterruptedTemporaryFile()
        {
            var store = new AtomicProfileStore(Path.Combine(_root, "profile.json"));
            store.Save(CreateProfile());
            store.Save(CreateProfile());
            File.WriteAllText(store.TemporaryPath, "interrupted");

            store.Delete();

            Assert.That(File.Exists(store.PrimaryPath), Is.False);
            Assert.That(File.Exists(store.BackupPath), Is.False);
            Assert.That(File.Exists(store.TemporaryPath), Is.False);
        }

        [Test]
        public void RestoredProfileRejectsUnknownPendingCampaignStep()
        {
            var store = new AtomicProfileStore(Path.Combine(_root, "profile.json"));
            store.Save(CreateProfile());
            var payload = JObject.Parse(File.ReadAllText(store.PrimaryPath));
            payload["pendingStep"] = "WorldCleared";
            payload["pendingWorldId"] = "valuehold";
            File.WriteAllText(store.PrimaryPath, payload.ToString(Formatting.None));

            Assert.Throws<InvalidDataException>(() => store.Load());
        }

        [TestCase("WorldCleared", "valuehold")]
        [TestCase("SealTrial", " ")]
        public void InvalidPendingArgumentsLeaveProfileUnchanged(
            string step,
            string worldId)
        {
            var profile = CreateProfile();

            Assert.Throws<ArgumentException>(() => profile.SetPending(step, worldId));
            Assert.That(profile.PendingStep, Is.Null);
            Assert.That(profile.PendingWorldId, Is.Null);
        }

        private static ProfileDataV1 CreateProfile()
        {
            var appearance = CampaignDefinitionTests.Fixtures.AppearanceCatalog()
                .CreateSelection(
                    "routekeeper-amber",
                    "hair-braid",
                    "mantle-scout",
                    "dye-lapis",
                    "dye-oxide",
                    "inlay-gold");
            return ProfileDataV1.CreateNew(
                "profile-local-001",
                "valuehold",
                "splitstaff",
                appearance);
        }
    }
}
