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
using Wayline.Learning.Assisted;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;
using Wayline.UI;
using Wayline.UI.Assisted;

namespace Wayline.Tests.Assisted
{
    public sealed class AssistedRoutePanelTests
    {
        [UnityTest]
        public IEnumerator ZeroOfTwoRunsOneSubmissionTwoFeedbackPagesAndStillClears()
        {
            var completion = new TaskCompletionSource<AssistedRouteCompleted>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var client = new AssistedPanelClient
            {
                Prepared = AssistedPanelData.Prepared(),
                Completion = completion
            };
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None));
            var speech = new AssistedRecordingSpeech();
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                speech);
            yield return WaitUntilInteractive(panel);

            Assert.That(panel.View, Is.EqualTo(AssistedRouteView.WorkedExample));
            Assert.That(panel.WorkedExamplePage.PromptText.text,
                Is.EqualTo(AssistedPanelData.WorkedPrompt));
            Assert.That(panel.WorkedExamplePage.TrustedAnswerText.text,
                Does.Contain("700"));
            Assert.That(panel.WorkedExamplePage.StepsText.text,
                Does.Contain("Seven hundreds equals 700"));
            Assert.That(panel.WorkedExamplePage.MethodText.text,
                Does.Contain("Name the digit's place"));

            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);

            Assert.That(panel.View, Is.EqualTo(AssistedRouteView.Question));
            Assert.That(panel.QuestionPage.ProgressText.text,
                Is.EqualTo("Supported question 1 of 2"));
            Assert.That(panel.PrimaryButton.interactable, Is.False);
            Assert.That(panel.QuestionPage.SelectedOptionCount, Is.Zero);
            Assert.That(panel.QuestionPage.ConfidenceControl.SelectedCount, Is.Zero);

            panel.QuestionPage.OptionButtons[0].onClick.Invoke();
            Assert.That(panel.PrimaryButton.interactable, Is.False);
            panel.QuestionPage.ConfidenceControl.Buttons[0].onClick.Invoke();
            Assert.That(panel.PrimaryButton.interactable, Is.True);
            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);

            Assert.That(panel.QuestionPage.ProgressText.text,
                Is.EqualTo("Supported question 2 of 2"));
            panel.QuestionPage.OptionButtons[0].onClick.Invoke();
            panel.QuestionPage.ConfidenceControl.Buttons[2].onClick.Invoke();
            Assert.That(panel.PrimaryLabel.text, Is.EqualTo("Submit assisted route"));

            panel.PrimaryButton.onClick.Invoke();
            panel.PrimaryButton.onClick.Invoke();
            yield return null;

            Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));
            Assert.That(panel.View, Is.EqualTo(AssistedRouteView.Submitting));
            AssertNoSealedSupportedCopy(panel);

            completion.SetResult(AssistedPanelData.CompletedZero());
            yield return WaitForView(panel, AssistedRouteView.Feedback);
            yield return WaitUntilInteractive(panel);

            Assert.That(controller.FinalResult.FinalCorrect, Is.Zero);
            Assert.That(controller.FinalResult.WorldCleared, Is.True);
            Assert.That(panel.FeedbackPage.ProgressText.text,
                Is.EqualTo("Supported method 1 of 2"));
            Assert.That(panel.FeedbackPage.ResultText.text,
                Is.EqualTo("Result: Incorrect"));
            Assert.That(panel.FeedbackPage.PossibleErrorText.text,
                Does.Contain(AssistedPanelData.SealedErrorOne));
            Assert.That(panel.FeedbackPage.PossibleErrorText.text,
                Does.StartWith("This answer can come from"));
            Assert.That(panel.FeedbackPage.MethodText.text,
                Does.Contain(AssistedPanelData.SealedMethodOne));
            Assert.That(panel.PrimaryLabel.text, Is.EqualTo("Next supported method"));

            panel.ReadAloudButton.onClick.Invoke();
            Assert.That(
                CountOccurrences(speech.LastText, AssistedPanelData.SealedErrorOne),
                Is.EqualTo(1));

            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);

            Assert.That(panel.FeedbackPage.ProgressText.text,
                Is.EqualTo("Supported method 2 of 2"));
            Assert.That(panel.FeedbackPage.PossibleErrorText.text,
                Does.Contain(AssistedPanelData.SealedErrorTwo));
            Assert.That(panel.PrimaryLabel.text, Is.EqualTo("Complete assisted route"));

            panel.PrimaryButton.onClick.Invoke();
            yield return WaitForView(panel, AssistedRouteView.Complete);

            Assert.That(panel.CompletionText.text,
                Is.EqualTo("ASSISTED ROUTE COMPLETE\nWorld route cleared."));
            Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));
            Assert.That(AllText(panel), Does.Not.Contain("answers are incorrect"));
            Assert.That(AllText(panel), Does.Not.Contain("Review answers"));

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator OneAndTwoOfTwoBothClearAndCorrectFeedbackDropsStaleErrorCopy()
        {
            foreach (var finalCorrect in new[] { 1, 2 })
            {
                var client = new AssistedPanelClient
                {
                    Prepared = AssistedPanelData.Prepared(),
                    Completed = AssistedPanelData.Completed(finalCorrect)
                };
                var controller = NewController(client);
                yield return Await(controller.PrepareAsync(
                    AssistedPanelData.WorldId,
                    AssistedPanelData.PrepareRequest(),
                    CancellationToken.None));
                var panel = AssistedRoutePanel.Create(
                    controller,
                    new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                    new AssistedRecordingSpeech());
                yield return WaitUntilInteractive(panel);
                yield return AnswerBothAndSubmit(panel);
                yield return WaitForView(panel, AssistedRouteView.Feedback);
                yield return WaitUntilInteractive(panel);

                Assert.That(controller.FinalResult.FinalCorrect, Is.EqualTo(finalCorrect));
                Assert.That(controller.FinalResult.WorldCleared, Is.True);
                if (finalCorrect == 1)
                {
                    Assert.That(panel.FeedbackPage.ResultText.text,
                        Is.EqualTo("Result: Incorrect"));
                    Assert.That(panel.FeedbackPage.PossibleErrorText.text,
                        Is.EqualTo(AssistedPanelData.SealedErrorOne));
                    Assert.That(panel.FeedbackPage.PossibleErrorText.gameObject.activeInHierarchy,
                        Is.True);
                }
                else
                {
                    AssertCorrectFeedbackHasNoPossibleError(panel);
                }

                panel.PrimaryButton.onClick.Invoke();
                yield return WaitUntilInteractive(panel);

                Assert.That(panel.FeedbackPage.ResultText.text,
                    Is.EqualTo("Result: Correct"));
                AssertCorrectFeedbackHasNoPossibleError(panel);
                panel.PrimaryButton.onClick.Invoke();
                yield return WaitForView(panel, AssistedRouteView.Complete);

                Assert.That(panel.CompletionText.text,
                    Is.EqualTo("ASSISTED ROUTE COMPLETE\nWorld route cleared."));
                Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));

                UnityEngine.Object.Destroy(panel.gameObject);
                yield return null;
            }
        }

        [UnityTest]
        public IEnumerator LiveLoadingThenSubmissionRestoresSecuringCopy()
        {
            var preparation = new TaskCompletionSource<AssistedRoutePrepared>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var completion = new TaskCompletionSource<AssistedRouteCompleted>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var client = new AssistedPanelClient
            {
                PrepareCompletion = preparation,
                Completion = completion
            };
            var controller = NewController(client);
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                new AssistedRecordingSpeech());

            var prepareTask = controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None);
            yield return null;
            Assert.That(panel.View, Is.EqualTo(AssistedRouteView.Loading));
            Assert.That(FindText(panel, "Submitting message").text,
                Is.EqualTo("Preparing the assisted route…"));

            preparation.SetResult(AssistedPanelData.Prepared());
            yield return Await(prepareTask);
            yield return WaitUntilInteractive(panel);
            yield return AnswerBothAndSubmit(panel);
            yield return WaitForView(panel, AssistedRouteView.Submitting);

            Assert.That(FindText(panel, "Submitting message").text,
                Is.EqualTo("Securing the supported route…"));

            completion.SetResult(AssistedPanelData.CompletedZero());
            yield return WaitForView(panel, AssistedRouteView.Feedback);
            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator RetryableCompletionUsesExactSubmissionAndSuppressesDuplicateRetry()
        {
            var completion = new TaskCompletionSource<AssistedRouteCompleted>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var client = new AssistedPanelClient
            {
                Prepared = AssistedPanelData.Prepared(),
                Completion = completion
            };
            client.CompleteFailures.Enqueue(
                new WaylineClientException("runtime_unavailable", 0));
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None));
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                new AssistedRecordingSpeech());
            yield return WaitUntilInteractive(panel);
            yield return AnswerBothAndSubmit(panel);
            yield return WaitForView(panel, AssistedRouteView.Unavailable);
            yield return WaitUntilInteractive(panel);

            Assert.That(controller.CanRetryCompletion, Is.True);
            Assert.That(panel.RetryButton.gameObject.activeSelf, Is.True);
            Assert.That(panel.ReturnToMapButton.gameObject.activeSelf, Is.True);
            Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));
            var originalSubmission = client.CompleteCalls[0];

            panel.RetryButton.onClick.Invoke();
            panel.RetryButton.onClick.Invoke();
            yield return null;

            Assert.That(panel.View, Is.EqualTo(AssistedRouteView.Submitting));
            Assert.That(client.CompleteCalls, Has.Count.EqualTo(2));
            Assert.That(client.CompleteCalls[1], Is.SameAs(originalSubmission));

            completion.SetResult(AssistedPanelData.CompletedZero());
            yield return WaitForView(panel, AssistedRouteView.Feedback);
            Assert.That(controller.CanRetryCompletion, Is.False);

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator NonRetryableFailureOffersOnlySafeReturnToMap()
        {
            var client = new AssistedPanelClient
            {
                Prepared = AssistedPanelData.Prepared(),
                Completed = AssistedPanelData.CompletedZero()
            };
            client.CompleteFailures.Enqueue(
                new WaylineClientException("safe_content_unavailable", 503));
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None));
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                new AssistedRecordingSpeech());
            var returnRequests = 0;
            panel.ReturnToMapRequested += () => returnRequests++;
            yield return WaitUntilInteractive(panel);
            yield return AnswerBothAndSubmit(panel);
            yield return WaitForView(panel, AssistedRouteView.Unavailable);
            yield return WaitUntilInteractive(panel);

            Assert.That(controller.CanRetryCompletion, Is.False);
            Assert.That(controller.CanRecoverPreparation, Is.False);
            Assert.That(panel.RetryButton.gameObject.activeSelf, Is.False);
            Assert.That(panel.ReturnToMapButton.gameObject.activeSelf, Is.True);
            Assert.That(panel.FocusOrder, Is.EqualTo(new[] { panel.ReturnToMapButton }));
            Assert.That(EventSystem.current.currentSelectedGameObject,
                Is.SameAs(panel.ReturnToMapButton.gameObject));

            panel.ReturnToMapButton.onClick.Invoke();
            Assert.That(returnRequests, Is.EqualTo(1));
            Assert.That(client.CompleteCalls, Has.Count.EqualTo(1));

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator RecoverablePreparationReplaysExactRequestAndSuppressesDuplicateRetry()
        {
            var preparation = new TaskCompletionSource<AssistedRoutePrepared>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            var client = new AssistedPanelClient
            {
                PrepareCompletion = preparation
            };
            client.PrepareFailures.Enqueue(
                new WaylineClientException("safe_content_unavailable", 503));
            var controller = NewController(client);
            var originalRequest = AssistedPanelData.PrepareRequest();
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                originalRequest,
                CancellationToken.None));
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                new AssistedRecordingSpeech());
            yield return WaitUntilInteractive(panel);

            Assert.That(controller.CanRecoverPreparation, Is.True);
            Assert.That(panel.RetryButton.gameObject.activeSelf, Is.True);
            Assert.That(panel.FocusOrder[0], Is.SameAs(panel.RetryButton));

            panel.RetryButton.onClick.Invoke();
            panel.RetryButton.onClick.Invoke();
            yield return null;

            Assert.That(panel.View, Is.EqualTo(AssistedRouteView.Loading));
            Assert.That(client.PrepareCalls, Has.Count.EqualTo(2));
            Assert.That(client.PrepareCalls[0], Is.SameAs(originalRequest));
            Assert.That(client.PrepareCalls[1], Is.SameAs(originalRequest));

            preparation.SetResult(AssistedPanelData.Prepared());
            yield return WaitForView(panel, AssistedRouteView.WorkedExample);

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator SupportedQuestionSpeechAndHierarchyStayKeylessUntilReveal()
        {
            var client = new AssistedPanelClient
            {
                Prepared = AssistedPanelData.Prepared(),
                Completion = new TaskCompletionSource<AssistedRouteCompleted>(
                    TaskCreationOptions.RunContinuationsAsynchronously)
            };
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None));
            var speech = new AssistedRecordingSpeech();
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                speech);
            yield return WaitUntilInteractive(panel);
            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);

            panel.ReadAloudButton.onClick.Invoke();

            StringAssert.Contains(AssistedPanelData.FirstPrompt, speech.LastText);
            StringAssert.Contains("Option A", speech.LastText);
            StringAssert.Contains("Confidence: not selected", speech.LastText);
            AssertNoSealedSupportedCopy(panel);
            foreach (var sealedText in AssistedPanelData.SealedCopy)
                StringAssert.DoesNotContain(sealedText, speech.LastText);

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator OptionalHeadfulQuestionCaptureIsInteractiveAndKeyless()
        {
            var client = new AssistedPanelClient
            {
                Prepared = AssistedPanelData.Prepared()
            };
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None));
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, false),
                new AssistedRecordingSpeech());
            yield return WaitUntilInteractive(panel);
            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);

            Assert.That(panel.View, Is.EqualTo(AssistedRouteView.Question));
            Assert.That(panel.Interactive, Is.True);
            AssertNoSealedSupportedCopy(panel);
            AssertTopBarFits(panel, panel.QuestionPage.ProgressText);

            var capturePath = Environment.GetEnvironmentVariable(
                "WAYLINE_ASSISTED_ROUTE_CAPTURE");
            if (!string.IsNullOrWhiteSpace(capturePath))
            {
                Screen.SetResolution(1920, 1080, false);
                yield return null;
                yield return null;
                ScreenCapture.CaptureScreenshot(capturePath, 1);
                yield return null;
                yield return null;
                yield return null;
            }

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator EveryPageReflowsAtOneHundredOneTwentyFiveAndOneFiftyPercent()
        {
            var client = new AssistedPanelClient
            {
                Prepared = AssistedPanelData.Prepared(),
                Completed = AssistedPanelData.CompletedZero()
            };
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None));
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                new AssistedRecordingSpeech());
            yield return WaitUntilInteractive(panel);

            foreach (var scale in new[] { 1f, 1.25f, 1.5f })
            {
                panel.ApplyTextScale(scale);
                Canvas.ForceUpdateCanvases();
                AssertNoClippedText(panel.WorkedExamplePage.gameObject);
                Assert.That(panel.WorkedExamplePage.PromptText.fontSize,
                    Is.GreaterThanOrEqualTo(Mathf.RoundToInt(32f * scale)));
            }

            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);
            foreach (var scale in new[] { 1f, 1.25f, 1.5f })
            {
                panel.ApplyTextScale(scale);
                Canvas.ForceUpdateCanvases();
                AssertNoClippedText(panel.QuestionPage.gameObject);
                Assert.That(panel.QuestionPage.SingleColumn, Is.EqualTo(scale >= 1.5f));
                AssertNoOverlap(panel.QuestionPage.OptionRects);
                AssertTopBarFits(panel, panel.QuestionPage.ProgressText);
            }

            AnswerCurrent(panel, 0, 0);
            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);
            AnswerCurrent(panel, 0, 2);
            panel.PrimaryButton.onClick.Invoke();
            yield return WaitForView(panel, AssistedRouteView.Feedback);
            yield return WaitUntilInteractive(panel);

            foreach (var scale in new[] { 1f, 1.25f, 1.5f })
            {
                panel.ApplyTextScale(scale);
                Canvas.ForceUpdateCanvases();
                AssertNoClippedText(panel.FeedbackPage.gameObject);
                Assert.That(panel.FeedbackPage.MethodText.fontSize,
                    Is.GreaterThanOrEqualTo(Mathf.RoundToInt(28f * scale)));
                AssertTopBarFits(panel, panel.FeedbackPage.ProgressText);
            }

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator FocusOrderIsExplicitAndMovesToEachStableView()
        {
            var client = new AssistedPanelClient
            {
                Prepared = AssistedPanelData.Prepared(),
                Completed = AssistedPanelData.CompletedZero()
            };
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None));
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                new AssistedRecordingSpeech());
            yield return WaitUntilInteractive(panel);

            Assert.That(EventSystem.current.GetComponent<InputSystemUIInputModule>(), Is.Not.Null);
            AssertExplicitChain(panel.FocusOrder);
            Assert.That(EventSystem.current.currentSelectedGameObject,
                Is.SameAs(panel.ReadAloudButton.gameObject));

            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);

            Assert.That(panel.FocusOrder, Has.Count.EqualTo(9));
            AssertExplicitChain(panel.FocusOrder);
            Assert.That(EventSystem.current.currentSelectedGameObject,
                Is.SameAs(panel.QuestionPage.OptionButtons[0].gameObject));

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;
        }

        [UnityTest]
        public IEnumerator DestroyUnsubscribesControllerAndRemovesOnlyItsExternalInputModule()
        {
            if (EventSystem.current != null)
            {
                UnityEngine.Object.Destroy(EventSystem.current.gameObject);
                yield return null;
            }
            var externalEventObject = new GameObject(
                "External assisted test event system",
                typeof(EventSystem));
            var externalEventSystem = externalEventObject.GetComponent<EventSystem>();
            Assert.That(externalEventSystem.GetComponent<InputSystemUIInputModule>(), Is.Null);

            var client = new AssistedPanelClient
            {
                Prepared = AssistedPanelData.Prepared()
            };
            var controller = NewController(client);
            yield return Await(controller.PrepareAsync(
                AssistedPanelData.WorldId,
                AssistedPanelData.PrepareRequest(),
                CancellationToken.None));
            var panel = AssistedRoutePanel.Create(
                controller,
                new AtlasTrialSettings("VALUEHOLD REACH", 1f, true),
                new AssistedRecordingSpeech());
            var addedModule = externalEventSystem.GetComponent<InputSystemUIInputModule>();
            Assert.That(addedModule, Is.Not.Null);

            UnityEngine.Object.Destroy(panel.gameObject);
            yield return null;

            Assert.That(externalEventObject == null, Is.False);
            Assert.That(addedModule == null, Is.True);
            Assert.That(
                externalEventSystem.GetComponent<InputSystemUIInputModule>() == null,
                Is.True);

            controller.AcknowledgeWorkedExample();
            yield return null;
            LogAssert.NoUnexpectedReceived();

            UnityEngine.Object.Destroy(externalEventObject);
            yield return null;
        }

        [Test]
        public void StandardAndReducedMotionReachTheSameSemanticStates()
        {
            var standardOpen = AssistedRouteMotionEvaluator.EvaluateOpening(0.66f, false);
            var reducedOpen = AssistedRouteMotionEvaluator.EvaluateOpening(0.18f, true);
            var standardAdvance = AssistedRouteMotionEvaluator.EvaluateAdvance(0.24f, false);
            var reducedAdvance = AssistedRouteMotionEvaluator.EvaluateAdvance(0.18f, true);

            Assert.That(standardOpen.Interactive, Is.True);
            Assert.That(reducedOpen.Interactive, Is.True);
            Assert.That(standardOpen.RouteProgress, Is.EqualTo(1f).Within(0.001f));
            Assert.That(reducedOpen.RouteProgress, Is.EqualTo(1f).Within(0.001f));
            Assert.That(standardAdvance.Interactive, Is.True);
            Assert.That(reducedAdvance.Interactive, Is.True);
            Assert.That(standardAdvance.SurfaceOpacity, Is.EqualTo(1f).Within(0.001f));
            Assert.That(reducedAdvance.SurfaceOpacity, Is.EqualTo(1f).Within(0.001f));
        }

        private static AssistedRouteController NewController(AssistedPanelClient client)
        {
            return new AssistedRouteController(client, () => "complete-assisted-001");
        }

        private static void AnswerCurrent(
            AssistedRoutePanel panel,
            int optionIndex,
            int confidenceIndex)
        {
            panel.QuestionPage.OptionButtons[optionIndex].onClick.Invoke();
            panel.QuestionPage.ConfidenceControl.Buttons[confidenceIndex].onClick.Invoke();
        }

        private static IEnumerator AnswerBothAndSubmit(AssistedRoutePanel panel)
        {
            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);
            AnswerCurrent(panel, 0, 0);
            panel.PrimaryButton.onClick.Invoke();
            yield return WaitUntilInteractive(panel);
            AnswerCurrent(panel, 0, 2);
            panel.PrimaryButton.onClick.Invoke();
            yield return null;
        }

        private static void AssertCorrectFeedbackHasNoPossibleError(
            AssistedRoutePanel panel)
        {
            Assert.That(panel.FeedbackPage.PossibleErrorText.text, Is.Empty);
            Assert.That(
                panel.FeedbackPage.PossibleErrorText.gameObject.activeInHierarchy,
                Is.False);
            Assert.That(AllText(panel), Does.Not.Contain("SEALED ERROR"));
        }

        private static Text FindText(AssistedRoutePanel panel, string name)
        {
            return panel.GetComponentsInChildren<Text>(true)
                .Single(text => text.name == name);
        }

        private static int CountOccurrences(string text, string value)
        {
            var count = 0;
            var offset = 0;
            while (text != null &&
                   value != null &&
                   (offset = text.IndexOf(value, offset, StringComparison.Ordinal)) >= 0)
            {
                count++;
                offset += value.Length;
            }
            return count;
        }

        private static IEnumerator Await(Task task)
        {
            while (!task.IsCompleted)
                yield return null;
            if (task.IsFaulted)
                throw task.Exception.InnerException;
        }

        private static IEnumerator WaitUntilInteractive(AssistedRoutePanel panel)
        {
            var deadline = Time.realtimeSinceStartup + 2f;
            while (!panel.Interactive && Time.realtimeSinceStartup < deadline)
                yield return null;
            Assert.That(panel.Interactive, Is.True);
        }

        private static IEnumerator WaitForView(
            AssistedRoutePanel panel,
            AssistedRouteView expected)
        {
            var deadline = Time.realtimeSinceStartup + 2f;
            while (panel.View != expected && Time.realtimeSinceStartup < deadline)
                yield return null;
            Assert.That(panel.View, Is.EqualTo(expected));
        }

        private static void AssertNoSealedSupportedCopy(AssistedRoutePanel panel)
        {
            var visible = AllText(panel);
            foreach (var sealedText in AssistedPanelData.SealedCopy)
                StringAssert.DoesNotContain(sealedText, visible);
        }

        private static string AllText(AssistedRoutePanel panel)
        {
            return string.Join(
                "\n",
                panel.GetComponentsInChildren<Text>(true).Select(text => text.text));
        }

        private static void AssertExplicitChain(IReadOnlyList<Selectable> focusOrder)
        {
            Assert.That(focusOrder, Is.Not.Empty);
            for (var index = 0; index < focusOrder.Count; index++)
            {
                Assert.That(focusOrder[index].navigation.mode,
                    Is.EqualTo(Navigation.Mode.Explicit));
                Assert.That(focusOrder[index].navigation.selectOnDown,
                    Is.SameAs(focusOrder[(index + 1) % focusOrder.Count]));
            }
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

        private static void AssertTopBarFits(
            AssistedRoutePanel panel,
            Text progressText)
        {
            var progressRect = (RectTransform)progressText.transform;
            var readButtonRect = (RectTransform)panel.ReadAloudButton.transform;
            Assert.That(
                WorldRect(progressRect).Overlaps(WorldRect(readButtonRect)),
                Is.False,
                "Progress text overlaps Read aloud");

            var readLabel = panel.ReadAloudButton
                .GetComponentsInChildren<Text>(true)
                .Single(text => text.name == "Label");
            var readLabelRect = (RectTransform)readLabel.transform;
            Assert.That(
                readLabel.preferredWidth,
                Is.LessThanOrEqualTo(readLabelRect.rect.width + 0.5f),
                "Read aloud label is horizontally clipped");
            Assert.That(
                readLabel.preferredHeight,
                Is.LessThanOrEqualTo(readLabelRect.rect.height + 0.5f),
                "Read aloud label is vertically clipped");
        }

        private static void AssertNoClippedText(GameObject root)
        {
            foreach (var text in root.GetComponentsInChildren<Text>(true))
            {
                if (!text.gameObject.activeInHierarchy)
                    continue;
                var rect = (RectTransform)text.transform;
                Assert.That(
                    text.preferredHeight,
                    Is.LessThanOrEqualTo(rect.rect.height + 0.5f),
                    $"{text.name} is vertically clipped");
            }
        }

        private static Rect WorldRect(RectTransform transform)
        {
            var corners = new Vector3[4];
            transform.GetWorldCorners(corners);
            return Rect.MinMaxRect(corners[0].x, corners[0].y, corners[2].x, corners[2].y);
        }
    }

    internal sealed class AssistedRecordingSpeech : IQuizTextToSpeech
    {
        public string LastText { get; private set; }

        public void Speak(string text)
        {
            LastText = text;
        }
    }

    internal sealed class AssistedPanelClient : IWaylineForgeClient
    {
        public AssistedRoutePrepared Prepared { get; set; }

        public AssistedRouteCompleted Completed { get; set; }

        public TaskCompletionSource<AssistedRouteCompleted> Completion { get; set; }

        public TaskCompletionSource<AssistedRoutePrepared> PrepareCompletion { get; set; }

        public Queue<Exception> PrepareFailures { get; } = new Queue<Exception>();

        public Queue<Exception> CompleteFailures { get; } = new Queue<Exception>();

        public List<AssistedRoutePrepare> PrepareCalls { get; } =
            new List<AssistedRoutePrepare>();

        public List<AssistedRouteComplete> CompleteCalls { get; } =
            new List<AssistedRouteComplete>();

        public Task<AssistedRoutePrepared> PrepareAssistedRouteAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken)
        {
            PrepareCalls.Add(request);
            if (PrepareFailures.Count > 0)
            {
                return Task.FromException<AssistedRoutePrepared>(
                    PrepareFailures.Dequeue());
            }
            return PrepareCompletion != null
                ? PrepareCompletion.Task
                : Task.FromResult(Prepared);
        }

        public Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken)
        {
            CompleteCalls.Add(request);
            if (CompleteFailures.Count > 0)
            {
                return Task.FromException<AssistedRouteCompleted>(
                    CompleteFailures.Dequeue());
            }
            return Completion != null ? Completion.Task : Task.FromResult(Completed);
        }

        public Task CheckHealthAsync(CancellationToken cancellationToken) =>
            throw new NotSupportedException();

        public Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<BossGateResult> GetBossGateAsync(
            string worldId,
            string sessionId,
            CancellationToken cancellationToken) => throw new NotSupportedException();
    }

    internal static class AssistedPanelData
    {
        public const string WorldId = "valuehold";
        public const string RouteId = "assisted-aaaaaaaaaaaaaaaaaaaaaaaa";
        public const string WorkedPrompt = "What is the value of the 7 in 4,782?";
        public const string FirstPrompt =
            "A long-range scanner marks the 6 in 6,241. What is the complete place value of that digit?";
        public const string SealedErrorOne =
            "This answer can come from reading the ones place. SEALED ERROR ONE.";
        public const string SealedMethodOne =
            "SEALED METHOD ONE: Name the place first, then write the full value.";
        public const string SealedErrorTwo =
            "This answer can come from dropping placeholder zeros. SEALED ERROR TWO.";
        public const string SealedMethodTwo =
            "SEALED METHOD TWO: Preserve every zero required by the digit's place.";

        public static readonly string[] SealedCopy =
        {
            SealedErrorOne,
            SealedMethodOne,
            "SEALED STEP ONE",
            SealedErrorTwo,
            SealedMethodTwo,
            "SEALED STEP TWO"
        };

        public static AssistedRoutePrepare PrepareRequest() => new AssistedRoutePrepare(
            "wayline.v1",
            "prepare-assisted-001",
            "session-assisted-001");

        public static AssistedRoutePrepared Prepared()
        {
            return new AssistedRoutePrepared(
                "wayline.v1",
                "prepare-assisted-001",
                WorldId,
                new AssistedRouteBatch(
                    RouteId,
                    WorldId,
                    new AssistedWorkedExample(
                        "item-worked-001",
                        WorkedPrompt,
                        "700",
                        new[]
                        {
                            "The 7 is in the hundreds place.",
                            "Seven hundreds equals 700. Keep the placeholder zeros."
                        },
                        "Name the digit's place, then write its complete value."),
                    new[]
                    {
                        Item(
                            "item-supported-001",
                            FirstPrompt,
                            "opt-supported-001",
                            new[]
                            {
                                "6 route units",
                                "60 route units",
                                "600 route units across the calibrated marker",
                                "6000 route units"
                            }),
                        Item(
                            "item-supported-002",
                            "The 3 in 3,508 powers a place-value lock. What full value does the marked digit represent?",
                            "opt-supported-002",
                            new[]
                            {
                                "3 lock units",
                                "30 lock units",
                                "300 lock units",
                                "3000 lock units across the full place-value channel"
                            })
                    }));
        }

        public static AssistedRouteCompleted CompletedZero()
        {
            return Completed(0);
        }

        public static AssistedRouteCompleted Completed(int finalCorrect)
        {
            if (finalCorrect < 0 || finalCorrect > 2)
                throw new ArgumentOutOfRangeException(nameof(finalCorrect));
            var firstCorrect = finalCorrect == 2;
            var secondCorrect = finalCorrect >= 1;
            return new AssistedRouteCompleted(
                "wayline.v1",
                "complete-assisted-001",
                WorldId,
                RouteId,
                1,
                2,
                finalCorrect,
                true,
                new[]
                {
                    Result(
                        "item-supported-001",
                        "opt-supported-001-a",
                        "6 route units",
                        Confidence.Certain,
                        "opt-supported-001-c",
                        "600 route units across the calibrated marker",
                        firstCorrect,
                        SealedErrorOne,
                        SealedMethodOne,
                        "SEALED STEP ONE: Locate the hundreds place before writing its value."),
                    Result(
                        "item-supported-002",
                        "opt-supported-002-a",
                        "3 lock units",
                        Confidence.Guessing,
                        "opt-supported-002-d",
                        "3000 lock units across the full place-value channel",
                        secondCorrect,
                        SealedErrorTwo,
                        SealedMethodTwo,
                        "SEALED STEP TWO: Keep three placeholder zeros after the digit.")
                });
        }

        private static AssistedSupportedItem Item(
            string itemId,
            string prompt,
            string optionPrefix,
            IReadOnlyList<string> displays)
        {
            return new AssistedSupportedItem(
                itemId,
                prompt,
                displays.Select((display, index) => new PublicQuizOption(
                    optionPrefix + "-" + (char)('a' + index),
                    display)).ToArray());
        }

        private static AssistedItemResult Result(
            string itemId,
            string selectedOptionId,
            string selectedAnswer,
            Confidence confidence,
            string correctOptionId,
            string correctAnswer,
            bool isCorrect,
            string possibleError,
            string method,
            string step)
        {
            if (isCorrect)
            {
                correctOptionId = selectedOptionId;
                correctAnswer = selectedAnswer;
                possibleError = null;
            }
            var canonical = new List<string>();
            if (possibleError != null)
                canonical.Add(possibleError);
            canonical.Add(method);
            canonical.Add(step);
            return new AssistedItemResult(
                itemId,
                selectedOptionId,
                selectedAnswer,
                confidence,
                correctOptionId,
                correctAnswer,
                isCorrect,
                possibleError,
                method,
                new[] { step },
                canonical);
        }
    }
}
