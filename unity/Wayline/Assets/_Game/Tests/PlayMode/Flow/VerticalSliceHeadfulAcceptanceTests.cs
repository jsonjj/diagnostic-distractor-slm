using System;
using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using UnityEngine.UI;
using Wayline.Combat.Simulation;
using Wayline.Flow;
using Wayline.Flow.Unity;
using Wayline.Gameplay;
using Wayline.Learning.Quiz;
using Wayline.UI;

namespace Wayline.Tests.Flow
{
    public sealed class VerticalSliceHeadfulAcceptanceTests
    {
        [Test]
        public void ProductionSymbolsCannotSelectDeterministicAcceptanceContent()
        {
            Assert.That(
                DeterministicAcceptanceGate.CanSelect(
                    unityEditor: false,
                    developmentBuild: false),
                Is.False);
        }

        [UnityTest]
        public IEnumerator ArenaComposesCombatTrialRewardAndRealCampaignState()
        {
            var operation = SceneManager.LoadSceneAsync("Arena_Graybox", LoadSceneMode.Single);
            while (!operation.isDone)
                yield return null;
            yield return null;

            var slice = UnityEngine.Object.FindFirstObjectByType<VerticalSliceRuntimeBootstrap>();
            Assert.That(slice, Is.Not.Null);
            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Title));
            Assert.That(slice.AcceptanceDataLabel.text, Is.EqualTo(
                "DETERMINISTIC LOCAL ACCEPTANCE DATA — NOT LIVE SLM"));

