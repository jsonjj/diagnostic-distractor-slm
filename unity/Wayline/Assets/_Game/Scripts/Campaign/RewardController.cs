using System;
using Wayline.Save;

namespace Wayline.Campaign
{
    public sealed class TrialPerformance
    {
        public TrialPerformance(int firstPassCorrect, int selfCorrected, int itemCount)
        {
            if (itemCount < 1)
                throw new ArgumentOutOfRangeException(nameof(itemCount));
            if (firstPassCorrect < 0 || firstPassCorrect > itemCount)
                throw new ArgumentOutOfRangeException(nameof(firstPassCorrect));
            if (selfCorrected < 0 || firstPassCorrect + selfCorrected > itemCount)
                throw new ArgumentOutOfRangeException(nameof(selfCorrected));
            FirstPassCorrect = firstPassCorrect;
            SelfCorrected = selfCorrected;
            ItemCount = itemCount;
        }

        public int FirstPassCorrect { get; }

        public int SelfCorrected { get; }

        public int ItemCount { get; }
    }

    public sealed class RewardGrant
    {
        public RewardGrant(int routeMarks, int focus)
        {
            if (routeMarks < 0)
                throw new ArgumentOutOfRangeException(nameof(routeMarks));
            if (focus < 0)
                throw new ArgumentOutOfRangeException(nameof(focus));
            RouteMarks = routeMarks;
            Focus = focus;
        }

        public int RouteMarks { get; }

        public int Focus { get; }
    }

    public sealed class RewardController
    {
        public RewardController(int maxFocusPerTrial)
        {
            if (maxFocusPerTrial < 0)
                throw new ArgumentOutOfRangeException(nameof(maxFocusPerTrial));
            MaxFocusPerTrial = maxFocusPerTrial;
        }

        public int MaxFocusPerTrial { get; }

        public RewardGrant Grant(
            ProfileDataV1 profile,
            BattleDefinition battle,
            TrialPerformance performance)
        {
            if (profile == null)
                throw new ArgumentNullException(nameof(profile));
            if (battle == null)
                throw new ArgumentNullException(nameof(battle));
            if (performance == null)
                throw new ArgumentNullException(nameof(performance));
            if (performance.ItemCount != battle.QuestionCount)
                throw new ArgumentException("Trial performance must match battle question count.", nameof(performance));
            if (profile.HasRewardedBattle(battle.Id))
                return new RewardGrant(routeMarks: 0, focus: 0);

            var focus = Math.Min(
                MaxFocusPerTrial,
                performance.FirstPassCorrect + performance.SelfCorrected);
            var grant = new RewardGrant(battle.BaseRouteMarks, focus);
            profile.ApplyReward(grant.RouteMarks, grant.Focus);
            profile.MarkBattleRewarded(battle.Id);
            return grant;
        }
    }
}
