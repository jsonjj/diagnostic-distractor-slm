using System.Threading;
using System.Threading.Tasks;
using Wayline.Learning.Client;
using Wayline.Learning.Contracts;

namespace Wayline.Flow.Unity
{
    internal sealed class FailClosedWaylineClient : IWaylineForgeClient
    {
        private static WaylineClientException Unavailable() =>
            new WaylineClientException("runtime_unavailable", 0);

        public Task CheckHealthAsync(CancellationToken cancellationToken) =>
            Task.FromException(Unavailable());

        public Task<PublicQuizBatch> PrepareBatchAsync(
            BattleQuizRequest request,
            CancellationToken cancellationToken) =>
            Task.FromException<PublicQuizBatch>(Unavailable());

        public Task<QuizSnapshot> GetQuizSnapshotAsync(
            string batchId,
            string sessionId,
            CancellationToken cancellationToken) =>
            Task.FromException<QuizSnapshot>(Unavailable());

        public Task<InitialSubmissionResult> SubmitInitialAsync(
            string sessionId,
            InitialSubmission submission,
            CancellationToken cancellationToken) =>
            Task.FromException<InitialSubmissionResult>(Unavailable());

        public Task<FinalQuizResult> SubmitRevisionAsync(
            string sessionId,
            RevisionSubmission submission,
            CancellationToken cancellationToken) =>
            Task.FromException<FinalQuizResult>(Unavailable());

        public Task<BossGateResult> GetBossGateAsync(
            string worldId,
            string sessionId,
            CancellationToken cancellationToken) =>
            Task.FromException<BossGateResult>(Unavailable());

        public Task<AssistedRoutePrepared> PrepareAssistedRouteAsync(
            string worldId,
            AssistedRoutePrepare request,
            CancellationToken cancellationToken) =>
            Task.FromException<AssistedRoutePrepared>(Unavailable());

        public Task<AssistedRouteCompleted> CompleteAssistedRouteAsync(
            string worldId,
            string routeId,
            AssistedRouteComplete request,
            CancellationToken cancellationToken) =>
            Task.FromException<AssistedRouteCompleted>(Unavailable());
    }
}
