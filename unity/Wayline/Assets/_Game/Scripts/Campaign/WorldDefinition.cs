using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.RegularExpressions;

namespace Wayline.Campaign
{
    public sealed class WorldDefinition
    {
        private static readonly BattleTier[] RequiredTiers =
        {
            BattleTier.Scout,
            BattleTier.Rival,
            BattleTier.Warden,
            BattleTier.Lieutenant,
            BattleTier.Boss
        };

        private static readonly Regex ColorPattern = new Regex(
            "^#[0-9A-Fa-f]{6}$",
            RegexOptions.CultureInvariant);

        public WorldDefinition(
            string id,
            string displayName,
            IEnumerable<string> launchSkillIds,
            string arenaId,
            string factionId,
            string bossId,
            string introducedWeaponId,
            string routeColorHex,
            IEnumerable<BattleDefinition> battles)
        {
            Id = Require(id, nameof(id));
            DisplayName = Require(displayName, nameof(displayName));
            ArenaId = Require(arenaId, nameof(arenaId));
            FactionId = Require(factionId, nameof(factionId));
            BossId = Require(bossId, nameof(bossId));
            IntroducedWeaponId = Require(introducedWeaponId, nameof(introducedWeaponId));
            if (routeColorHex == null || !ColorPattern.IsMatch(routeColorHex))
                throw new ArgumentException("Route color must be a six-digit hex color.", nameof(routeColorHex));
            RouteColorHex = routeColorHex.ToUpperInvariant();
            LaunchSkillIds = UniqueStrings(launchSkillIds, nameof(launchSkillIds));
            if (LaunchSkillIds.Count == 0)
                throw new ArgumentException("At least one launch skill is required.", nameof(launchSkillIds));

            if (battles == null)
                throw new ArgumentNullException(nameof(battles));
            var battleArray = battles.ToArray();
            if (battleArray.Length != RequiredTiers.Length)
                throw new ArgumentException("A world requires exactly five battles.", nameof(battles));
            if (battleArray.Any(item => item == null))
                throw new ArgumentException("Battle definitions cannot be null.", nameof(battles));
            if (!battleArray.Select(item => item.Tier).SequenceEqual(RequiredTiers))
                throw new ArgumentException("World battle tiers must follow 3/4/4/5/8 cadence.", nameof(battles));
            if (battleArray.Select(item => item.Id).Distinct(StringComparer.Ordinal).Count() != battleArray.Length)
                throw new ArgumentException("Battle identifiers must be unique within a world.", nameof(battles));
            if (battleArray.Any(item =>
                    !item.Id.StartsWith(Id + "-", StringComparison.Ordinal) &&
                    !item.Id.StartsWith(Id + "_", StringComparison.Ordinal)))
            {
                throw new ArgumentException("Battle identifiers must be owned by their world.", nameof(battles));
            }

            Battles = new ReadOnlyCollection<BattleDefinition>(battleArray);
            LeadInBattles = new ReadOnlyCollection<BattleDefinition>(battleArray.Take(4).ToArray());
            BossBattle = battleArray[4];
        }

        public string Id { get; }

        public string DisplayName { get; }

        public IReadOnlyList<string> LaunchSkillIds { get; }

        public string ArenaId { get; }

        public string FactionId { get; }

        public string BossId { get; }

        public string IntroducedWeaponId { get; }

        public string RouteColorHex { get; }

        public IReadOnlyList<BattleDefinition> Battles { get; }

        public IReadOnlyList<BattleDefinition> LeadInBattles { get; }

        public BattleDefinition BossBattle { get; }

        public BattleDefinition Battle(string battleId)
        {
            var result = Battles.FirstOrDefault(item =>
                string.Equals(item.Id, battleId, StringComparison.Ordinal));
            if (result == null)
                throw new KeyNotFoundException("The battle does not belong to this world.");
            return result;
        }

        private static IReadOnlyList<string> UniqueStrings(
            IEnumerable<string> values,
            string parameter)
        {
            if (values == null)
                throw new ArgumentNullException(parameter);
            var result = values.Select(value => Require(value, parameter)).ToArray();
            if (result.Distinct(StringComparer.Ordinal).Count() != result.Length)
                throw new ArgumentException("Identifiers must be unique.", parameter);
            return new ReadOnlyCollection<string>(result);
        }

        private static string Require(string value, string parameter)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("A world value is required.", parameter);
            return value;
        }
    }
}