            slice.EnterMapButton.onClick.Invoke();
            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Map));
            slice.StartBattleButton.onClick.Invoke();
            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Combat));

            slice.Runner.RunAutomatically = false;
            slice.Runner.SetCommandSources(
                new AcceptanceAggressiveSource(),
                new AcceptanceIdleSource());
            for (var frame = 0;
                 frame < 1000 &&
                 slice.Runner.State.Result == CombatResult.InProgress;
                 frame++)
            {
                slice.Runner.AdvanceFrame(1.0 / 60.0);
            }
            Assert.That(slice.Runner.State.Result, Is.EqualTo(CombatResult.PlayerWon));

            yield return WaitForTrial(slice);

            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(slice.TrialPanel.View, Is.EqualTo(AtlasTrialView.Answering));
            for (var item = 0; item < 3; item++)
            {
                slice.TrialPanel.QuestionPage.OptionButtons[0].onClick.Invoke();
                slice.TrialPanel.QuestionPage.ConfidenceControl.Buttons[item % 3]
                    .onClick.Invoke();
                slice.TrialPanel.PrimaryButton.onClick.Invoke();
                yield return null;
            }

            Assert.That(slice.TrialPanel.View, Is.EqualTo(AtlasTrialView.WrongCount));
            Assert.That(slice.TrialPanel.WrongCountPanel.ExactCountText.text,
                Is.EqualTo("0 of 3\nanswers are incorrect"));
            slice.TrialPanel.PrimaryButton.onClick.Invoke();
            yield return null;

            for (var advance = 0;
                 advance < 12 && slice.TrialController.State != QuizState.Complete;
                 advance++)
            {
                slice.TrialPanel.PrimaryButton.onClick.Invoke();
                yield return null;
            }
            Assert.That(slice.TrialController.State, Is.EqualTo(QuizState.Complete));
            yield return WaitFor(
                () => slice.Flow.State == FlowState.Reward &&
                      slice.RewardButton.gameObject.activeInHierarchy,
                120,
                "The authoritative completion did not reach the reward state.");

            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Reward));
            Assert.That(slice.Profile.HasCombatVictory(slice.Battle.BattleId), Is.True);
            Assert.That(slice.Profile.IsBattleCompleted(slice.Battle.BattleId), Is.True);
            Assert.That(slice.Profile.HasRewardedBattle(slice.Battle.BattleId), Is.True);
            Assert.That(slice.RewardButton.gameObject.activeInHierarchy, Is.True);
            Canvas.ForceUpdateCanvases();
            Assert.That(slice.Runner.Hud.enabled, Is.False,
                "Combat HUD must not bleed through the reward presentation.");
            var meridian = GameObject.Find("Meridian line").GetComponent<RectTransform>();
            Assert.That(meridian.rect.height, Is.LessThanOrEqualTo(6f),
                "The meridian signature must remain a narrow line, not a decorative band.");
            Assert.That(slice.AcceptanceDataLabel.gameObject.activeInHierarchy, Is.True);
            var labelCorners = new Vector3[4];
            ((RectTransform)slice.AcceptanceDataLabel.transform).GetWorldCorners(labelCorners);
            Assert.That(labelCorners[0].y, Is.GreaterThanOrEqualTo(0f));
            Assert.That(labelCorners[2].y, Is.LessThanOrEqualTo(Screen.height + 0.5f),
                "The acceptance-data warning must be fully inside the rendered frame.");

            var capturePath = Environment.GetEnvironmentVariable(
                "WAYLINE_VERTICAL_SLICE_CAPTURE");
            if (!string.IsNullOrWhiteSpace(capturePath))
            {
                ScreenCapture.CaptureScreenshot(capturePath, 1);
                yield return null;
                yield return null;
                yield return null;
            }

            slice.RewardButton.onClick.Invoke();
            yield return WaitFor(
                () => slice.Flow.State == FlowState.Map,
                120,
                "Reward acknowledgement did not return to the map.");
            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Map));
            Assert.That(slice.Flow.LastCheckpoint.StableState, Is.EqualTo(FlowState.Map));
        }

        [UnityTest]
        public IEnumerator PendingTrialMapActionResumesWithoutReplayingCombat()
        {
            var operation = SceneManager.LoadSceneAsync("Arena_Graybox", LoadSceneMode.Single);
            while (!operation.isDone)
                yield return null;
            yield return null;

            var slice = UnityEngine.Object.FindFirstObjectByType<VerticalSliceRuntimeBootstrap>();
            Assert.That(slice, Is.Not.Null);
            slice.EnterMapButton.onClick.Invoke();
            slice.StartBattleButton.onClick.Invoke();
            slice.Runner.RunAutomatically = false;
            slice.Runner.SetCommandSources(
                new AcceptanceAggressiveSource(),
                new AcceptanceIdleSource());
            for (var frame = 0;
                 frame < 1000 &&
                 slice.Runner.State.Result == CombatResult.InProgress;
                 frame++)
            {
                slice.Runner.AdvanceFrame(1.0 / 60.0);
            }
            Assert.That(slice.Runner.State.Result, Is.EqualTo(CombatResult.PlayerWon));
            yield return WaitForTrial(slice);

            var pending = slice.Flow.LastCheckpoint;
            var combatSnapshot = slice.Runner.SerializeSnapshot();
            var victoryCount = slice.Profile.CombatVictoryBattleIds.Length;
            slice.Flow.SuspendTrial("headful_pending_test");
            slice.Flow.ReturnToMapFromUnavailable();

            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.Map));
            Assert.That(slice.Flow.HasPendingTrial, Is.True);
            Assert.That(
                slice.StartBattleButton.GetComponentInChildren<Text>().text,
                Is.EqualTo("RESUME ROUTE TRIAL"));

            slice.StartBattleButton.onClick.Invoke();
            yield return null;

            Assert.That(slice.Flow.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(slice.Flow.LastCheckpoint, Is.SameAs(pending));
            Assert.That(slice.Flow.HasPendingTrial, Is.False);
            Assert.That(slice.Profile.CombatVictoryBattleIds.Length, Is.EqualTo(victoryCount));
            Assert.That(slice.Runner.SerializeSnapshot(), Is.EqualTo(combatSnapshot));
        }

        private static IEnumerator WaitFor(
            Func<bool> condition,
            int maximumFrames,
            string failureMessage)
        {
            for (var frame = 0; frame < maximumFrames; frame++)
            {
                if (condition())
                    yield break;

                yield return null;
            }

            Assert.Fail(failureMessage);
        }

        private static IEnumerator WaitForTrial(VerticalSliceRuntimeBootstrap slice)
        {
            var deadline = Time.realtimeSinceStartup + 2f;
            while (Time.realtimeSinceStartup < deadline)
            {
                if (slice.Flow.State == FlowState.NormalTrial &&
                    slice.TrialPanel != null &&
                    slice.TrialPanel.Interactive)
                {
                    yield break;
                }

                yield return null;
            }

            Assert.Fail(
                "The composed route trial did not become interactive. " +
                $"Flow={slice.Flow?.State}, " +
                $"Panel={slice.TrialPanel?.View}, " +
                $"Controller={slice.TrialController?.State}, " +
                $"Failure={slice.TrialController?.LastFailureCode}");
        }

        private sealed class AcceptanceAggressiveSource : ICombatCommandSource
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

        private sealed class AcceptanceIdleSource : ICombatCommandSource
        {
            public CombatCommand NextCommand(CombatWorldState state, FighterSide side) =>
                CombatCommand.None;
        }
    }
}
