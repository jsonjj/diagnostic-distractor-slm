using System;
using System.Collections.Generic;

namespace Wayline.Flow.Runtime
{
    public interface IRuntimeCampaignMutations
    {
        void PreserveCombatVictory(FlowBattle battle);

        void CommitAuthoritativeTrial(AuthoritativeTrialCompletion completion);

        void CommitReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId);
    }

    public interface IRuntimeFlowPresentation
    {
        void PresentMap();

        void PresentReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId);
    }

    public interface IRuntimeFlowPersistence
    {
        void Store(FlowCheckpoint checkpoint);
    }

    public sealed class RuntimeCampaignFlowAdapter : ICampaignFlowPort
    {
        private readonly IRuntimeCampaignMutations _mutations;
        private readonly IRuntimeFlowPresentation _presentation;
        private readonly IRuntimeFlowPersistence _persistence;
        private readonly HashSet<FlowBattle> _preservedVictories =
            new HashSet<FlowBattle>();
        private readonly HashSet<string> _committedTrials =
            new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> _committedRewards =
            new HashSet<string>(StringComparer.Ordinal);

        public RuntimeCampaignFlowAdapter(
            IRuntimeCampaignMutations mutations,
            IRuntimeFlowPresentation presentation,
            IRuntimeFlowPersistence persistence,
            FlowCheckpoint restoredCheckpoint,
            IEnumerable<FlowBattle> preservedVictories)
        {
            _mutations = mutations ?? throw new ArgumentNullException(nameof(mutations));
            _presentation = presentation ??
                throw new ArgumentNullException(nameof(presentation));
            _persistence = persistence ?? throw new ArgumentNullException(nameof(persistence));

            if (restoredCheckpoint != null)
            {
                foreach (var completionId in restoredCheckpoint.CommittedTrialIds)
                    _committedTrials.Add(completionId);
                foreach (var rewardId in restoredCheckpoint.CommittedRewardIds)
                    _committedRewards.Add(rewardId);
            }

            if (preservedVictories == null)
                throw new ArgumentNullException(nameof(preservedVictories));
            foreach (var battle in preservedVictories)
            {
                if (battle == null)
                    throw new ArgumentException(
                        "Preserved battle identities cannot be null.",
                        nameof(preservedVictories));
                _preservedVictories.Add(battle);
            }
        }

        public void PresentMap() => _presentation.PresentMap();

        public void PreserveCombatVictory(FlowBattle battle)
        {
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            if (!_preservedVictories.Add(battle))
                return;

            try
            {
                _mutations.PreserveCombatVictory(battle);
            }
            catch
            {
                _preservedVictories.Remove(battle);
                throw;
            }
        }

        public void CommitAuthoritativeTrial(AuthoritativeTrialCompletion completion)
        {
            if (completion == null)
                throw new ArgumentNullException(nameof(completion));
            if (!_committedTrials.Add(completion.CompletionId))
                return;

            try
            {
                _mutations.CommitAuthoritativeTrial(completion);
            }
            catch
            {
                _committedTrials.Remove(completion.CompletionId);
                throw;
            }
        }

        public void PresentReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId)
        {
            _presentation.PresentReward(
                battle ?? throw new ArgumentNullException(nameof(battle)),
                Require(sourceCompletionId, nameof(sourceCompletionId)),
                Require(authorityReceiptId, nameof(authorityReceiptId)));
        }

        public void CommitReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId)
        {
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            sourceCompletionId = Require(sourceCompletionId, nameof(sourceCompletionId));
            authorityReceiptId = Require(authorityReceiptId, nameof(authorityReceiptId));
            if (!_committedRewards.Add(sourceCompletionId))
                return;

            try
            {
                _mutations.CommitReward(
                    battle,
                    sourceCompletionId,
                    authorityReceiptId);
            }
            catch
            {
                _committedRewards.Remove(sourceCompletionId);
                throw;
            }
        }

        public void StoreCheckpoint(FlowCheckpoint checkpoint)
        {
            _persistence.Store(checkpoint ?? throw new ArgumentNullException(nameof(checkpoint)));
        }

        private static string Require(string value, string parameterName)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("A stable authority identity is required.", parameterName);
            return value;
        }
    }
}
