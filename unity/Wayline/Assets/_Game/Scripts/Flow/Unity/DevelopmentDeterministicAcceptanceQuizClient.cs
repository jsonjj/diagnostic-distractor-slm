#if UNITY_EDITOR || DEVELOPMENT_BUILD
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;

namespace Wayline.Flow.Unity
{
    internal sealed class DevelopmentDeterministicAcceptanceQuizClient : IWaylineForgeClient
    {
        public const string VisibleLabel =
            "DETERMINISTIC LOCAL ACCEPTANCE DATA — NOT LIVE SLM";

        private PublicQuizBatch _batch;
        private AssistedRouteBatch _assistedBatch;
        private InitialSubmission _initial;

        public Task CheckHealthAsync(CancellationToken cancellationToken) =>
            Task.CompletedTask;

        public Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            _batch = BuildBatch(request);
            _initial = null;
            return Task.FromResult(_batch);
        }

        /// <summary>
        /// Replaces the learner-visible batch while retaining this client's
        /// sealed scoring/revision authority. Intended only for the development
        /// live-SLM wrapper after every generated distractor has passed the
        /// external verifier. Option IDs and correct option position remain
        /// unchanged.
        /// </summary>
        internal void OverridePreparedBatch(PublicQuizBatch verifiedBatch)
        {
            _batch = verifiedBatch ?? throw new ArgumentNullException(nameof(verifiedBatch));
            _initial = null;
        }

