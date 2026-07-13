using System;
using System.Collections;
using System.Collections.Generic;
using NUnit.Framework;
using UnityEngine.TestTools;
using Wayline.Flow;

namespace Wayline.Tests.Flow
{
    public sealed class VerticalSliceFlowPlayModeTests
    {
        [UnityTest]
        public IEnumerator VictoryTrialRewardMapSequenceSurvivesFrameBoundaries()
        {
            var fixture = new PlayModeFlowFixture();
            fixture.Controller.EnterMap();
            fixture.Controller.StartCombat(fixture.Battle);
            yield return null;

            fixture.Controller.ResolveCombat(FlowCombatOutcome.Victory);
            yield return null;

            var completion = fixture.Completion(
                "normal-complete-play-001",
                FlowTrialStage.Normal,
                AuthoritativeNextStep.Reward);
            fixture.Controller.CompleteTrial(completion);
            fixture.Controller.CompleteTrial(completion);
            yield return null;

            fixture.Controller.CompleteReward();
            fixture.Controller.CompleteReward();
            yield return null;

            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Map));
            Assert.That(fixture.Campaign.PreservedVictories, Has.Count.EqualTo(1));
            Assert.That(fixture.Campaign.CommittedTrials, Has.Count.EqualTo(1));
            Assert.That(fixture.Campaign.CommittedRewards, Has.Count.EqualTo(1));
        }

        [UnityTest]
        public IEnumerator ReloadedPendingTrialReturnsFromMapWithoutReplayingVictory()
        {
            var first = PlayModeFlowFixture.InNormalTrial();
            var checkpoint = first.Controller.LastCheckpoint;
            var reloaded = new PlayModeFlowFixture();

            reloaded.Controller.Restore(checkpoint);
            reloaded.Controller.SuspendTrial("runtime_unavailable");
            reloaded.Controller.ReturnToMapFromUnavailable();
            yield return null;
            reloaded.Controller.ResumePending();
            yield return null;

            Assert.That(reloaded.Controller.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(reloaded.Controller.LastCheckpoint.CombatVictoryPreserved, Is.True);
            Assert.That(reloaded.Campaign.PreservedVictories, Is.Empty);
            Assert.That(reloaded.Trial.Normal, Has.Count.EqualTo(2));
        }
    }

    internal sealed class PlayModeFlowFixture
    {
        public PlayModeFlowFixture()
        {
            Combat = new PlayModeCombatPort();
            Trial = new PlayModeTrialPort();
            Campaign = new PlayModeCampaignPort();
            Controller = new VerticalSliceFlowController(Combat, Trial, Campaign);
        }

        public PlayModeCombatPort Combat { get; }

        public PlayModeTrialPort Trial { get; }

        public PlayModeCampaignPort Campaign { get; }

        public VerticalSliceFlowController Controller { get; }

        public FlowBattle Battle { get; } =
            new FlowBattle("valuehold", "valuehold-scout");

        public static PlayModeFlowFixture InNormalTrial()
        {
            var fixture = new PlayModeFlowFixture();
            fixture.Controller.EnterMap();
            fixture.Controller.StartCombat(fixture.Battle);
            fixture.Controller.ResolveCombat(FlowCombatOutcome.Victory);
            return fixture;
        }

        public AuthoritativeTrialCompletion Completion(
            string id,
            FlowTrialStage stage,
            AuthoritativeNextStep next)
        {
            return new AuthoritativeTrialCompletion(
                id,
                "receipt-" + id,
                stage,
                Battle,
                next);
        }
    }

    internal sealed class PlayModeCombatPort : ICombatFlowPort
    {
        public List<FlowBattle> Presented { get; } = new List<FlowBattle>();

        public void PresentCombat(FlowBattle battle) => Presented.Add(battle);
    }

    internal sealed class PlayModeTrialPort : ITrialFlowPort
    {
        public List<FlowBattle> Normal { get; } = new List<FlowBattle>();
        public List<FlowBattle> Loss { get; } = new List<FlowBattle>();
        public List<FlowBattle> Seal { get; } = new List<FlowBattle>();
        public List<FlowBattle> Assisted { get; } = new List<FlowBattle>();

        public void PresentNormalTrial(FlowBattle battle) => Normal.Add(battle);

        public void PresentLossTrial(FlowBattle battle) => Loss.Add(battle);

        public void PresentSealTrial(FlowBattle battle) => Seal.Add(battle);

        public void PresentAssistedRoute(FlowBattle battle) => Assisted.Add(battle);
    }

    internal sealed class PlayModeCampaignPort : ICampaignFlowPort
    {
        public int PresentMapCount { get; private set; }
        public List<FlowBattle> PreservedVictories { get; } = new List<FlowBattle>();
        public List<AuthoritativeTrialCompletion> CommittedTrials { get; } =
            new List<AuthoritativeTrialCompletion>();
        public List<string> PresentedRewards { get; } = new List<string>();
        public List<string> CommittedRewards { get; } = new List<string>();
        public List<FlowCheckpoint> Stored { get; } = new List<FlowCheckpoint>();

        public void PresentMap() => PresentMapCount++;

        public void PreserveCombatVictory(FlowBattle battle) => PreservedVictories.Add(battle);

        public void CommitAuthoritativeTrial(AuthoritativeTrialCompletion completion) =>
            CommittedTrials.Add(completion);

        public void PresentReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId) => PresentedRewards.Add(sourceCompletionId);

        public void CommitReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId) => CommittedRewards.Add(sourceCompletionId);

        public void StoreCheckpoint(FlowCheckpoint checkpoint) => Stored.Add(checkpoint);
    }
}
