using System;
using System.Threading;
using System.Threading.Tasks;
using Wayline.Learning.Contracts;

namespace Wayline.Learning.Client
{
    public interface IWaylineForgeClient
    {
        Task CheckHealthAsync(CancellationToken cancellationToken);

        Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken);

        Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken);

        Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken);

        Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken);

        Task<BossGateResult> GetBossGateAsync(
            string worldId,
            string sessionId,
            CancellationToken cancellationToken);

        Task<AssistedRoutePrepared> PrepareAssistedRouteAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken);

        Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken);
    }

    public enum WaylineHttpMethod
    {
        Get,
        Post
    }

    public sealed class WaylineHttpRequest
    {
        public WaylineHttpRequest(
            WaylineHttpMethod method,
            string relativePath,
            string body,
            string sessionId)
        {
            if (string.IsNullOrEmpty(relativePath) || relativePath[0] != '/')
                throw new ArgumentException("relativePath must be rooted", nameof(relativePath));
            Method = method;
            RelativePath = relativePath;
            Body = body;
            SessionId = sessionId;
        }

        public WaylineHttpMethod Method { get; }

        public string RelativePath { get; }

        public string Body { get; }

        public string SessionId { get; }

        public override string ToString()
        {
            return $"{Method} {RelativePath} (body and session redacted)";
        }
    }

    public sealed class WaylineHttpResponse
    {
        public WaylineHttpResponse(long statusCode, string body)
        {
            StatusCode = statusCode;
            Body = body ?? string.Empty;
        }

        public long StatusCode { get; }

        public string Body { get; }

        public override string ToString()
        {
            return $"HTTP {StatusCode} (body redacted)";
        }
    }

    public interface IWaylineHttpTransport
    {
        Task<WaylineHttpResponse> SendAsync(
            WaylineHttpRequest request,
            CancellationToken cancellationToken);
    }

    public sealed class WaylineClientException : Exception
    {
        public WaylineClientException(string code, long statusCode)
            : base(code ?? "integrity_failure")
        {
            Code = code ?? "integrity_failure";
            StatusCode = statusCode;
        }

        public string Code { get; }

        public long StatusCode { get; }

        public override string ToString()
        {
            return $"WaylineClientException(Code={Code}, StatusCode={StatusCode})";
        }
    }

    public interface IWaylineHealthProbe
    {
        Task CheckReadyAsync(CancellationToken cancellationToken);
    }
}
