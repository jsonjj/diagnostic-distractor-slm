using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;

namespace Wayline.Learning.Quiz
{
    [JsonObject(MemberSerialization.OptIn)]
    public sealed class QuizController
    {
        private readonly IWaylineForgeClient _client;
        private readonly Func<string> _requestIdFactory;
        private readonly List<QuizAnswerState> _answers = new List<QuizAnswerState>();
        private Task _initialTask;
        private Task _revisionTask;
        private BattleQuizRequest _prepareRequest;
        private InitialSubmission _initialSubmission;
        private RevisionSubmission _revisionSubmission;
        private string _sessionId;
        private string _batchId;
        private int _feedbackIndex;

        public QuizController(
            IWaylineForgeClient client,
            Func<string> requestIdFactory)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            _requestIdFactory = requestIdFactory ??
                throw new ArgumentNullException(nameof(requestIdFactory));
            Answers = new ReadOnlyCollection<QuizAnswerState>(_answers);
        }

        public event Action Changed;

        [JsonProperty("state")]
        public QuizState State { get; private set; } = QuizState.Loading;

        [JsonProperty("hasFailure")]
        public bool HasFailure => LastFailureCode != null;

        public PublicQuizBatch Batch { get; private set; }

        public IReadOnlyList<QuizAnswerState> Answers { get; }

        public int CurrentItemIndex { get; private set; }

        public int? WrongCount { get; private set; }

        public bool IsCountMomentVisible { get; private set; }

        public FinalQuizResult FinalResult { get; private set; }

        public string LastFailureCode { get; private set; }

        public bool CanRecover { get; private set; }

        public bool CanContinueCurrent =>
            Batch != null &&
            CurrentItemIndex >= 0 &&
            CurrentItemIndex < _answers.Count &&
            IsComplete(_answers[CurrentItemIndex]);

        public bool CanSubmitCurrentPass =>
            (State == QuizState.Answering || State == QuizState.Reviewing) &&
            !IsCountMomentVisible &&
            _answers.Count > 0 &&
            _answers.All(IsComplete);

        public bool AnswersRemainEditable =>
            (State == QuizState.Answering || State == QuizState.Reviewing) &&
            !IsCountMomentVisible;

        public FinalQuizItemResult CurrentFeedback =>
            State == QuizState.Revealed &&
            !IsCountMomentVisible &&
            FinalResult != null &&
            _feedbackIndex >= 0 &&
            _feedbackIndex < FinalResult.Items.Count
                ? FinalResult.Items[_feedbackIndex]
                : null;

        public string FinalActionLabel =>
            CurrentFeedback != null && _feedbackIndex == FinalResult.Items.Count - 1
                ? "Complete route trial"
                : "Next method";

