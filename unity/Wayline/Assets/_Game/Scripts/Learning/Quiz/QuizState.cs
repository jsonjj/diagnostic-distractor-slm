using Wayline.Learning.Contracts;

namespace Wayline.Learning.Quiz
{
    public enum QuizState
    {
        Loading,
        Answering,
        SubmittingInitial,
        Reviewing,
        SubmittingRevision,
        Revealed,
        Complete
    }

    public sealed class QuizAnswerState
    {
        internal QuizAnswerState(string itemId)
        {
            ItemId = itemId;
        }

        public string ItemId { get; }

        public string SelectedOptionId { get; internal set; }

        public Confidence? SelectedConfidence { get; internal set; }

        public string FirstOptionId { get; internal set; }

        public Confidence? FirstConfidence { get; internal set; }

        internal void CaptureFirst()
        {
            FirstOptionId = SelectedOptionId;
            FirstConfidence = SelectedConfidence;
        }
    }
}
