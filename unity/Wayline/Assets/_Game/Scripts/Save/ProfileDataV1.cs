using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Wayline.Characters;

namespace Wayline.Save
{
    [JsonObject(MemberSerialization.OptIn)]
    public sealed class ProfileDataV1
    {
        public const int CurrentSchemaVersion = 1;
        private const string SealTrialPendingStep = "SealTrial";
        private const string AssistedRoutePendingStep = "AssistedRoute";

        [JsonConstructor]
        public ProfileDataV1(
            int schemaVersion,
            string sidecarProfileId,
            string activeWorldId,
            int campaignOrdinal,
            int routeMarks,
            int focus,
            string equippedWeaponId,
            HeroAppearanceSelection heroAppearance,
            string[] combatVictoryBattleIds,
            string[] completedBattleIds,
            string[] clearedWorldIds,
            string[] unlockedWeaponIds,
            string[] rewardedBattleIds,
            string pendingStep,
            string pendingWorldId)
        {
            SchemaVersion = schemaVersion;
            SidecarProfileId = Require(sidecarProfileId, nameof(sidecarProfileId));
            ActiveWorldId = Require(activeWorldId, nameof(activeWorldId));
            CampaignOrdinal = campaignOrdinal;
            RouteMarks = routeMarks;
            Focus = focus;
            EquippedWeaponId = Require(equippedWeaponId, nameof(equippedWeaponId));
            HeroAppearance = heroAppearance ?? throw new ArgumentNullException(nameof(heroAppearance));
            CombatVictoryBattleIds = CanonicalIds(combatVictoryBattleIds, nameof(combatVictoryBattleIds));
            CompletedBattleIds = CanonicalIds(completedBattleIds, nameof(completedBattleIds));
            ClearedWorldIds = CanonicalIds(clearedWorldIds, nameof(clearedWorldIds));
            UnlockedWeaponIds = CanonicalIds(unlockedWeaponIds, nameof(unlockedWeaponIds));
            RewardedBattleIds = CanonicalIds(rewardedBattleIds, nameof(rewardedBattleIds));
            PendingStep = NormalizeOptional(pendingStep);
            PendingWorldId = NormalizeOptional(pendingWorldId);
            Validate();
        }

        [JsonProperty("schemaVersion", Required = Required.Always, Order = 1)]
        public int SchemaVersion { get; private set; }

        [JsonProperty("sidecarProfileId", Required = Required.Always, Order = 2)]
        public string SidecarProfileId { get; private set; }

        [JsonProperty("activeWorldId", Required = Required.Always, Order = 3)]
        public string ActiveWorldId { get; private set; }

        [JsonProperty("campaignOrdinal", Required = Required.Always, Order = 4)]
        public int CampaignOrdinal { get; private set; }

        [JsonProperty("routeMarks", Required = Required.Always, Order = 5)]
        public int RouteMarks { get; private set; }

        [JsonProperty("focus", Required = Required.Always, Order = 6)]
        public int Focus { get; private set; }

        [JsonProperty("equippedWeaponId", Required = Required.Always, Order = 7)]
        public string EquippedWeaponId { get; private set; }

        [JsonProperty("heroAppearance", Required = Required.Always, Order = 8)]
        public HeroAppearanceSelection HeroAppearance { get; private set; }

        [JsonProperty("combatVictoryBattleIds", Required = Required.Always, Order = 9)]
        public string[] CombatVictoryBattleIds { get; private set; }

        [JsonProperty("completedBattleIds", Required = Required.Always, Order = 10)]
        public string[] CompletedBattleIds { get; private set; }

        [JsonProperty("clearedWorldIds", Required = Required.Always, Order = 11)]
        public string[] ClearedWorldIds { get; private set; }

        [JsonProperty("unlockedWeaponIds", Required = Required.Always, Order = 12)]
        public string[] UnlockedWeaponIds { get; private set; }

        [JsonProperty("rewardedBattleIds", Required = Required.Always, Order = 13)]
        public string[] RewardedBattleIds { get; private set; }

        [JsonProperty("pendingStep", Required = Required.AllowNull, Order = 14)]
        public string PendingStep { get; private set; }

        [JsonProperty("pendingWorldId", Required = Required.AllowNull, Order = 15)]
        public string PendingWorldId { get; private set; }

        public static ProfileDataV1 CreateNew(
            string sidecarProfileId,
            string activeWorldId,
            string startingWeaponId,
            HeroAppearanceSelection appearance)
        {
            return new ProfileDataV1(
                CurrentSchemaVersion,
                sidecarProfileId,
                activeWorldId,
                campaignOrdinal: 0,
                routeMarks: 0,
                focus: 0,
                equippedWeaponId: startingWeaponId,
                heroAppearance: appearance,
                combatVictoryBattleIds: Array.Empty<string>(),
                completedBattleIds: Array.Empty<string>(),
                clearedWorldIds: Array.Empty<string>(),
                unlockedWeaponIds: new[] { startingWeaponId },
                rewardedBattleIds: Array.Empty<string>(),
                pendingStep: null,
                pendingWorldId: null);
        }

