using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;
using UnityEngine;
using Wayline.Flow.Unity;
using Wayline.Learning.Assisted;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;

namespace Wayline.Tests.Flow
{
    public sealed class DeterministicAcceptanceGateTests
    {
        [Test]
        public void AcceptanceClientSourceIsWhollyDevelopmentGatedAndClearlyLabeled()
        {
            var source = ReadFlowUnitySource(
                "DevelopmentDeterministicAcceptanceQuizClient.cs");
            var directives = source
                .Replace("\r\n", "\n")
                .Split('\n')
                .Where(line => !string.IsNullOrWhiteSpace(line))
                .ToArray();

            Assert.That(
                directives.First(),
                Is.EqualTo("#if UNITY_EDITOR || DEVELOPMENT_BUILD"));
            Assert.That(directives.Last(), Is.EqualTo("#endif"));
            StringAssert.Contains("NOT LIVE SLM", source);
        }

        [Test]
        public void BootstrapHasAnExplicitNonDevelopmentFailClosedBranch()
        {
            var source = ReadFlowUnitySource("VerticalSliceRuntimeBootstrap.cs")
                .Replace("\r\n", "\n");

            StringAssert.Contains(
                "#else\n            ConfigureFailClosedQuizBoundary();\n" +
                "            return;\n#endif",
                source);
            StringAssert.Contains(
                "private void ConfigureFailClosedQuizBoundary()",
                source);
            StringAssert.Contains(
                "_quizClient = new FailClosedWaylineClient();",
                source);
        }

        [Test]
        public void BootstrapUsesOnlyTheIdentityBoundProgressionMapper()
        {
            var source = WithoutWhitespace(
                ReadFlowUnitySource("VerticalSliceRuntimeBootstrap.cs"));

            StringAssert.DoesNotContain("AuthoritativeFlowMapper.", source);
            StringAssert.Contains("usingWayline.Flow.Authority;", source);
            StringAssert.Contains(
                "AuthoritativeProgressionMapper.FromBattle(" +
                "Battle,result.BatchId,command,server)",
                source);
            StringAssert.Contains(
                "AuthoritativeProgressionMapper.FromSeal(" +
                "Battle,attemptNumber,result.BatchId,command,server)",
                source);
            StringAssert.Contains(
                "varcommand=AssistedController.CompletionRequest;",
                source);
            StringAssert.Contains(
                "AuthoritativeProgressionMapper.FromAssisted(" +
                "Battle,AssistedController.Batch.RouteId,command,server)",
                source);

            var assemblyDefinition = File.ReadAllText(Path.Combine(
                Application.dataPath,
                "_Game",
                "Scripts",
                "Flow",
                "Unity",
                "Wayline.Flow.Unity.asmdef"));
            StringAssert.Contains("\"Wayline.Flow.Authority\"", assemblyDefinition);
        }

        [Test]
        public void LegacyMapperSourceIsWhollyExcludedFromProductionCompilation()
        {
            var source = ReadFlowUnitySource("AuthoritativeFlowMapper.cs");
            var directives = source
                .Replace("\r\n", "\n")
                .Split('\n')
                .Where(line => !string.IsNullOrWhiteSpace(line))
                .ToArray();

            Assert.That(
                directives.First(),
                Is.EqualTo("#if UNITY_EDITOR || DEVELOPMENT_BUILD"));
            Assert.That(directives.Last(), Is.EqualTo("#endif"));
        }

        [TestCase(0)]
        [TestCase(1)]
        [TestCase(2)]
        public async Task DevelopmentAssistedCompletionMatchesPublicSelectedAndCorrectDisplays(
            int correctCount)
        {
            var clientType = typeof(DeterministicAcceptanceGate).Assembly.GetType(
                "Wayline.Flow.Unity.DevelopmentDeterministicAcceptanceQuizClient",
                throwOnError: true);
            var client = (IWaylineForgeClient)Activator.CreateInstance(
                clientType,
                nonPublic: true);
            var requestIds = new Queue<string>(new[] { "complete-assisted-001" });
            var controller = new AssistedRouteController(client, requestIds.Dequeue);
            await controller.PrepareAsync(
                "valuehold",
                new AssistedRoutePrepare(
                    "wayline.v1",
                    "prepare-assisted-001",
                    "session-assisted-001"),
                CancellationToken.None);
            controller.AcknowledgeWorkedExample();
            for (var index = 0; index < controller.Batch.Items.Count; index++)
            {
                var item = controller.Batch.Items[index];
                var option = item.Options[index < correctCount ? 0 : 1];
                controller.SelectOption(item.ItemId, option.OptionId);
                controller.SelectConfidence(item.ItemId, Confidence.Certain);
            }

            await controller.SubmitAsync(CancellationToken.None);

            Assert.That(controller.State, Is.EqualTo(AssistedRouteState.Revealed));
            Assert.That(controller.LastFailureCode, Is.Null);
            Assert.That(controller.FinalResult.FinalCorrect, Is.EqualTo(correctCount));
            foreach (var result in controller.FinalResult.Items)
            {
                var publicItem = controller.Batch.Items.Single(
                    item => item.ItemId == result.ItemId);
                Assert.That(
                    result.SelectedAnswer,
                    Is.EqualTo(publicItem.Options.Single(
                        option => option.OptionId == result.SelectedOptionId).DisplayText));
                Assert.That(
                    result.CorrectAnswer,
                    Is.EqualTo(publicItem.Options.Single(
                        option => option.OptionId == result.CorrectOptionId).DisplayText));
            }
        }

        private static string ReadFlowUnitySource(string fileName)
        {
            var path = Path.Combine(
                Application.dataPath,
                "_Game",
                "Scripts",
                "Flow",
                "Unity",
                fileName);
            return File.ReadAllText(path);
        }

        private static string WithoutWhitespace(string source) =>
            new string(source.Where(character => !char.IsWhiteSpace(character)).ToArray());
    }
}
