using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using Wayline.Save;

namespace Wayline.Campaign
{
    public sealed class ServerBossGateState
    {
        public ServerBossGateState(
            string worldId,
            bool unlocked,
            int leadInWins,
            int requiredLeadInWins,
            IEnumerable<string> unmetRequirements)
        {
            if (string.IsNullOrWhiteSpace(worldId))
                throw new ArgumentException("A world identifier is required.", nameof(worldId));
            if (leadInWins < 0)
                throw new ArgumentOutOfRangeException(nameof(leadInWins));
            if (requiredLeadInWins < 1)
                throw new ArgumentOutOfRangeException(nameof(requiredLeadInWins));
            if (unmetRequirements == null)
                throw new ArgumentNullException(nameof(unmetRequirements));
            var unmet = unmetRequirements.ToArray();
            if (unmet.Any(string.IsNullOrWhiteSpace) ||
                unmet.Distinct(StringComparer.Ordinal).Count() != unmet.Length)
            {
                throw new ArgumentException("Unmet requirements must be unique stable identifiers.", nameof(unmetRequirements));
            }
            if (unlocked && unmet.Length != 0)
                throw new ArgumentException("An unlocked server gate cannot report unmet requirements.", nameof(unmetRequirements));
            WorldId = worldId;
            Unlocked = unlocked;
            LeadInWins = leadInWins;
            RequiredLeadInWins = requiredLeadInWins;
            UnmetRequirements = new ReadOnlyCollection<string>(unmet);
        }

        public string WorldId { get; }

        public bool Unlocked { get; }

        public int LeadInWins { get; }

        public int RequiredLeadInWins { get; }

        public IReadOnlyList<string> UnmetRequirements { get; }
    }

    public sealed class BossGateDecision
    {
        public BossGateDecision(bool canEnter, string reason)
        {
            CanEnter = canEnter;
            Reason = reason ?? throw new ArgumentNullException(nameof(reason));
        }

        public bool CanEnter { get; }

        public string Reason { get; }
    }

    public sealed class BossGateController
    {
        public BossGateDecision Evaluate(
            WorldDefinition world,
            ProfileDataV1 profile,
            ServerBossGateState serverState)
        {
            if (world == null)
                throw new ArgumentNullException(nameof(world));
            if (profile == null)
                throw new ArgumentNullException(nameof(profile));
            if (serverState == null)
                throw new ArgumentNullException(nameof(serverState));
            if (!string.Equals(world.Id, serverState.WorldId, StringComparison.Ordinal))
                throw new ArgumentException("Server gate state belongs to a different world.", nameof(serverState));

            var localLeadInWins = world.LeadInBattles.Count(battle =>
                profile.HasCombatVictory(battle.Id));
            if (localLeadInWins != world.LeadInBattles.Count ||
                serverState.LeadInWins < serverState.RequiredLeadInWins)
            {
                return new BossGateDecision(false, "lead_in_wins_required");
            }
            if (!serverState.Unlocked)
                return new BossGateDecision(false, "server_learning_gate_locked");
            return new BossGateDecision(true, "unlocked");
        }
    }
}
