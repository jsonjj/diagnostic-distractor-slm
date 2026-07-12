using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;

namespace Wayline.Tests.Learning
{
    public sealed class WaylineForgeClientTests
    {
        [Test]
        public async Task PrepareUsesTheAuthenticatedPublicBatchRoute()
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(201, JsonConvert.SerializeObject(RouteTrialTestData.Batch()));
            var client = new WaylineForgeClient(transport);

            var result = await client.PrepareBatchAsync(
                RouteTrialTestData.Request(),
                CancellationToken.None);

            Assert.That(result.BatchId, Is.EqualTo("batch-001"));
            Assert.That(transport.Requests, Has.Count.EqualTo(1));
            Assert.That(transport.Requests[0].Method, Is.EqualTo(WaylineHttpMethod.Post));
            Assert.That(transport.Requests[0].RelativePath, Is.EqualTo("/v1/quiz-batches"));
            Assert.That(transport.Requests[0].SessionId, Is.EqualTo("session-001"));
            StringAssert.Contains("\"battleTier\":\"route_1\"", transport.Requests[0].Body);
        }

        [Test]
        public async Task SnapshotSubmissionRevisionAndGateUseOnlyFrozenPaths()
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                200,
                JsonConvert.SerializeObject(RouteTrialTestData.RevisionOpenSnapshot()));
            transport.Enqueue(
                200,
                JsonConvert.SerializeObject(RouteTrialTestData.NonzeroInitialResult()));
            transport.Enqueue(
                200,
                JsonConvert.SerializeObject(RouteTrialTestData.RevisedFinalResult()));
            transport.Enqueue(
                200,
                "{\"schemaVersion\":\"wayline.v1\",\"worldId\":\"valuehold\"," +
                "\"unlocked\":true,\"leadInWins\":4,\"requiredLeadInWins\":4," +
                "\"validWorldItems\":16,\"requiredValidWorldItems\":16," +
                "\"latestTenItemCount\":10,\"latestTenCorrectCount\":7," +
                "\"requiredLatestTenCorrectCount\":7,\"coreSubskillCount\":2," +
                "\"readyCoreSubskillCount\":2,\"unmetRequirements\":[]}");
            var client = new WaylineForgeClient(transport);
            var initial = RouteTrialTestData.RevisionOpenSnapshot().InitialSubmission;
            var revision = new RevisionSubmission(
                "wayline.v1",
                "revision-request-001",
                "batch-001",
                3,
                initial.Selections);

            await client.GetQuizSnapshotAsync(
                "batch-001", "session-001", CancellationToken.None);
            await client.SubmitInitialAsync(
                "session-001", initial, CancellationToken.None);
            await client.SubmitRevisionAsync(
                "session-001", revision, CancellationToken.None);
            await client.GetBossGateAsync(
                "valuehold", "session-001", CancellationToken.None);

            CollectionAssert.AreEqual(
                new[]
                {
                    "/v1/quiz-batches/batch-001",
                    "/v1/quiz-batches/batch-001/initial",
                    "/v1/quiz-batches/batch-001/revision",
                    "/v1/worlds/valuehold/gate"
                },
                transport.Requests.ConvertAll(request => request.RelativePath));
            Assert.That(
                transport.Requests.TrueForAll(request => request.SessionId == "session-001"),
                Is.True);
        }

        [Test]
        public void SnapshotResponseMustMatchPathOwnedBatch()
        {
            var transport = new RecordingWaylineTransport();
            var differentBatch = JsonConvert
                .SerializeObject(RouteTrialTestData.RevisionOpenSnapshot())
                .Replace("batch-001", "batch-002");
            transport.Enqueue(200, differentBatch);
            var client = new WaylineForgeClient(transport);

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.GetQuizSnapshotAsync(
                    "batch-001",
                    "session-001",
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("integrity_failure"));
        }

        [Test]
        public void BossGateResponseMustMatchPathOwnedWorld()
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                200,
                "{\"schemaVersion\":\"wayline.v1\",\"worldId\":\"decimara\"," +
                "\"unlocked\":true,\"leadInWins\":4,\"requiredLeadInWins\":4," +
                "\"validWorldItems\":16,\"requiredValidWorldItems\":16," +
                "\"latestTenItemCount\":10,\"latestTenCorrectCount\":7," +
                "\"requiredLatestTenCorrectCount\":7,\"coreSubskillCount\":2," +
                "\"readyCoreSubskillCount\":2,\"unmetRequirements\":[]}");
            var client = new WaylineForgeClient(transport);

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.GetBossGateAsync(
                    "valuehold",
                    "session-001",
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("integrity_failure"));
        }

        [Test]
        public void PublicFailureBecomesStableExceptionWithoutEchoingPayload()
        {
            const string payload =
                "{\"schemaVersion\":\"wayline.error.v1\",\"code\":\"storage_busy\"}";
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(503, payload);
            var client = new WaylineForgeClient(transport);

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.PrepareBatchAsync(
                    RouteTrialTestData.Request(),
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("storage_busy"));
            Assert.That(error.StatusCode, Is.EqualTo(503));
            StringAssert.DoesNotContain(payload, error.ToString());
        }

        [Test]
        public void MalformedSuccessfulResponseFailsClosedAsIntegrityFailure()
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                201,
                "{\"schemaVersion\":\"wayline.v1\",\"batchId\":\"batch-001\"," +
                "\"itemCount\":\"3\",\"items\":[]}");
            var client = new WaylineForgeClient(transport);

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.PrepareBatchAsync(
                    RouteTrialTestData.Request(),
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("integrity_failure"));
        }

        [Test]
        public async Task HealthUsesNoSessionScopedHeader()
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                200,
                "{\"schemaVersion\":\"wayline.health.v1\",\"status\":\"ready\"}");
            var client = new WaylineForgeClient(transport);

            await client.CheckHealthAsync(CancellationToken.None);

            Assert.That(transport.Requests[0].RelativePath, Is.EqualTo("/v1/health"));
            Assert.That(transport.Requests[0].SessionId, Is.Null);
        }

        [Test]
        public async Task AssistedPreparationAndCompletionUseOnlyPathOwnedRoutes()
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                201,
                File.ReadAllText(TestPaths.Contract(
                    "valid/assisted-route-prepared.json")));
            transport.Enqueue(
                200,
                File.ReadAllText(TestPaths.Contract(
                    "valid/assisted-route-completed.json")));
            var client = new WaylineForgeClient(transport);
            var prepare = StrictQuizValidator.Deserialize<AssistedRoutePrepare>(
                File.ReadAllText(TestPaths.Contract(
                    "valid/assisted-route-prepare.json")));
            var complete = StrictQuizValidator.Deserialize<AssistedRouteComplete>(
                File.ReadAllText(TestPaths.Contract(
                    "valid/assisted-route-complete.json")));

            var prepared = await client.PrepareAssistedRouteAsync(
                "valuehold",
                prepare,
                CancellationToken.None);
            var completed = await client.CompleteAssistedRouteAsync(
                "valuehold",
                prepared.Batch.RouteId,
                complete,
                CancellationToken.None);

            Assert.That(completed.WorldCleared, Is.True);
            CollectionAssert.AreEqual(
                new[]
                {
                    "/v1/worlds/valuehold/assisted-routes",
                    "/v1/worlds/valuehold/assisted-routes/" +
                    "assisted-aaaaaaaaaaaaaaaaaaaaaaaa/completion"
                },
                transport.Requests.ConvertAll(request => request.RelativePath));
            Assert.That(
                transport.Requests.TrueForAll(request =>
                    request.Method == WaylineHttpMethod.Post &&
                    request.SessionId == "session-assisted-001"),
                Is.True);
            StringAssert.DoesNotContain("worldId", transport.Requests[0].Body);
            StringAssert.DoesNotContain("routeId", transport.Requests[1].Body);
            StringAssert.DoesNotContain("correctOptionId", transport.Requests[1].Body);
        }

        [Test]
        public void AssistedResponseIdentityMismatchFailsClosed()
        {
            var response = File.ReadAllText(TestPaths.Contract(
                "valid/assisted-route-prepared.json"))
                .Replace("\"worldId\": \"valuehold\"", "\"worldId\": \"decimara\"");
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(201, response);
            var client = new WaylineForgeClient(transport);
            var prepare = StrictQuizValidator.Deserialize<AssistedRoutePrepare>(
                File.ReadAllText(TestPaths.Contract(
                    "valid/assisted-route-prepare.json")));

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.PrepareAssistedRouteAsync(
                    "valuehold",
                    prepare,
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("integrity_failure"));
        }

        [Test]
        public async Task ProgressionCommandsUseOnlyPathOwnedRoutesAndBodies()
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(200, ProgressionFixture("battle-completed.json"));
            transport.Enqueue(201, SealPrepared("seal-batch-001"));
            transport.Enqueue(200, SealCompleted("seal-batch-001"));
            transport.Enqueue(200, ProgressionFixture("world-activated.json"));
            IWaylineProgressionClient client = new WaylineForgeClient(transport);

            await client.CompleteBattleAsync(
                "valuehold",
                "valuehold_route_1",
                "batch-001",
                ProgressionCommand<BattleComplete>("battle-complete.json"),
                CancellationToken.None);
            await client.PrepareSealTrialAsync(
                "valuehold",
                ProgressionCommand<SealTrialPrepare>("seal-trial-prepare.json"),
                CancellationToken.None);
            await client.CompleteSealTrialAsync(
                "valuehold",
                "seal-batch-001",
                ProgressionCommand<SealTrialComplete>("seal-trial-complete.json"),
                CancellationToken.None);
            await client.ActivateWorldAsync(
                "valuehold",
                "decimara",
                ProgressionCommand<WorldActivate>("world-activate.json"),
                CancellationToken.None);

            CollectionAssert.AreEqual(
                new[]
                {
                    "/v1/worlds/valuehold/battles/valuehold_route_1/" +
                    "quiz-batches/batch-001/completion",
                    "/v1/worlds/valuehold/seal-trials",
                    "/v1/worlds/valuehold/seal-trials/seal-batch-001/completion",
                    "/v1/worlds/valuehold/successors/decimara/activation"
                },
                transport.Requests.ConvertAll(request => request.RelativePath));
            Assert.That(
                transport.Requests.TrueForAll(request =>
                    request.Method == WaylineHttpMethod.Post &&
                    request.SessionId == "session-001"),
                Is.True);
            StringAssert.DoesNotContain("\"worldId\"", transport.Requests[0].Body);
            StringAssert.DoesNotContain("\"battleId\"", transport.Requests[0].Body);
            StringAssert.DoesNotContain("\"batchId\"", transport.Requests[0].Body);
            StringAssert.DoesNotContain("\"worldId\"", transport.Requests[1].Body);
            StringAssert.DoesNotContain("\"worldId\"", transport.Requests[2].Body);
            StringAssert.DoesNotContain("\"batchId\"", transport.Requests[2].Body);
            StringAssert.DoesNotContain("\"completedWorldId\"", transport.Requests[3].Body);
            StringAssert.DoesNotContain("\"activeWorldId\"", transport.Requests[3].Body);
        }

        [TestCase("requestId", "different-request-001")]
        [TestCase("worldId", "decimara")]
        [TestCase("battleId", "valuehold_route_2")]
        [TestCase("batchId", "different-batch-001")]
        public void BattleProgressionResponseIdentityMismatchFailsClosed(
            string member,
            string replacement)
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                200,
                MutateProgressionFixture("battle-completed.json", member, replacement));
            IWaylineProgressionClient client = new WaylineForgeClient(transport);

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.CompleteBattleAsync(
                    "valuehold",
                    "valuehold_route_1",
                    "batch-001",
                    ProgressionCommand<BattleComplete>("battle-complete.json"),
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("integrity_failure"));
        }

        [TestCase("requestId", "different-request-001")]
        [TestCase("worldId", "decimara")]
        public void SealPreparationResponseIdentityMismatchFailsClosed(
            string member,
            string replacement)
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                201,
                MutateJson(SealPrepared("seal-batch-001"), member, replacement));
            IWaylineProgressionClient client = new WaylineForgeClient(transport);

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.PrepareSealTrialAsync(
                    "valuehold",
                    ProgressionCommand<SealTrialPrepare>("seal-trial-prepare.json"),
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("integrity_failure"));
        }

        [TestCase("requestId", "different-request-001")]
        [TestCase("worldId", "decimara")]
        [TestCase("batchId", "different-batch-001")]
        public void SealCompletionResponseIdentityMismatchFailsClosed(
            string member,
            string replacement)
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                200,
                MutateJson(SealCompleted("seal-batch-001"), member, replacement));
            IWaylineProgressionClient client = new WaylineForgeClient(transport);

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.CompleteSealTrialAsync(
                    "valuehold",
                    "seal-batch-001",
                    ProgressionCommand<SealTrialComplete>("seal-trial-complete.json"),
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("integrity_failure"));
        }

        [TestCase("requestId", "different-request-001")]
        [TestCase("completedWorldId", "fracture")]
        [TestCase("activeWorldId", "fracture")]
        public void WorldActivationResponseIdentityMismatchFailsClosed(
            string member,
            string replacement)
        {
            var transport = new RecordingWaylineTransport();
            transport.Enqueue(
                200,
                MutateProgressionFixture("world-activated.json", member, replacement));
            IWaylineProgressionClient client = new WaylineForgeClient(transport);

            var error = Assert.ThrowsAsync<WaylineClientException>(async () =>
                await client.ActivateWorldAsync(
                    "valuehold",
                    "decimara",
                    ProgressionCommand<WorldActivate>("world-activate.json"),
                    CancellationToken.None));

            Assert.That(error.Code, Is.EqualTo("integrity_failure"));
        }

        private static T ProgressionCommand<T>(string fixture)
        {
            return StrictQuizValidator.Deserialize<T>(ProgressionFixture(fixture));
        }

        private static string ProgressionFixture(string fixture)
        {
            return File.ReadAllText(TestPaths.Contract("valid/" + fixture));
        }

        private static string SealPrepared(string batchId)
        {
            var value = JObject.Parse(ProgressionFixture("seal-trial-prepared.json"));
            value["batch"]["batchId"] = batchId;
            return value.ToString(Formatting.None);
        }

        private static string SealCompleted(string batchId)
        {
            return MutateProgressionFixture(
                "seal-trial-completed.json",
                "batchId",
                batchId);
        }

        private static string MutateProgressionFixture(
            string fixture,
            string member,
            string replacement)
        {
            return MutateJson(ProgressionFixture(fixture), member, replacement);
        }

        private static string MutateJson(
            string json,
            string member,
            string replacement)
        {
            var value = JObject.Parse(json);
            value[member] = replacement;
            return value.ToString(Formatting.None);
        }
    }

    internal sealed class RecordingWaylineTransport : IWaylineHttpTransport
    {
        private readonly Queue<WaylineHttpResponse> _responses =
            new Queue<WaylineHttpResponse>();

        public List<WaylineHttpRequest> Requests { get; } =
            new List<WaylineHttpRequest>();

        public void Enqueue(long statusCode, string body)
        {
            _responses.Enqueue(new WaylineHttpResponse(statusCode, body));
        }

        public Task<WaylineHttpResponse> SendAsync(
            WaylineHttpRequest request,
            CancellationToken cancellationToken)
        {
            Requests.Add(request);
            return Task.FromResult(_responses.Dequeue());
        }
    }
}
