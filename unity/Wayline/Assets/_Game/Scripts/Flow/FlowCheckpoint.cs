using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;

namespace Wayline.Flow
{
    public sealed class FlowCheckpoint
    {
        private readonly ReadOnlyCollection<string> _committedTrialIds;
        private readonly ReadOnlyCollection<string> _committedRewardIds;

        public FlowCheckpoint(
            FlowState stableState,
            FlowBattle battle,
            bool combatVictoryPreserved,
            IEnumerable<string> committedTrialIds,
            IEnumerable<string> committedRewardIds,
            string rewardSourceCompletionId,
            string rewardAuthorityReceiptId)
        {
            if (!Enum.IsDefined(typeof(FlowState), stableState))
                throw new ArgumentOutOfRangeException(nameof(stableState), stableState, null);
            if (stableState == FlowState.Unavailable)
                throw new ArgumentException("Unavailable is transient and cannot be checkpointed.", nameof(stableState));

            var requiresBattle = stableState == FlowState.Combat ||
                                 stableState == FlowState.NormalTrial ||
                                 stableState == FlowState.SealTrial ||
                                 stableState == FlowState.AssistedRoute ||
                                 stableState == FlowState.Reward;
            if (requiresBattle && battle == null)
                throw new ArgumentNullException(nameof(battle));
            if (!requiresBattle && battle != null)
            {
                throw new ArgumentException(
                    "Title and map checkpoints cannot retain a battle identity.",
                    nameof(battle));
            }

            var requiresPreservedVictory = stableState == FlowState.NormalTrial ||
                                           stableState == FlowState.SealTrial ||
                                           stableState == FlowState.AssistedRoute ||
                                           stableState == FlowState.Reward;
            if (requiresPreservedVictory && !combatVictoryPreserved)
            {
                throw new ArgumentException(
                    "Post-combat checkpoints require a preserved victory.",
                    nameof(combatVictoryPreserved));
            }
            if (!requiresPreservedVictory && combatVictoryPreserved)
            {
                throw new ArgumentException(
                    "Victory preservation is only valid after won combat.",
                    nameof(combatVictoryPreserved));
            }

            var trialIds = CopyIdentifiers(committedTrialIds, nameof(committedTrialIds));
            var rewardIds = CopyIdentifiers(committedRewardIds, nameof(committedRewardIds));
            foreach (var rewardId in rewardIds)
            {
                if (!Contains(trialIds, rewardId))
                {
                    throw new ArgumentException(
                        "Every committed reward must belong to a committed trial.",
                        nameof(committedRewardIds));
                }
            }

            if (stableState != FlowState.Reward &&
                (rewardSourceCompletionId != null || rewardAuthorityReceiptId != null))
            {
                throw new ArgumentException(
                    "Reward authority metadata is valid only in the reward state.",
                    nameof(rewardSourceCompletionId));
            }

            if (stableState == FlowState.Reward &&
                string.IsNullOrWhiteSpace(rewardSourceCompletionId))
            {
                throw new ArgumentException(
                    "A reward checkpoint requires its source completion.",
                    nameof(rewardSourceCompletionId));
            }

            if (stableState == FlowState.Reward &&
                string.IsNullOrWhiteSpace(rewardAuthorityReceiptId))
            {
                throw new ArgumentException(
                    "A reward checkpoint requires its authority receipt.",
                    nameof(rewardAuthorityReceiptId));
            }
            if (stableState == FlowState.Reward &&
                !Contains(trialIds, rewardSourceCompletionId))
            {
                throw new ArgumentException(
                    "A reward source must be an already committed trial completion.",
                    nameof(rewardSourceCompletionId));
            }
            if (stableState == FlowState.Reward &&
                Contains(rewardIds, rewardSourceCompletionId))
            {
                throw new ArgumentException(
                    "An already committed reward cannot remain pending.",
                    nameof(rewardSourceCompletionId));
            }

            StableState = stableState;
            Battle = battle;
            CombatVictoryPreserved = combatVictoryPreserved;
            _committedTrialIds = trialIds;
            _committedRewardIds = rewardIds;
            RewardSourceCompletionId = rewardSourceCompletionId;
            RewardAuthorityReceiptId = rewardAuthorityReceiptId;
        }

        public FlowState StableState { get; }

        public FlowBattle Battle { get; }

        public bool CombatVictoryPreserved { get; }

        public IReadOnlyList<string> CommittedTrialIds => _committedTrialIds;

        public IReadOnlyList<string> CommittedRewardIds => _committedRewardIds;

        public string RewardSourceCompletionId { get; }

        public string RewardAuthorityReceiptId { get; }

        private static ReadOnlyCollection<string> CopyIdentifiers(
            IEnumerable<string> source,
            string parameterName)
        {
            if (source == null)
                throw new ArgumentNullException(parameterName);

            var copy = new List<string>();
            var unique = new HashSet<string>(StringComparer.Ordinal);
            foreach (var identifier in source)
            {
                if (string.IsNullOrWhiteSpace(identifier))
                    throw new ArgumentException("Checkpoint identifiers cannot be empty.", parameterName);
                if (!unique.Add(identifier))
                    throw new ArgumentException("Checkpoint identifiers must be unique.", parameterName);

                copy.Add(identifier);
            }

            return new ReadOnlyCollection<string>(copy);
        }

        private static bool Contains(
            IEnumerable<string> identifiers,
            string expected)
        {
            foreach (var identifier in identifiers)
            {
                if (string.Equals(identifier, expected, StringComparison.Ordinal))
                    return true;
            }

            return false;
        }
    }
}
