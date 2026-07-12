using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Wayline.Learning.Contracts;

namespace Wayline.Learning.Client
{
    public sealed class WaylineForgeClient :
        IWaylineForgeClient,
        IWaylineProgressionClient,
        IWaylineHealthProbe
    {
        private const int MaximumResponseCharacters = 1_048_576;

        private static readonly Regex IdentifierPattern = new Regex(
            "^[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$",
            RegexOptions.CultureInvariant);

        private static readonly HashSet<string> PublicErrorCodes =
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
                "request_malformed",
                "route_not_found",
                "runtime_state_unavailable",
                "safe_content_unavailable",
                "session_not_current",
                "snapshot_not_ready",
                "snapshot_unavailable",
                "storage_busy"
            };

        private readonly IWaylineHttpTransport _transport;

        public WaylineForgeClient(IWaylineHttpTransport transport)
        {
            _transport = transport ?? throw new ArgumentNullException(nameof(transport));
        }

        public async Task CheckHealthAsync(CancellationToken cancellationToken)
        {
            var response = await SendRawAsync(
                new WaylineHttpRequest(WaylineHttpMethod.Get, "/v1/health", null, null),
                cancellationToken);
            try
            {
                var value = ParseObject(response.Body);
                RequireExactMembers(value, "schemaVersion", "status");
                if ((string)value["schemaVersion"] != "wayline.health.v1" ||
                    (string)value["status"] != "ready")
                {
                    throw new JsonSerializationException("health response is invalid");
                }
            }
            catch (JsonException)
            {
                throw new WaylineClientException("integrity_failure", response.StatusCode);
            }
        }

        public Task CheckReadyAsync(CancellationToken cancellationToken)
        {
            return CheckHealthAsync(cancellationToken);
        }

