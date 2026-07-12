using System;
using System.Collections.Generic;
using System.Linq;
using Wayline.Save;

namespace Wayline.Campaign
{
    public enum CampaignStep
    {
        BattleSelect = 0,
        SealTrial = 1,
        AssistedRoute = 2,
        WorldCleared = 3
    }

    public sealed class TrialDescriptor
    {
        public TrialDescriptor(string worldId, string battleId, BattleTier tier, int questionCount)
        {
            WorldId = worldId;
            BattleId = battleId;
            Tier = tier;
            QuestionCount = questionCount;
        }

        public string WorldId { get; }

        public string BattleId { get; }

        public BattleTier Tier { get; }

        public int QuestionCount { get; }
    }

    public sealed class BossTrialResolution
    {
        public BossTrialResolution(
            string worldId,
            string battleId,
            int finalCorrect,
            int itemCount,
            bool worldCleared,
            bool sealTrialRequired)
        {
            RequireScore(finalCorrect, itemCount);
            if (worldCleared == sealTrialRequired)
                throw new ArgumentException("Boss completion must clear the world or require a Seal Trial.");
            WorldId = Require(worldId, nameof(worldId));
            BattleId = Require(battleId, nameof(battleId));
            FinalCorrect = finalCorrect;
            ItemCount = itemCount;
            WorldCleared = worldCleared;
            SealTrialRequired = sealTrialRequired;
        }

        public string WorldId { get; }

        public string BattleId { get; }

        public int FinalCorrect { get; }

        public int ItemCount { get; }

        public bool WorldCleared { get; }

        public bool SealTrialRequired { get; }

        private static void RequireScore(int correct, int count)
        {
            if (count < 1)
                throw new ArgumentOutOfRangeException(nameof(count));
            if (correct < 0 || correct > count)
                throw new ArgumentOutOfRangeException(nameof(correct));
        }

