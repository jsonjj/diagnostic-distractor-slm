using System;
using System.Collections.Generic;

namespace Wayline.Flow
{
    public sealed class VerticalSliceFlowController
    {
        private readonly ICombatFlowPort _combat;
        private readonly ITrialFlowPort _trial;
        private readonly ICampaignFlowPort _campaign;
        private readonly List<string> _committedTrialIds = new List<string>();
        private readonly HashSet<string> _committedTrialIndex =
            new HashSet<string>(StringComparer.Ordinal);
        private readonly List<string> _committedRewardIds = new List<string>();
        private readonly HashSet<string> _committedRewardIndex =
            new HashSet<string>(StringComparer.Ordinal);

        private FlowCheckpoint _pendingCheckpoint;

        public VerticalSliceFlowController(
            ICombatFlowPort combat,
            ITrialFlowPort trial,
            ICampaignFlowPort campaign)
        {
            _combat = combat ?? throw new ArgumentNullException(nameof(combat));
            _trial = trial ?? throw new ArgumentNullException(nameof(trial));
            _campaign = campaign ?? throw new ArgumentNullException(nameof(campaign));

            State = FlowState.Title;
            LastCheckpoint = CreateCheckpoint(
                FlowState.Title,
                null,
                combatVictoryPreserved: false,
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
        }

        public FlowState State { get; private set; }

        public FlowCheckpoint LastCheckpoint { get; private set; }

        public bool HasPendingTrial => _pendingCheckpoint != null;

        public void EnterMap()
        {
            if (State != FlowState.Title)
                throw new InvalidOperationException("The map can only be entered from the title state.");

            TransitionStable(
                FlowState.Map,
                null,
                combatVictoryPreserved: false,
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
        }

        public void StartCombat(FlowBattle battle)
        {
            if (State != FlowState.Map)
                throw new InvalidOperationException("Combat can only start from the map.");
            if (_pendingCheckpoint != null)
            {
                throw new InvalidOperationException(
                    "A pending trial must be resumed before another combat can start.");
            }

            TransitionStable(
                FlowState.Combat,
                battle ?? throw new ArgumentNullException(nameof(battle)),
                combatVictoryPreserved: false,
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
        }

        public bool ResolveCombat(FlowCombatOutcome outcome)
        {
            if (State != FlowState.Combat)
                return false;

            var battle = RequireCurrentBattle();
            if (outcome == FlowCombatOutcome.Defeat)
            {
                TransitionStable(
                    FlowState.Map,
                    null,
                    combatVictoryPreserved: false,
                    rewardSourceCompletionId: null,
                    rewardAuthorityReceiptId: null);
                return true;
            }

            if (outcome != FlowCombatOutcome.Victory)
                throw new ArgumentOutOfRangeException(nameof(outcome), outcome, "Unknown combat outcome.");

            _campaign.PreserveCombatVictory(battle);
            TransitionStable(
                FlowState.NormalTrial,
                battle,
                combatVictoryPreserved: true,
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null);
            return true;
        }

        public void SuspendTrial(string reason)
        {
            if (string.IsNullOrWhiteSpace(reason))
                throw new ArgumentException("An unavailable reason is required.", nameof(reason));
            if (!IsTrialState(State))
                throw new InvalidOperationException("Only an active trial can be suspended.");

            _pendingCheckpoint = LastCheckpoint;
            State = FlowState.Unavailable;
        }

        public void RetryUnavailable()
        {
            if (State != FlowState.Unavailable || _pendingCheckpoint == null)
                throw new InvalidOperationException("There is no suspended trial to retry.");

            var checkpoint = _pendingCheckpoint;
            _pendingCheckpoint = null;
            PresentStable(checkpoint);
        }

        public void ReturnToMapFromUnavailable()
        {
            if (State != FlowState.Unavailable || _pendingCheckpoint == null)
                throw new InvalidOperationException("There is no suspended trial to leave.");

            State = FlowState.Map;
            _campaign.PresentMap();
        }

        public void ResumePending()
        {
            if (State != FlowState.Map || _pendingCheckpoint == null)
                throw new InvalidOperationException("There is no pending trial to resume.");

            var checkpoint = _pendingCheckpoint;
            _pendingCheckpoint = null;
            PresentStable(checkpoint);
        }

        public bool CompleteTrial(AuthoritativeTrialCompletion completion)
        {
            if (completion == null)
                throw new ArgumentNullException(nameof(completion));
            if (_committedTrialIndex.Contains(completion.CompletionId))
                return false;

            var currentStage = StageForState(State);
            if (completion.Stage != currentStage)
            {
                throw new InvalidOperationException(
                    $"A {completion.Stage} completion cannot resolve the {currentStage} stage.");
            }

            var battle = RequireCurrentBattle();
            if (completion.Battle != battle)
                throw new ArgumentException("The completion belongs to a different battle.", nameof(completion));

            ValidateRoute(completion.Stage, completion.NextStep);

            _campaign.CommitAuthoritativeTrial(completion);
            AddCommittedTrial(completion.CompletionId);

            switch (completion.NextStep)
            {
                case AuthoritativeNextStep.Reward:
                    TransitionStable(
                        FlowState.Reward,
                        battle,
                        combatVictoryPreserved: true,
                        rewardSourceCompletionId: completion.CompletionId,
                        rewardAuthorityReceiptId: completion.AuthorityReceiptId,
                        onPersistenceFailure: () =>
                            RemoveCommittedTrial(completion.CompletionId));
                    break;
                case AuthoritativeNextStep.SealTrial:
                    TransitionStable(
                        FlowState.SealTrial,
                        battle,
                        combatVictoryPreserved: true,
                        rewardSourceCompletionId: null,
                        rewardAuthorityReceiptId: null,
                        onPersistenceFailure: () =>
                            RemoveCommittedTrial(completion.CompletionId));
                    break;
                case AuthoritativeNextStep.AssistedRoute:
                    TransitionStable(
                        FlowState.AssistedRoute,
                        battle,
                        combatVictoryPreserved: true,
                        rewardSourceCompletionId: null,
                        rewardAuthorityReceiptId: null,
                        onPersistenceFailure: () =>
                            RemoveCommittedTrial(completion.CompletionId));
                    break;
                default:
                    throw new ArgumentOutOfRangeException(
                        nameof(completion),
                        completion.NextStep,
                        "Unknown authoritative route.");
            }

            return true;
        }

        public bool CompleteReward()
        {
            if (State != FlowState.Reward)
                return false;

            var checkpoint = LastCheckpoint;
            var rewardId = checkpoint.RewardSourceCompletionId;
            if (_committedRewardIndex.Contains(rewardId))
                return false;

            _campaign.CommitReward(
                RequireCurrentBattle(),
                rewardId,
                checkpoint.RewardAuthorityReceiptId);
            AddCommittedReward(rewardId);
            TransitionStable(
                FlowState.Map,
                null,
                combatVictoryPreserved: false,
                rewardSourceCompletionId: null,
                rewardAuthorityReceiptId: null,
                onPersistenceFailure: () => RemoveCommittedReward(rewardId));
            return true;
        }

        public void Restore(FlowCheckpoint checkpoint)
        {
            if (checkpoint == null)
                throw new ArgumentNullException(nameof(checkpoint));
            if (checkpoint.StableState == FlowState.Unavailable)
                throw new ArgumentException("A transient unavailable state cannot be restored.", nameof(checkpoint));

            _committedTrialIds.Clear();
            _committedTrialIndex.Clear();
            foreach (var completionId in checkpoint.CommittedTrialIds)
                AddCommittedTrial(completionId);

            _committedRewardIds.Clear();
            _committedRewardIndex.Clear();
            foreach (var rewardId in checkpoint.CommittedRewardIds)
                AddCommittedReward(rewardId);

            _pendingCheckpoint = null;
            LastCheckpoint = checkpoint;
            PresentStable(checkpoint);
        }

        private static bool IsTrialState(FlowState state)
        {
            return state == FlowState.NormalTrial ||
                   state == FlowState.SealTrial ||
                   state == FlowState.AssistedRoute;
        }

        private static FlowTrialStage StageForState(FlowState state)
        {
            switch (state)
            {
                case FlowState.NormalTrial:
                    return FlowTrialStage.Normal;
                case FlowState.SealTrial:
                    return FlowTrialStage.Seal;
                case FlowState.AssistedRoute:
                    return FlowTrialStage.Assisted;
                default:
                    throw new InvalidOperationException("There is no active trial to complete.");
            }
        }

        private static void ValidateRoute(FlowTrialStage stage, AuthoritativeNextStep next)
        {
            var allowed = stage == FlowTrialStage.Normal
                ? next == AuthoritativeNextStep.Reward || next == AuthoritativeNextStep.SealTrial
                : stage == FlowTrialStage.Seal
                    ? next == AuthoritativeNextStep.Reward ||
                      next == AuthoritativeNextStep.SealTrial ||
                      next == AuthoritativeNextStep.AssistedRoute
                    : next == AuthoritativeNextStep.Reward;

            if (!allowed)
            {
                throw new ArgumentException(
                    $"The authoritative route {next} is not valid after {stage}.",
                    nameof(next));
            }
        }

        private void TransitionStable(
            FlowState stableState,
            FlowBattle battle,
            bool combatVictoryPreserved,
            string rewardSourceCompletionId,
            string rewardAuthorityReceiptId,
            Action onPersistenceFailure = null)
        {
            var previousState = State;
            var previousCheckpoint = LastCheckpoint;
            var previousPending = _pendingCheckpoint;
            FlowCheckpoint checkpoint;
            try
            {
                checkpoint = CreateCheckpoint(
                    stableState,
                    battle,
                    combatVictoryPreserved,
                    rewardSourceCompletionId,
                    rewardAuthorityReceiptId);
                LastCheckpoint = checkpoint;
                State = stableState;
                _campaign.StoreCheckpoint(checkpoint);
            }
            catch
            {
                State = previousState;
                LastCheckpoint = previousCheckpoint;
                _pendingCheckpoint = previousPending;
                onPersistenceFailure?.Invoke();
                throw;
            }

            _pendingCheckpoint = null;
            PresentStable(checkpoint);
        }

        private FlowCheckpoint CreateCheckpoint(
            FlowState stableState,
            FlowBattle battle,
            bool combatVictoryPreserved,
            string rewardSourceCompletionId,
            string rewardAuthorityReceiptId)
        {
            return new FlowCheckpoint(
                stableState,
                battle,
                combatVictoryPreserved,
                _committedTrialIds,
                _committedRewardIds,
                rewardSourceCompletionId,
                rewardAuthorityReceiptId);
        }

        private void PresentStable(FlowCheckpoint checkpoint)
        {
            State = checkpoint.StableState;
            switch (checkpoint.StableState)
            {
                case FlowState.Title:
                    return;
                case FlowState.Map:
                    _campaign.PresentMap();
                    return;
                case FlowState.Combat:
                    _combat.PresentCombat(checkpoint.Battle);
                    return;
                case FlowState.NormalTrial:
                    _trial.PresentNormalTrial(checkpoint.Battle);
                    return;
                case FlowState.SealTrial:
                    _trial.PresentSealTrial(checkpoint.Battle);
                    return;
                case FlowState.AssistedRoute:
                    _trial.PresentAssistedRoute(checkpoint.Battle);
                    return;
                case FlowState.Reward:
                    _campaign.PresentReward(
                        checkpoint.Battle,
                        checkpoint.RewardSourceCompletionId,
                        checkpoint.RewardAuthorityReceiptId);
                    return;
                default:
                    throw new ArgumentOutOfRangeException(
                        nameof(checkpoint),
                        checkpoint.StableState,
                        "Unknown stable presentation state.");
            }
        }

        private FlowBattle RequireCurrentBattle()
        {
            return LastCheckpoint.Battle ??
                   throw new InvalidOperationException("The current flow has no battle identity.");
        }

        private void AddCommittedTrial(string completionId)
        {
            if (_committedTrialIndex.Add(completionId))
                _committedTrialIds.Add(completionId);
        }

        private void RemoveCommittedTrial(string completionId)
        {
            _committedTrialIndex.Remove(completionId);
            _committedTrialIds.Remove(completionId);
        }

        private void AddCommittedReward(string rewardId)
        {
            if (_committedRewardIndex.Add(rewardId))
                _committedRewardIds.Add(rewardId);
        }

        private void RemoveCommittedReward(string rewardId)
        {
            _committedRewardIndex.Remove(rewardId);
            _committedRewardIds.Remove(rewardId);
        }
    }
}