        public Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken)
        {
            StrictQuizValidator.Validate(request);
            return SendContractAsync<PublicQuizBatch>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/quiz-batches",
                    JsonConvert.SerializeObject(request),
                    request.SessionId),
                cancellationToken);
        }

        public async Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken)
        {
            var ownedBatchId = RequireIdentifier(batchId, nameof(batchId));
            var result = await SendContractAsync<QuizSnapshot>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Get,
                    "/v1/quiz-batches/" + PathIdentifier(ownedBatchId),
                    null,
                    RequireIdentifier(sessionId, nameof(sessionId))),
                cancellationToken);
            if (result.BatchId != ownedBatchId)
                throw new WaylineClientException("integrity_failure", 0);
            return result;
        }

        public Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken)
        {
            StrictQuizValidator.Validate(submission);
            return SendContractAsync<InitialSubmissionResult>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/quiz-batches/" + PathIdentifier(submission.BatchId) + "/initial",
                    JsonConvert.SerializeObject(submission),
                    RequireIdentifier(sessionId, nameof(sessionId))),
                cancellationToken);
        }

        public Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken)
        {
            StrictQuizValidator.Validate(submission);
            return SendContractAsync<FinalQuizResult>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/quiz-batches/" + PathIdentifier(submission.BatchId) + "/revision",
                    JsonConvert.SerializeObject(submission),
                    RequireIdentifier(sessionId, nameof(sessionId))),
                cancellationToken);
        }

        public async Task<BossGateResult> GetBossGateAsync(
            string worldId,
            string sessionId,
            CancellationToken cancellationToken)
        {
            var ownedWorldId = RequireIdentifier(worldId, nameof(worldId));
            var result = await SendContractAsync<BossGateResult>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Get,
                    "/v1/worlds/" + PathIdentifier(ownedWorldId) + "/gate",
                    null,
                    RequireIdentifier(sessionId, nameof(sessionId))),
                cancellationToken);
            if (result.WorldId != ownedWorldId)
                throw new WaylineClientException("integrity_failure", 0);
            return result;
        }

        public async Task<AssistedRoutePrepared> PrepareAssistedRouteAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken)
        {
            var ownedWorldId = RequireIdentifier(worldId, nameof(worldId));
            StrictQuizValidator.Validate(request);
            var result = await SendContractAsync<AssistedRoutePrepared>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/worlds/" + PathIdentifier(ownedWorldId) + "/assisted-routes",
                    JsonConvert.SerializeObject(request),
                    request.SessionId),
                cancellationToken);
            if (result.RequestId != request.RequestId ||
                result.WorldId != ownedWorldId ||
                result.Batch.WorldId != ownedWorldId)
            {
                throw new WaylineClientException("integrity_failure", 0);
            }
            return result;
        }

        public async Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken)
        {
            var ownedWorldId = RequireIdentifier(worldId, nameof(worldId));
            var ownedRouteId = RequireIdentifier(routeId, nameof(routeId));
            StrictQuizValidator.Validate(request);
            var result = await SendContractAsync<AssistedRouteCompleted>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/worlds/" + PathIdentifier(ownedWorldId) +
                    "/assisted-routes/" + PathIdentifier(ownedRouteId) +
                    "/completion",
                    JsonConvert.SerializeObject(request),
                    request.SessionId),
                cancellationToken);
            if (result.RequestId != request.RequestId ||
                result.WorldId != ownedWorldId ||
                result.RouteId != ownedRouteId ||
                result.Items.Count != request.Selections.Count)
            {
                throw new WaylineClientException("integrity_failure", 0);
            }
            for (var index = 0; index < result.Items.Count; index++)
            {
                var item = result.Items[index];
                var selection = request.Selections[index];
                if (item.ItemId != selection.ItemId ||
                    item.SelectedOptionId != selection.OptionId ||
                    item.Confidence != selection.Confidence)
                {
                    throw new WaylineClientException("integrity_failure", 0);
                }
            }
            return result;
        }

        public async Task<BattleCompleted> CompleteBattleAsync(
            string worldId,
            string battleId,
            string batchId,
            BattleComplete request,
            CancellationToken cancellationToken)
        {
            var ownedWorldId = RequireIdentifier(worldId, nameof(worldId));
            var ownedBattleId = RequireIdentifier(battleId, nameof(battleId));
            var ownedBatchId = RequireIdentifier(batchId, nameof(batchId));
            StrictQuizValidator.Validate(request);
            var result = await SendContractAsync<BattleCompleted>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/worlds/" + PathIdentifier(ownedWorldId) +
                    "/battles/" + PathIdentifier(ownedBattleId) +
                    "/quiz-batches/" + PathIdentifier(ownedBatchId) +
                    "/completion",
                    JsonConvert.SerializeObject(request),
                    request.SessionId),
                cancellationToken);
            if (result.RequestId != request.RequestId ||
                result.WorldId != ownedWorldId ||
                result.BattleId != ownedBattleId ||
                result.BatchId != ownedBatchId)
            {
                throw new WaylineClientException("integrity_failure", 0);
            }
            return result;
        }

        public async Task<SealTrialPrepared> PrepareSealTrialAsync(
            string worldId,
            SealTrialPrepare request,
            CancellationToken cancellationToken)
        {
            var ownedWorldId = RequireIdentifier(worldId, nameof(worldId));
            StrictQuizValidator.Validate(request);
            var result = await SendContractAsync<SealTrialPrepared>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/worlds/" + PathIdentifier(ownedWorldId) + "/seal-trials",
                    JsonConvert.SerializeObject(request),
                    request.SessionId),
                cancellationToken);
            if (result.RequestId != request.RequestId ||
                result.WorldId != ownedWorldId)
            {
                throw new WaylineClientException("integrity_failure", 0);
            }
            return result;
        }

        public async Task<SealTrialCompleted> CompleteSealTrialAsync(
            string worldId,
            string batchId,
            SealTrialComplete request,
            CancellationToken cancellationToken)
        {
            var ownedWorldId = RequireIdentifier(worldId, nameof(worldId));
            var ownedBatchId = RequireIdentifier(batchId, nameof(batchId));
            StrictQuizValidator.Validate(request);
            var result = await SendContractAsync<SealTrialCompleted>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/worlds/" + PathIdentifier(ownedWorldId) +
                    "/seal-trials/" + PathIdentifier(ownedBatchId) +
                    "/completion",
                    JsonConvert.SerializeObject(request),
                    request.SessionId),
                cancellationToken);
            if (result.RequestId != request.RequestId ||
                result.WorldId != ownedWorldId ||
                result.BatchId != ownedBatchId)
            {
                throw new WaylineClientException("integrity_failure", 0);
            }
            return result;
        }

        public async Task<WorldActivated> ActivateWorldAsync(
            string completedWorldId,
            string nextWorldId,
            WorldActivate request,
            CancellationToken cancellationToken)
        {
            var ownedCompletedWorldId = RequireIdentifier(
                completedWorldId,
                nameof(completedWorldId));
            var ownedNextWorldId = RequireIdentifier(nextWorldId, nameof(nextWorldId));
            StrictQuizValidator.Validate(request);
            var result = await SendContractAsync<WorldActivated>(
                new WaylineHttpRequest(
                    WaylineHttpMethod.Post,
                    "/v1/worlds/" + PathIdentifier(ownedCompletedWorldId) +
                    "/successors/" + PathIdentifier(ownedNextWorldId) +
                    "/activation",
                    JsonConvert.SerializeObject(request),
                    request.SessionId),
                cancellationToken);
            if (result.RequestId != request.RequestId ||
                result.CompletedWorldId != ownedCompletedWorldId ||
                result.ActiveWorldId != ownedNextWorldId)
            {
                throw new WaylineClientException("integrity_failure", 0);
            }
            return result;
        }

        private async Task<T> SendContractAsync<T>(
            WaylineHttpRequest request,
            CancellationToken cancellationToken)
        {
            var response = await SendRawAsync(request, cancellationToken);
            try
            {
                return StrictQuizValidator.Deserialize<T>(response.Body);
            }
            catch (JsonException)
            {
                throw new WaylineClientException("integrity_failure", response.StatusCode);
            }
        }

        private async Task<WaylineHttpResponse> SendRawAsync(
            WaylineHttpRequest request,
            CancellationToken cancellationToken)
        {
            WaylineHttpResponse response;
            try
            {
                response = await _transport.SendAsync(request, cancellationToken);
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (WaylineClientException)
            {
                throw;
            }
            catch (Exception)
            {
                throw new WaylineClientException("runtime_unavailable", 0);
            }

            if (response.Body.Length > MaximumResponseCharacters)
                throw new WaylineClientException("integrity_failure", response.StatusCode);
            if (response.StatusCode >= 200 && response.StatusCode <= 299)
                return response;
            throw ParsePublicError(response);
        }

        private static WaylineClientException ParsePublicError(WaylineHttpResponse response)
        {
            try
            {
                var value = ParseObject(response.Body);
                RequireExactMembers(value, "schemaVersion", "code");
                var schema = value["schemaVersion"];
                var code = value["code"];
                if (schema.Type != JTokenType.String ||
                    code.Type != JTokenType.String ||
                    (string)schema != "wayline.error.v1" ||
                    !PublicErrorCodes.Contains((string)code))
                {
                    return new WaylineClientException("integrity_failure", response.StatusCode);
                }
                return new WaylineClientException((string)code, response.StatusCode);
            }
            catch (JsonException)
            {
                return new WaylineClientException("integrity_failure", response.StatusCode);
            }
        }

        private static JObject ParseObject(string json)
        {
            var token = JToken.Parse(
                json,
                new JsonLoadSettings
                {
                    DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error
                });
            if (token.Type != JTokenType.Object)
                throw new JsonSerializationException("response must be an object");
            return (JObject)token;
        }

        private static void RequireExactMembers(JObject value, params string[] names)
        {
            if (value.Count != names.Length)
                throw new JsonSerializationException("response members are invalid");
            foreach (var name in names)
            {
                if (value.Property(name, StringComparison.Ordinal) == null)
                    throw new JsonSerializationException("response members are invalid");
            }
        }

        private static string PathIdentifier(string value)
        {
            return Uri.EscapeDataString(RequireIdentifier(value, nameof(value)));
        }

        private static string RequireIdentifier(string value, string name)
        {
            if (value == null || !IdentifierPattern.IsMatch(value))
                throw new ArgumentException(name + " is invalid", name);
            return value;
        }
    }
}
