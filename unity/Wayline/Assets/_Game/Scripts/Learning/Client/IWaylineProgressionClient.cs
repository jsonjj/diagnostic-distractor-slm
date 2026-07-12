using System.Threading;
using System.Threading.Tasks;
using Wayline.Learning.Contracts;

namespace Wayline.Learning.Client
{
    public interface IWaylineProgressionClient
    {
        Task<BattleCompleted> CompleteBattleAsync(
            string worldId,
            string battleId,
            string batchId,
            BattleComplete request,
            CancellationToken cancellationToken);

        Task<SealTrialPrepared> PrepareSealTrialAsync(
            string worldId,
            SealTrialPrepare request,
            CancellationToken cancellationToken);

        Task<SealTrialCompleted> CompleteSealTrialAsync(
            string worldId,
            string batchId,
            SealTrialComplete request,
            CancellationToken cancellationToken);

        Task<WorldActivated> ActivateWorldAsync(
            string completedWorldId,
            string nextWorldId,
            WorldActivate request,
            CancellationToken cancellationToken);
    }
}
