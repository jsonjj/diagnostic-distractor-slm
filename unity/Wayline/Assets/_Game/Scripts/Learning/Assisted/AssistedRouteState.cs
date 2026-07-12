using Wayline.Learning.Contracts;

namespace Wayline.Learning.Assisted
{
    public enum AssistedRouteState
    {
        Empty,
        Preparing,
        WorkedExample,
        Answering,
        Submitting,
        Revealed,
        Complete,
        Failed
    }

    public sealed class AssistedAnswerState
    {
        internal AssistedAnswerState(string itemId)
        {
            ItemId = itemId;
        }

        public string ItemId { get; }

        public string SelectedOptionId { get; internal set; }

        public Confidence? SelectedConfidence { get; internal set; }
    }
}
