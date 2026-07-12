using System;
using System.Collections;
using System.IO;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.UI;
using Wayline.Characters;
using Wayline.Combat.Simulation;
using Wayline.Flow;
using Wayline.Flow.Runtime;
using Wayline.Flow.Unity;
using Wayline.Gameplay;
using Wayline.Learning.Contracts;
using Wayline.Learning.Quiz;
using Wayline.Save;

namespace Wayline.Tests.Flow
{
    /// <summary>
    /// Verifies the scaled three-world campaign advances the atlas from one route
    /// to the next as scout battles are completed, and that the opponent is
    /// re-themed for the new world.
    /// </summary>
    public sealed class ThreeWorldProgressionTests
    {
        private string _directory;
        private string _sessionPath;
        private CombatWorldRunner _runner;
        private VerticalSliceRuntimeBootstrap _slice;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            _directory = Path.Combine(
                Path.GetTempPath(),
                "wayline-threeworld-" + Guid.NewGuid().ToString("N"));
            _sessionPath = Path.Combine(_directory, "runtime-session.json");

            var operation = SceneManager.LoadSceneAsync("Arena_Graybox", LoadSceneMode.Single);
            while (!operation.isDone)
                yield return null;
            yield return null;

            var existing = UnityEngine.Object.FindFirstObjectByType<VerticalSliceRuntimeBootstrap>();
            Assert.That(existing, Is.Not.Null);
            _runner = existing.Runner;
            _runner.RunAutomatically = false;
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
        public IEnumerator ClearingValueholdAdvancesTheAtlasToDecimara()
        {
            var profile = CreateProfile();
            profile.RecordCombatVictory("valuehold-scout");
            profile.RecordBattleCompleted("valuehold-scout");
            profile.MarkBattleRewarded("valuehold-scout");
            profile.ApplyReward(routeMarks: 10, focus: 3);
            new RuntimeSessionStore(_sessionPath).Save(profile, MapCheckpoint());

            CreateRestoredBootstrap();
            yield return null;

            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.Map));
            Assert.That(_slice.Battle.WorldId, Is.EqualTo("decimara"));
            Assert.That(_slice.Battle.BattleId, Is.EqualTo("decimara-scout"));
            Assert.That(
                _slice.StartBattleButton.GetComponentInChildren<Text>().text,
                Does.Contain("TIDE MARSHAL"));
        }

        [UnityTest]
        public IEnumerator ClearingTwoWorldsAdvancesToFractureAndThenCompletes()
        {
            var profile = CreateProfile();
            foreach (var scout in new[] { "valuehold-scout", "decimara-scout" })
            {
                profile.RecordCombatVictory(scout);
                profile.RecordBattleCompleted(scout);
                profile.MarkBattleRewarded(scout);
            }
            profile.ApplyReward(routeMarks: 20, focus: 6);
            new RuntimeSessionStore(_sessionPath).Save(profile, MapCheckpoint());

            CreateRestoredBootstrap();
            yield return null;

            Assert.That(_slice.Battle.BattleId, Is.EqualTo("fracture-scout"));
            Assert.That(
                _slice.StartBattleButton.GetComponentInChildren<Text>().text,
                Does.Contain("CHAIN WARDEN"));

            // Completing the final world marks the campaign complete.
            var finished = CreateProfile();
            foreach (var scout in new[] { "valuehold-scout", "decimara-scout", "fracture-scout" })
            {
                finished.RecordCombatVictory(scout);
                finished.RecordBattleCompleted(scout);
                finished.MarkBattleRewarded(scout);
            }
            UnityEngine.Object.Destroy(_slice.gameObject);
            _slice = null;
            yield return null;
            new RuntimeSessionStore(_sessionPath).Save(finished, MapCheckpoint());
            CreateRestoredBootstrap();
            yield return null;

            Assert.That(
                _slice.StartBattleButton.GetComponentInChildren<Text>().text,
                Does.Contain("COMPLETE"));
        }

        [UnityTest]
        public IEnumerator LegacyNextWorldTrialSaveMigratesAndShowsQuestions()
        {
            // Exact shape written by the first three-world build: Valuehold is
            // fully completed, Decimara combat is won and its NormalTrial is
            // stable, but activeWorldId still says Valuehold.
            var profile = CreateProfile();
            profile.RecordCombatVictory("valuehold-scout");
            profile.RecordBattleCompleted("valuehold-scout");
            profile.MarkBattleRewarded("valuehold-scout");
            profile.RecordCombatVictory("decimara-scout");
            var trial = new FlowCheckpoint(
                FlowState.NormalTrial,
                new FlowBattle("decimara", "decimara-scout"),
                combatVictoryPreserved: true,
                committedTrialIds: new[] { "complete-battle-valuehold-001" },
                committedRewardIds: new[] { "complete-battle-valuehold-001" },
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
            new RuntimeSessionStore(_sessionPath).Save(profile, trial);

            CreateRestoredBootstrap();
            yield return null;
            yield return null;

            Assert.That(_slice.Profile.ActiveWorldId, Is.EqualTo("decimara"));
            Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(_slice.TrialPanel, Is.Not.Null);
            Assert.That(_slice.TrialController, Is.Not.Null);
            Assert.That(
                _slice.TrialController.State,
                Is.EqualTo(Wayline.Learning.Quiz.QuizState.Answering));

            var repaired = new RuntimeSessionStore(_sessionPath).Load();
            Assert.That(repaired.Profile.ActiveWorldId, Is.EqualTo("decimara"));
            Assert.That(
                repaired.Checkpoint.StableState,
                Is.EqualTo(FlowState.NormalTrial));
        }

        [UnityTest]
        public IEnumerator EveryWorldRequiresTrialAndRewardBeforeNextFight()
        {
            new RuntimeSessionStore(_sessionPath).Save(
                CreateProfile(),
                new FlowCheckpoint(
                    FlowState.Title,
                    null,
                    combatVictoryPreserved: false,
                    committedTrialIds: Array.Empty<string>(),
                    committedRewardIds: Array.Empty<string>(),
                    rewardSourceCompletionId: null,
                    rewardAuthorityReceiptId: null));
            CreateRestoredBootstrap();
            yield return null;

            _slice.EnterMapButton.onClick.Invoke();
            yield return null;

            var worlds = new[] { "valuehold", "decimara", "fracture" };
            foreach (var worldId in worlds)
            {
                Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.Map));
                Assert.That(_slice.Battle.WorldId, Is.EqualTo(worldId));

                _slice.StartBattleButton.onClick.Invoke();
                Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.Combat));
                _runner.RunAutomatically = false;
                _runner.SetCommandSources(
                    new AggressiveCommandSource(),
                    new IdleCommandSource());
                for (var frame = 0;
                     frame < 1000 &&
                     _runner.State.Result == CombatResult.InProgress;
                     frame++)
                {
                    _runner.AdvanceFrame(1.0 / 60.0);
                }
                Assert.That(_runner.State.Result, Is.EqualTo(CombatResult.PlayerWon));

                yield return WaitFor(() =>
                    _slice.Flow.State == FlowState.NormalTrial &&
                    _slice.TrialController != null &&
                    _slice.TrialController.State == QuizState.Answering);

                // The key regression assertion: a new world may not advance
                // directly from combat to map/reward; its questions must exist.
                Assert.That(_slice.Flow.State, Is.EqualTo(FlowState.NormalTrial));
                Assert.That(
                    _slice.Profile.IsBattleCompleted(worldId + "-scout"),
                    Is.False);

                for (var item = 0; item < _slice.TrialController.Batch.ItemCount; item++)
                {
                    var publicItem = _slice.TrialController.Batch.Items[item];
                    _slice.TrialController.SelectOption(
                        publicItem.ItemId,
                        publicItem.Options[0].OptionId);
                    _slice.TrialController.SelectConfidence(
                        publicItem.ItemId,
                        Confidence.Certain);
                }

                var submission = _slice.TrialController.SubmitInitialAsync(
                    System.Threading.CancellationToken.None);
                while (!submission.IsCompleted)
                    yield return null;
                Assert.That(submission.IsFaulted, Is.False);
                _slice.TrialController.AcknowledgeWrongCount();
                while (_slice.TrialController.State == QuizState.Revealed)
                    _slice.TrialController.AdvanceFinalFeedback();

                yield return WaitFor(() =>
                    _slice.Flow.State == FlowState.Reward &&
                    _slice.RewardButton.gameObject.activeInHierarchy);
                Assert.That(
                    _slice.Profile.IsBattleCompleted(worldId + "-scout"),
                    Is.True);

                _slice.RewardButton.onClick.Invoke();
                yield return WaitFor(() => _slice.Flow.State == FlowState.Map);
            }

            Assert.That(
                _slice.StartBattleButton.GetComponentInChildren<Text>().text,
                Does.Contain("COMPLETE"));
        }

        private void CreateRestoredBootstrap()
        {
            var runtime = new GameObject("Restored Three World Runtime");
            runtime.SetActive(false);
            _slice = runtime.AddComponent<VerticalSliceRuntimeBootstrap>();
            _slice.Configure(
                _runner,
                deterministicAcceptanceData: true,
                runtimeSessionPath: _sessionPath);
            runtime.SetActive(true);
        }

        private static FlowCheckpoint MapCheckpoint()
        {
            return new FlowCheckpoint(
                FlowState.Map,
                null,
                combatVictoryPreserved: false,
                committedTrialIds: Array.Empty<string>(),
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
        }

        private static ProfileDataV1 CreateProfile()
        {
            return ProfileDataV1.CreateNew(
                "profile-threeworld-001",
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

        private static IEnumerator WaitFor(Func<bool> condition)
        {
            var deadline = Time.realtimeSinceStartup + 4f;
            while (Time.realtimeSinceStartup < deadline)
            {
                if (condition())
                    yield break;
                yield return null;
            }
            Assert.Fail("The three-world flow did not reach the expected state.");
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
            public CombatCommand NextCommand(
                CombatWorldState state,
                FighterSide side) => CombatCommand.None;
        }
    }
}
