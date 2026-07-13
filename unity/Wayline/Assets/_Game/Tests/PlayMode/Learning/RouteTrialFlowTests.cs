using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.TestTools;
using UnityEngine.UI;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;
using Wayline.Learning.Quiz;
using Wayline.UI;

namespace Wayline.Tests.Learning
{
    public sealed class RouteTrialFlowTests
    {
        [UnityTest]
        public IEnumerator ZeroWrongShowcaseUsesExactCountThenCompleteFeedback()
        {
            var client = new ShowcaseWaylineClient(true);
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            var speech = new RecordingQuizSpeech();
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, false),
                speech);

            yield return DriveFirstPass(panel, new[] { 0, 0, 0 });

            Assert.That(panel.View, Is.EqualTo(AtlasTrialView.WrongCount));
            Assert.That(panel.WrongCountPanel.ExactCountText.text,
                Is.EqualTo("0 of 3\nanswers are incorrect"));
            Assert.That(client.RevisionSubmissions, Is.Empty);
            panel.PrimaryButton.onClick.Invoke();
            yield return null;

            Assert.That(panel.View, Is.EqualTo(AtlasTrialView.FinalFeedback));
            CollectionAssert.AreEqual(
                new[]
                {
                    "First choice",
                    "Not changed",
                    "Result: Correct",
                    "Reliable method"
                },
                panel.FinalFeedbackPanel.VisibleSectionLabels);
            Assert.That(panel.PrimaryLabel.text, Is.EqualTo("Next method"));

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [UnityTest]
        public IEnumerator NonzeroShowcaseKeepsReviewNeutralAndFinishesExactlyOnce()
        {
            var client = new ShowcaseWaylineClient(false);
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, false),
                new RecordingQuizSpeech());

            yield return DriveFirstPass(panel, new[] { 0, 1, 1 });

            Assert.That(panel.WrongCountPanel.ExactCountText.text,
                Is.EqualTo("2 of 3\nanswers are incorrect"));
            Assert.That(panel.WrongCountPanel.SupportingText.text,
                Is.EqualTo("You have one review pass. We won't mark which ones yet."));
            panel.PrimaryButton.onClick.Invoke();
            yield return null;

            Assert.That(panel.View, Is.EqualTo(AtlasTrialView.Reviewing));
            Assert.That(panel.QuestionPage.ResultMarkersVisible, Is.False);
            Assert.That(panel.QuestionPage.FirstChoiceText.text,
                Does.StartWith("First choice:"));
            yield return DriveReview(panel, new[] { 0, 0, 0 });

            Assert.That(client.RevisionSubmissions, Has.Count.EqualTo(1));
            Assert.That(panel.View, Is.EqualTo(AtlasTrialView.FinalFeedback));
            Assert.That(controller.FinalResult, Is.Not.Null);

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [UnityTest]
        public IEnumerator TextScalingReflowsAtOneHundredOneTwentyFiveAndOneFiftyPercent()
        {
            var client = new ShowcaseWaylineClient(true);
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, false),
                new RecordingQuizSpeech());

            foreach (var scale in new[] { 1f, 1.25f, 1.5f })
            {
                panel.ApplyTextScale(scale);
                Canvas.ForceUpdateCanvases();

                Assert.That(panel.QuestionPage.PromptText.fontSize,
                    Is.GreaterThanOrEqualTo(Mathf.RoundToInt(32f * scale)));
                Assert.That(panel.QuestionPage.ProgressText.fontSize,
                    Is.GreaterThanOrEqualTo(Mathf.RoundToInt(28f * scale)));
                Assert.That(panel.QuestionPage.SingleColumn, Is.EqualTo(scale >= 1.5f));
                AssertNoOverlap(panel.QuestionPage.OptionRects);
                Assert.That(panel.QuestionPage.HasClippedText, Is.False);
            }

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [UnityTest]
        public IEnumerator FocusOrderIsExplicitAndSubmitStartsDisabled()
        {
            var client = new ShowcaseWaylineClient(true);
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, false),
                new RecordingQuizSpeech());

            Assert.That(panel.PrimaryButton.interactable, Is.False);
            Assert.That(panel.QuestionPage.FocusOrder, Has.Count.EqualTo(9));
            for (var index = 0; index < panel.QuestionPage.FocusOrder.Count; index++)
            {
                var selectable = panel.QuestionPage.FocusOrder[index];
                Assert.That(selectable.navigation.mode, Is.EqualTo(Navigation.Mode.Explicit));
                if (index < panel.QuestionPage.FocusOrder.Count - 1)
                {
                    Assert.That(selectable.navigation.selectOnDown,
                        Is.SameAs(panel.QuestionPage.FocusOrder[index + 1]));
                }
            }
            EventSystem.current.SetSelectedGameObject(
                panel.QuestionPage.FocusOrder[0].gameObject);
            Assert.That(EventSystem.current.currentSelectedGameObject,
                Is.SameAs(panel.QuestionPage.OptionButtons[0].gameObject));

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [Test]
        public void StandardAndReducedMotionReachIdenticalSemanticStates()
        {
            var standard = AtlasMotionEvaluator.EvaluateOpening(0.66f, false);
            var reduced = AtlasMotionEvaluator.EvaluateOpening(0.18f, true);
            var standardCount = AtlasMotionEvaluator.EvaluateWrongCount(0f, false);
            var reducedCount = AtlasMotionEvaluator.EvaluateWrongCount(0f, true);

            Assert.That(standard.Interactive, Is.True);
            Assert.That(reduced.Interactive, Is.True);
            Assert.That(standard.LineProgress, Is.EqualTo(1f).Within(0.001f));
            Assert.That(reduced.LineProgress, Is.EqualTo(1f).Within(0.001f));
            Assert.That(standardCount.Scale, Is.EqualTo(0.96f).Within(0.001f));
            Assert.That(reducedCount.Scale, Is.EqualTo(1f).Within(0.001f));
            Assert.That(reducedCount.Opacity, Is.LessThan(standardCount.Opacity));
        }

        [UnityTest]
        public IEnumerator ReadAloudCannotSpeakSealedFeedbackBeforeReveal()
        {
            var client = new ShowcaseWaylineClient(true);
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            var speech = new RecordingQuizSpeech();
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, false),
                speech);

            panel.ReadAloudButton.onClick.Invoke();

            StringAssert.Contains("What is 2.4 + 0.35?", speech.LastText);
            StringAssert.DoesNotContain("Trusted answer", speech.LastText);
            StringAssert.DoesNotContain("Reliable method", speech.LastText);
            StringAssert.DoesNotContain("correct option", speech.LastText.ToLowerInvariant());

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [UnityTest]
        public IEnumerator RuntimeUnavailableOffersRetryAndReturnWithoutSubstituteContent()
        {
            var controller = NewController(new ShowcaseWaylineClient(true));
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                new RecordingQuizSpeech());

            panel.ShowRuntimeUnavailable();
            yield return null;

            Assert.That(panel.View, Is.EqualTo(AtlasTrialView.Unavailable));
            Assert.That(panel.UnavailableText.text,
                Is.EqualTo("The route trial is unavailable. Your combat result is safe."));
            Assert.That(panel.RetryButton.gameObject.activeSelf, Is.True);
            Assert.That(panel.ReturnToMapButton.gameObject.activeSelf, Is.True);
            Assert.That(panel.QuestionPage.gameObject.activeSelf, Is.False);

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [UnityTest]
        public IEnumerator LossQuestionsUseNonVictoryCopyAndOfferNoMapBypass()
        {
            var controller = NewController(new ShowcaseWaylineClient(true));
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings(
                    "VALUEHOLD REACH",
                    1f,
                    true,
                    AtlasTrialPurpose.DefeatRecovery),
                new RecordingQuizSpeech());

            Assert.That(panel.View, Is.EqualTo(AtlasTrialView.Loading));
            Assert.That(panel.UnavailableText.text, Does.Contain("NEXT-TRY QUESTIONS"));
            Assert.That(
                panel.UnavailableText.text,
                Does.Contain("before returning to the map"));

            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            yield return null;

            var header = panel.QuestionPage
                .GetComponentsInChildren<Text>(includeInactive: true)
                .Single(text => text.gameObject.name == "Route label");
            Assert.That(
                header.text,
                Is.EqualTo("VALUEHOLD REACH / NEXT-TRY QUESTIONS"));

            panel.ShowRuntimeUnavailable();
            yield return null;

            Assert.That(panel.View, Is.EqualTo(AtlasTrialView.Unavailable));
            Assert.That(
                panel.UnavailableText.text,
                Does.Contain("is not counted as a win"));
            Assert.That(panel.RetryButton.gameObject.activeSelf, Is.True);
            Assert.That(panel.ReturnToMapButton.gameObject.activeSelf, Is.False);

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [UnityTest]
        public IEnumerator OpeningInstallsRealInputAndGatesChoicesUntilFocusIsStable()
        {
            var controller = NewController(new ShowcaseWaylineClient(true));
            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, false),
                new RecordingQuizSpeech());

            Assert.That(
                EventSystem.current.GetComponent<InputSystemUIInputModule>(),
                Is.Not.Null);
            Assert.That(panel.QuestionPage.OptionButtons[0].interactable, Is.False);

            yield return new WaitForSecondsRealtime(0.7f);

            Assert.That(panel.QuestionPage.OptionButtons[0].interactable, Is.True);
            Assert.That(EventSystem.current.currentSelectedGameObject,
                Is.SameAs(panel.QuestionPage.OptionButtons[0].gameObject));

            var capturePath = Environment.GetEnvironmentVariable(
                "WAYLINE_ROUTE_TRIAL_CAPTURE");
            if (!string.IsNullOrWhiteSpace(capturePath))
            {
                ScreenCapture.CaptureScreenshot(capturePath, 1);
                yield return null;
                yield return null;
                yield return null;
            }

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [UnityTest]
        public IEnumerator SameFrameDoubleActivationCannotSkipNeutralReview()
        {
            var client = new ShowcaseWaylineClient(false);
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, false),
                new RecordingQuizSpeech());
            yield return DriveFirstPass(panel, new[] { 0, 1, 1 });

            panel.PrimaryButton.onClick.Invoke();
            panel.PrimaryButton.onClick.Invoke();
            yield return null;

            Assert.That(panel.View, Is.EqualTo(AtlasTrialView.Reviewing));
            Assert.That(controller.CurrentItemIndex, Is.Zero);
            Assert.That(client.RevisionSubmissions, Is.Empty);

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        [UnityTest]
        public IEnumerator LongVerifiedContentReflowsIntoScrollSurfaceWithoutClipping()
        {
            var client = new ShowcaseWaylineClient(true, ShowcaseData.LongBatch());
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                ShowcaseData.Request(), CancellationToken.None));
            var panel = AtlasTrialPanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1.5f, true),
                new RecordingQuizSpeech());

            panel.ApplyTextScale(1.5f);
            Canvas.ForceUpdateCanvases();

            Assert.That(panel.QuestionPage.SingleColumn, Is.True);
            Assert.That(panel.QuestionPage.HasClippedText, Is.False);
            AssertNoOverlap(panel.QuestionPage.OptionRects);

            UnityEngine.Object.Destroy(panel.gameObject);
        }

        private static QuizController NewController(ShowcaseWaylineClient client)
        {
            var identifiers = new Queue<string>(new[]
            {
                "initial-request-001",
                "revision-request-001"
            });
            return new QuizController(client, identifiers.Dequeue);
        }

        private static IEnumerator DriveFirstPass(
            AtlasTrialPanel panel,
            IReadOnlyList<int> optionIndexes)
        {
            for (var index = 0; index < optionIndexes.Count; index++)
            {
                panel.QuestionPage.OptionButtons[optionIndexes[index]].onClick.Invoke();
                panel.QuestionPage.ConfidenceControl.Buttons[index % 3].onClick.Invoke();
                panel.PrimaryButton.onClick.Invoke();
                yield return null;
            }
        }

        private static IEnumerator DriveReview(
            AtlasTrialPanel panel,
            IReadOnlyList<int> optionIndexes)
        {
            for (var index = 0; index < optionIndexes.Count; index++)
            {
                panel.QuestionPage.OptionButtons[optionIndexes[index]].onClick.Invoke();
                panel.QuestionPage.ConfidenceControl.Buttons[index % 3].onClick.Invoke();
                panel.PrimaryButton.onClick.Invoke();
                yield return null;
            }
        }

        private static IEnumerator Await(Task task)
        {
            while (!task.IsCompleted)
                yield return null;
            if (task.IsFaulted)
                throw task.Exception.InnerException;
        }

        private static void AssertNoOverlap(IReadOnlyList<RectTransform> rectangles)
        {
            for (var left = 0; left < rectangles.Count; left++)
            {
                for (var right = left + 1; right < rectangles.Count; right++)
                {
                    Assert.That(WorldRect(rectangles[left]).Overlaps(WorldRect(rectangles[right])),
                        Is.False,
                        $"Option rectangles {left} and {right} overlap");
                }
            }
        }

        private static Rect WorldRect(RectTransform transform)
        {
            var corners = new Vector3[4];
            transform.GetWorldCorners(corners);
            return Rect.MinMaxRect(corners[0].x, corners[0].y, corners[2].x, corners[2].y);
        }
    }

    internal sealed class RecordingQuizSpeech : IQuizTextToSpeech
    {
        public string LastText { get; private set; }

        public void Speak(string text)
        {
            LastText = text;
        }
    }

    internal sealed class ShowcaseWaylineClient : IWaylineForgeClient
    {
        private readonly bool _zeroWrong;
        private readonly PublicQuizBatch _batch;

        public ShowcaseWaylineClient(bool zeroWrong, PublicQuizBatch batch = null)
        {
            _zeroWrong = zeroWrong;
            _batch = batch;
        }

        public List<InitialSubmission> InitialSubmissions { get; } =
            new List<InitialSubmission>();

        public List<RevisionSubmission> RevisionSubmissions { get; } =
            new List<RevisionSubmission>();

        public Task CheckHealthAsync(CancellationToken cancellationToken) =>
            Task.CompletedTask;

        public Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken) =>
            Task.FromResult(_batch ?? ShowcaseData.Batch());

        public Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken)
        {
            InitialSubmissions.Add(submission);
            return Task.FromResult(_zeroWrong
                ? new InitialSubmissionResult(
                    "wayline.v1", "batch-001", 3, 0, false,
                    ShowcaseData.Final(false, new[] { 0, 0, 0 }, new[] { 0, 0, 0 }))
                : new InitialSubmissionResult(
                    "wayline.v1", "batch-001", 3, 2, true, null));
        }

        public Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken)
        {
            RevisionSubmissions.Add(submission);
            return Task.FromResult(
                ShowcaseData.Final(true, new[] { 0, 1, 1 }, new[] { 0, 0, 0 }));
        }

        public Task<BossGateResult> GetBossGateAsync(
            string worldId,
            string sessionId,
            CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<AssistedRoutePrepared> PrepareAssistedRouteAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken) =>
            throw new NotSupportedException();
    }

    internal static class ShowcaseData
    {
        private static readonly Confidence[] Confidences =
        {
            Confidence.Certain,
            Confidence.Leaning,
            Confidence.Guessing
        };

        public static BattleQuizRequest Request() => new BattleQuizRequest(
            "wayline.v1", "prepare-request-001", "session-001",
            "valuehold_route_1", "valuehold", BattleTier.Route1);

        public static PublicQuizBatch Batch()
        {
            var items = new List<PublicQuizItem>();
            for (var item = 1; item <= 3; item++)
            {
                items.Add(new PublicQuizItem(
                    $"item-{item:000}",
                    item == 1 ? "What is 2.4 + 0.35?" : $"What is {item} + {item}?",
                    Enumerable.Range(0, 4).Select(option => new PublicQuizOption(
                        $"opt-{item:000}-{(char)('a' + option)}",
                        $"Route value {item * 10 + option}"))
                        .ToArray()));
            }
            return new PublicQuizBatch("wayline.v1", "batch-001", 3, items);
        }

        public static PublicQuizBatch LongBatch()
        {
            var prompt = string.Join(
                " ",
                Enumerable.Repeat("Compare the route values and explain the place-value step.", 14));
            var optionText = string.Join(
                " ",
                Enumerable.Repeat("A verified route value with a careful place-value comparison.", 4));
            var items = Enumerable.Range(1, 3).Select(item => new PublicQuizItem(
                $"item-{item:000}",
                prompt,
                Enumerable.Range(0, 4).Select(option => new PublicQuizOption(
                    $"opt-{item:000}-{(char)('a' + option)}",
                    optionText + $" Path {option + 1}."))
                    .ToArray()))
                .ToArray();
            return new PublicQuizBatch("wayline.v1", "batch-001", 3, items);
        }

        public static FinalQuizResult Final(
            bool revised,
            IReadOnlyList<int> firstOptions,
            IReadOnlyList<int> finalOptions)
        {
            var results = new List<FinalQuizItemResult>();
            for (var index = 0; index < 3; index++)
            {
                var firstId = OptionId(index, firstOptions[index]);
                var finalId = OptionId(index, finalOptions[index]);
                var correctId = OptionId(index, 0);
                var firstCorrect = firstOptions[index] == 0;
                var finalCorrect = finalOptions[index] == 0;
                results.Add(new FinalQuizItemResult(
                    $"item-{index + 1:000}",
                    new RevealedSelection(firstId, Confidences[index], firstCorrect),
                    new RevealedSelection(finalId, Confidences[index], finalCorrect),
                    correctId,
                    $"Route value {(index + 1) * 10}",
                    new[] { "Align each place.", "Calculate, then check." },
                    firstCorrect ? null : "This answer can come from changing the place value.",
                    "Align each place, calculate, then check the operation.",
                    !firstCorrect && finalCorrect));
            }
            return new FinalQuizResult(
                "wayline.v1", "batch-001", 3,
                firstOptions.Count(option => option != 0),
                finalOptions.Count(option => option == 0),
                revised,
                results);
        }

        private static string OptionId(int itemIndex, int optionIndex) =>
            $"opt-{itemIndex + 1:000}-{(char)('a' + optionIndex)}";
    }
}
