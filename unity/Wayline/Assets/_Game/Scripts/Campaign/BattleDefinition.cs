using System;

namespace Wayline.Campaign
{
    public enum BattleTier
    {
        Scout = 0,
        Rival = 1,
        Warden = 2,
        Lieutenant = 3,
        Boss = 4,
        CampaignFinale = 5
    }

    public sealed class BattleDefinition
    {
        public BattleDefinition(
            string id,
            BattleTier tier,
            string aiProfileId,
            string opponentId,
            int baseRouteMarks,
            string narrativeCardId)
        {
            if (!Enum.IsDefined(typeof(BattleTier), tier))
                throw new ArgumentOutOfRangeException(nameof(tier));
            if (baseRouteMarks < 1)
                throw new ArgumentOutOfRangeException(nameof(baseRouteMarks));
            Id = Require(id, nameof(id));
            Tier = tier;
            AiProfileId = Require(aiProfileId, nameof(aiProfileId));
            OpponentId = Require(opponentId, nameof(opponentId));
            BaseRouteMarks = baseRouteMarks;
            NarrativeCardId = Require(narrativeCardId, nameof(narrativeCardId));
            QuestionCount = QuestionCountFor(tier);
        }

        public string Id { get; }

        public BattleTier Tier { get; }

        public int QuestionCount { get; }

        public string AiProfileId { get; }

        public string OpponentId { get; }

        public int BaseRouteMarks { get; }

        public string NarrativeCardId { get; }

        public static int QuestionCountFor(BattleTier tier)
        {
            switch (tier)
            {
                case BattleTier.Scout:
                    return 3;
                case BattleTier.Rival:
                case BattleTier.Warden:
                    return 4;
                case BattleTier.Lieutenant:
                    return 5;
                case BattleTier.Boss:
                    return 8;
                case BattleTier.CampaignFinale:
                    return 10;
                default:
                    throw new ArgumentOutOfRangeException(nameof(tier));
            }
        }

        private static string Require(string value, string parameter)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("A battle identifier is required.", parameter);
            return value;
        }
    }
}
