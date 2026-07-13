using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using NUnit.Framework;
using Wayline.Flow;

namespace Wayline.Tests.Flow
{
    public sealed class VerticalSliceFlowControllerTests
    {
        [Test]
        public void TitleMapAndCombatProjectThroughInjectedPorts()
        {
            var fixture = new FlowFixture();

            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Title));
            fixture.Controller.EnterMap();
            fixture.Controller.StartCombat(fixture.Battle);

            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Combat));
            Assert.That(fixture.Combat.Presented, Is.EqualTo(new[] { fixture.Battle }));
            Assert.That(fixture.Campaign.Stored.Last().StableState,
                Is.EqualTo(FlowState.Combat));
            Assert.That(fixture.Campaign.PresentMapCount, Is.EqualTo(1));
        }

        [Test]
        public void VictoryIsPreservedBeforeNormalTrialAndDuplicateOutcomeIsIgnored()
        {
            var fixture = FlowFixture.InCombat();

            var applied = fixture.Controller.ResolveCombat(FlowCombatOutcome.Victory);
            var duplicate = fixture.Controller.ResolveCombat(FlowCombatOutcome.Victory);

            Assert.That(applied, Is.True);
            Assert.That(duplicate, Is.False);
            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(fixture.Controller.LastCheckpoint.CombatVictoryPreserved, Is.True);
            Assert.That(fixture.Campaign.PreservedVictories,
                Is.EqualTo(new[] { fixture.Battle }));
            Assert.That(fixture.Trial.Normal, Is.EqualTo(new[] { fixture.Battle }));
            CollectionAssert.AreEqual(
                new[] { "preserve", "save:NormalTrial", "trial:normal" },
                fixture.SharedLog.Where(value => value != "map" && value != "combat").ToArray());
        }

        [Test]
        public void DefeatRequiresQuestionsForEveryBattleAndCompletesWithoutVictoryProgress()
        {
            var battles = new[] { "valuehold", "decimara", "fracture" }
                .SelectMany(worldId => new[]
                {
                    "scout",
                    "rival",
                    "warden",
                    "lieutenant",
                    "boss"
                }.Select(tier => new FlowBattle(
                    worldId,
                    worldId + "-" + tier)))
                .ToArray();

            foreach (var battle in battles)
            {
                var sharedLog = new List<string>();
                var combat = new RecordingCombatPort(sharedLog);
                var trial = new RecordingTrialPort(sharedLog);
                var campaign = new RecordingCampaignPort(sharedLog);
                var controller = new VerticalSliceFlowController(combat, trial, campaign);
                controller.EnterMap();
                controller.StartCombat(battle);
                sharedLog.Clear();

                var applied = controller.ResolveCombat(FlowCombatOutcome.Defeat);
                var duplicate = controller.ResolveCombat(FlowCombatOutcome.Defeat);

                Assert.That(applied, Is.True, battle.BattleId);
                Assert.That(duplicate, Is.False, battle.BattleId);
                Assert.That(controller.State, Is.EqualTo(FlowState.LossTrial), battle.BattleId);
                Assert.That(controller.LastCheckpoint.Battle, Is.EqualTo(battle), battle.BattleId);
                Assert.That(controller.LastCheckpoint.CombatVictoryPreserved, Is.False);
                Assert.That(campaign.PresentMapCount, Is.EqualTo(1), battle.BattleId);
                Assert.That(campaign.PreservedVictories, Is.Empty, battle.BattleId);
                Assert.That(campaign.CommittedTrials, Is.Empty, battle.BattleId);
                Assert.That(campaign.PresentedRewards, Is.Empty, battle.BattleId);
                Assert.That(campaign.CommittedRewards, Is.Empty, battle.BattleId);
                Assert.That(trial.Loss, Is.EqualTo(new[] { battle }), battle.BattleId);

                Assert.That(controller.CompleteLossTrial(), Is.True, battle.BattleId);
                Assert.That(controller.CompleteLossTrial(), Is.False, battle.BattleId);
                Assert.That(controller.State, Is.EqualTo(FlowState.Map), battle.BattleId);
                Assert.That(controller.LastCheckpoint.Battle, Is.Null, battle.BattleId);
                Assert.That(campaign.PresentMapCount, Is.EqualTo(2), battle.BattleId);
                Assert.That(campaign.PreservedVictories, Is.Empty, battle.BattleId);
                Assert.That(campaign.CommittedTrials, Is.Empty, battle.BattleId);
                Assert.That(campaign.PresentedRewards, Is.Empty, battle.BattleId);
                Assert.That(campaign.CommittedRewards, Is.Empty, battle.BattleId);
            }
        }

        [Test]
        public void LossQuestionsCannotReturnToMapWhenLearningIsUnavailable()
        {
            var fixture = FlowFixture.InCombat();
            fixture.Controller.ResolveCombat(FlowCombatOutcome.Defeat);
            fixture.Controller.SuspendTrial("runtime_unavailable");

            Assert.That(
                () => fixture.Controller.ReturnToMapFromUnavailable(),
                Throws.InvalidOperationException);
            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Unavailable));
            Assert.That(fixture.Controller.HasPendingTrial, Is.True);
            Assert.That(fixture.Campaign.PresentMapCount, Is.EqualTo(1));

            fixture.Controller.RetryUnavailable();

            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.LossTrial));
            Assert.That(fixture.Controller.HasPendingTrial, Is.False);
            Assert.That(fixture.Trial.Loss, Has.Count.EqualTo(2));
        }

        [Test]
        public void TrialFailureCanReturnToMapAndResumeWithoutLosingVictory()
        {
            var fixture = FlowFixture.InNormalTrial();
            var pendingCheckpoint = fixture.Controller.LastCheckpoint;

            fixture.Controller.SuspendTrial("runtime_unavailable");
            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Unavailable));
            Assert.That(fixture.Controller.LastCheckpoint.StableState,
                Is.EqualTo(FlowState.NormalTrial));

            fixture.Controller.ReturnToMapFromUnavailable();
            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Map));
            Assert.That(fixture.Controller.LastCheckpoint.CombatVictoryPreserved, Is.True);
            Assert.That(fixture.Controller.HasPendingTrial, Is.True);
            Assert.Throws<InvalidOperationException>(() =>
                fixture.Controller.StartCombat(
                    new FlowBattle("valuehold", "valuehold-rival")));
            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Map));
            Assert.That(fixture.Controller.HasPendingTrial, Is.True);
            Assert.That(fixture.Controller.LastCheckpoint, Is.SameAs(pendingCheckpoint));

            fixture.Controller.ResumePending();

            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(fixture.Controller.HasPendingTrial, Is.False);
            Assert.That(fixture.Trial.Normal.Count, Is.EqualTo(2));
            Assert.That(fixture.Campaign.PreservedVictories.Count, Is.EqualTo(1));
        }

        [Test]
        public void AuthoritativeRewardCompletionAndRewardAcknowledgeAreIdempotent()
        {
            var fixture = FlowFixture.InNormalTrial();
            var result = fixture.Completion(
                "normal-complete-001",
                FlowTrialStage.Normal,
                AuthoritativeNextStep.Reward);

            Assert.That(fixture.Controller.CompleteTrial(result), Is.True);
            Assert.That(fixture.Controller.CompleteTrial(result), Is.False);
            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Reward));
            Assert.That(fixture.Campaign.CommittedTrials, Is.EqualTo(new[] { result }));
            Assert.That(fixture.Campaign.PresentedRewards, Has.Count.EqualTo(1));

            Assert.That(fixture.Controller.CompleteReward(), Is.True);
            Assert.That(fixture.Controller.CompleteReward(), Is.False);

            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Map));
            Assert.That(fixture.Campaign.CommittedRewards, Has.Count.EqualTo(1));
            Assert.That(fixture.Controller.LastCheckpoint.StableState,
                Is.EqualTo(FlowState.Map));
        }

        [Test]
        public void ServerDirectivesAloneRouteSealAndAssistedPlaceholders()
        {
            var fixture = FlowFixture.InNormalTrial();

            fixture.Controller.CompleteTrial(fixture.Completion(
                "normal-boss-001",
                FlowTrialStage.Normal,
                AuthoritativeNextStep.SealTrial));
            fixture.Controller.CompleteTrial(fixture.Completion(
                "seal-001",
                FlowTrialStage.Seal,
                AuthoritativeNextStep.SealTrial));
            fixture.Controller.CompleteTrial(fixture.Completion(
                "seal-002",
                FlowTrialStage.Seal,
                AuthoritativeNextStep.AssistedRoute));
            fixture.Controller.CompleteTrial(fixture.Completion(
                "assisted-001",
                FlowTrialStage.Assisted,
                AuthoritativeNextStep.Reward));

            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Reward));
            Assert.That(fixture.Trial.Seal.Count, Is.EqualTo(2));
            Assert.That(fixture.Trial.Assisted.Count, Is.EqualTo(1));
            Assert.That(fixture.Campaign.CommittedTrials.Count, Is.EqualTo(4));

            var forbidden = new[] { "Correct", "Score", "Answer", "Misconception" };
            var publicNames = typeof(AuthoritativeTrialCompletion)
                .GetMembers(BindingFlags.Public | BindingFlags.Instance)
                .Select(member => member.Name)
                .ToArray();
            foreach (var name in forbidden)
                Assert.That(publicNames, Has.None.Contains(name));
        }

        [Test]
        public void ReloadProjectsStableCheckpointWithoutReplayingCampaignMutation()
        {
            var battle = new FlowBattle("valuehold", "valuehold-boss");
            var completionId = "normal-complete-001";
            var checkpoint = new FlowCheckpoint(
                FlowState.Reward,
                battle,
                combatVictoryPreserved: true,
                committedTrialIds: new[] { completionId },
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: completionId,
                rewardAuthorityReceiptId: "receipt-001");
            var fixture = new FlowFixture();

            fixture.Controller.Restore(checkpoint);

            Assert.That(fixture.Controller.State, Is.EqualTo(FlowState.Reward));
            Assert.That(fixture.Campaign.PreservedVictories, Is.Empty);
            Assert.That(fixture.Campaign.CommittedTrials, Is.Empty);
            Assert.That(fixture.Campaign.Stored, Is.Empty);
            Assert.That(fixture.Campaign.PresentedRewards, Has.Count.EqualTo(1));
            Assert.That(fixture.Controller.CompleteTrial(new AuthoritativeTrialCompletion(
                completionId,
                "receipt-001",
                FlowTrialStage.Normal,
                battle,
                AuthoritativeNextStep.Reward)), Is.False);
        }

        [Test]
        public void InvalidStageIdentityAndImpossibleRoutingFailBeforeCampaignCommit()
        {
            var fixture = FlowFixture.InNormalTrial();

            Assert.Throws<InvalidOperationException>(() =>
                fixture.Controller.CompleteTrial(fixture.Completion(
                    "wrong-stage",
                    FlowTrialStage.Seal,
                    AuthoritativeNextStep.Reward)));
            Assert.Throws<ArgumentException>(() =>
                fixture.Controller.CompleteTrial(new AuthoritativeTrialCompletion(
                    "wrong-battle",
                    "receipt-wrong",
                    FlowTrialStage.Normal,
                    new FlowBattle("valuehold", "valuehold-rival"),
                    AuthoritativeNextStep.Reward)));
            Assert.Throws<ArgumentException>(() =>
                fixture.Controller.CompleteTrial(fixture.Completion(
                    "normal-cannot-assist",
                    FlowTrialStage.Normal,
                    AuthoritativeNextStep.AssistedRoute)));

            Assert.That(fixture.Campaign.CommittedTrials, Is.Empty);
        }

        [Test]
        public void RestoreProjectsEveryStablePresentationState()
        {
            foreach (var state in new[]
            {
                FlowState.Title,
                FlowState.Map,
                FlowState.Combat,
                FlowState.NormalTrial,
                FlowState.LossTrial,
                FlowState.SealTrial,
                FlowState.AssistedRoute
            })
            {
                var fixture = new FlowFixture();
                var hasBattle = state != FlowState.Title && state != FlowState.Map;
                fixture.Controller.Restore(new FlowCheckpoint(
                    state,
                    hasBattle ? fixture.Battle : null,
                    combatVictoryPreserved: state == FlowState.NormalTrial ||
                                              state == FlowState.SealTrial ||
                                              state == FlowState.AssistedRoute,
                    committedTrialIds: Array.Empty<string>(),
                    committedRewardIds: Array.Empty<string>(),
                    rewardSourceCompletionId: null,
                    rewardAuthorityReceiptId: null));
                Assert.That(fixture.Controller.State, Is.EqualTo(state));
            }
        }

        [Test]
        public void CheckpointRejectsBattleIdentityOutsideBattleStates()
        {
            foreach (var state in new[] { FlowState.Title, FlowState.Map })
            {
                Assert.That(
                    () => new FlowCheckpoint(
                        state,
                        new FlowBattle("valuehold", "valuehold-scout"),
                        combatVictoryPreserved: false,
                        committedTrialIds: Array.Empty<string>(),
                        committedRewardIds: Array.Empty<string>(),
                        rewardSourceCompletionId: null,
                        rewardAuthorityReceiptId: null),
                    Throws.ArgumentException);
            }
        }

        [Test]
        public void CheckpointRejectsUnpreservedVictoryForPostCombatStates()
        {
            foreach (var state in new[]
            {
                FlowState.NormalTrial,
                FlowState.SealTrial,
                FlowState.AssistedRoute,
                FlowState.Reward
            })
            {
                var completionIds = state == FlowState.Reward
                    ? new[] { "normal-complete-001" }
                    : Array.Empty<string>();
                Assert.That(
                    () => new FlowCheckpoint(
                        state,
                        new FlowBattle("valuehold", "valuehold-scout"),
                        combatVictoryPreserved: false,
                        committedTrialIds: completionIds,
                        committedRewardIds: Array.Empty<string>(),
                        rewardSourceCompletionId: state == FlowState.Reward
                            ? "normal-complete-001"
                            : null,
                        rewardAuthorityReceiptId: state == FlowState.Reward
                            ? "receipt-001"
                            : null),
                    Throws.ArgumentException);
            }
        }

        [Test]
        public void CheckpointRejectsRewardMetadataOutsideRewardState()
        {
            Assert.That(
                () => new FlowCheckpoint(
                    FlowState.NormalTrial,
                    new FlowBattle("valuehold", "valuehold-scout"),
                    combatVictoryPreserved: true,
                    committedTrialIds: new[] { "normal-complete-001" },
                    committedRewardIds: Array.Empty<string>(),
                    rewardSourceCompletionId: "normal-complete-001",
                    rewardAuthorityReceiptId: "receipt-001"),
                Throws.ArgumentException);
        }

        [Test]
        public void RewardCheckpointRequiresAnUnclaimedCommittedSource()
        {
            var battle = new FlowBattle("valuehold", "valuehold-scout");

            Assert.That(
                () => new FlowCheckpoint(
                    FlowState.Reward,
                    battle,
                    combatVictoryPreserved: true,
                    committedTrialIds: Array.Empty<string>(),
                    committedRewardIds: Array.Empty<string>(),
                    rewardSourceCompletionId: "normal-complete-001",
                    rewardAuthorityReceiptId: "receipt-001"),
                Throws.ArgumentException);
            Assert.That(
                () => new FlowCheckpoint(
                    FlowState.Reward,
                    battle,
                    combatVictoryPreserved: true,
                    committedTrialIds: new[] { "normal-complete-001" },
                    committedRewardIds: new[] { "normal-complete-001" },
                    rewardSourceCompletionId: "normal-complete-001",
                    rewardAuthorityReceiptId: "receipt-001"),
                Throws.ArgumentException);
        }

        [Test]
        public void CheckpointRejectsOrphanCommittedRewardIdentity()
        {
            Assert.That(
                () => new FlowCheckpoint(
                    FlowState.Map,
                    null,
                    combatVictoryPreserved: false,
                    committedTrialIds: new[] { "normal-complete-001" },
                    committedRewardIds: new[] { "orphan-completion-999" },
                    rewardSourceCompletionId: null,
                    rewardAuthorityReceiptId: null),
                Throws.ArgumentException);
        }
    }

    internal sealed class FlowFixture
    {
        public FlowFixture()
        {
            Combat = new RecordingCombatPort(SharedLog);
            Trial = new RecordingTrialPort(SharedLog);
            Campaign = new RecordingCampaignPort(SharedLog);
            Controller = new VerticalSliceFlowController(Combat, Trial, Campaign);
        }

        public List<string> SharedLog { get; } = new List<string>();

        public RecordingCombatPort Combat { get; }

        public RecordingTrialPort Trial { get; }

        public RecordingCampaignPort Campaign { get; }

        public VerticalSliceFlowController Controller { get; }

        public FlowBattle Battle { get; } =
            new FlowBattle("valuehold", "valuehold-scout");

        public static FlowFixture InCombat()
        {
            var fixture = new FlowFixture();
            fixture.Controller.EnterMap();
            fixture.Controller.StartCombat(fixture.Battle);
            fixture.SharedLog.Clear();
            return fixture;
        }

        public static FlowFixture InNormalTrial()
        {
            var fixture = InCombat();
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

    internal sealed class RecordingCombatPort : ICombatFlowPort
    {
        private readonly IList<string> _log;

        public RecordingCombatPort(IList<string> log) => _log = log;

        public List<FlowBattle> Presented { get; } = new List<FlowBattle>();

        public void PresentCombat(FlowBattle battle)
        {
            Presented.Add(battle);
            _log.Add("combat");
        }
    }

    internal sealed class RecordingTrialPort : ITrialFlowPort
    {
        private readonly IList<string> _log;

        public RecordingTrialPort(IList<string> log) => _log = log;

        public List<FlowBattle> Normal { get; } = new List<FlowBattle>();
        public List<FlowBattle> Loss { get; } = new List<FlowBattle>();
        public List<FlowBattle> Seal { get; } = new List<FlowBattle>();
        public List<FlowBattle> Assisted { get; } = new List<FlowBattle>();

        public void PresentNormalTrial(FlowBattle battle)
        {
            Normal.Add(battle);
            _log.Add("trial:normal");
        }

        public void PresentLossTrial(FlowBattle battle)
        {
            Loss.Add(battle);
            _log.Add("trial:loss");
        }

        public void PresentSealTrial(FlowBattle battle)
        {
            Seal.Add(battle);
            _log.Add("trial:seal");
        }

        public void PresentAssistedRoute(FlowBattle battle)
        {
            Assisted.Add(battle);
            _log.Add("trial:assisted");
        }
    }

    internal sealed class RecordingCampaignPort : ICampaignFlowPort
    {
        private readonly IList<string> _log;

        public RecordingCampaignPort(IList<string> log) => _log = log;

        public int PresentMapCount { get; private set; }
        public List<FlowBattle> PreservedVictories { get; } = new List<FlowBattle>();
        public List<AuthoritativeTrialCompletion> CommittedTrials { get; } =
            new List<AuthoritativeTrialCompletion>();
        public List<RewardPresentation> PresentedRewards { get; } =
            new List<RewardPresentation>();
        public List<RewardPresentation> CommittedRewards { get; } =
            new List<RewardPresentation>();
        public List<FlowCheckpoint> Stored { get; } = new List<FlowCheckpoint>();

        public void PresentMap()
        {
            PresentMapCount++;
            _log.Add("map");
        }

        public void PreserveCombatVictory(FlowBattle battle)
        {
            PreservedVictories.Add(battle);
            _log.Add("preserve");
        }

        public void CommitAuthoritativeTrial(AuthoritativeTrialCompletion completion)
        {
            CommittedTrials.Add(completion);
            _log.Add("commit:trial");
        }

        public void PresentReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId)
        {
            PresentedRewards.Add(new RewardPresentation(
                battle,
                sourceCompletionId,
                authorityReceiptId));
            _log.Add("reward");
        }

        public void CommitReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId)
        {
            CommittedRewards.Add(new RewardPresentation(
                battle,
                sourceCompletionId,
                authorityReceiptId));
            _log.Add("commit:reward");
        }

        public void StoreCheckpoint(FlowCheckpoint checkpoint)
        {
            Stored.Add(checkpoint);
            _log.Add("save:" + checkpoint.StableState);
        }
    }

    internal sealed class RewardPresentation
    {
        public RewardPresentation(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId)
        {
            Battle = battle;
            SourceCompletionId = sourceCompletionId;
            AuthorityReceiptId = authorityReceiptId;
        }

        public FlowBattle Battle { get; }
        public string SourceCompletionId { get; }
        public string AuthorityReceiptId { get; }
    }
}
