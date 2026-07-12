using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;

namespace Wayline.Learning.Assisted
{
    [JsonObject(MemberSerialization.OptIn)]
    public sealed class AssistedRouteController
    {
        private static readonly HashSet<string> StableFailureCodes =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "authorization_required",
                "batch_unavailable",
                "body_too_large",
                "boss_gate_locked",
                "catalog_conflict",
                "content_type_unsupported",
                "contract_invalid",
                "evidence_sync_unavailable",
                "idempotency_conflict",
                "integrity_failure",
                "invalid_submission",
                "method_not_allowed",
                "origin_forbidden",
                "profile_not_found",
                "quiz_in_progress",
                "quiz_state_conflict",
                "request_cancelled",
                "request_malformed",
                "route_not_found",
                "runtime_unavailable",
                "runtime_state_unavailable",
                "safe_content_unavailable",
                "session_not_current",
                "snapshot_not_ready",
                "snapshot_unavailable",
                "storage_busy"
            };

        private static readonly HashSet<string> RetryableCompletionCodes =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "evidence_sync_unavailable",
                "quiz_in_progress",
                "request_cancelled",
                "runtime_unavailable",
                "storage_busy"
            };

        private readonly IWaylineForgeClient _client;
        private readonly Func<string> _requestIdFactory;
        private readonly List<AssistedAnswerState> _answers =
            new List<AssistedAnswerState>();
        private Task _submissionTask;
        private AssistedRoutePrepare _prepareRequest;
        private AssistedRouteComplete _submission;
        private string _worldId;
        private string _routeId;
        private int _feedbackIndex;

        public AssistedRouteController(
            IWaylineForgeClient client,
            Func<string> requestIdFactory)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            _requestIdFactory = requestIdFactory ??
                throw new ArgumentNullException(nameof(requestIdFactory));
            Answers = new ReadOnlyCollection<AssistedAnswerState>(_answers);
        }

        public event Action Changed;

        [JsonProperty("state")]
        public AssistedRouteState State { get; private set; } = AssistedRouteState.Empty;

        [JsonProperty("hasFailure")]
        public bool HasFailure => LastFailureCode != null;

        public AssistedRouteBatch Batch { get; private set; }

        public IReadOnlyList<AssistedAnswerState> Answers { get; }

        public int CurrentItemIndex { get; private set; }

        public AssistedRouteCompleted FinalResult { get; private set; }

        public AssistedRouteComplete CompletionRequest => _submission;

        public string LastFailureCode { get; private set; }

        public bool CanRecoverPreparation { get; private set; }

        public bool CanRetryCompletion { get; private set; }

        public bool CanContinueCurrent =>
            State == AssistedRouteState.Answering &&
            CurrentItemIndex >= 0 &&
            CurrentItemIndex < _answers.Count &&
            IsComplete(_answers[CurrentItemIndex]);

        public bool CanSubmit =>
            State == AssistedRouteState.Answering &&
            _answers.Count == 2 &&
            _answers.All(IsComplete);

        public bool AnswersRemainEditable => State == AssistedRouteState.Answering;

        public AssistedItemResult CurrentFeedback =>
            State == AssistedRouteState.Revealed &&
            FinalResult != null &&
            _feedbackIndex >= 0 &&
            _feedbackIndex < FinalResult.Items.Count
                ? FinalResult.Items[_feedbackIndex]
                : null;

        public async Task PrepareAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(worldId))
                throw new ArgumentException("worldId is required", nameof(worldId));
            if (request == null)
                throw new ArgumentNullException(nameof(request));
            if (State == AssistedRouteState.Preparing ||
                State == AssistedRouteState.Submitting)
            {
                throw new InvalidOperationException("an assisted request is already in flight");
            }

            ResetForPreparation(worldId, request);
            NotifyChanged();
            try
            {
                StrictQuizValidator.Validate(request);
                var prepared = await _client.PrepareAssistedRouteAsync(
                    worldId,
                    request,
                    cancellationToken);
                StrictQuizValidator.Validate(prepared);
                RequirePreparationIdentity(worldId, request, prepared);
                InitializeBatch(prepared.Batch);
                State = AssistedRouteState.WorkedExample;
                NotifyChanged();
            }
            catch (OperationCanceledException)
            {
                SetFailure("request_cancelled", true, false);
                throw;
            }
            catch (WaylineClientException error)
            {
                SetFailure(error.Code, true, false);
            }
            catch (Exception)
            {
                SetFailure("integrity_failure", true, false);
            }
        }

        public Task RecoverPreparationAsync(CancellationToken cancellationToken)
        {
            if (!CanRecoverPreparation ||
                string.IsNullOrEmpty(_worldId) ||
                _prepareRequest == null)
            {
                throw new InvalidOperationException("preparation recovery is not available");
            }
            return PrepareAsync(_worldId, _prepareRequest, cancellationToken);
        }

        public void AcknowledgeWorkedExample()
        {
            if (State != AssistedRouteState.WorkedExample)
                throw new InvalidOperationException("worked example is not visible");
            State = AssistedRouteState.Answering;
            CurrentItemIndex = 0;
            NotifyChanged();
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
            {
                throw new ArgumentException(
                    "optionId is not public for this item",
                    nameof(optionId));
            }
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

        public Task SubmitAsync(CancellationToken cancellationToken)
        {
            if (_submissionTask != null && !_submissionTask.IsCompleted)
                return _submissionTask;
            if (State != AssistedRouteState.Answering)
                throw new InvalidOperationException("assisted completion is not available");
            if (_submission != null || FinalResult != null)
                throw new InvalidOperationException("assisted completion has already been used");
            if (!CanSubmit)
            {
                throw new InvalidOperationException(
                    "every supported answer and confidence is required");
            }

            AssistedRouteComplete submission;
            try
            {
                submission = BuildSubmission();
                StrictQuizValidator.Validate(submission);
            }
            catch (Exception)
            {
                SetFailure("integrity_failure", false, false);
                return Task.CompletedTask;
            }

            _submission = submission;
            State = AssistedRouteState.Submitting;
            LastFailureCode = null;
            CanRecoverPreparation = false;
            CanRetryCompletion = false;
            NotifyChanged();
            _submissionTask = SubmitCoreAsync(submission, cancellationToken);
            return _submissionTask;
        }

        public Task RetryCompletionAsync(CancellationToken cancellationToken)
        {
            if (_submissionTask != null && !_submissionTask.IsCompleted)
                return _submissionTask;
            if (!CanRetryCompletion || _submission == null || FinalResult != null)
                throw new InvalidOperationException("assisted completion retry is not available");

            State = AssistedRouteState.Submitting;
            LastFailureCode = null;
            CanRecoverPreparation = false;
            CanRetryCompletion = false;
            NotifyChanged();
            _submissionTask = SubmitCoreAsync(_submission, cancellationToken);
            return _submissionTask;
        }

        public void AdvanceFeedback()
        {
            if (CurrentFeedback == null)
                throw new InvalidOperationException("assisted feedback is not visible");
            if (_feedbackIndex == FinalResult.Items.Count - 1)
            {
                State = AssistedRouteState.Complete;
            }
            else
            {
                _feedbackIndex++;
            }
            NotifyChanged();
        }

        public string GetLogSafeState()
        {
            return HasFailure
                ? $"AssistedRoute(State={State}, HasFailure=True, FailureCode={LastFailureCode})"
                : $"AssistedRoute(State={State}, HasFailure=False)";
        }

        private async Task SubmitCoreAsync(
            AssistedRouteComplete submission,
            CancellationToken cancellationToken)
        {
            try
            {
                var result = await _client.CompleteAssistedRouteAsync(
                    _worldId,
                    _routeId,
                    submission,
                    cancellationToken);
                StrictQuizValidator.Validate(result);
                ValidateCompletion(result, submission);
                FinalResult = result;
                _feedbackIndex = 0;
                State = AssistedRouteState.Revealed;
                LastFailureCode = null;
                CanRecoverPreparation = false;
                CanRetryCompletion = false;
                NotifyChanged();
            }
            catch (OperationCanceledException)
            {
                SetFailure("request_cancelled", false, true);
                throw;
            }
            catch (WaylineClientException error)
            {
                var stable = StableFailureCodes.Contains(error.Code ?? string.Empty)
                    ? error.Code
                    : "integrity_failure";
                SetFailure(
                    stable,
                    false,
                    RetryableCompletionCodes.Contains(stable));
            }
            catch (Exception)
            {
                SetFailure("integrity_failure", false, false);
            }
        }

        private AssistedRouteComplete BuildSubmission()
        {
            var selections = _answers
                .Select(answer => new AssistedSelection(
                    answer.ItemId,
                    answer.SelectedOptionId,
                    answer.SelectedConfidence.Value))
                .ToList()
                .AsReadOnly();
            return new AssistedRouteComplete(
                "wayline.v1",
                _requestIdFactory(),
                _prepareRequest.SessionId,
                selections);
        }

        private void ValidateCompletion(
            AssistedRouteCompleted result,
            AssistedRouteComplete submission)
        {
            if (result.RequestId != submission.RequestId ||
                result.WorldId != _worldId ||
                result.RouteId != _routeId ||
                result.Items.Count != submission.Selections.Count ||
                result.Items.Count != Batch.Items.Count)
            {
                throw new JsonSerializationException(
                    "assisted completion identity does not match the submission");
            }

            for (var index = 0; index < result.Items.Count; index++)
            {
                var publicItem = Batch.Items[index];
                var selection = submission.Selections[index];
                var revealed = result.Items[index];
                var selectedOption = publicItem.Options.FirstOrDefault(
                    option => option.OptionId == selection.OptionId);
                var correctOption = publicItem.Options.FirstOrDefault(
                    option => option.OptionId == revealed.CorrectOptionId);
                if (selection.ItemId != publicItem.ItemId ||
                    revealed.ItemId != selection.ItemId ||
                    revealed.SelectedOptionId != selection.OptionId ||
                    revealed.Confidence != selection.Confidence ||
                    selectedOption == null ||
                    correctOption == null ||
                    revealed.SelectedAnswer != selectedOption.DisplayText ||
                    revealed.CorrectAnswer != correctOption.DisplayText)
                {
                    throw new JsonSerializationException(
                        "assisted completion does not match immutable selections");
                }
            }
        }

        private void RequirePreparationIdentity(
            string worldId,
            AssistedRoutePrepare request,
            AssistedRoutePrepared prepared)
        {
            if (prepared.RequestId != request.RequestId ||
                prepared.WorldId != worldId ||
                prepared.Batch.WorldId != worldId)
            {
                throw new JsonSerializationException(
                    "assisted preparation identity does not match the request");
            }
        }

        private void InitializeBatch(AssistedRouteBatch batch)
        {
            Batch = batch;
            _routeId = batch.RouteId;
            _answers.Clear();
            foreach (var item in batch.Items)
                _answers.Add(new AssistedAnswerState(item.ItemId));
            CurrentItemIndex = 0;
            FinalResult = null;
            _feedbackIndex = 0;
            LastFailureCode = null;
            CanRecoverPreparation = false;
            CanRetryCompletion = false;
        }

        private void ResetForPreparation(string worldId, AssistedRoutePrepare request)
        {
            State = AssistedRouteState.Preparing;
            Batch = null;
            _answers.Clear();
            CurrentItemIndex = 0;
            FinalResult = null;
            LastFailureCode = null;
            CanRecoverPreparation = false;
            _worldId = worldId;
            _routeId = null;
            _prepareRequest = request;
            _submission = null;
            _submissionTask = null;
            _feedbackIndex = 0;
        }

        private AssistedAnswerState FindAnswer(string itemId)
        {
            var answer = _answers.FirstOrDefault(value => value.ItemId == itemId);
            return answer ??
                throw new ArgumentException("itemId is not in this route", nameof(itemId));
        }

        private AssistedSupportedItem FindItem(string itemId)
        {
            var item = Batch.Items.FirstOrDefault(value => value.ItemId == itemId);
            return item ??
                throw new ArgumentException("itemId is not in this route", nameof(itemId));
        }

        private void RequireEditable()
        {
            if (!AnswersRemainEditable)
                throw new InvalidOperationException("assisted answers are not editable");
        }

        private static bool IsComplete(AssistedAnswerState answer)
        {
            return answer.SelectedOptionId != null && answer.SelectedConfidence.HasValue;
        }

        private void SetFailure(
            string code,
            bool canRecoverPreparation,
            bool canRetryCompletion)
        {
            State = AssistedRouteState.Failed;
            LastFailureCode = StableFailureCodes.Contains(code ?? string.Empty)
                ? code
                : "integrity_failure";
            CanRecoverPreparation = canRecoverPreparation;
            CanRetryCompletion = canRetryCompletion;
            NotifyChanged();
        }

        private void NotifyChanged()
        {
            Changed?.Invoke();
        }
    }
}