        public Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken) =>
            Task.FromException<QuizSnapshot>(new NotSupportedException());

        public Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            _initial = submission ?? throw new ArgumentNullException(nameof(submission));
            var wrong = CountWrong(submission.Selections);
            return Task.FromResult(new InitialSubmissionResult(
                "wayline.v1",
                submission.BatchId,
                submission.ItemCount,
                wrong,
                wrong > 0,
                wrong == 0
                    ? BuildFinal(submission.Selections, submission.Selections, false)
                    : null));
        }

        public Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (_initial == null)
                throw new InvalidOperationException("Initial acceptance submission is absent.");
            return Task.FromResult(BuildFinal(
                _initial.Selections,
                submission.Selections,
                true));
        }

        public Task<BossGateResult> GetBossGateAsync(
            string worldId,
            string sessionId,
            CancellationToken cancellationToken) =>
            Task.FromException<BossGateResult>(new NotSupportedException());

        public Task<AssistedRoutePrepared> PrepareAssistedRouteAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            _assistedBatch = new AssistedRouteBatch(
                "assisted-acceptance-000000001",
                worldId,
                new AssistedWorkedExample(
                    "item-worked-acceptance-001",
                    "What is the value of the 7 in 4,782?",
                    "700",
                    new[]
                    {
                        "The 7 is in the hundreds place.",
                        "Seven hundreds is 700. Keep both placeholder zeros."
                    },
                    "Name the place, then write the digit's complete value."),
                new[]
                {
                    AssistedItem(
                        "item-assisted-acceptance-001",
                        "What is the value of the 6 in 6,241?",
                        "6000"),
                    AssistedItem(
                        "item-assisted-acceptance-002",
                        "What is the value of the 3 in 3,508?",
                        "3000")
                });
            return Task.FromResult(new AssistedRoutePrepared(
                "wayline.v1",
                request.RequestId,
                worldId,
                _assistedBatch));
        }

        public Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var items = request.Selections.Select(selection =>
            {
                var publicItem = _assistedBatch.Items.Single(
                    item => item.ItemId == selection.ItemId);
                var selectedOption = publicItem.Options.Single(
                    option => option.OptionId == selection.OptionId);
                var correctOption = publicItem.Options[0];
                var correct = selection.OptionId == correctOption.OptionId;
                const string method =
                    "Name the place first, then preserve every required placeholder zero.";
                const string step =
                    "Read the place, multiply the digit by that place value, and check the zeros.";
                var possible = correct
                    ? null
                    : "This answer can come from naming the digit without its place value.";
                var canonical = new List<string>();
                if (possible != null)
                    canonical.Add(possible);
                canonical.Add(method);
                canonical.Add(step);
                return new AssistedItemResult(
                    selection.ItemId,
                    selection.OptionId,
                    selectedOption.DisplayText,
                    selection.Confidence,
                    correctOption.OptionId,
                    correctOption.DisplayText,
                    correct,
                    possible,
                    method,
                    new[] { step },
                    canonical);
            }).ToArray();
            return Task.FromResult(new AssistedRouteCompleted(
                "wayline.v1",
                request.RequestId,
                worldId,
                routeId,
                1,
                2,
                items.Count(item => item.IsCorrect),
                true,
                items));
        }

        public BattleCompleted AuthorizeBattle(
            string requestId,
            FlowBattle battle,
            FinalQuizResult result,
            bool bossBattle,
            bool worldCleared,
            bool sealTrialRequired)
        {
            return new BattleCompleted(
                "wayline.v1",
                requestId,
                battle.WorldId,
                battle.BattleId,
                result.BatchId,
                result.FinalCorrectCount,
                result.ItemCount,
                bossBattle,
                worldCleared,
                sealTrialRequired);
        }

        public SealTrialCompleted AuthorizeSeal(
            string requestId,
            FlowBattle battle,
            FinalQuizResult result,
            int attemptNumber)
        {
            return new SealTrialCompleted(
                "wayline.v1",
                requestId,
                battle.WorldId,
                attemptNumber,
                result.BatchId,
                Math.Max(2, Math.Min(3, result.FinalCorrectCount)),
                3,
                true,
                true,
                false);
        }

        private static PublicQuizBatch BuildBatch(BattleQuizRequest request)
        {
            var batchId = request.BattleTier == BattleTier.SealTrial
                ? "acceptance-seal-batch-001"
                : "acceptance-normal-batch-001";
            return new PublicQuizBatch(
                "wayline.v1",
                batchId,
                3,
                new[]
                {
                    Item("item-acceptance-001", "In 63,482, what is the value of the digit 3?",
                        "3,000", "300", "30", "3"),
                    Item("item-acceptance-002", "What is 4,208 + 590?",
                        "4,798", "4,708", "9,308", "4,267"),
                    Item("item-acceptance-003", "What is 7,000 − 2,465?",
                        "4,535", "5,465", "4,645", "5,535")
                });
        }

        private static PublicQuizItem Item(
            string itemId,
            string prompt,
            params string[] options)
        {
            return new PublicQuizItem(
                itemId,
                prompt,
                options.Select((display, index) => new PublicQuizOption(
                    itemId + "-" + (char)('a' + index),
                    display)).ToArray());
        }

        private static AssistedSupportedItem AssistedItem(
            string itemId,
            string prompt,
            string correct)
        {
            return new AssistedSupportedItem(
                itemId,
                prompt,
                new[]
                {
                    new PublicQuizOption(itemId + "-a", correct),
                    new PublicQuizOption(itemId + "-b", "The marked digit only"),
                    new PublicQuizOption(itemId + "-c", "One place smaller"),
                    new PublicQuizOption(itemId + "-d", "One place larger")
                });
        }

        private static int CountWrong(IReadOnlyList<SubmissionSelection> selections)
        {
            return selections.Count(selection =>
                !selection.OptionId.EndsWith("-a", StringComparison.Ordinal));
        }

        private FinalQuizResult BuildFinal(
            IReadOnlyList<SubmissionSelection> first,
            IReadOnlyList<SubmissionSelection> final,
            bool revised)
        {
            var results = new List<FinalQuizItemResult>();
            for (var index = 0; index < _batch.Items.Count; index++)
            {
                var item = _batch.Items[index];
                var firstSelection = first[index];
                var finalSelection = final[index];
                var correctOption = item.Options[0];
                var firstCorrect = firstSelection.OptionId == correctOption.OptionId;
                var finalCorrect = finalSelection.OptionId == correctOption.OptionId;
                results.Add(new FinalQuizItemResult(
                    item.ItemId,
                    new RevealedSelection(
                        firstSelection.OptionId,
                        firstSelection.Confidence,
                        firstCorrect),
                    new RevealedSelection(
                        finalSelection.OptionId,
                        finalSelection.Confidence,
                        finalCorrect),
                    correctOption.OptionId,
                    correctOption.DisplayText,
                    new[]
                    {
                        "Name the place or operation before calculating.",
                        "Calculate carefully, then estimate to check the result."
                    },
                    finalCorrect
                        ? null
                        : "This answer can come from applying a tempting place-value shortcut.",
                    "Use place value and the written operation, then check with an estimate.",
                    !firstCorrect && finalCorrect));
            }

            return new FinalQuizResult(
                "wayline.v1",
                _batch.BatchId,
                _batch.ItemCount,
                CountWrong(first),
                results.Count(item => item.FinalSelection.IsCorrect),
                revised,
                results);
        }
    }
}
#endif