        public async Task PrepareAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken)
        {
            if (request == null)
                throw new ArgumentNullException(nameof(request));
            ResetForLoading();
            _prepareRequest = request;
            _sessionId = request.SessionId;
            NotifyChanged();
            try
            {
                var batch = await _client.PrepareBatchAsync(request, cancellationToken);
                StrictQuizValidator.Validate(batch);
                InitializeBatch(batch);
                State = QuizState.Answering;
                NotifyChanged();
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (WaylineClientException error)
            {
                SetFailure(error.Code, QuizState.Loading, true);
            }
            catch (Exception)
            {
                SetFailure("integrity_failure", QuizState.Loading, true);
            }
        }

        public async Task ResumeAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken)
        {
            ResetForLoading();
            _batchId = batchId;
            _sessionId = sessionId;
            NotifyChanged();
            try
            {
                var snapshot = await _client.GetQuizSnapshotAsync(
                    batchId,
                    sessionId,
                    cancellationToken);
                StrictQuizValidator.Validate(snapshot);
                if (!string.Equals(snapshot.BatchId, batchId, StringComparison.Ordinal))
                {
                    throw new JsonSerializationException(
                        "snapshot does not match the requested batch");
                }
                ApplySnapshot(snapshot);
                NotifyChanged();
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (WaylineClientException error)
            {
                SetFailure(error.Code, QuizState.Loading, true);
            }
            catch (Exception)
            {
                SetFailure("integrity_failure", QuizState.Loading, true);
            }
        }

        public void GoToItem(int itemIndex)
        {
            RequireEditable();
            if (itemIndex < 0 || itemIndex >= _answers.Count)
                throw new ArgumentOutOfRangeException(nameof(itemIndex));
            CurrentItemIndex = itemIndex;
            NotifyChanged();
        }

        public void SelectOption(string itemId, string optionId)
        {
            RequireEditable();
            var answer = FindAnswer(itemId);
            var item = FindItem(itemId);
            if (!item.Options.Any(option => option.OptionId == optionId))
                throw new ArgumentException("optionId is not public for this item", nameof(optionId));
            answer.SelectedOptionId = optionId;
            NotifyChanged();
        }

        public void SelectConfidence(string itemId, Confidence confidence)
        {
            RequireEditable();
            if (!Enum.IsDefined(typeof(Confidence), confidence))
                throw new ArgumentOutOfRangeException(nameof(confidence));
            FindAnswer(itemId).SelectedConfidence = confidence;
            NotifyChanged();
        }

        public Task SubmitInitialAsync(CancellationToken cancellationToken)
        {
            if (_initialTask != null && !_initialTask.IsCompleted)
                return _initialTask;
            if (State != QuizState.Answering)
                throw new InvalidOperationException("initial submission is not available");
            if (!CanSubmitCurrentPass)
                throw new InvalidOperationException("every answer and confidence is required");
            var submission = BuildInitialSubmission();
            State = QuizState.SubmittingInitial;
            LastFailureCode = null;
            CanRecover = false;
            NotifyChanged();
            _initialTask = SubmitInitialCoreAsync(submission, cancellationToken);
            return _initialTask;
        }

        public Task SubmitRevisionAsync(CancellationToken cancellationToken)
        {
            if (_revisionTask != null && !_revisionTask.IsCompleted)
                return _revisionTask;
            if (State != QuizState.Reviewing || IsCountMomentVisible)
                throw new InvalidOperationException("revision is not available");
            if (_revisionSubmission != null || FinalResult != null)
                throw new InvalidOperationException("revision has already been used");
            if (!CanSubmitCurrentPass)
                throw new InvalidOperationException("every review answer and confidence is required");
            var submission = BuildRevisionSubmission();
            State = QuizState.SubmittingRevision;
            LastFailureCode = null;
            CanRecover = false;
            NotifyChanged();
            _revisionTask = SubmitRevisionCoreAsync(submission, cancellationToken);
            return _revisionTask;
        }

        public async Task RecoverAsync(CancellationToken cancellationToken)
        {
            if (!CanRecover || string.IsNullOrEmpty(_batchId) || string.IsNullOrEmpty(_sessionId))
                throw new InvalidOperationException("recovery is not available");
            var previousState = State;
            State = QuizState.Loading;
            NotifyChanged();
            try
            {
                var snapshot = await _client.GetQuizSnapshotAsync(
                    _batchId,
                    _sessionId,
                    cancellationToken);
                StrictQuizValidator.Validate(snapshot);
                ApplySnapshot(snapshot);
                LastFailureCode = null;
                CanRecover = false;
                NotifyChanged();
            }
            catch (OperationCanceledException)
            {
                State = previousState;
                NotifyChanged();
                throw;
            }
            catch (WaylineClientException error)
            {
                SetFailure(error.Code, previousState, true);
            }
            catch (Exception)
            {
                SetFailure("integrity_failure", previousState, true);
            }
        }

        public void AcknowledgeWrongCount()
        {
            if (!IsCountMomentVisible ||
                (State != QuizState.Reviewing && State != QuizState.Revealed))
            {
                throw new InvalidOperationException("wrong count is not visible");
            }
            IsCountMomentVisible = false;
            CurrentItemIndex = 0;
            _feedbackIndex = 0;
            NotifyChanged();
        }

        public void AdvanceFinalFeedback()
        {
            if (CurrentFeedback == null)
                throw new InvalidOperationException("final feedback is not visible");
            if (_feedbackIndex == FinalResult.Items.Count - 1)
            {
                State = QuizState.Complete;
            }
            else
            {
                _feedbackIndex++;
            }
            NotifyChanged();
        }

        public string GetLogSafeState()
        {
            return $"RouteTrial(State={State}, HasFailure={HasFailure})";
        }

        private async Task SubmitInitialCoreAsync(
            InitialSubmission submission,
            CancellationToken cancellationToken)
        {
            try
            {
                var result = await _client.SubmitInitialAsync(
                    _sessionId,
                    submission,
                    cancellationToken);
                StrictQuizValidator.Validate(result);
                RequireInitialResponseIdentity(submission, result);
                if (result.WrongCount == 0)
                {
                    ValidateFinalAgainstSubmissions(
                        result.FinalResult,
                        submission.Selections,
                        submission.Selections);
                }
                _initialSubmission = submission;
                foreach (var answer in _answers)
                    answer.CaptureFirst();
                WrongCount = result.WrongCount;
                IsCountMomentVisible = true;
                if (result.WrongCount == 0)
                {
                    FinalResult = result.FinalResult;
                    State = QuizState.Revealed;
                }
                else
                {
                    State = QuizState.Reviewing;
                }
                LastFailureCode = null;
                CanRecover = false;
                NotifyChanged();
            }
            catch (OperationCanceledException)
            {
                State = QuizState.Answering;
                NotifyChanged();
                throw;
            }
            catch (WaylineClientException error)
            {
                SetFailure(error.Code, QuizState.Answering, true);
            }
            catch (Exception)
            {
                SetFailure("integrity_failure", QuizState.Answering, true);
            }
        }

        private async Task SubmitRevisionCoreAsync(
            RevisionSubmission submission,
            CancellationToken cancellationToken)
        {
            try
            {
                var result = await _client.SubmitRevisionAsync(
                    _sessionId,
                    submission,
                    cancellationToken);
                StrictQuizValidator.Validate(result);
                ValidateFinalAgainstSubmissions(
                    result,
                    _initialSubmission.Selections,
                    submission.Selections);
                _revisionSubmission = submission;
                FinalResult = result;
                State = QuizState.Revealed;
                _feedbackIndex = 0;
                LastFailureCode = null;
                CanRecover = false;
                NotifyChanged();
            }
            catch (OperationCanceledException)
            {
                State = QuizState.Reviewing;
                NotifyChanged();
                throw;
            }
            catch (WaylineClientException error)
            {
                SetFailure(error.Code, QuizState.Reviewing, true);
            }
            catch (Exception)
            {
                SetFailure("integrity_failure", QuizState.Reviewing, true);
            }
        }

        private void RequireInitialResponseIdentity(
            InitialSubmission submission,
            InitialSubmissionResult result)
        {
            if (result.BatchId != submission.BatchId ||
                result.ItemCount != submission.ItemCount)
            {
                throw new JsonSerializationException(
                    "initial result does not match the submitted batch");
            }
        }

        private void ValidateFinalAgainstSubmissions(
            FinalQuizResult result,
            IReadOnlyList<SubmissionSelection> initial,
            IReadOnlyList<SubmissionSelection> final)
        {
            if (result == null ||
                result.BatchId != _batchId ||
                result.ItemCount != Batch.ItemCount ||
                result.Items.Count != Batch.Items.Count)
            {
                throw new JsonSerializationException(
                    "final result does not match the submitted batch");
            }
            var initialByItem = initial.ToDictionary(value => value.ItemId, StringComparer.Ordinal);
            var finalByItem = final.ToDictionary(value => value.ItemId, StringComparer.Ordinal);
            var publicByItem = Batch.Items.ToDictionary(value => value.ItemId, StringComparer.Ordinal);
            foreach (var item in result.Items)
            {
                if (!initialByItem.TryGetValue(item.ItemId, out var first) ||
                    !finalByItem.TryGetValue(item.ItemId, out var reviewed) ||
                    !publicByItem.TryGetValue(item.ItemId, out var publicItem) ||
                    item.FirstSelection.OptionId != first.OptionId ||
                    item.FirstSelection.Confidence != first.Confidence ||
                    item.FinalSelection.OptionId != reviewed.OptionId ||
                    item.FinalSelection.Confidence != reviewed.Confidence ||
                    !publicItem.Options.Any(option =>
                        option.OptionId == item.CorrectOptionId &&
                        string.Equals(
                            option.DisplayText,
                            item.CorrectAnswer,
                            StringComparison.Ordinal)))
                {
                    throw new JsonSerializationException(
                        "final result does not match immutable submissions");
                }
            }
        }

        private InitialSubmission BuildInitialSubmission()
        {
            return new InitialSubmission(
                "wayline.v1",
                _requestIdFactory(),
                _batchId,
                Batch.ItemCount,
                BuildSelections());
        }

        private RevisionSubmission BuildRevisionSubmission()
        {
            return new RevisionSubmission(
                "wayline.v1",
                _requestIdFactory(),
                _batchId,
                Batch.ItemCount,
                BuildSelections());
        }

        private IReadOnlyList<SubmissionSelection> BuildSelections()
        {
            return _answers.Select(answer => new SubmissionSelection(
                answer.ItemId,
                answer.SelectedOptionId,
                answer.SelectedConfidence.Value)).ToArray();
        }

        private void ApplySnapshot(QuizSnapshot snapshot)
        {
            InitializeBatch(snapshot.PublicBatch);
            _batchId = snapshot.BatchId;
            _initialSubmission = snapshot.InitialSubmission;
            _revisionSubmission = snapshot.RevisionSubmission;
            if (snapshot.InitialSubmission != null)
            {
                foreach (var selection in snapshot.InitialSubmission.Selections)
                {
                    var answer = FindAnswer(selection.ItemId);
                    answer.SelectedOptionId = selection.OptionId;
                    answer.SelectedConfidence = selection.Confidence;
                    answer.CaptureFirst();
                }
            }
            if (snapshot.RevisionSubmission != null)
            {
                foreach (var selection in snapshot.RevisionSubmission.Selections)
                {
                    var answer = FindAnswer(selection.ItemId);
                    answer.SelectedOptionId = selection.OptionId;
                    answer.SelectedConfidence = selection.Confidence;
                }
            }
            WrongCount = snapshot.InitialResult?.WrongCount;
            FinalResult = snapshot.FinalResult;
            _feedbackIndex = 0;
            switch (snapshot.QuizState)
            {
                case QuizSnapshotState.Ready:
                    State = QuizState.Answering;
                    break;
                case QuizSnapshotState.InitialLocked:
                    State = QuizState.SubmittingInitial;
                    CanRecover = true;
                    break;
                case QuizSnapshotState.RevisionOpen:
                    State = QuizState.Reviewing;
                    IsCountMomentVisible = true;
                    break;
                case QuizSnapshotState.Revealed:
                    State = QuizState.Revealed;
                    break;
                case QuizSnapshotState.Closed:
                    State = QuizState.Complete;
                    break;
                default:
                    throw new JsonSerializationException("snapshot state is invalid");
            }
        }

        private void InitializeBatch(PublicQuizBatch batch)
        {
            Batch = batch;
            _batchId = batch.BatchId;
            _answers.Clear();
            foreach (var item in batch.Items)
                _answers.Add(new QuizAnswerState(item.ItemId));
            CurrentItemIndex = 0;
            WrongCount = null;
            IsCountMomentVisible = false;
            FinalResult = null;
            _feedbackIndex = 0;
            LastFailureCode = null;
            CanRecover = false;
        }

        private void ResetForLoading()
        {
            State = QuizState.Loading;
            Batch = null;
            _answers.Clear();
            CurrentItemIndex = 0;
            WrongCount = null;
            IsCountMomentVisible = false;
            FinalResult = null;
            LastFailureCode = null;
            CanRecover = false;
            _initialSubmission = null;
            _revisionSubmission = null;
            _initialTask = null;
            _revisionTask = null;
        }

        private QuizAnswerState FindAnswer(string itemId)
        {
            var answer = _answers.FirstOrDefault(value => value.ItemId == itemId);
            return answer ?? throw new ArgumentException("itemId is not in this batch", nameof(itemId));
        }

        private PublicQuizItem FindItem(string itemId)
        {
            var item = Batch.Items.FirstOrDefault(value => value.ItemId == itemId);
            return item ?? throw new ArgumentException("itemId is not in this batch", nameof(itemId));
        }

        private void RequireEditable()
        {
            if (!AnswersRemainEditable)
                throw new InvalidOperationException("answers are not editable");
        }

        private static bool IsComplete(QuizAnswerState answer)
        {
            return answer.SelectedOptionId != null && answer.SelectedConfidence.HasValue;
        }

        private void SetFailure(string code, QuizState state, bool canRecover)
        {
            State = state;
            LastFailureCode = code;
            CanRecover = canRecover;
            NotifyChanged();
        }

        private void NotifyChanged()
        {
            Changed?.Invoke();
        }
    }
}
