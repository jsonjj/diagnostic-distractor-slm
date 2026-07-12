using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using NUnit.Framework;
using Wayline.Learning.Assisted;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;

namespace Wayline.Tests.Learning
{
    public sealed class AssistedRouteControllerTests
    {
        [Test]
        public void NewControllerStartsEmptyWithoutLearnerOrRouteState()
        {
            var controller = NewController(new AssistedRouteFakeClient());

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Empty));
            Assert.That(controller.Batch, Is.Null);
            Assert.That(controller.Answers, Is.Empty);
            Assert.That(controller.FinalResult, Is.Null);
            Assert.That(controller.LastFailureCode, Is.Null);
            Assert.That(controller.CanRecoverPreparation, Is.False);
            Assert.That(controller.AnswersRemainEditable, Is.False);
            Assert.That(controller.CanSubmit, Is.False);
        }

        [Test]
        public void StateModelContainsOnlyTheOneShotAssistedFlow()
        {
            Assert.That(
                Enum.GetNames(typeof(AssistedRouteState)),
                Is.EqualTo(new[]
                {
                    "Empty",
                    "Preparing",
                    "WorkedExample",
                    "Answering",
                    "Submitting",
                    "Revealed",
                    "Complete",
                    "Failed"
                }));

            var publicMembers = typeof(AssistedRouteController)
                .GetMembers(BindingFlags.Instance | BindingFlags.Public)
                .Select(member => member.Name)
                .ToArray();
            Assert.That(publicMembers, Has.None.EqualTo("WrongCount"));
            Assert.That(publicMembers, Has.None.EqualTo("AcknowledgeWrongCount"));
            Assert.That(publicMembers, Has.None.EqualTo("SubmitRevisionAsync"));
            Assert.That(publicMembers, Has.None.EqualTo("Reviewing"));
        }

        [Test]
        public async Task PreparationWaitsForClientAndShowsOneWorkedExampleBeforeTwoKeylessMcqs()
        {
            var pending = new TaskCompletionSource<AssistedRoutePrepared>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var client = new AssistedRouteFakeClient
            {
                PrepareCompletion = pending
            };
            var controller = NewController(client);
            var request = AssistedRouteTestData.PrepareRequest();

            var preparation = controller.PrepareAsync(
                AssistedRouteTestData.WorldId,
                request,
                CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Preparing));
            Assert.That(controller.Batch, Is.Null);
            Assert.That(client.PrepareCalls, Has.Count.EqualTo(1));
            Assert.That(client.PrepareCalls[0].WorldId, Is.EqualTo(AssistedRouteTestData.WorldId));
            Assert.That(client.PrepareCalls[0].Request, Is.SameAs(request));

            pending.SetResult(AssistedRouteTestData.Prepared());
            await preparation;

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.WorkedExample));
            Assert.That(controller.Batch.WorkedExample.ItemId, Is.EqualTo("item-worked-001"));
            Assert.That(controller.Batch.Items.Count, Is.EqualTo(2));
            Assert.That(controller.Answers.Count, Is.EqualTo(2));
            Assert.That(controller.Answers.All(answer => answer.SelectedOptionId == null), Is.True);
            Assert.That(controller.Answers.All(answer => answer.SelectedConfidence == null), Is.True);
            Assert.That(controller.AnswersRemainEditable, Is.False);
            Assert.That(controller.CanSubmit, Is.False);

            var supportedJson = JsonConvert.SerializeObject(controller.Batch.Items);
            foreach (var forbidden in new[]
            {
                "correctOptionId",
                "correctAnswer",
                "isCorrect",
                "possibleError",
                "reliableMethod",
                "trustedSteps",
                "procedureId"
            })
            {
                StringAssert.DoesNotContain(forbidden, supportedJson);
            }

            controller.AcknowledgeWorkedExample();

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Answering));
            Assert.That(controller.CurrentItemIndex, Is.Zero);
            Assert.That(controller.AnswersRemainEditable, Is.True);
            Assert.Throws<InvalidOperationException>(() => controller.AcknowledgeWorkedExample());
        }

        [TestCase("different-request", "valuehold", "valuehold")]
        [TestCase("prepare-assisted-001", "different-world", "different-world")]
        [TestCase("prepare-assisted-001", "valuehold", "different-world")]
        public async Task PreparationRejectsMismatchedRequestOrWorldIdentity(
            string responseRequestId,
            string responseWorldId,
            string batchWorldId)
        {
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared(
                    responseRequestId,
                    responseWorldId,
                    batchWorldId)
            };
            var controller = NewController(client);

            await controller.PrepareAsync(
                AssistedRouteTestData.WorldId,
                AssistedRouteTestData.PrepareRequest(),
                CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Failed));
            Assert.That(controller.LastFailureCode, Is.EqualTo("integrity_failure"));
            Assert.That(controller.Batch, Is.Null);
            Assert.That(controller.Answers, Is.Empty);
        }

        [Test]
        public async Task BothAnswerAndConfidenceAreRequiredForEverySupportedItem()
        {
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared()
            };
            var controller = await PreparedControllerAsync(client);
            controller.AcknowledgeWorkedExample();

            Assert.That(controller.CanContinueCurrent, Is.False);
            Assert.That(controller.CanSubmit, Is.False);

            controller.SelectOption("item-supported-001", "opt-supported-001-d");
            Assert.That(controller.CanContinueCurrent, Is.False);
            controller.SelectConfidence("item-supported-001", Confidence.Certain);
            Assert.That(controller.CanContinueCurrent, Is.True);
            Assert.That(controller.CanSubmit, Is.False);

            controller.GoToItem(1);
            controller.SelectConfidence("item-supported-002", Confidence.Leaning);
            Assert.That(controller.CanContinueCurrent, Is.False);
            Assert.That(controller.CanSubmit, Is.False);
            controller.SelectOption("item-supported-002", "opt-supported-002-b");

            Assert.That(controller.CanContinueCurrent, Is.True);
            Assert.That(controller.CanSubmit, Is.True);
            Assert.Throws<ArgumentException>(() =>
                controller.SelectOption("item-supported-002", "opt-supported-001-d"));
        }

        [Test]
        public async Task DuplicateSubmitSharesOneRequestAndSelectionsFollowPreparedOrder()
        {
            var pending = new TaskCompletionSource<AssistedRouteCompleted>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared(),
                CompleteCompletion = pending
            };
            var controller = await PreparedControllerAsync(client);
            controller.AcknowledgeWorkedExample();
            AssistedRouteTestData.AnswerInReverseInteractionOrder(controller);

            var first = controller.SubmitAsync(CancellationToken.None);
            var duplicate = controller.SubmitAsync(CancellationToken.None);

            Assert.That(duplicate, Is.SameAs(first));
            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Submitting));
            Assert.That(controller.AnswersRemainEditable, Is.False);
            Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));
            var call = client.CompleteCalls[0];
            Assert.That(call.WorldId, Is.EqualTo(AssistedRouteTestData.WorldId));
            Assert.That(call.RouteId, Is.EqualTo(AssistedRouteTestData.RouteId));
            Assert.That(call.Request.RequestId, Is.EqualTo("complete-assisted-001"));
            Assert.That(call.Request.SessionId, Is.EqualTo(AssistedRouteTestData.SessionId));
            Assert.That(
                call.Request.Selections.Select(selection => selection.ItemId),
                Is.EqualTo(new[] { "item-supported-001", "item-supported-002" }));
            Assert.That(
                call.Request.Selections.Select(selection => selection.OptionId),
                Is.EqualTo(new[] { "opt-supported-001-d", "opt-supported-002-b" }));
            Assert.That(
                call.Request.Selections.Select(selection => selection.Confidence),
                Is.EqualTo(new[] { Confidence.Certain, Confidence.Leaning }));

            pending.SetResult(AssistedRouteTestData.Completed(1));
            await first;

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Revealed));
            Assert.That(controller.FinalResult.FinalCorrect, Is.EqualTo(1));
            Assert.ThrowsAsync<InvalidOperationException>(async () =>
                await controller.SubmitAsync(CancellationToken.None));
            Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));
        }

        [Test]
        public async Task CompletionRequestExposesTheExactReadOnlySubmission()
        {
            var pending = new TaskCompletionSource<AssistedRouteCompleted>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared(),
                CompleteCompletion = pending
            };
            var controller = await PreparedControllerAsync(client);
            controller.AcknowledgeWorkedExample();
            AssistedRouteTestData.AnswerInReverseInteractionOrder(controller);

            Assert.That(controller.CompletionRequest, Is.Null);

            var submission = controller.SubmitAsync(CancellationToken.None);

            Assert.That(
                controller.CompletionRequest,
                Is.SameAs(client.CompleteCalls.Single().Request));
            var selections = (ICollection<AssistedSelection>)
                controller.CompletionRequest.Selections;
            Assert.That(selections.IsReadOnly, Is.True);
            Assert.Throws<NotSupportedException>(() => selections.Clear());
            Assert.That(
                typeof(AssistedRouteController)
                    .GetProperty(nameof(AssistedRouteController.CompletionRequest))
                    ?.CanWrite,
                Is.False);

            pending.SetResult(AssistedRouteTestData.Completed(1));
            await submission;

            Assert.That(
                controller.CompletionRequest,
                Is.SameAs(client.CompleteCalls.Single().Request));
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        public async Task ServerAuthoritativeClearRevealsAndCompletesAtEveryScore(int finalCorrect)
        {
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared(),
                CompletedRoute = AssistedRouteTestData.Completed(finalCorrect)
            };
            var controller = await PreparedControllerAsync(client);
            controller.AcknowledgeWorkedExample();
            AssistedRouteTestData.AnswerInReverseInteractionOrder(controller);

            await controller.SubmitAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Revealed));
            Assert.That(controller.FinalResult.FinalCorrect, Is.EqualTo(finalCorrect));
            Assert.That(controller.FinalResult.WorldCleared, Is.True);
            Assert.That(controller.CurrentFeedback.ItemId, Is.EqualTo("item-supported-001"));

            controller.AdvanceFeedback();
            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Revealed));
            Assert.That(controller.CurrentFeedback.ItemId, Is.EqualTo("item-supported-002"));
            controller.AdvanceFeedback();

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Complete));
            Assert.That(controller.CurrentFeedback, Is.Null);
        }

        [TestCase("request")]
        [TestCase("world")]
        [TestCase("route")]
        [TestCase("item")]
        [TestCase("option")]
        [TestCase("confidence")]
        public async Task CompletionRejectsMismatchedIdentityOrEchoedSelection(string mismatch)
        {
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared(),
                CompletedRoute = AssistedRouteTestData.CompletedWithMismatch(mismatch)
            };
            var controller = await PreparedControllerAsync(client);
            controller.AcknowledgeWorkedExample();
            AssistedRouteTestData.AnswerInReverseInteractionOrder(controller);

            await controller.SubmitAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Failed));
            Assert.That(controller.LastFailureCode, Is.EqualTo("integrity_failure"));
            Assert.That(controller.FinalResult, Is.Null);
            Assert.That(controller.CurrentFeedback, Is.Null);
            Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));
        }

        [Test]
        public async Task FailedPreparationCanRecoverOnlyByReplayingTheExactRequest()
        {
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared()
            };
            client.PrepareFailures.Enqueue(
                new WaylineClientException("safe_content_unavailable", 503));
            var controller = NewController(client);
            var request = AssistedRouteTestData.PrepareRequest();

            await controller.PrepareAsync(
                AssistedRouteTestData.WorldId,
                request,
                CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Failed));
            Assert.That(controller.LastFailureCode, Is.EqualTo("safe_content_unavailable"));
            Assert.That(controller.CanRecoverPreparation, Is.True);

            await controller.RecoverPreparationAsync(CancellationToken.None);

            Assert.That(client.PrepareCalls, Has.Count.EqualTo(2));
            Assert.That(client.PrepareCalls[1].WorldId, Is.EqualTo(client.PrepareCalls[0].WorldId));
            Assert.That(client.PrepareCalls[1].Request, Is.SameAs(client.PrepareCalls[0].Request));
            Assert.That(client.PrepareCalls[1].Request, Is.SameAs(request));
            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.WorkedExample));
            Assert.That(controller.LastFailureCode, Is.Null);
            Assert.That(controller.CanRecoverPreparation, Is.False);
        }

        [Test]
        public async Task FailedCompletionRetriesTheExactImmutableSubmission()
        {
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared(),
                CompletedRoute = AssistedRouteTestData.Completed(1)
            };
            client.CompleteFailures.Enqueue(
                new WaylineClientException("runtime_unavailable", 0));
            var controller = await PreparedControllerAsync(client);
            controller.AcknowledgeWorkedExample();
            AssistedRouteTestData.AnswerInReverseInteractionOrder(controller);

            await controller.SubmitAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Failed));
            Assert.That(controller.LastFailureCode, Is.EqualTo("runtime_unavailable"));
            Assert.That(controller.CanRetryCompletion, Is.True);
            Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));
            var original = client.CompleteCalls[0].Request;

            await controller.RetryCompletionAsync(CancellationToken.None);

            Assert.That(client.CompleteCalls, Has.Count.EqualTo(2));
            Assert.That(client.CompleteCalls[1].Request, Is.SameAs(original));
            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Revealed));
            Assert.That(controller.LastFailureCode, Is.Null);
            Assert.That(controller.CanRetryCompletion, Is.False);
        }

        [Test]
        public async Task UnexpectedFailureExposesOnlyAStableCode()
        {
            var client = new AssistedRouteFakeClient();
            client.PrepareFailures.Enqueue(new InvalidOperationException(
                "session-assisted-001 leaked item-supported-001 answer 6000"));
            var controller = NewController(client);

            await controller.PrepareAsync(
                AssistedRouteTestData.WorldId,
                AssistedRouteTestData.PrepareRequest(),
                CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Failed));
            Assert.That(controller.LastFailureCode, Is.EqualTo("integrity_failure"));
            Assert.That(
                typeof(AssistedRouteController).GetProperties()
                    .Any(property => typeof(Exception).IsAssignableFrom(property.PropertyType)),
                Is.False);
            var logState = controller.GetLogSafeState();
            StringAssert.DoesNotContain("session-assisted-001", logState);
            StringAssert.DoesNotContain("item-supported-001", logState);
            StringAssert.DoesNotContain("6000", logState);
        }

        [Test]
        public async Task LogSafeStateContainsNoRouteLearnerOrAnswerIdentifiers()
        {
            var client = new AssistedRouteFakeClient
            {
                PreparedRoute = AssistedRouteTestData.Prepared()
            };
            var controller = await PreparedControllerAsync(client);
            controller.AcknowledgeWorkedExample();
            controller.SelectOption("item-supported-001", "opt-supported-001-d");
            controller.SelectConfidence("item-supported-001", Confidence.Certain);

            var logState = controller.GetLogSafeState();

            StringAssert.Contains("Answering", logState);
            foreach (var forbidden in new[]
            {
                AssistedRouteTestData.WorldId,
                AssistedRouteTestData.RouteId,
                AssistedRouteTestData.SessionId,
                "prepare-assisted-001",
                "complete-assisted-001",
                "item-worked-001",
                "item-supported-001",
                "item-supported-002",
                "opt-supported-001-d",
                "6000",
                "What is the value of the 7 in 4,782?",
                "The 7 is in the hundreds place."
            })
            {
                StringAssert.DoesNotContain(forbidden, logState);
            }
        }

        private static AssistedRouteController NewController(AssistedRouteFakeClient client)
        {
            var requestIds = new Queue<string>(new[]
            {
                "complete-assisted-001",
                "complete-assisted-unexpected"
            });
            return new AssistedRouteController(client, () => requestIds.Dequeue());
        }

        private static async Task<AssistedRouteController> PreparedControllerAsync(
            AssistedRouteFakeClient client)
        {
            var controller = NewController(client);
            await controller.PrepareAsync(
                AssistedRouteTestData.WorldId,
                AssistedRouteTestData.PrepareRequest(),
                CancellationToken.None);
            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.WorkedExample));
            return controller;
        }
    }

    internal sealed class AssistedRouteFakeClient : IWaylineForgeClient
    {
        public AssistedRoutePrepared PreparedRoute { get; set; }

        public AssistedRouteCompleted CompletedRoute { get; set; }

        public TaskCompletionSource<AssistedRoutePrepared> PrepareCompletion { get; set; }

        public TaskCompletionSource<AssistedRouteCompleted> CompleteCompletion { get; set; }

        public Queue<Exception> PrepareFailures { get; } = new Queue<Exception>();

        public Queue<Exception> CompleteFailures { get; } = new Queue<Exception>();

        public List<AssistedPrepareCall> PrepareCalls { get; } =
            new List<AssistedPrepareCall>();

        public List<AssistedCompleteCall> CompleteCalls { get; } =
            new List<AssistedCompleteCall>();

        public Task CheckHealthAsync(CancellationToken cancellationToken)
        {
            throw new NotSupportedException();
        }

        public Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken)
        {
            throw new NotSupportedException();
        }

        public Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken)
        {
            throw new NotSupportedException();
        }

        public Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken)
        {
            throw new NotSupportedException();
        }

        public Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken)
        {
            throw new NotSupportedException();
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
            PrepareCalls.Add(new AssistedPrepareCall(worldId, request));
            if (PrepareFailures.Count > 0)
                return Task.FromException<AssistedRoutePrepared>(PrepareFailures.Dequeue());
            return PrepareCompletion != null
                ? PrepareCompletion.Task
                : Task.FromResult(PreparedRoute);
        }

        public Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken)
        {
            CompleteCalls.Add(new AssistedCompleteCall(worldId, routeId, request));
            if (CompleteFailures.Count > 0)
                return Task.FromException<AssistedRouteCompleted>(CompleteFailures.Dequeue());
            return CompleteCompletion != null
                ? CompleteCompletion.Task
                : Task.FromResult(CompletedRoute);
        }
    }

    internal sealed class AssistedPrepareCall
    {
        public AssistedPrepareCall(string worldId, AssistedRoutePrepare request)
        {
            WorldId = worldId;
            Request = request;
        }

        public string WorldId { get; }

        public AssistedRoutePrepare Request { get; }
    }

    internal sealed class AssistedCompleteCall
    {
        public AssistedCompleteCall(
            string worldId,
            string routeId,
            AssistedRouteComplete request)
        {
            WorldId = worldId;
            RouteId = routeId;
            Request = request;
        }

        public string WorldId { get; }

        public string RouteId { get; }

        public AssistedRouteComplete Request { get; }
    }

    internal static class AssistedRouteTestData
    {
        public const string WorldId = "valuehold";
        public const string RouteId = "assisted-aaaaaaaaaaaaaaaaaaaaaaaa";
        public const string SessionId = "session-assisted-001";

        public static AssistedRoutePrepare PrepareRequest()
        {
            return new AssistedRoutePrepare(
                "wayline.v1",
                "prepare-assisted-001",
                SessionId);
        }

        public static AssistedRoutePrepared Prepared(
            string requestId = "prepare-assisted-001",
            string worldId = WorldId,
            string batchWorldId = WorldId)
        {
            return new AssistedRoutePrepared(
                "wayline.v1",
                requestId,
                worldId,
                new AssistedRouteBatch(
                    RouteId,
                    batchWorldId,
                    new AssistedWorkedExample(
                        "item-worked-001",
                        "What is the value of the 7 in 4,782?",
                        "700",
                        new[]
                        {
                            "The 7 is in the hundreds place.",
                            "Seven hundreds equals 700."
                        },
                        "Name the digit's place, then write its value."),
                    new[]
                    {
                        SupportedItem(
                            "item-supported-001",
                            "What is the value of the 6 in 6,241?",
                            "opt-supported-001"),
                        SupportedItem(
                            "item-supported-002",
                            "What is the value of the 3 in 1,305?",
                            "opt-supported-002")
                    }));
        }

        public static AssistedRouteCompleted Completed(int finalCorrect)
        {
            return CompletedCore(
                finalCorrect,
                "complete-assisted-001",
                WorldId,
                RouteId,
                "item-supported-001",
                "opt-supported-001-d",
                Confidence.Certain);
        }

        public static AssistedRouteCompleted CompletedWithMismatch(string mismatch)
        {
            return CompletedCore(
                1,
                mismatch == "request" ? "different-request" : "complete-assisted-001",
                mismatch == "world" ? "different-world" : WorldId,
                mismatch == "route" ? "assisted-bbbbbbbbbbbbbbbbbbbbbbbb" : RouteId,
                mismatch == "item" ? "item-supported-other" : "item-supported-001",
                mismatch == "option" ? "opt-supported-001-c" : "opt-supported-001-d",
                mismatch == "confidence" ? Confidence.Guessing : Confidence.Certain);
        }

        public static void AnswerInReverseInteractionOrder(AssistedRouteController controller)
        {
            controller.GoToItem(1);
            controller.SelectOption("item-supported-002", "opt-supported-002-b");
            controller.SelectConfidence("item-supported-002", Confidence.Leaning);
            controller.GoToItem(0);
            controller.SelectOption("item-supported-001", "opt-supported-001-d");
            controller.SelectConfidence("item-supported-001", Confidence.Certain);
        }

        private static AssistedSupportedItem SupportedItem(
            string itemId,
            string prompt,
            string optionPrefix)
        {
            return new AssistedSupportedItem(
                itemId,
                prompt,
                new[]
                {
                    new PublicQuizOption(optionPrefix + "-a", "6"),
                    new PublicQuizOption(optionPrefix + "-b", "60"),
                    new PublicQuizOption(optionPrefix + "-c", "600"),
                    new PublicQuizOption(optionPrefix + "-d", "6000")
                });
        }

        private static AssistedRouteCompleted CompletedCore(
            int finalCorrect,
            string requestId,
            string worldId,
            string routeId,
            string firstItemId,
            string firstSelectedOptionId,
            Confidence firstConfidence)
        {
            if (finalCorrect < 0 || finalCorrect > 2)
                throw new ArgumentOutOfRangeException(nameof(finalCorrect));

            var firstCorrect = finalCorrect >= 1;
            var secondCorrect = finalCorrect == 2;
            var items = new[]
            {
                ResultItem(
                    firstItemId,
                    firstSelectedOptionId,
                    firstConfidence,
                    firstCorrect,
                    "6000"),
                ResultItem(
                    "item-supported-002",
                    "opt-supported-002-b",
                    Confidence.Leaning,
                    secondCorrect,
                    "60")
            };
            return new AssistedRouteCompleted(
                "wayline.v1",
                requestId,
                worldId,
                routeId,
                1,
                2,
                finalCorrect,
                true,
                items);
        }

        private static AssistedItemResult ResultItem(
            string itemId,
            string selectedOptionId,
            Confidence confidence,
            bool isCorrect,
            string selectedAnswer)
        {
            var correctOptionId = isCorrect
                ? selectedOptionId
                : DifferentOption(selectedOptionId);
            var possibleError = isCorrect
                ? null
                : "This answer can come from reading the wrong place value.";
            const string method = "Name the digit's place, then write its value.";
            var steps = new[]
            {
                "Locate the named digit's place.",
                "Write the complete place value."
            };
            var feedback = new List<string>();
            if (possibleError != null)
                feedback.Add(possibleError);
            feedback.Add(method);
            feedback.AddRange(steps);
            return new AssistedItemResult(
                itemId,
                selectedOptionId,
                selectedAnswer,
                confidence,
                correctOptionId,
                isCorrect ? selectedAnswer : "600",
                isCorrect,
                possibleError,
                method,
                steps,
                feedback);
        }

        private static string DifferentOption(string selectedOptionId)
        {
            return selectedOptionId.EndsWith("-c", StringComparison.Ordinal)
                ? selectedOptionId.Substring(0, selectedOptionId.Length - 1) + "d"
                : selectedOptionId.Substring(0, selectedOptionId.Length - 1) + "c";
        }
    }
}