        private static string Require(string value, string parameter)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("An identifier is required.", parameter);
            return value;
        }
    }

    public sealed class SealTrialResolution
    {
        public SealTrialResolution(
            string worldId,
            int attemptNumber,
            bool passed,
            bool worldCleared,
            bool assistedRouteUnlocked)
        {
            if (string.IsNullOrWhiteSpace(worldId))
                throw new ArgumentException("A world identifier is required.", nameof(worldId));
            if (attemptNumber < 1)
                throw new ArgumentOutOfRangeException(nameof(attemptNumber));
            if (passed != worldCleared)
                throw new ArgumentException("A passed Seal Trial must clear the world.");
            if (worldCleared && assistedRouteUnlocked)
                throw new ArgumentException("A cleared world cannot also unlock assistance.");
            if (assistedRouteUnlocked && attemptNumber < 2)
                throw new ArgumentException("Assistance unlocks only after two missed Seal Trials.");
            WorldId = worldId;
            AttemptNumber = attemptNumber;
            Passed = passed;
            WorldCleared = worldCleared;
            AssistedRouteUnlocked = assistedRouteUnlocked;
        }

        public string WorldId { get; }

        public int AttemptNumber { get; }

        public bool Passed { get; }

        public bool WorldCleared { get; }

        public bool AssistedRouteUnlocked { get; }
    }

    public sealed class AssistedRouteResolution
    {
        public AssistedRouteResolution(
            string worldId,
            int finalCorrect,
            int itemCount,
            bool worldCleared)
        {
            if (string.IsNullOrWhiteSpace(worldId))
                throw new ArgumentException("A world identifier is required.", nameof(worldId));
            if (itemCount != 2)
                throw new ArgumentException("The fresh assisted route has exactly two supported MCQs.", nameof(itemCount));
            if (finalCorrect < 0 || finalCorrect > itemCount)
                throw new ArgumentOutOfRangeException(nameof(finalCorrect));
            if (!worldCleared)
                throw new ArgumentException("Fresh assisted completion always clears the world.", nameof(worldCleared));
            WorldId = worldId;
            FinalCorrect = finalCorrect;
            ItemCount = itemCount;
            WorldCleared = true;
        }

        public string WorldId { get; }

        public int FinalCorrect { get; }

        public int ItemCount { get; }

        public bool WorldCleared { get; }
    }

    public sealed class CampaignController
    {
        private readonly Dictionary<string, WorldDefinition> _worlds;
        private readonly RewardController _rewards;
        private readonly BossGateController _bossGate = new BossGateController();

        public CampaignController(
            IEnumerable<WorldDefinition> worlds,
            ProfileDataV1 profile,
            RewardController rewards)
        {
            if (worlds == null)
                throw new ArgumentNullException(nameof(worlds));
            Profile = profile ?? throw new ArgumentNullException(nameof(profile));
            _rewards = rewards ?? throw new ArgumentNullException(nameof(rewards));
            _worlds = new Dictionary<string, WorldDefinition>(StringComparer.Ordinal);
            foreach (var world in worlds)
            {
                if (world == null || !_worlds.TryAdd(world.Id, world))
                    throw new ArgumentException("World definitions must be non-null and unique.", nameof(worlds));
            }
            if (_worlds.Count == 0 || !_worlds.ContainsKey(Profile.ActiveWorldId))
                throw new ArgumentException("Profile active world must exist in the campaign.", nameof(worlds));
            if (Profile.PendingWorldId != null && !_worlds.ContainsKey(Profile.PendingWorldId))
                throw new ArgumentException("Profile pending world must exist in the campaign.", nameof(worlds));
        }

        public ProfileDataV1 Profile { get; }

        public TrialDescriptor TrialFor(string worldId, string battleId)
        {
            var world = World(worldId);
            var battle = world.Battle(battleId);
            return new TrialDescriptor(world.Id, battle.Id, battle.Tier, battle.QuestionCount);
        }

        public void RecordCombatVictory(string worldId, string battleId)
        {
            var world = World(worldId);
            var battle = world.Battle(battleId);
            Profile.RecordCombatVictory(battle.Id);
            if (battle.Tier == BattleTier.Boss || battle.Tier == BattleTier.CampaignFinale)
                Profile.UnlockWeapon(world.IntroducedWeaponId);
        }

        public RewardGrant CompleteStandardBattle(
            string worldId,
            string battleId,
            TrialPerformance performance)
        {
            var world = World(worldId);
            var battle = world.Battle(battleId);
            if (battle.Tier == BattleTier.Boss || battle.Tier == BattleTier.CampaignFinale)
                throw new InvalidOperationException("Boss trials use the authoritative boss completion path.");
            RequireCombatVictory(battle.Id);
            var grant = _rewards.Grant(Profile, battle, performance);
            Profile.RecordBattleCompleted(battle.Id);
            Profile.ClearPending();
            return grant;
        }

        public BossGateDecision EvaluateBossGate(ServerBossGateState serverState)
        {
            if (serverState == null)
                throw new ArgumentNullException(nameof(serverState));
            return _bossGate.Evaluate(World(serverState.WorldId), Profile, serverState);
        }

        public CampaignStep ApplyBossTrial(BossTrialResolution result)
        {
            if (result == null)
                throw new ArgumentNullException(nameof(result));
            var world = World(result.WorldId);
            var boss = world.Battle(result.BattleId);
            if (boss.Tier != BattleTier.Boss && boss.Tier != BattleTier.CampaignFinale)
                throw new ArgumentException("The result does not target a boss battle.", nameof(result));
            if (result.ItemCount != boss.QuestionCount)
                throw new ArgumentException("Boss result count does not match campaign data.", nameof(result));
            RequireCombatVictory(boss.Id);
            _rewards.Grant(Profile, boss, new TrialPerformance(0, 0, boss.QuestionCount));

            if (result.WorldCleared)
            {
                Profile.RecordBattleCompleted(boss.Id);
                Profile.RecordWorldCleared(world.Id);
                return CampaignStep.WorldCleared;
            }
            if (!result.SealTrialRequired)
                throw new ArgumentException("An uncleared boss result must require a Seal Trial.", nameof(result));
            Profile.SetPending(CampaignStep.SealTrial.ToString(), world.Id);
            return CampaignStep.SealTrial;
        }

        public CampaignStep ApplySealTrial(SealTrialResolution result)
        {
            if (result == null)
                throw new ArgumentNullException(nameof(result));
            RequirePending(CampaignStep.SealTrial, result.WorldId);
            var world = World(result.WorldId);
            RequireCombatVictory(world.BossBattle.Id);
            if (result.WorldCleared)
            {
                Profile.RecordBattleCompleted(world.BossBattle.Id);
                Profile.RecordWorldCleared(world.Id);
                return CampaignStep.WorldCleared;
            }
            var next = result.AssistedRouteUnlocked
                ? CampaignStep.AssistedRoute
                : CampaignStep.SealTrial;
            Profile.SetPending(next.ToString(), world.Id);
            return next;
        }

        public CampaignStep ApplyAssistedRoute(AssistedRouteResolution result)
        {
            if (result == null)
                throw new ArgumentNullException(nameof(result));
            RequirePending(CampaignStep.AssistedRoute, result.WorldId);
            var world = World(result.WorldId);
            RequireCombatVictory(world.BossBattle.Id);
            Profile.RecordBattleCompleted(world.BossBattle.Id);
            Profile.RecordWorldCleared(world.Id);
            return CampaignStep.WorldCleared;
        }

        public bool BossReplayRequired(string worldId)
        {
            var world = World(worldId);
            return !Profile.HasCombatVictory(world.BossBattle.Id);
        }

        public void EquipWeapon(string weaponId)
        {
            Profile.EquipWeapon(weaponId);
        }

        private WorldDefinition World(string worldId)
        {
            if (worldId == null || !_worlds.TryGetValue(worldId, out var world))
                throw new KeyNotFoundException("The world is not part of this campaign.");
            return world;
        }

        private void RequireCombatVictory(string battleId)
        {
            if (!Profile.HasCombatVictory(battleId))
                throw new InvalidOperationException("Combat victory is required before trial completion.");
        }

        private void RequirePending(CampaignStep step, string worldId)
        {
            if (!string.Equals(Profile.PendingStep, step.ToString(), StringComparison.Ordinal) ||
                !string.Equals(Profile.PendingWorldId, worldId, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(
                    "Campaign completion does not match the pending stage and world.");
            }
        }
    }
}
