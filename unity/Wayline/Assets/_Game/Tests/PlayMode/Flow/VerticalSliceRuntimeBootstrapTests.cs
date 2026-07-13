using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Wayline.Campaign;
using Wayline.Characters;
using Wayline.Combat.Simulation;
using Wayline.Flow;
using Wayline.Flow.Runtime;
using Wayline.Flow.Unity;
using Wayline.Gameplay;
using Wayline.Learning.Contracts;
using Wayline.Learning.Quiz;
using Wayline.Save;
using CampaignBattleTier = Wayline.Campaign.BattleTier;
using LearningBattleTier = Wayline.Learning.Contracts.BattleTier;

namespace Wayline.Tests.Flow
{
    public sealed class VerticalSliceRuntimeBootstrapTests
    {
        private string _directory;
        private string _sessionPath;
        private CombatWorldRunner _runner;
        private byte[] _combatBeforeRestore;
        private VerticalSliceRuntimeBootstrap _slice;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _directory = Path.Combine(
                Path.GetTempPath(),
                "wayline-bootstrap-tests-" + Guid.NewGuid().ToString("N"));
            _sessionPath = Path.Combine(_directory, "runtime-session.json");

            var operation = SceneManager.LoadSceneAsync("Arena_Graybox", LoadSceneMode.Single);
            while (!operation.isDone)
                yield return null;
            yield return null;

            var existing = UnityEngine.Object.FindFirstObjectByType<VerticalSliceRuntimeBootstrap>();
            Assert.That(existing, Is.Not.Null);
            _runner = existing.Runner;
            _runner.RunAutomatically = false;
            _combatBeforeRestore = _runner.SerializeSnapshot();
            UnityEngine.Object.Destroy(existing.gameObject);
            yield return null;
        }

        [UnityTearDown]
        public IEnumerator TearDown()
        {
            if (_slice != null)
                UnityEngine.Object.Destroy(_slice.gameObject);
            yield return null;
            if (Directory.Exists(_directory))
                Directory.Delete(_directory, recursive: true);
        }

