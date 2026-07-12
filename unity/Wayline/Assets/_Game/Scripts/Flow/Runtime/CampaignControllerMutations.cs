using System;
using System.Collections.Generic;
using Wayline.Campaign;

namespace Wayline.Flow.Runtime
{
    public sealed class CampaignControllerMutations : IRuntimeCampaignMutations
    {
        private sealed class RegisteredMutation
        {
            public RegisteredMutation(
                FlowBattle battle,
                Action<CampaignController> apply)
            {
                Battle = battle;
                Apply = apply;
            }

            public FlowBattle Battle { get; }

            public Action<CampaignController> Apply { get; }
        }

        private readonly CampaignController _campaign;
        private readonly Dictionary<string, RegisteredMutation> _trialMutations =
            new Dictionary<string, RegisteredMutation>(StringComparer.Ordinal);
        private readonly HashSet<string> _appliedTrialIds =
            new HashSet<string>(StringComparer.Ordinal);

        public CampaignControllerMutations(CampaignController campaign)
        {
            _campaign = campaign ?? throw new ArgumentNullException(nameof(campaign));
        }

        public void RegisterAuthoritativeTrial(
            string completionId,
            FlowBattle battle,
            Action<CampaignController> apply)
        {
            if (string.IsNullOrWhiteSpace(completionId))
                throw new ArgumentException("A completion identity is required.", nameof(completionId));
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            if (apply == null)
                throw new ArgumentNullException(nameof(apply));
            if (_trialMutations.TryGetValue(completionId, out var existing))
            {
                if (existing.Battle == battle)
                    return;
                throw new InvalidOperationException(
                    "An authoritative completion identity cannot be rebound.");
            }

            _trialMutations.Add(completionId, new RegisteredMutation(battle, apply));
        }

        public void PreserveCombatVictory(FlowBattle battle)
        {
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            _campaign.RecordCombatVictory(battle.WorldId, battle.BattleId);
        }

        public void CommitAuthoritativeTrial(AuthoritativeTrialCompletion completion)
        {
            if (completion == null)
                throw new ArgumentNullException(nameof(completion));
            if (!_trialMutations.TryGetValue(completion.CompletionId, out var mutation))
            {
                throw new InvalidOperationException(
                    "No trusted campaign mutation is registered for this authority receipt.");
            }
            if (mutation.Battle != completion.Battle)
            {
                throw new ArgumentException(
                    "The registered campaign mutation belongs to another battle.",
                    nameof(completion));
            }
            if (_appliedTrialIds.Contains(completion.CompletionId))
                return;

            mutation.Apply(_campaign);
            _appliedTrialIds.Add(completion.CompletionId);
        }

        public void CommitReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId)
        {
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            if (string.IsNullOrWhiteSpace(sourceCompletionId))
                throw new ArgumentException("A reward source is required.", nameof(sourceCompletionId));
            if (string.IsNullOrWhiteSpace(authorityReceiptId))
                throw new ArgumentException("A reward authority receipt is required.", nameof(authorityReceiptId));

            // Campaign mutation occurs only when the authoritative trial is committed.
            // This method acknowledges presentation without recalculating learning truth.
        }
    }
}