        public void Validate()
        {
            if (SchemaVersion != CurrentSchemaVersion)
                throw new InvalidOperationException("Unsupported profile schema version.");
            if (CampaignOrdinal < 0 || RouteMarks < 0 || Focus < 0)
                throw new InvalidOperationException("Profile counters cannot be negative.");
            Require(SidecarProfileId, nameof(SidecarProfileId));
            Require(ActiveWorldId, nameof(ActiveWorldId));
            Require(EquippedWeaponId, nameof(EquippedWeaponId));
            if (HeroAppearance == null)
                throw new InvalidOperationException("A hero appearance is required.");
            CombatVictoryBattleIds = CanonicalIds(CombatVictoryBattleIds, nameof(CombatVictoryBattleIds));
            CompletedBattleIds = CanonicalIds(CompletedBattleIds, nameof(CompletedBattleIds));
            ClearedWorldIds = CanonicalIds(ClearedWorldIds, nameof(ClearedWorldIds));
            UnlockedWeaponIds = CanonicalIds(UnlockedWeaponIds, nameof(UnlockedWeaponIds));
            RewardedBattleIds = CanonicalIds(RewardedBattleIds, nameof(RewardedBattleIds));
            if (!IsWeaponUnlocked(EquippedWeaponId))
                throw new InvalidOperationException("The equipped weapon must be unlocked.");
            if ((PendingStep == null) != (PendingWorldId == null))
                throw new InvalidOperationException("Pending campaign state must be complete or absent.");
            if (PendingStep != null && !IsSupportedPendingStep(PendingStep))
                throw new InvalidOperationException("Pending campaign step is unsupported.");
        }

        public void RecordCombatVictory(string battleId)
        {
            CombatVictoryBattleIds = AddUnique(CombatVictoryBattleIds, battleId);
        }

        public bool HasCombatVictory(string battleId)
        {
            return Contains(CombatVictoryBattleIds, battleId);
        }

        public int CombatVictoryCount(string worldId)
        {
            Require(worldId, nameof(worldId));
            return CombatVictoryBattleIds.Count(id =>
                id.StartsWith(worldId + "-", StringComparison.Ordinal) ||
                id.StartsWith(worldId + "_", StringComparison.Ordinal));
        }

        public void RecordBattleCompleted(string battleId)
        {
            CompletedBattleIds = AddUnique(CompletedBattleIds, battleId);
        }

        public bool IsBattleCompleted(string battleId)
        {
            return Contains(CompletedBattleIds, battleId);
        }

        public void RecordWorldCleared(string worldId)
        {
            ClearedWorldIds = AddUnique(ClearedWorldIds, worldId);
            PendingStep = null;
            PendingWorldId = null;
        }

        public bool IsWorldCleared(string worldId)
        {
            return Contains(ClearedWorldIds, worldId);
        }

        /// <summary>
        /// Advances the local campaign to a known next world. World membership
        /// is validated by CampaignController/session coherence before this
        /// mutation is persisted.
        /// </summary>
        public void ActivateWorld(string worldId)
        {
            worldId = Require(worldId, nameof(worldId));
            if (string.Equals(ActiveWorldId, worldId, StringComparison.Ordinal))
                return;
            ActiveWorldId = worldId;
            CampaignOrdinal = checked(CampaignOrdinal + 1);
            ClearPending();
        }

        public void UnlockWeapon(string weaponId)
        {
            UnlockedWeaponIds = AddUnique(UnlockedWeaponIds, weaponId);
        }

        public bool IsWeaponUnlocked(string weaponId)
        {
            return Contains(UnlockedWeaponIds, weaponId);
        }

        public void EquipWeapon(string weaponId)
        {
            if (!IsWeaponUnlocked(weaponId))
                throw new InvalidOperationException("Only an unlocked weapon can be equipped.");
            EquippedWeaponId = weaponId;
        }

        public void ApplyReward(int routeMarks, int focus)
        {
            if (routeMarks < 0)
                throw new ArgumentOutOfRangeException(nameof(routeMarks));
            if (focus < 0)
                throw new ArgumentOutOfRangeException(nameof(focus));
            RouteMarks = checked(RouteMarks + routeMarks);
            Focus = checked(Focus + focus);
        }

        public bool HasRewardedBattle(string battleId)
        {
            return Contains(RewardedBattleIds, battleId);
        }

        public void MarkBattleRewarded(string battleId)
        {
            RewardedBattleIds = AddUnique(RewardedBattleIds, battleId);
        }

        public void SetPending(string step, string worldId)
        {
            step = Require(step, nameof(step));
            if (!IsSupportedPendingStep(step))
                throw new ArgumentException("Pending campaign step is unsupported.", nameof(step));
            worldId = Require(worldId, nameof(worldId));
            PendingStep = step;
            PendingWorldId = worldId;
        }

        public void ClearPending()
        {
            PendingStep = null;
            PendingWorldId = null;
        }

        private static string[] AddUnique(string[] values, string value)
        {
            value = Require(value, nameof(value));
            if (Contains(values, value))
                return values;
            return values.Concat(new[] { value })
                .OrderBy(item => item, StringComparer.Ordinal)
                .ToArray();
        }

        private static bool Contains(IEnumerable<string> values, string value)
        {
            return value != null && values.Contains(value, StringComparer.Ordinal);
        }

        private static string[] CanonicalIds(IEnumerable<string> values, string parameter)
        {
            if (values == null)
                throw new ArgumentNullException(parameter);
            var result = values.Select(value => Require(value, parameter))
                .OrderBy(value => value, StringComparer.Ordinal)
                .ToArray();
            if (result.Distinct(StringComparer.Ordinal).Count() != result.Length)
                throw new ArgumentException("Profile identifier collections must be unique.", parameter);
            return result;
        }

        private static string NormalizeOptional(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? null : value;
        }

        private static bool IsSupportedPendingStep(string value)
        {
            return string.Equals(value, SealTrialPendingStep, StringComparison.Ordinal) ||
                   string.Equals(value, AssistedRoutePendingStep, StringComparison.Ordinal);
        }

        private static string Require(string value, string parameter)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("A nonempty identifier is required.", parameter);
            return value;
        }
    }
}