        [UnityTest]
        public IEnumerator ExistingProfileAndNormalTrialResumeWithoutRestartingCombat()
        {
            var profile = CreateProfile();
            profile.RecordCombatVictory("valuehold-scout");
            profile.ApplyReward(routeMarks: 7, focus: 2);
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            var checkpoint = new FlowCheckpoint(
                FlowState.NormalTrial,
                battle,
                combatVictoryPreserved: true,
                committedTrialIds: new[] { "prior-completion-001" },
                committedRewardIds: new[] { "prior-completion-001" },
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
            new RuntimeSessionStore(_sessionPath).Save(profile, checkpoint);

            CreateRestoredBootstrap();
            yield return null;

            Assert.That(_slice.Profile.SidecarProfileId, Is.EqualTo(profile.SidecarProfileId));
            Assert.That(_slice.Profile.RouteMarks, Is.EqualTo(7));
            Assert.That(_slice.Profile.Focus, Is.EqualTo(2));
            Assert.That(_slice.Profile.HasCombatVictory(battle.BattleId), Is.True);
            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(_slice.Flow.LastCheckpoint.Battle, Is.EqualTo(battle));
            Assert.That(_slice.Flow.LastCheckpoint.CommittedTrialIds,
                Is.EqualTo(new[] { "prior-completion-001" }));
            Assert.That(_runner.SerializeSnapshot(), Is.EqualTo(_combatBeforeRestore));
            Assert.That(_slice.TrialPanel, Is.Not.Null);
        }

        [UnityTest]
        public IEnumerator CombatDefeatEntersQuestionsBeforeMapWithoutRecordingVictory()
        {
            CreateRestoredBootstrap();
            _slice.EnterMapButton.onClick.Invoke();
            _slice.StartBattleButton.onClick.Invoke();
            _runner.RunAutomatically = false;
            _runner.SetCommandSources(
                new IdleCommandSource(),
                new AggressiveCommandSource());
            for (var frame = 0;
                 frame < 1000 && _runner.State.Result == CombatResult.InProgress;
                 frame++)
            {
                _runner.AdvanceFrame(1.0 / 60.0);
            }

            Assert.That(_runner.State.Result, Is.EqualTo(CombatResult.EnemyWon));
            InvokeBootstrapUpdate();

            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.LossTrial));
            Assert.That(_slice.Flow.LastCheckpoint.CombatVictoryPreserved, Is.False);
            Assert.That(_slice.Profile.CombatVictoryBattleIds, Is.Empty);
            Assert.That(_slice.Profile.CompletedBattleIds, Is.Empty);
            Assert.That(_slice.Profile.RewardedBattleIds, Is.Empty);
            yield return WaitFor(() =>
                _slice.TrialController != null &&
                _slice.TrialController.State == QuizState.Answering);
            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.LossTrial));
            Assert.That(_slice.TrialPanel, Is.Not.Null);
            Assert.That(_slice.RewardButton.gameObject.activeInHierarchy, Is.False);
        }

        [UnityTest]
        public IEnumerator ExistingLossTrialResumesQuestionsWithoutCombatVictory()
        {
            var profile = CreateProfile();
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            new RuntimeSessionStore(_sessionPath).Save(
                profile,
                LossTrialCheckpoint(battle));

            CreateRestoredBootstrap();
            yield return WaitFor(() =>
                _slice.TrialController != null &&
                _slice.TrialController.State == QuizState.Answering);

            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.LossTrial));
            Assert.That(_slice.Flow.LastCheckpoint.Battle, Is.EqualTo(battle));
            Assert.That(_slice.Flow.LastCheckpoint.CombatVictoryPreserved, Is.False);
            Assert.That(_slice.Profile.CombatVictoryBattleIds, Is.Empty);
            Assert.That(_slice.Profile.CompletedBattleIds, Is.Empty);
            Assert.That(_slice.Profile.RewardedBattleIds, Is.Empty);
            Assert.That(_runner.SerializeSnapshot(), Is.EqualTo(_combatBeforeRestore));

            var persisted = new RuntimeSessionStore(_sessionPath).Load();
            Assert.That(persisted.Checkpoint.StableState, Is.EqualTo(FlowState.LossTrial));
            Assert.That(persisted.Checkpoint.Battle, Is.EqualTo(battle));
            Assert.That(persisted.Profile.CombatVictoryBattleIds, Is.Empty);
        }

        [UnityTest]
        public IEnumerator CompletedLossQuestionsReturnToRematchWithoutProgressionOrRewards()
        {
            var profile = CreateProfile();
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            new RuntimeSessionStore(_sessionPath).Save(
                profile,
                LossTrialCheckpoint(battle));
            CreateRestoredBootstrap();
            yield return WaitFor(() =>
                _slice.TrialController != null &&
                _slice.TrialController.State == QuizState.Answering);

            CompleteStandardTrialController(_slice.TrialController);
            Assert.That(_slice.TrialController.State, Is.EqualTo(QuizState.Complete));
            InvokeBootstrapUpdate();

            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.Map));
            Assert.That(_slice.Battle, Is.EqualTo(battle));
            Assert.That(_slice.StartBattleButton.gameObject.activeInHierarchy, Is.True);
            Assert.That(_slice.RewardButton.gameObject.activeInHierarchy, Is.False);
            Assert.That(_slice.Profile.CombatVictoryBattleIds, Is.Empty);
            Assert.That(_slice.Profile.CompletedBattleIds, Is.Empty);
            Assert.That(_slice.Profile.ClearedWorldIds, Is.Empty);
            Assert.That(_slice.Profile.RewardedBattleIds, Is.Empty);
            Assert.That(_slice.Profile.RouteMarks, Is.Zero);
            Assert.That(_slice.Profile.Focus, Is.Zero);

            var persisted = new RuntimeSessionStore(_sessionPath).Load();
            Assert.That(persisted.Checkpoint.StableState, Is.EqualTo(FlowState.Map));
            Assert.That(persisted.Checkpoint.Battle, Is.Null);
            Assert.That(persisted.Checkpoint.CommittedTrialIds, Is.Empty);
            Assert.That(persisted.Checkpoint.CommittedRewardIds, Is.Empty);
            Assert.That(persisted.Profile.CombatVictoryBattleIds, Is.Empty);
            Assert.That(persisted.Profile.CompletedBattleIds, Is.Empty);
            Assert.That(persisted.Profile.RewardedBattleIds, Is.Empty);
        }

        [UnityTest]
        public IEnumerator RewardResumePresentsSavedRewardWithoutReapplyingTrialMutation()
        {
            var profile = CreateProfile();
            profile.RecordCombatVictory("valuehold-scout");
            profile.RecordBattleCompleted("valuehold-scout");
            profile.MarkBattleRewarded("valuehold-scout");
            profile.ApplyReward(routeMarks: 9, focus: 3);
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            var checkpoint = new FlowCheckpoint(
                FlowState.Reward,
                battle,
                combatVictoryPreserved: true,
                committedTrialIds: new[] { "normal-completion-001" },
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: "normal-completion-001",
                rewardAuthorityReceiptId: "receipt-normal-completion-001");
            new RuntimeSessionStore(_sessionPath).Save(profile, checkpoint);

            CreateRestoredBootstrap();
            yield return null;

            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.Reward));
            Assert.That(_slice.Profile.RouteMarks, Is.EqualTo(9));
            Assert.That(_slice.Profile.Focus, Is.EqualTo(3));
            Assert.That(_slice.Profile.IsBattleCompleted(battle.BattleId), Is.True);
            Assert.That(_slice.Profile.HasRewardedBattle(battle.BattleId), Is.True);
            Assert.That(_slice.RewardButton.gameObject.activeInHierarchy, Is.True);
            Assert.That(_runner.SerializeSnapshot(), Is.EqualTo(_combatBeforeRestore));
        }

        [UnityTest]
        public IEnumerator IncoherentPrimaryFallsBackToCoherentBackupBeforeComposition()
        {
            var store = new RuntimeSessionStore(_sessionPath);
            store.Save(
                CreateProfile("profile-coherent-backup"),
                Checkpoint(FlowState.Title));
            store.Save(
                CreateProfile("profile-incoherent-primary"),
                Checkpoint(FlowState.NormalTrial, "valuehold-scout"));

            CreateRestoredBootstrap();
            yield return null;

            Assert.That(
                _slice.Profile.SidecarProfileId,
                Is.EqualTo("profile-coherent-backup"));
            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.Title));
        }

        [Test]
        public void CatalogWorldReferencesMustAllBeKnown()
        {
            var unknownActive = CreateProfile(
                "profile-unknown-active",
                activeWorldId: "unknown-world");
            Assert.That(IsCoherent(unknownActive, Checkpoint(FlowState.Title)), Is.False);

            var unknownPending = BossPendingProfile(
                "profile-unknown-pending",
                "SealTrial",
                rewarded: true,
                pendingWorldId: "unknown-world");
            Assert.That(
                IsCoherent(
                    unknownPending,
                    Checkpoint(FlowState.SealTrial, "valuehold-boss")),
                Is.False);

            var unknownCleared = CreateProfile("profile-unknown-cleared");
            unknownCleared.RecordWorldCleared("unknown-world");
            Assert.That(IsCoherent(unknownCleared, Checkpoint(FlowState.Title)), Is.False);
        }

        [Test]
        public void EveryProfileAndCheckpointBattleIdentityMustBeKnown()
        {
            var unknownCombat = CreateProfile("profile-unknown-combat");
            unknownCombat.RecordCombatVictory("unknown-battle");
            Assert.That(IsCoherent(unknownCombat, Checkpoint(FlowState.Title)), Is.False);

            var unknownCompleted = CreateProfile("profile-unknown-completed");
            unknownCompleted.RecordBattleCompleted("unknown-battle");
            Assert.That(IsCoherent(unknownCompleted, Checkpoint(FlowState.Title)), Is.False);

            var unknownRewarded = CreateProfile("profile-unknown-rewarded");
            unknownRewarded.MarkBattleRewarded("unknown-battle");
            Assert.That(IsCoherent(unknownRewarded, Checkpoint(FlowState.Title)), Is.False);

            Assert.That(
                IsCoherent(
                    CreateProfile("profile-unknown-checkpoint"),
                    Checkpoint(FlowState.Combat, "valuehold-unknown")),
                Is.False);
        }

        [Test]
        public void CompletedAndRewardedBattlesRequireTheirCombatVictory()
        {
            var completed = CreateProfile("profile-completed-without-victory");
            completed.RecordBattleCompleted("valuehold-scout");
            Assert.That(IsCoherent(completed, Checkpoint(FlowState.Title)), Is.False);

            var rewarded = CreateProfile("profile-rewarded-without-victory");
            rewarded.MarkBattleRewarded("valuehold-scout");
            Assert.That(IsCoherent(rewarded, Checkpoint(FlowState.Title)), Is.False);
        }

        [Test]
        public void NormalTrialRequiresItsPreservedProfileVictory()
        {
            Assert.That(
                IsCoherent(
                    CreateProfile("profile-normal-without-victory"),
                    Checkpoint(FlowState.NormalTrial, "valuehold-scout")),
                Is.False);
        }

        [Test]
        public void SealAndAssistedTrialsRequireBossPendingStateAndGrantedBossReward()
        {
            var wrongSealStep = BossPendingProfile(
                "profile-seal-wrong-step",
                "AssistedRoute",
                rewarded: true);
            Assert.That(
                IsCoherent(
                    wrongSealStep,
                    Checkpoint(FlowState.SealTrial, "valuehold-boss")),
                Is.False);

            var assistedScout = CreateProfile("profile-assisted-scout");
            assistedScout.RecordCombatVictory("valuehold-scout");
            assistedScout.MarkBattleRewarded("valuehold-scout");
            assistedScout.SetPending("AssistedRoute", "valuehold");
            Assert.That(
                IsCoherent(
                    assistedScout,
                    Checkpoint(FlowState.AssistedRoute, "valuehold-scout")),
                Is.False);

            var missingBossReward = BossPendingProfile(
                "profile-assisted-no-reward",
                "AssistedRoute",
                rewarded: false);
            Assert.That(
                IsCoherent(
                    missingBossReward,
                    Checkpoint(FlowState.AssistedRoute, "valuehold-boss")),
                Is.False);

            var pendingOutsideTrial = BossPendingProfile(
                "profile-title-with-pending",
                "SealTrial",
                rewarded: true);
            Assert.That(
                IsCoherent(pendingOutsideTrial, Checkpoint(FlowState.Title)),
                Is.False);
        }

        [Test]
        public void RewardRequiresVictoryCompletionRewardFlagAndBossWorldClear()
        {
            var missingFlags = CreateProfile("profile-reward-missing-flags");
            missingFlags.RecordCombatVictory("valuehold-scout");
            Assert.That(
                IsCoherent(
                    missingFlags,
                    Checkpoint(FlowState.Reward, "valuehold-scout")),
                Is.False);

            var missingReward = CreateProfile("profile-reward-missing-reward");
            missingReward.RecordCombatVictory("valuehold-scout");
            missingReward.RecordBattleCompleted("valuehold-scout");
            Assert.That(
                IsCoherent(
                    missingReward,
                    Checkpoint(FlowState.Reward, "valuehold-scout")),
                Is.False);

            var missingCompletion = CreateProfile("profile-reward-missing-completion");
            missingCompletion.RecordCombatVictory("valuehold-scout");
            missingCompletion.MarkBattleRewarded("valuehold-scout");
            Assert.That(
                IsCoherent(
                    missingCompletion,
                    Checkpoint(FlowState.Reward, "valuehold-scout")),
                Is.False);

            var unclearedBoss = CreateProfile("profile-boss-reward-uncleared");
            unclearedBoss.RecordCombatVictory("valuehold-boss");
            unclearedBoss.RecordBattleCompleted("valuehold-boss");
            unclearedBoss.MarkBattleRewarded("valuehold-boss");
            Assert.That(
                IsCoherent(
                    unclearedBoss,
                    Checkpoint(FlowState.Reward, "valuehold-boss")),
                Is.False);
        }

        [Test]
        public void CheckpointWorldMustMatchTheActiveWorldEvenWhenBothAreKnown()
        {
            var profile = CreateProfile("profile-wrong-active-world");
            profile.RecordCombatVictory("fraction-frontier-scout");

            Assert.That(
                IsCoherent(
                    profile,
                    Checkpoint(
                        FlowState.NormalTrial,
                        "fraction-frontier-scout",
                        "fraction-frontier"),
                    new[] { CreateWorld(), CreateSecondWorld() }),
                Is.False);
        }

        [Test]
        public void ClearedWorldRequiresItsBossVictoryRewardAndCompletion()
        {
            var profile = CreateProfile("profile-unsupported-world-clear");
            profile.RecordWorldCleared("valuehold");

            Assert.That(IsCoherent(profile, Checkpoint(FlowState.Title)), Is.False);
        }

        [Test]
        public void ClearedWorldCannotRetainSealOrAssistedPendingState()
        {
            foreach (var state in new[] { FlowState.SealTrial, FlowState.AssistedRoute })
            {
                var profile = BossPendingProfile(
                    "profile-cleared-with-" + state,
                    state.ToString(),
                    rewarded: true);
                profile.RecordBattleCompleted("valuehold-boss");
                profile.RecordWorldCleared("valuehold");
                profile.SetPending(state.ToString(), "valuehold");

                Assert.That(
                    IsCoherent(profile, Checkpoint(state, "valuehold-boss")),
                    Is.False);
            }
        }

        [Test]
        public void CompletionRequiresRewardAndOnlyPendingBossMayBeRewardedUncompleted()
        {
            var completedWithoutReward = CreateProfile("profile-complete-no-reward");
            completedWithoutReward.RecordCombatVictory("valuehold-scout");
            completedWithoutReward.RecordBattleCompleted("valuehold-scout");
            Assert.That(
                IsCoherent(completedWithoutReward, Checkpoint(FlowState.Title)),
                Is.False);

            var scoutRewardWithoutCompletion = CreateProfile("profile-scout-reward-only");
            scoutRewardWithoutCompletion.RecordCombatVictory("valuehold-scout");
            scoutRewardWithoutCompletion.MarkBattleRewarded("valuehold-scout");
            Assert.That(
                IsCoherent(scoutRewardWithoutCompletion, Checkpoint(FlowState.Title)),
                Is.False);

            foreach (var state in new[] { FlowState.SealTrial, FlowState.AssistedRoute })
            {
                var validPendingBoss = BossPendingProfile(
                    "profile-valid-pending-" + state,
                    state.ToString(),
                    rewarded: true);
                Assert.That(
                    IsCoherent(
                        validPendingBoss,
                        Checkpoint(state, "valuehold-boss")),
                    Is.True);
            }
        }

        [UnityTest]
        public IEnumerator CombatPersistenceFailureRetriesWithoutRecordingVictoryTwice()
        {
            CreateRestoredBootstrap();
            _slice.EnterMapButton.onClick.Invoke();
            _slice.StartBattleButton.onClick.Invoke();
            _runner.RunAutomatically = false;
            _runner.SetCommandSources(
                new AggressiveCommandSource(),
                new IdleCommandSource());
            for (var frame = 0;
                 frame < 1000 && _runner.State.Result == CombatResult.InProgress;
                 frame++)
            {
                _runner.AdvanceFrame(1.0 / 60.0);
            }
            Assert.That(_runner.State.Result, Is.EqualTo(CombatResult.PlayerWon));
            var blockedTemporaryPath = _sessionPath + ".tmp";
            Directory.CreateDirectory(blockedTemporaryPath);
            try
            {
                var failure = Assert.Throws<TargetInvocationException>(InvokeBootstrapUpdate);
                Assert.That(
                    failure.InnerException,
                    Is.InstanceOf<IOException>().Or.InstanceOf<UnauthorizedAccessException>());
            }
            finally
            {
                Directory.Delete(blockedTemporaryPath, recursive: true);
            }

            Assert.That(ReadPrivate<bool>("_combatResolved"), Is.False);
            InvokeBootstrapUpdate();

            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(_slice.Profile.CombatVictoryBattleIds,
                Is.EqualTo(new[] { "valuehold-scout" }));
            var persisted = new RuntimeSessionStore(_sessionPath).Load();
            Assert.That(persisted.Checkpoint.StableState,
                Is.EqualTo(FlowState.NormalTrial));
            Assert.That(persisted.Profile.CombatVictoryBattleIds,
                Is.EqualTo(new[] { "valuehold-scout" }));
            yield return null;
        }

        [UnityTest]
        public IEnumerator TrialPersistenceFailureRetriesSameCompletionWithoutDuplicateReward()
        {
            var profile = CreateProfile();
            profile.RecordCombatVictory("valuehold-scout");
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            new RuntimeSessionStore(_sessionPath).Save(
                profile,
                new FlowCheckpoint(
                    FlowState.NormalTrial,
                    battle,
                    combatVictoryPreserved: true,
                    committedTrialIds: Array.Empty<string>(),
                    committedRewardIds: Array.Empty<string>(),
                    rewardSourceCompletionId: null,
                    rewardAuthorityReceiptId: null));
            CreateRestoredBootstrap();
            yield return WaitFor(() =>
                _slice.TrialController != null &&
                _slice.TrialController.State == QuizState.Answering);
            CompleteStandardTrialController(_slice.TrialController);
            Assert.That(_slice.TrialController.State, Is.EqualTo(QuizState.Complete));

            var blockedTemporaryPath = _sessionPath + ".tmp";
            Directory.CreateDirectory(blockedTemporaryPath);
            try
            {
                var failure = Assert.Throws<TargetInvocationException>(InvokeBootstrapUpdate);
                Assert.That(
                    failure.InnerException,
                    Is.InstanceOf<IOException>().Or.InstanceOf<UnauthorizedAccessException>());
            }
            finally
            {
                Directory.Delete(blockedTemporaryPath, recursive: true);
            }
            var cachedCompletion =
                ReadPrivate<AuthoritativeTrialCompletion>("_pendingAuthorityCompletion");
            Assert.That(cachedCompletion, Is.Not.Null);
            Assert.That(cachedCompletion.CompletionId, Does.StartWith("complete-battle-"));
            Assert.That(ReadPrivate<bool>("_trialCommitted"), Is.False);
            InvokeBootstrapUpdate();

            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.Reward));
            Assert.That(
                ReadPrivate<AuthoritativeTrialCompletion>("_pendingAuthorityCompletion"),
                Is.Null);
            Assert.That(_slice.Profile.RouteMarks, Is.EqualTo(10));
            Assert.That(_slice.Profile.Focus, Is.EqualTo(3));
            Assert.That(_slice.Profile.RewardedBattleIds,
                Is.EqualTo(new[] { "valuehold-scout" }));
            var persisted = new RuntimeSessionStore(_sessionPath).Load();
            Assert.That(persisted.Checkpoint.StableState, Is.EqualTo(FlowState.Reward));
            Assert.That(persisted.Checkpoint.CommittedTrialIds, Has.Count.EqualTo(1));
            Assert.That(
                persisted.Checkpoint.RewardSourceCompletionId,
                Is.EqualTo(cachedCompletion.CompletionId));
            Assert.That(persisted.Profile.RouteMarks, Is.EqualTo(10));
            Assert.That(persisted.Profile.RewardedBattleIds,
                Is.EqualTo(new[] { "valuehold-scout" }));
        }

        [UnityTest]
        public IEnumerator RequestIdentityDoesNotRepeatAcrossRuntimeRestart()
        {
            CreateRestoredBootstrap();
            var first = InvokePrivate<string>("NextRequestId");
            StrictQuizValidator.Validate(new BattleQuizRequest(
                "wayline.v1",
                first,
                "session-restart-test",
                "valuehold-scout",
                "valuehold",
                LearningBattleTier.Route1));
            UnityEngine.Object.Destroy(_slice.gameObject);
            _slice = null;
            yield return null;

            CreateRestoredBootstrap();
            var second = InvokePrivate<string>("NextRequestId");
            StrictQuizValidator.Validate(new BattleQuizRequest(
                "wayline.v1",
                second,
                "session-restart-test",
                "valuehold-scout",
                "valuehold",
                LearningBattleTier.Route1));

            Assert.That(second, Is.Not.EqualTo(first));
        }

        [UnityTest]
        public IEnumerator RestoredSealMissMakesTheNextDeterministicAttemptNumberTwo()
        {
            var profile = BossPendingProfile(
                "profile-restored-seal-miss",
                "SealTrial",
                rewarded: true);
            var priorSealCompletion =
                "complete-seal-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
            new RuntimeSessionStore(_sessionPath).Save(
                profile,
                new FlowCheckpoint(
                    FlowState.SealTrial,
                    new FlowBattle("valuehold", "valuehold-boss"),
                    combatVictoryPreserved: true,
                    committedTrialIds: new[]
                    {
                        "complete-battle-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        priorSealCompletion
                    },
                    committedRewardIds: Array.Empty<string>(),
                    rewardSourceCompletionId: null,
                    rewardAuthorityReceiptId: null));

            CreateRestoredBootstrap();
            yield return WaitFor(() =>
                _slice.TrialController != null &&
                _slice.TrialController.State == QuizState.Answering);

            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.SealTrial));
            Assert.That(ReadPrivate<int>("_sealAttempt"), Is.EqualTo(1));
            CompleteStandardTrialController(_slice.TrialController);
            InvokeBootstrapUpdate();

            Assert.That(ReadPrivate<int>("_sealAttempt"), Is.EqualTo(2));
            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.Reward));
            Assert.That(_slice.Profile.IsWorldCleared("valuehold"), Is.True);
            var persisted = new RuntimeSessionStore(_sessionPath).Load();
            Assert.That(
                persisted.Checkpoint.CommittedTrialIds.Count(id =>
                    id.StartsWith("complete-seal-", StringComparison.Ordinal)),
                Is.EqualTo(2));
        }

        private void CreateRestoredBootstrap()
        {
            var runtime = new GameObject("Restored Vertical Slice Runtime");
            runtime.SetActive(false);
            _slice = runtime.AddComponent<VerticalSliceRuntimeBootstrap>();
            _slice.Configure(
                _runner,
                deterministicAcceptanceData: true,
                runtimeSessionPath: _sessionPath);
            runtime.SetActive(true);
        }

        private void InvokeBootstrapUpdate()
        {
            typeof(VerticalSliceRuntimeBootstrap)
                .GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic)
                .Invoke(_slice, null);
        }

        private T ReadPrivate<T>(string fieldName)
        {
            return (T)typeof(VerticalSliceRuntimeBootstrap)
                .GetField(fieldName, BindingFlags.Instance | BindingFlags.NonPublic)
                .GetValue(_slice);
        }

        private T InvokePrivate<T>(string methodName)
        {
            return (T)typeof(VerticalSliceRuntimeBootstrap)
                .GetMethod(methodName, BindingFlags.Instance | BindingFlags.NonPublic)
                .Invoke(_slice, null);
        }

        private bool IsCoherent(
            ProfileDataV1 profile,
            FlowCheckpoint checkpoint,
            WorldDefinition[] worlds = null)
        {
            var path = Path.Combine(
                _directory,
                "coherence-" + Guid.NewGuid().ToString("N") + ".json");
            var store = new RuntimeSessionStore(path);
            store.Save(profile, checkpoint);
            var snapshot = store.Load();
            var validator = typeof(VerticalSliceRuntimeBootstrap).GetMethod(
                "IsRuntimeSessionCoherent",
                BindingFlags.Static | BindingFlags.NonPublic);
            Assert.That(validator, Is.Not.Null);
            return (bool)validator.Invoke(
                null,
                new object[] { worlds ?? new[] { CreateWorld() }, snapshot });
        }

        private static void CompleteStandardTrialController(QuizController controller)
        {
            foreach (var item in controller.Batch.Items)
            {
                controller.SelectOption(item.ItemId, item.Options[0].OptionId);
                controller.SelectConfidence(item.ItemId, Confidence.Certain);
            }
            controller.SubmitInitialAsync(CancellationToken.None).GetAwaiter().GetResult();
            controller.AcknowledgeWrongCount();
            while (controller.State == QuizState.Revealed)
                controller.AdvanceFinalFeedback();
        }

        private static FlowCheckpoint LossTrialCheckpoint(FlowBattle battle)
        {
            return new FlowCheckpoint(
                FlowState.LossTrial,
                battle,
                combatVictoryPreserved: false,
                committedTrialIds: Array.Empty<string>(),
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
        }

        private static IEnumerator WaitFor(Func<bool> condition)
        {
            var deadline = Time.realtimeSinceStartup + 2f;
            while (Time.realtimeSinceStartup < deadline)
            {
                if (condition())
                    yield break;
                yield return null;
            }
            Assert.Fail("The composed runtime did not reach the expected state.");
        }

        private static ProfileDataV1 CreateProfile(
            string sidecarProfileId = "profile-restored-001",
            string activeWorldId = "valuehold")
        {
            return ProfileDataV1.CreateNew(
                sidecarProfileId,
                activeWorldId,
                "splitstaff",
                new HeroAppearanceSelection(
                    "routekeeper-amber",
                    "hair-braid",
                    "mantle-scout",
                    "dye-lapis",
                    "dye-oxide",
                    "inlay-gold"));
        }

        private static ProfileDataV1 BossPendingProfile(
            string sidecarProfileId,
            string pendingStep,
            bool rewarded,
            string pendingWorldId = "valuehold")
        {
            var profile = CreateProfile(sidecarProfileId);
            profile.RecordCombatVictory("valuehold-boss");
            if (rewarded)
                profile.MarkBattleRewarded("valuehold-boss");
            profile.SetPending(pendingStep, pendingWorldId);
            return profile;
        }

        private static FlowCheckpoint Checkpoint(
            FlowState state,
            string battleId = null,
            string worldId = "valuehold")
        {
            var battle = battleId == null
                ? null
                : new FlowBattle(worldId, battleId);
            var reward = state == FlowState.Reward;
            return new FlowCheckpoint(
                state,
                battle,
                combatVictoryPreserved: state == FlowState.NormalTrial ||
                                          state == FlowState.SealTrial ||
                                          state == FlowState.AssistedRoute ||
                                          reward,
                committedTrialIds: reward
                    ? new[] { "completion-reward-001" }
                    : Array.Empty<string>(),
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: reward ? "completion-reward-001" : null,
                rewardAuthorityReceiptId: reward ? "receipt-reward-001" : null);
        }

        private static WorldDefinition CreateWorld()
        {
            return new WorldDefinition(
                "valuehold",
                "Valuehold Reach",
                new[] { "place_value", "mental_add_sub" },
                "arena-valuehold-graybox",
                "surveyors",
                "surveyor-general",
                "folding-lance",
                "#E6AF3B",
                new[]
                {
                    new BattleDefinition("valuehold-scout", CampaignBattleTier.Scout,
                        "ai-scout", "surveyor-scout", 10, "card-scout"),
                    new BattleDefinition("valuehold-rival", CampaignBattleTier.Rival,
                        "ai-rival", "surveyor-rival", 12, "card-rival"),
                    new BattleDefinition("valuehold-warden", CampaignBattleTier.Warden,
                        "ai-warden", "surveyor-warden", 14, "card-warden"),
                    new BattleDefinition("valuehold-lieutenant", CampaignBattleTier.Lieutenant,
                        "ai-lieutenant", "surveyor-lieutenant", 16, "card-lieutenant"),
                    new BattleDefinition("valuehold-boss", CampaignBattleTier.Boss,
                        "ai-boss", "surveyor-general", 25, "card-boss")
                });
        }

        private static WorldDefinition CreateSecondWorld()
        {
            return new WorldDefinition(
                "fraction-frontier",
                "Fraction Frontier",
                new[] { "fraction_equivalence", "fraction_operations" },
                "arena-fraction-frontier",
                "dividers",
                "divider-general",
                "ratio-blade",
                "#2D7F83",
                new[]
                {
                    new BattleDefinition("fraction-frontier-scout", CampaignBattleTier.Scout,
                        "ai-scout", "divider-scout", 10, "card-scout"),
                    new BattleDefinition("fraction-frontier-rival", CampaignBattleTier.Rival,
                        "ai-rival", "divider-rival", 12, "card-rival"),
                    new BattleDefinition("fraction-frontier-warden", CampaignBattleTier.Warden,
                        "ai-warden", "divider-warden", 14, "card-warden"),
                    new BattleDefinition("fraction-frontier-lieutenant", CampaignBattleTier.Lieutenant,
                        "ai-lieutenant", "divider-lieutenant", 16, "card-lieutenant"),
                    new BattleDefinition("fraction-frontier-boss", CampaignBattleTier.Boss,
                        "ai-boss", "divider-general", 25, "card-boss")
                });
        }

        private sealed class AggressiveCommandSource : ICombatCommandSource
        {
            public CombatCommand NextCommand(CombatWorldState state, FighterSide side)
            {
                var fighter = side == FighterSide.Player ? state.Player : state.Enemy;
                return fighter.CurrentAction == CombatAction.None &&
                       fighter.StunTicksRemaining == 0
                    ? CombatCommand.LightAttack
                    : CombatCommand.None;
            }
        }

        private sealed class IdleCommandSource : ICombatCommandSource
        {
            public CombatCommand NextCommand(CombatWorldState state, FighterSide side) =>
                CombatCommand.None;
        }
    }
}
