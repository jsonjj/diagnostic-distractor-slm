using System;

namespace Wayline.Flow
{
    public sealed class FlowBattle : IEquatable<FlowBattle>
    {
        public FlowBattle(string worldId, string battleId)
        {
            WorldId = RequireIdentifier(worldId, nameof(worldId));
            BattleId = RequireIdentifier(battleId, nameof(battleId));
        }

        public string WorldId { get; }

        public string BattleId { get; }

        public bool Equals(FlowBattle other)
        {
            return other != null &&
                   string.Equals(WorldId, other.WorldId, StringComparison.Ordinal) &&
                   string.Equals(BattleId, other.BattleId, StringComparison.Ordinal);
        }

        public override bool Equals(object obj) => Equals(obj as FlowBattle);

        public override int GetHashCode()
        {
            unchecked
            {
                return (StringComparer.Ordinal.GetHashCode(WorldId) * 397) ^
                       StringComparer.Ordinal.GetHashCode(BattleId);
            }
        }

        public static bool operator ==(FlowBattle left, FlowBattle right)
        {
            return ReferenceEquals(left, right) ||
                   (!ReferenceEquals(left, null) && left.Equals(right));
        }

        public static bool operator !=(FlowBattle left, FlowBattle right) => !(left == right);

        private static string RequireIdentifier(string value, string parameterName)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("A non-empty identifier is required.", parameterName);

            return value;
        }
    }

    public sealed class AuthoritativeTrialCompletion
    {
        public AuthoritativeTrialCompletion(
            string completionId,
            string authorityReceiptId,
            FlowTrialStage stage,
            FlowBattle battle,
            AuthoritativeNextStep nextStep)
        {
            if (string.IsNullOrWhiteSpace(completionId))
                throw new ArgumentException("A completion identifier is required.", nameof(completionId));
            if (string.IsNullOrWhiteSpace(authorityReceiptId))
                throw new ArgumentException("An authority receipt is required.", nameof(authorityReceiptId));

            CompletionId = completionId;
            AuthorityReceiptId = authorityReceiptId;
            Stage = stage;
            Battle = battle ?? throw new ArgumentNullException(nameof(battle));
            NextStep = nextStep;
        }

        public string CompletionId { get; }

        public string AuthorityReceiptId { get; }

        public FlowTrialStage Stage { get; }

        public FlowBattle Battle { get; }

        public AuthoritativeNextStep NextStep { get; }
    }

    public interface ICombatFlowPort
    {
        void PresentCombat(FlowBattle battle);
    }

    public interface ITrialFlowPort
    {
        void PresentNormalTrial(FlowBattle battle);

        void PresentLossTrial(FlowBattle battle);

        void PresentSealTrial(FlowBattle battle);

        void PresentAssistedRoute(FlowBattle battle);
    }

    public interface ICampaignFlowPort
    {
        void PresentMap();

        void PreserveCombatVictory(FlowBattle battle);

        void CommitAuthoritativeTrial(AuthoritativeTrialCompletion completion);

        void PresentReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId);

        void CommitReward(
            FlowBattle battle,
            string sourceCompletionId,
            string authorityReceiptId);

        void StoreCheckpoint(FlowCheckpoint checkpoint);
    }
}
