using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using NUnit.Framework;
using Wayline.Campaign;
using Wayline.Characters;
using Wayline.Flow;
using Wayline.Flow.Runtime;
using Wayline.Flow.Unity;
using Wayline.Save;

namespace Wayline.Tests.Flow
{
    public sealed class RuntimeCampaignFlowAdapterTests
    {
        [Test]
        public void VictorySaveFailureRollsBackAndRetryDoesNotPreserveTwice()
        {
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            var map = new FlowCheckpoint(
                FlowState.Map,
                null,
                combatVictoryPreserved: false,
                committedTrialIds: Array.Empty<string>(),
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
            var mutations = new RecordingRuntimeMutations();
            var persistence = new FailingRuntimePersistence();
            var campaign = new RuntimeCampaignFlowAdapter(
                mutations,
                new RecordingRuntimePresentation(),
                persistence,
                map,
                Array.Empty<FlowBattle>());
            var controller = new VerticalSliceFlowController(
                new RecordingCombatPort(new List<string>()),
                new RecordingTrialPort(new List<string>()),
                campaign);
            controller.Restore(map);
            controller.StartCombat(battle);
            persistence.FailNextStore = true;

            Assert.Throws<IOException>(() =>
                controller.ResolveCombat(FlowCombatOutcome.Victory));

            Assert.That(controller.State, Is.EqualTo(FlowState.Combat));
            Assert.That(controller.LastCheckpoint.StableState, Is.EqualTo(FlowState.Combat));
            Assert.That(controller.LastCheckpoint.CombatVictoryPreserved, Is.False);
            Assert.That(mutations.PreservedVictories, Is.EqualTo(new[] { battle.BattleId }));

            Assert.That(controller.ResolveCombat(FlowCombatOutcome.Victory), Is.True);
            Assert.That(controller.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(mutations.PreservedVictories, Has.Count.EqualTo(1));
        }

        [Test]
        public void SaveFailureRollsFlowBackAndRetryDoesNotReplayCampaignMutation()
        {
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            var restored = PendingTrial(battle);
            var mutations = new RecordingRuntimeMutations();
            var presentation = new RecordingRuntimePresentation();
            var persistence = new FailingRuntimePersistence { FailNextStore = true };
            var campaign = new RuntimeCampaignFlowAdapter(
                mutations,
                presentation,
                persistence,
                restored,
                new[] { battle });
            var trial = new RecordingTrialPort(new List<string>());
            var controller = new VerticalSliceFlowController(
                new RecordingCombatPort(new List<string>()),
                trial,
                campaign);
            controller.Restore(restored);
            var completion = new AuthoritativeTrialCompletion(
                "normal-complete-001",
                "receipt-normal-complete-001",
                FlowTrialStage.Normal,
                battle,
                AuthoritativeNextStep.Reward);

            Assert.Throws<IOException>(() => controller.CompleteTrial(completion));

            Assert.That(controller.State, Is.EqualTo(FlowState.NormalTrial));
            Assert.That(controller.LastCheckpoint.CommittedTrialIds, Is.Empty);
            Assert.That(mutations.CommittedTrials, Is.EqualTo(new[] { completion.CompletionId }));

            Assert.That(controller.CompleteTrial(completion), Is.True);
            Assert.That(controller.State, Is.EqualTo(FlowState.Reward));
            Assert.That(mutations.CommittedTrials, Has.Count.EqualTo(1));
            Assert.That(persistence.Stored, Has.Count.EqualTo(1));

            var reloadedMutations = new RecordingRuntimeMutations();
            var reloaded = new RuntimeCampaignFlowAdapter(
                reloadedMutations,
                new RecordingRuntimePresentation(),
                new FailingRuntimePersistence(),
                controller.LastCheckpoint,
                new[] { battle });
            reloaded.CommitAuthoritativeTrial(completion);

            Assert.That(reloadedMutations.CommittedTrials, Is.Empty);
        }

        [Test]
        public void PresentationFailureKeepsPersistedCompletionAndCannotReplayMutation()
        {
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            var restored = PendingTrial(battle);
            var mutations = new RecordingRuntimeMutations();
            var presentation = new RecordingRuntimePresentation
            {
                FailNextRewardPresentation = true
            };
            var persistence = new FailingRuntimePersistence();
            var campaign = new RuntimeCampaignFlowAdapter(
                mutations,
                presentation,
                persistence,
                restored,
                new[] { battle });
            var controller = new VerticalSliceFlowController(
                new RecordingCombatPort(new List<string>()),
                new RecordingTrialPort(new List<string>()),
                campaign);
            controller.Restore(restored);
            var completion = new AuthoritativeTrialCompletion(
                "normal-complete-001",
                "receipt-normal-complete-001",
                FlowTrialStage.Normal,
                battle,
                AuthoritativeNextStep.Reward);

            Assert.Throws<InvalidOperationException>(() =>
                controller.CompleteTrial(completion));

            Assert.That(controller.State, Is.EqualTo(FlowState.Reward));
            Assert.That(controller.LastCheckpoint.CommittedTrialIds,
                Is.EqualTo(new[] { completion.CompletionId }));
            Assert.That(persistence.Stored, Has.Count.EqualTo(1));
            Assert.That(controller.CompleteTrial(completion), Is.False);
            Assert.That(mutations.CommittedTrials, Has.Count.EqualTo(1));
        }

        [Test]
        public void RewardSaveFailureRollsBackAndRetryDoesNotCommitRewardTwice()
        {
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            var reward = new FlowCheckpoint(
                FlowState.Reward,
                battle,
                combatVictoryPreserved: true,
                committedTrialIds: new[] { "normal-complete-001" },
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: "normal-complete-001",
                rewardAuthorityReceiptId: "receipt-normal-complete-001");
            var mutations = new RecordingRuntimeMutations();
            var persistence = new FailingRuntimePersistence { FailNextStore = true };
            var campaign = new RuntimeCampaignFlowAdapter(
                mutations,
                new RecordingRuntimePresentation(),
                persistence,
                reward,
                new[] { battle });
            var controller = new VerticalSliceFlowController(
                new RecordingCombatPort(new List<string>()),
                new RecordingTrialPort(new List<string>()),
                campaign);
            controller.Restore(reward);

            Assert.Throws<IOException>(() => controller.CompleteReward());

            Assert.That(controller.State, Is.EqualTo(FlowState.Reward));
            Assert.That(controller.LastCheckpoint.StableState, Is.EqualTo(FlowState.Reward));
            Assert.That(controller.LastCheckpoint.CommittedRewardIds, Is.Empty);
            Assert.That(mutations.CommittedRewards,
                Is.EqualTo(new[] { "normal-complete-001" }));

            Assert.That(controller.CompleteReward(), Is.True);
            Assert.That(controller.State, Is.EqualTo(FlowState.Map));
            Assert.That(mutations.CommittedRewards, Has.Count.EqualTo(1));
        }

        [Test]
        public void VictoryAndRewardMutationsAreIdempotentByStableAuthorityIdentity()
        {
            var battle = new FlowBattle("valuehold", "valuehold-scout");
            var rewardCheckpoint = new FlowCheckpoint(
                FlowState.Reward,
                battle,
                combatVictoryPreserved: true,
                committedTrialIds: new[] { "normal-complete-001" },
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: "normal-complete-001",
                rewardAuthorityReceiptId: "receipt-normal-complete-001");
            var mutations = new RecordingRuntimeMutations();
            var adapter = new RuntimeCampaignFlowAdapter(
                mutations,
                new RecordingRuntimePresentation(),
                new FailingRuntimePersistence(),
                rewardCheckpoint,
                new[] { battle });

            adapter.PreserveCombatVictory(battle);
            adapter.PreserveCombatVictory(battle);
            adapter.CommitReward(
                battle,
                "normal-complete-001",
                "receipt-normal-complete-001");
            adapter.CommitReward(
                battle,
                "normal-complete-001",
                "receipt-normal-complete-001");

            Assert.That(mutations.PreservedVictories, Is.Empty);
            Assert.That(mutations.CommittedRewards,
                Is.EqualTo(new[] { "normal-complete-001" }));
        }

        [Test]
        public void SealPassRegistrationRetryKeepsOriginalMutationAndAppliesAtMostOnce()
        {
            var world = CreateWorld();
            var profile = CreateProfile();
            profile.RecordCombatVictory(world.BossBattle.Id);
            profile.SetPending(CampaignStep.SealTrial.ToString(), world.Id);
            var campaign = new CampaignController(
                new[] { world },
                profile,
                new RewardController(maxFocusPerTrial: 3));
            var mutations = new CampaignControllerMutations(campaign);
            var battle = new FlowBattle(world.Id, world.BossBattle.Id);
            var completion = new AuthoritativeTrialCompletion(
                "seal-complete-stable-001",
                "receipt-seal-complete-stable-001",
                FlowTrialStage.Seal,
                battle,
                AuthoritativeNextStep.Reward);

            mutations.RegisterAuthoritativeTrial(
                completion.CompletionId,
                battle,
                controller => controller.ApplySealTrial(new SealTrialResolution(
                    world.Id,
                    attemptNumber: 1,
                    passed: true,
                    worldCleared: true,
                    assistedRouteUnlocked: false)));
            mutations.RegisterAuthoritativeTrial(
                completion.CompletionId,
                battle,
                controller => throw new InvalidOperationException(
                    "A retry must never replace the original trusted mutation."));

            mutations.CommitAuthoritativeTrial(completion);
            mutations.CommitAuthoritativeTrial(completion);

            Assert.That(profile.IsWorldCleared(world.Id), Is.True);
            Assert.That(profile.PendingStep, Is.Null);
        }

        [Test]
        public void AssistedClearCommitRetryAppliesPendingMutationAtMostOnce()
        {
            var world = CreateWorld();
            var profile = CreateProfile();
            profile.RecordCombatVictory(world.BossBattle.Id);
            profile.SetPending(CampaignStep.AssistedRoute.ToString(), world.Id);
            var campaign = new CampaignController(
                new[] { world },
                profile,
                new RewardController(maxFocusPerTrial: 3));
            var mutations = new CampaignControllerMutations(campaign);
            var battle = new FlowBattle(world.Id, world.BossBattle.Id);
            var completion = new AuthoritativeTrialCompletion(
                "assisted-complete-stable-001",
                "receipt-assisted-complete-stable-001",
                FlowTrialStage.Assisted,
                battle,
                AuthoritativeNextStep.Reward);
            mutations.RegisterAuthoritativeTrial(
                completion.CompletionId,
                battle,
                controller => controller.ApplyAssistedRoute(new AssistedRouteResolution(
                    world.Id,
                    finalCorrect: 0,
                    itemCount: 2,
                    worldCleared: true)));

            mutations.CommitAuthoritativeTrial(completion);
            mutations.CommitAuthoritativeTrial(completion);

            Assert.That(profile.IsWorldCleared(world.Id), Is.True);
            Assert.That(profile.PendingStep, Is.Null);
        }

        [Test]
        public void CompletionIdentityCannotBeReboundToAnotherBattle()
        {
            var world = CreateWorld();
            var campaign = new CampaignController(
                new[] { world },
                CreateProfile(),
                new RewardController(maxFocusPerTrial: 3));
            var mutations = new CampaignControllerMutations(campaign);
            mutations.RegisterAuthoritativeTrial(
                "completion-bound-001",
                new FlowBattle(world.Id, world.LeadInBattles[0].Id),
                _ => { });

            Assert.Throws<InvalidOperationException>(() =>
                mutations.RegisterAuthoritativeTrial(
                    "completion-bound-001",
                    new FlowBattle(world.Id, world.LeadInBattles[1].Id),
                    _ => { }));
        }

        [Test]
        public void SuccessfulCompletionKeepsCommitGuardOpenForANextTrialStage()
        {
            var helper = typeof(VerticalSliceRuntimeBootstrap).GetMethod(
                "ShouldCloseTrialCommitGuard",
                BindingFlags.Static | BindingFlags.NonPublic);

            Assert.That(helper, Is.Not.Null);
            Assert.That(
                (bool)helper.Invoke(null, new object[] { FlowState.SealTrial }),
                Is.False);
            Assert.That(
                (bool)helper.Invoke(null, new object[] { FlowState.AssistedRoute }),
                Is.False);
            Assert.That(
                (bool)helper.Invoke(null, new object[] { FlowState.Reward }),
                Is.True);
        }

        private static FlowCheckpoint PendingTrial(FlowBattle battle)
        {
            return new FlowCheckpoint(
                FlowState.NormalTrial,
                battle,
                combatVictoryPreserved: true,
                committedTrialIds: Array.Empty<string>(),
                committedRewardIds: Array.Empty<string>(),
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
        }

        private static ProfileDataV1 CreateProfile()
        {
            return ProfileDataV1.CreateNew(
                "profile-mutation-test",
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
                    new BattleDefinition("valuehold-scout", BattleTier.Scout,
                        "ai-scout", "surveyor-scout", 10, "card-scout"),
                    new BattleDefinition("valuehold-rival", BattleTier.Rival,
                        "ai-rival", "surveyor-rival", 12, "card-rival"),
                    new BattleDefinition("valuehold-warden", BattleTier.Warden,
                        "ai-warden", "surveyor-warden", 14, "card-warden"),
                    new BattleDefinition("valuehold-lieutenant", BattleTier.Lieutenant,
                        "ai-lieutenant", "surveyor-lieutenant", 16, "card-lieutenant"),
                    new BattleDefinition("valuehold-boss", BattleTier.Boss,
                        "ai-boss", "surveyor-general", 25, "card-boss")
                });
        }
    }

    internal sealed class RecordingRuntimeMutations : IRuntimeCampaignMutations
    {
        public List<string> PreservedVictories { get; } = new List<string>();
        public List<string> CommittedTrials { get; } = new List<string>();
        public List<string> CommittedRewards { get; } = new List<string>();

        public void PreserveCombatVictory(FlowBattle battle) =>
            PreservedVictories.Add(battle.BattleId);

        public void CommitAuthoritativeTrial(AuthoritativeTrialCompletion completion) =>
            CommittedTrials.Add(completion.CompletionId);

        public void CommitReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId) => CommittedRewards.Add(sourceCompletionId);
    }

    internal sealed class RecordingRuntimePresentation : IRuntimeFlowPresentation
    {
        public bool FailNextRewardPresentation { get; set; }
        public int MapCount { get; private set; }
        public int RewardCount { get; private set; }

        public void PresentMap() => MapCount++;

        public void PresentReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId)
        {
            if (FailNextRewardPresentation)
            {
                FailNextRewardPresentation = false;
                throw new InvalidOperationException("presentation_failed");
            }

            RewardCount++;
        }
    }

    internal sealed class FailingRuntimePersistence : IRuntimeFlowPersistence
    {
        public bool FailNextStore { get; set; }
        public List<FlowCheckpoint> Stored { get; } = new List<FlowCheckpoint>();

        public void Store(FlowCheckpoint checkpoint)
        {
            if (FailNextStore)
            {
                FailNextStore = false;
                throw new IOException("save_failed");
            }

            Stored.Add(checkpoint);
        }
    }
}
