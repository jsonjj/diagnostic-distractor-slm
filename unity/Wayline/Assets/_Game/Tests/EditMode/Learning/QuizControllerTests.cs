using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using NUnit.Framework;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;
using Wayline.Learning.Quiz;

namespace Wayline.Tests.Learning
{
    public sealed class QuizControllerTests
    {
        [Test]
        public async Task NothingIsPreselectedAndAnswerPlusConfidenceAreRequired()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch()
            };
            var controller = NewController(client);

            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Answering));
            Assert.That(controller.Answers, Has.Count.EqualTo(3));
            Assert.That(controller.Answers.All(answer => answer.SelectedOptionId == null), Is.True);
            Assert.That(controller.Answers.All(answer => answer.SelectedConfidence == null), Is.True);
            Assert.That(controller.CanContinueCurrent, Is.False);

            controller.SelectOption("item-001", "opt-001-a");
            Assert.That(controller.CanContinueCurrent, Is.False);
            controller.SelectConfidence("item-001", Confidence.Certain);

            Assert.That(controller.CanContinueCurrent, Is.True);
            Assert.That(controller.CanSubmitCurrentPass, Is.False);
        }

        [Test]
        public async Task NonzeroServerCountOpensOneNeutralFullBatchReview()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialResult = RouteTrialTestData.NonzeroInitialResult()
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseTwoWrongFirstPass(controller);

            await controller.SubmitInitialAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Reviewing));
            Assert.That(controller.WrongCount, Is.EqualTo(2));
            Assert.That(controller.IsCountMomentVisible, Is.True);
            Assert.That(controller.FinalResult, Is.Null);
            Assert.That(controller.Answers.All(answer => answer.FirstOptionId != null), Is.True);
            Assert.That(
                typeof(QuizAnswerState).GetProperties().Select(property => property.Name),
                Has.None.Contains("Correct"));
            Assert.That(client.InitialSubmissions, Has.Count.EqualTo(1));
            Assert.That(client.InitialSubmissions[0].Selections.Count, Is.EqualTo(3));

            controller.AcknowledgeWrongCount();

            Assert.That(controller.IsCountMomentVisible, Is.False);
            Assert.That(controller.CurrentItemIndex, Is.Zero);
            Assert.That(controller.AnswersRemainEditable, Is.True);
        }

        [Test]
        public async Task ZeroWrongStillShowsExactCountThenSkipsRevision()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialResult = RouteTrialTestData.ZeroWrongInitialResult()
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseAllCorrectFirstPass(controller);

            await controller.SubmitInitialAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Revealed));
            Assert.That(controller.WrongCount, Is.Zero);
            Assert.That(controller.IsCountMomentVisible, Is.True);
            Assert.That(controller.FinalResult, Is.Not.Null);
            Assert.That(client.RevisionSubmissions, Is.Empty);

            controller.AcknowledgeWrongCount();

            Assert.That(controller.CurrentFeedback.ItemId, Is.EqualTo("item-001"));
        }

        [Test]
        public async Task DuplicateFinishReviewClicksShareOneRequestAndRevisionCannotRepeat()
        {
            var completion = new TaskCompletionSource<FinalQuizResult>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialResult = RouteTrialTestData.NonzeroInitialResult(),
                RevisionCompletion = completion
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseTwoWrongFirstPass(controller);
            await controller.SubmitInitialAsync(CancellationToken.None);
            controller.AcknowledgeWrongCount();
            RouteTrialTestData.ChooseAllCorrectReview(controller);

            var first = controller.SubmitRevisionAsync(CancellationToken.None);
            var duplicate = controller.SubmitRevisionAsync(CancellationToken.None);

            Assert.That(duplicate, Is.SameAs(first));
            Assert.That(controller.State, Is.EqualTo(QuizState.SubmittingRevision));
            Assert.That(client.RevisionSubmissions, Has.Count.EqualTo(1));

            completion.SetResult(RouteTrialTestData.RevisedFinalResult());
            await first;

            Assert.That(controller.State, Is.EqualTo(QuizState.Revealed));
            Assert.ThrowsAsync<InvalidOperationException>(async () =>
                await controller.SubmitRevisionAsync(CancellationToken.None));
        }

        [Test]
        public async Task ReloadResumesServerRevisionWithoutCreatingAnotherInitialPass()
        {
            var client = new FakeWaylineForgeClient
            {
                Snapshot = RouteTrialTestData.RevisionOpenSnapshot()
            };
            var controller = NewController(client);

            await controller.ResumeAsync("batch-001", "session-001", CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Reviewing));
            Assert.That(controller.WrongCount, Is.EqualTo(2));
            Assert.That(controller.Answers[1].FirstOptionId, Is.EqualTo("opt-002-b"));
            Assert.That(controller.Answers[1].SelectedOptionId, Is.EqualTo("opt-002-b"));
            Assert.That(client.InitialSubmissions, Is.Empty);
        }

        [Test]
        public async Task SnapshotResponseMustMatchPathOwnedBatch()
        {
            var differentBatch = StrictQuizValidator.Deserialize<QuizSnapshot>(
                JsonConvert.SerializeObject(RouteTrialTestData.RevisionOpenSnapshot())
                    .Replace("batch-001", "batch-002"));
            var client = new FakeWaylineForgeClient
            {
                Snapshot = differentBatch
            };
            var controller = NewController(client);

            await controller.ResumeAsync(
                "batch-001",
                "session-001",
                CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Loading));
            Assert.That(controller.LastFailureCode, Is.EqualTo("integrity_failure"));
            Assert.That(controller.Batch, Is.Null);
        }

        [Test]
        public async Task FailedSubmissionPreservesChoicesAndRecoversFromServerSnapshot()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialFailure = new WaylineClientException("storage_busy", 503),
                Snapshot = RouteTrialTestData.RevisionOpenSnapshot()
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseTwoWrongFirstPass(controller);

            await controller.SubmitInitialAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Answering));
            Assert.That(controller.LastFailureCode, Is.EqualTo("storage_busy"));
            Assert.That(controller.Answers[1].SelectedOptionId, Is.EqualTo("opt-002-b"));
            Assert.That(controller.CanRecover, Is.True);

            await controller.RecoverAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Reviewing));
            Assert.That(controller.LastFailureCode, Is.Null);
            Assert.That(client.InitialSubmissions, Has.Count.EqualTo(1));
        }

        [Test]
        public async Task WrongCountResponseMustMatchTheImmutableSubmittedBatch()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialResult = new InitialSubmissionResult(
                    "wayline.v1", "different-batch", 3, 2, true, null)
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseTwoWrongFirstPass(controller);

            await controller.SubmitInitialAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Answering));
            Assert.That(controller.LastFailureCode, Is.EqualTo("integrity_failure"));
            Assert.That(controller.WrongCount, Is.Null);
            Assert.That(controller.FinalResult, Is.Null);
        }

        [Test]
        public async Task RevealSelectionsMustMatchBothImmutableLocalSubmissions()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialResult = RouteTrialTestData.NonzeroInitialResult(),
                RevisionResult = RouteTrialTestData.MismatchedRevisedFinalResult()
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseTwoWrongFirstPass(controller);
            await controller.SubmitInitialAsync(CancellationToken.None);
            controller.AcknowledgeWrongCount();
            RouteTrialTestData.ChooseAllCorrectReview(controller);

            await controller.SubmitRevisionAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Reviewing));
            Assert.That(controller.LastFailureCode, Is.EqualTo("integrity_failure"));
            Assert.That(controller.FinalResult, Is.Null);
        }

        [Test]
        public async Task RevealedCorrectAnswerMustMatchItsPublicOptionDisplay()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialResult = RouteTrialTestData.ZeroWrongInitialResult(
                    correctAnswer: "Contradictory trusted answer")
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseAllCorrectFirstPass(controller);

            await controller.SubmitInitialAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(QuizState.Answering));
            Assert.That(controller.LastFailureCode, Is.EqualTo("integrity_failure"));
            Assert.That(controller.WrongCount, Is.Null);
            Assert.That(controller.FinalResult, Is.Null);
        }

        [Test]
        public async Task DefaultSerializedAndLogSafeStateContainNoSealedOrLearnerPayload()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialResult = RouteTrialTestData.NonzeroInitialResult()
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseTwoWrongFirstPass(controller);
            await controller.SubmitInitialAsync(CancellationToken.None);

            var serialized = JsonConvert.SerializeObject(controller);

            foreach (var forbidden in new[]
            {
                "correctOptionId",
                "correctAnswer",
                "trustedSteps",
                "possibleError",
                "reliableMethod",
                "isCorrect",
                "prompt",
                "optionId",
                "confidence",
                "session-001"
            })
            {
                StringAssert.DoesNotContain(forbidden, serialized);
            }
            StringAssert.DoesNotContain("Trusted answer", controller.GetLogSafeState());
        }

        [Test]
        public async Task FinalFeedbackAdvancesInOrderAndCompletesOnlyFromLastMethod()
        {
            var client = new FakeWaylineForgeClient
            {
                PreparedBatch = RouteTrialTestData.Batch(),
                InitialResult = RouteTrialTestData.ZeroWrongInitialResult()
            };
            var controller = NewController(client);
            await controller.PrepareAsync(RouteTrialTestData.Request(), CancellationToken.None);
            RouteTrialTestData.ChooseAllCorrectFirstPass(controller);
            await controller.SubmitInitialAsync(CancellationToken.None);
            controller.AcknowledgeWrongCount();

            Assert.That(controller.CurrentFeedback.ItemId, Is.EqualTo("item-001"));
            Assert.That(controller.FinalActionLabel, Is.EqualTo("Next method"));
            controller.AdvanceFinalFeedback();
            controller.AdvanceFinalFeedback();
            Assert.That(controller.CurrentFeedback.ItemId, Is.EqualTo("item-003"));
            Assert.That(controller.FinalActionLabel, Is.EqualTo("Complete route trial"));

            controller.AdvanceFinalFeedback();

            Assert.That(controller.State, Is.EqualTo(QuizState.Complete));
        }

        private static QuizController NewController(FakeWaylineForgeClient client)
        {
            var ids = new Queue<string>(new[]
            {
                "initial-request-001",
                "revision-request-001"
            });
            return new QuizController(client, () => ids.Dequeue());
        }
    }

    internal sealed class FakeWaylineForgeClient : IWaylineForgeClient
    {
        public PublicQuizBatch PreparedBatch { get; set; }

        public InitialSubmissionResult InitialResult { get; set; }

        public FinalQuizResult RevisionResult { get; set; }

        public QuizSnapshot Snapshot { get; set; }

        public WaylineClientException InitialFailure { get; set; }

        public TaskCompletionSource<FinalQuizResult> RevisionCompletion { get; set; }

        public List<InitialSubmission> InitialSubmissions { get; } =
            new List<InitialSubmission>();

        public List<RevisionSubmission> RevisionSubmissions { get; } =
            new List<RevisionSubmission>();

        public Task CheckHealthAsync(CancellationToken cancellationToken)
        {
            return Task.CompletedTask;
        }

        public Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken)
        {
            return Task.FromResult(PreparedBatch);
        }

        public Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken)
        {
            return Task.FromResult(Snapshot);
        }

        public Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken)
        {
            InitialSubmissions.Add(submission);
            if (InitialFailure != null)
                return Task.FromException<InitialSubmissionResult>(InitialFailure);
            return Task.FromResult(InitialResult);
        }

        public Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken)
        {
            RevisionSubmissions.Add(submission);
            return RevisionCompletion != null
                ? RevisionCompletion.Task
                : Task.FromResult(RevisionResult);
        }

        public Task<BossGateResult> GetBossGateAsync(
            string worldId,
            string sessionId,
            CancellationToken cancellationToken)
        {
            throw new NotSupportedException();
        }

        public Task<AssistedRoutePrepared> PrepareAssistedRouteAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken)
        {
            throw new NotSupportedException();
        }

        public Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken)
        {
            throw new NotSupportedException();
        }
    }

    internal static class RouteTrialTestData
    {
        public static BattleQuizRequest Request()
        {
            return new BattleQuizRequest(
                "wayline.v1",
                "prepare-request-001",
                "session-001",
                "valuehold_route_1",
                "valuehold",
                BattleTier.Route1);
        }

        public static PublicQuizBatch Batch()
        {
            var items = new List<PublicQuizItem>();
            for (var itemIndex = 1; itemIndex <= 3; itemIndex++)
            {
                var itemId = $"item-{itemIndex:000}";
                items.Add(new PublicQuizItem(
                    itemId,
                    itemIndex == 1
                        ? "What is 2.4 + 0.35?"
                        : $"What is {itemIndex} + {itemIndex}?",
                    new[]
                    {
                        new PublicQuizOption($"opt-{itemIndex:000}-a", "Answer A"),
                        new PublicQuizOption($"opt-{itemIndex:000}-b", "Answer B"),
                        new PublicQuizOption($"opt-{itemIndex:000}-c", "Answer C"),
                        new PublicQuizOption($"opt-{itemIndex:000}-d", "Answer D")
                    }));
            }

            return new PublicQuizBatch("wayline.v1", "batch-001", 3, items);
        }

        public static InitialSubmissionResult NonzeroInitialResult()
        {
            return new InitialSubmissionResult(
                "wayline.v1",
                "batch-001",
                3,
                2,
                true,
                null);
        }

        public static InitialSubmissionResult ZeroWrongInitialResult(
            string correctAnswer = "Answer A")
        {
            var final = ZeroWrongFinalResult(correctAnswer);
            return new InitialSubmissionResult(
                "wayline.v1",
                "batch-001",
                3,
                0,
                false,
                final);
        }

        public static FinalQuizResult ZeroWrongFinalResult(
            string correctAnswer = "Answer A")
        {
            return FinalResult(
                false,
                new[] { "opt-001-a", "opt-002-a", "opt-003-a" },
                new[] { "opt-001-a", "opt-002-a", "opt-003-a" },
                correctAnswer);
        }

        public static FinalQuizResult RevisedFinalResult()
        {
            return FinalResult(
                true,
                new[] { "opt-001-a", "opt-002-b", "opt-003-b" },
                new[] { "opt-001-a", "opt-002-a", "opt-003-a" },
                "Answer A");
        }

        public static FinalQuizResult MismatchedRevisedFinalResult()
        {
            return FinalResult(
                true,
                new[] { "opt-001-a", "opt-002-c", "opt-003-b" },
                new[] { "opt-001-a", "opt-002-a", "opt-003-a" },
                "Answer A");
        }

        public static QuizSnapshot RevisionOpenSnapshot()
        {
            var initial = new InitialSubmission(
                "wayline.v1",
                "initial-request-001",
                "batch-001",
                3,
                new[]
                {
                    new SubmissionSelection("item-001", "opt-001-a", Confidence.Certain),
                    new SubmissionSelection("item-002", "opt-002-b", Confidence.Leaning),
                    new SubmissionSelection("item-003", "opt-003-b", Confidence.Guessing)
                });
            return new QuizSnapshot(
                "wayline.v1",
                "batch-001",
                QuizSnapshotState.RevisionOpen,
                3,
                Batch(),
                initial,
                NonzeroInitialResult(),
                null,
                null);
        }

        public static void ChooseTwoWrongFirstPass(QuizController controller)
        {
            Choose(controller, 0, "opt-001-a", Confidence.Certain);
            Choose(controller, 1, "opt-002-b", Confidence.Leaning);
            Choose(controller, 2, "opt-003-b", Confidence.Guessing);
        }

        public static void ChooseAllCorrectFirstPass(QuizController controller)
        {
            Choose(controller, 0, "opt-001-a", Confidence.Certain);
            Choose(controller, 1, "opt-002-a", Confidence.Leaning);
            Choose(controller, 2, "opt-003-a", Confidence.Guessing);
        }

        public static void ChooseAllCorrectReview(QuizController controller)
        {
            ChooseAllCorrectFirstPass(controller);
        }

        private static void Choose(
            QuizController controller,
            int itemIndex,
            string optionId,
            Confidence confidence)
        {
            controller.GoToItem(itemIndex);
            var itemId = $"item-{itemIndex + 1:000}";
            controller.SelectOption(itemId, optionId);
            controller.SelectConfidence(itemId, confidence);
        }

        private static FinalQuizResult FinalResult(
            bool revisionUsed,
            IReadOnlyList<string> firstOptions,
            IReadOnlyList<string> finalOptions,
            string correctAnswer)
        {
            var items = new List<FinalQuizItemResult>();
            var firstWrong = 0;
            var finalCorrect = 0;
            for (var index = 0; index < 3; index++)
            {
                var correct = $"opt-{index + 1:000}-a";
                var confidence = index == 0
                    ? Confidence.Certain
                    : index == 1
                        ? Confidence.Leaning
                        : Confidence.Guessing;
                var firstIsCorrect = firstOptions[index] == correct;
                var finalIsCorrect = finalOptions[index] == correct;
                if (!firstIsCorrect)
                    firstWrong++;
                if (finalIsCorrect)
                    finalCorrect++;
                items.Add(new FinalQuizItemResult(
                    $"item-{index + 1:000}",
                    new RevealedSelection(firstOptions[index], confidence, firstIsCorrect),
                    new RevealedSelection(finalOptions[index], confidence, finalIsCorrect),
                    correct,
                    correctAnswer,
                    new[] { "Use place value.", "Check the operation." },
                    firstIsCorrect ? null : "This answer can come from changing the place value.",
                    "Align each place, then calculate and check.",
                    !firstIsCorrect && finalIsCorrect));
            }

            return new FinalQuizResult(
                "wayline.v1",
                "batch-001",
                3,
                firstWrong,
                finalCorrect,
                revisionUsed,
                items);
        }
    }
}
