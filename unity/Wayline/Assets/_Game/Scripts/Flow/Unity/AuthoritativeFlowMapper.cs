#if UNITY_EDITOR || DEVELOPMENT_BUILD
using System;
using Wayline.Learning.Contracts;

namespace Wayline.Flow.Unity
{
    public static class AuthoritativeFlowMapper
    {
        public static AuthoritativeTrialCompletion FromBattle(
            BattleCompleted result,
            FlowBattle battle)
        {
            if (result == null)
                throw new ArgumentNullException(nameof(result));
            StrictQuizValidator.Validate(result);
            RequireBattle(result.WorldId, result.BattleId, battle);

            return new AuthoritativeTrialCompletion(
                result.RequestId,
                "battle-authority:" + result.BatchId,
                FlowTrialStage.Normal,
                battle,
                result.SealTrialRequired
                    ? AuthoritativeNextStep.SealTrial
                    : AuthoritativeNextStep.Reward);
        }

        public static AuthoritativeTrialCompletion FromSeal(
            SealTrialCompleted result,
            FlowBattle battle)
        {
            if (result == null)
                throw new ArgumentNullException(nameof(result));
            StrictQuizValidator.Validate(result);
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            if (!string.Equals(result.WorldId, battle.WorldId, StringComparison.Ordinal))
                throw new ArgumentException("Seal authority belongs to a different world.", nameof(result));

            var next = result.WorldCleared
                ? AuthoritativeNextStep.Reward
                : result.AssistedRouteUnlocked
                    ? AuthoritativeNextStep.AssistedRoute
                    : AuthoritativeNextStep.SealTrial;
            return new AuthoritativeTrialCompletion(
                result.RequestId,
                "seal-authority:" + result.BatchId,
                FlowTrialStage.Seal,
                battle,
                next);
        }

        public static AuthoritativeTrialCompletion FromAssisted(
            AssistedRouteCompleted result,
            FlowBattle battle)
        {
            if (result == null)
                throw new ArgumentNullException(nameof(result));
            StrictQuizValidator.Validate(result);
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            if (!string.Equals(result.WorldId, battle.WorldId, StringComparison.Ordinal))
            {
                throw new ArgumentException(
                    "Assisted authority belongs to a different world.",
                    nameof(result));
            }

            return new AuthoritativeTrialCompletion(
                result.RequestId,
                "assisted-authority:" + result.RouteId,
                FlowTrialStage.Assisted,
                battle,
                AuthoritativeNextStep.Reward);
        }

        private static void RequireBattle(
            string worldId,
            string battleId,
            FlowBattle battle)
        {
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            if (!string.Equals(worldId, battle.WorldId, StringComparison.Ordinal) ||
                !string.Equals(battleId, battle.BattleId, StringComparison.Ordinal))
            {
                throw new ArgumentException(
                    "Authoritative completion belongs to a different battle.",
                    nameof(battle));
            }
        }
    }
}
#endif
