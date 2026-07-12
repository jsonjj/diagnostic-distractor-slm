#if UNITY_EDITOR || DEVELOPMENT_BUILD
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;

namespace Wayline.Flow.Unity
{
    /// <summary>
    /// Development-only live-Qwen wrapper. The bridge returns answers only after
    /// the Python consistency checker verifies all three computations. Sealed
    /// scoring/revision stays in the deterministic authority. Any bridge/model
    /// failure falls back to the complete deterministic batch.
    /// </summary>
    internal sealed class DevelopmentLiveAcceptanceQuizClient :
        IWaylineForgeClient,
        IDisposable
    {
        public const string VisibleLabel =
            "LIVE LOCAL QWEN — VERIFIED DISTRACTORS";

        private readonly DevelopmentDeterministicAcceptanceQuizClient _authority =
            new DevelopmentDeterministicAcceptanceQuizClient();
        private readonly HttpClient _http;
        private readonly Uri _generateUri;

        public DevelopmentLiveAcceptanceQuizClient(Uri bridgeBaseUri)
        {
            if (bridgeBaseUri == null ||
                !bridgeBaseUri.IsAbsoluteUri ||
                !bridgeBaseUri.IsLoopback ||
                bridgeBaseUri.Scheme != Uri.UriSchemeHttp)
            {
                throw new ArgumentException(
                    "Live bridge must be an absolute IPv4/localhost HTTP loopback URI.",
                    nameof(bridgeBaseUri));
            }

            _generateUri = new Uri(bridgeBaseUri, "/generate");
            _http = new HttpClient(
                new HttpClientHandler { UseProxy = false },
                disposeHandler: true)
            {
                Timeout = TimeSpan.FromSeconds(120)
            };
        }

        internal DevelopmentDeterministicAcceptanceQuizClient Authority => _authority;

        public Task CheckHealthAsync(CancellationToken cancellationToken) =>
            _authority.CheckHealthAsync(cancellationToken);

        public async Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken)
        {
            var fallback = await _authority.PrepareBatchAsync(
                request,
                cancellationToken);

            try
            {
                var topic = TopicFor(request.WorldId);
                var tasks = fallback.Items.Select(item =>
                    GenerateAsync(
                        item.Prompt,
                        item.Options[0].DisplayText,
                        topic,
                        cancellationToken)).ToArray();
                var generated = await Task.WhenAll(tasks);
                var items = new List<PublicQuizItem>(fallback.ItemCount);
                for (var index = 0; index < fallback.Items.Count; index++)
                {
                    var original = fallback.Items[index];
                    var wrong = generated[index].answers;
                    if (wrong == null || wrong.Length != 3)
                        throw new InvalidOperationException("Live bridge returned an invalid answer count.");
                    items.Add(new PublicQuizItem(
                        original.ItemId,
                        original.Prompt,
                        new[]
                        {
                            original.Options[0],
                            new PublicQuizOption(original.ItemId + "-b", wrong[0]),
                            new PublicQuizOption(original.ItemId + "-c", wrong[1]),
                            new PublicQuizOption(original.ItemId + "-d", wrong[2]),
                        }));
                }

                var liveBatch = new PublicQuizBatch(
                    fallback.SchemaVersion,
                    fallback.BatchId,
                    fallback.ItemCount,
                    items);
                _authority.OverridePreparedBatch(liveBatch);
                return liveBatch;
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception exception)
            {
                Debug.LogWarning(
                    "live_qwen_batch_fallback: " + exception.GetType().Name);
                return fallback;
            }
        }

        public Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken) =>
            _authority.GetQuizSnapshotAsync(batchId, sessionId, cancellationToken);

        public Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken) =>
            _authority.SubmitInitialAsync(sessionId, submission, cancellationToken);

        public Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken) =>
            _authority.SubmitRevisionAsync(sessionId, submission, cancellationToken);

        public Task<BossGateResult> GetBossGateAsync(
            string worldId,
            string sessionId,
            CancellationToken cancellationToken) =>
            _authority.GetBossGateAsync(worldId, sessionId, cancellationToken);

        public Task<AssistedRoutePrepared> PrepareAssistedRouteAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken) =>
            _authority.PrepareAssistedRouteAsync(worldId, request, cancellationToken);

        public Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken) =>
            _authority.CompleteAssistedRouteAsync(
                worldId,
                routeId,
                request,
                cancellationToken);

        public void Dispose()
        {
            _http.Dispose();
        }

        private async Task<BridgeResponse> GenerateAsync(
            string question,
            string correct,
            string topic,
            CancellationToken cancellationToken)
        {
            var payload = JsonUtility.ToJson(new BridgeRequest
            {
                question = question,
                correct = correct,
                topic = topic,
            });
            using (var request = new HttpRequestMessage(HttpMethod.Post, _generateUri))
            {
                request.Content = new StringContent(
                    payload,
                    Encoding.UTF8,
                    "application/json");
                using (var response = await _http.SendAsync(
                           request,
                           HttpCompletionOption.ResponseContentRead,
                           cancellationToken))
                {
                    if (!response.IsSuccessStatusCode)
                        throw new HttpRequestException(
                            "Live bridge rejected generated distractors.");
                    var body = await response.Content.ReadAsStringAsync();
                    var decoded = JsonUtility.FromJson<BridgeResponse>(body);
                    if (decoded == null || decoded.verified != true)
                        throw new InvalidOperationException(
                            "Live bridge response was not verified.");
                    return decoded;
                }
            }
        }

        private static string TopicFor(string worldId)
        {
            switch (worldId)
            {
                case "decimara":
                    return "Adding and Subtracting with Decimals";
                case "fracture":
                    return "Adding and Subtracting Fractions";
                default:
                    return "Place Value";
            }
        }

        [Serializable]
        private sealed class BridgeRequest
        {
            public string question;
            public string correct;
            public string topic;
        }

        [Serializable]
        private sealed class BridgeResponse
        {
            public bool verified;
            public string[] answers;
        }
    }
}
#endif
