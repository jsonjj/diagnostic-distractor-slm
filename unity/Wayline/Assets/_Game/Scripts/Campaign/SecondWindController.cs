using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;

namespace Wayline.Campaign
{
    public enum KnockoutChoiceKind
    {
        RetryNow = 0,
        SecondWind = 1
    }

    public sealed class KnockoutChoice
    {
        public KnockoutChoice(KnockoutChoiceKind kind, int visualWeight)
        {
            if (visualWeight < 1)
                throw new ArgumentOutOfRangeException(nameof(visualWeight));
            Kind = kind;
            VisualWeight = visualWeight;
        }

        public KnockoutChoiceKind Kind { get; }

        public int VisualWeight { get; }
    }

    public sealed class SecondWindResolution
    {
        public SecondWindResolution(int reviveHealthPercent, int shieldPercent)
        {
            ReviveHealthPercent = reviveHealthPercent;
            ShieldPercent = shieldPercent;
        }

        public int ReviveHealthPercent { get; }

        public int ShieldPercent { get; }
    }

    public sealed class SecondWindController
    {
        private const int RequiredItemCount = 3;
        private const int RevivePercent = 35;
        private const int ShieldPerCorrect = 5;
        private const int MaximumShield = 15;

        public SecondWindController()
        {
            Choices = new ReadOnlyCollection<KnockoutChoice>(new[]
            {
                new KnockoutChoice(KnockoutChoiceKind.RetryNow, visualWeight: 1),
                new KnockoutChoice(KnockoutChoiceKind.SecondWind, visualWeight: 1)
            });
        }

        public IReadOnlyList<KnockoutChoice> Choices { get; }

        public SecondWindResolution Resolve(int finalCorrect, int itemCount)
        {
            if (itemCount != RequiredItemCount)
                throw new ArgumentException("Second Wind must contain exactly three questions.", nameof(itemCount));
            if (finalCorrect < 0 || finalCorrect > itemCount)
                throw new ArgumentOutOfRangeException(nameof(finalCorrect));
            return new SecondWindResolution(
                RevivePercent,
                Math.Min(MaximumShield, finalCorrect * ShieldPerCorrect));
        }
    }
}
