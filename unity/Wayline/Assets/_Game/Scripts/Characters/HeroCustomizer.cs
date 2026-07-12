using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;

namespace Wayline.Characters
{
    public sealed class HeroCustomizer
    {
        public const string SharedRoutekeeperRigId = "routekeeper-shared-rig-v1";

        private readonly Dictionary<string, HeroAppearanceDefinition> _appearances;
        private readonly HashSet<string> _hairIds;
        private readonly HashSet<string> _mantleIds;
        private readonly HashSet<string> _dyeIds;
        private readonly HashSet<string> _inlayIds;

        public HeroCustomizer(
            IEnumerable<HeroAppearanceDefinition> appearances,
            IEnumerable<string> hairIds,
            IEnumerable<string> mantleIds,
            IEnumerable<string> dyeIds,
            IEnumerable<string> inlayIds)
        {
            _appearances = UniqueDefinitions(appearances);
            if (_appearances.Count != 4)
                throw new ArgumentException("Wayline requires exactly four authored appearances.", nameof(appearances));
            _hairIds = IdentifierSet(hairIds, nameof(hairIds));
            _mantleIds = IdentifierSet(mantleIds, nameof(mantleIds));
            _dyeIds = IdentifierSet(dyeIds, nameof(dyeIds));
            _inlayIds = IdentifierSet(inlayIds, nameof(inlayIds));
            foreach (var appearance in _appearances.Values)
            {
                if (!_hairIds.Contains(appearance.DefaultHairId) ||
                    !_mantleIds.Contains(appearance.DefaultMantleId))
                {
                    throw new ArgumentException(
                        "Every authored appearance must use allowlisted default modules.",
                        nameof(appearances));
                }
            }

            AvailableAppearances = new ReadOnlyCollection<HeroAppearanceDefinition>(
                _appearances.Values.OrderBy(item => item.Id, StringComparer.Ordinal).ToArray());
        }

        public string SharedRigId => SharedRoutekeeperRigId;

        public IReadOnlyList<HeroAppearanceDefinition> AvailableAppearances { get; }

        public HeroAppearanceSelection CreateSelection(
            string appearanceId,
            string hairId,
            string mantleId,
            string primaryDyeId,
            string secondaryDyeId,
            string inlayColorId)
        {
            if (!_appearances.ContainsKey(appearanceId))
                throw new ArgumentException("The appearance is not authored.", nameof(appearanceId));
            RequireAllowed(_hairIds, hairId, nameof(hairId));
            RequireAllowed(_mantleIds, mantleId, nameof(mantleId));
            RequireAllowed(_dyeIds, primaryDyeId, nameof(primaryDyeId));
            RequireAllowed(_dyeIds, secondaryDyeId, nameof(secondaryDyeId));
            RequireAllowed(_inlayIds, inlayColorId, nameof(inlayColorId));
            return new HeroAppearanceSelection(
                appearanceId,
                hairId,
                mantleId,
                primaryDyeId,
                secondaryDyeId,
                inlayColorId);
        }

        private static Dictionary<string, HeroAppearanceDefinition> UniqueDefinitions(
            IEnumerable<HeroAppearanceDefinition> definitions)
        {
            if (definitions == null)
                throw new ArgumentNullException(nameof(definitions));
            var result = new Dictionary<string, HeroAppearanceDefinition>(StringComparer.Ordinal);
            foreach (var definition in definitions)
            {
                if (definition == null || !result.TryAdd(definition.Id, definition))
                    throw new ArgumentException("Appearance definitions must be non-null and unique.", nameof(definitions));
            }
            return result;
        }

        private static HashSet<string> IdentifierSet(IEnumerable<string> values, string parameter)
        {
            if (values == null)
                throw new ArgumentNullException(parameter);
            var result = new HashSet<string>(StringComparer.Ordinal);
            foreach (var value in values)
            {
                if (string.IsNullOrWhiteSpace(value) || !result.Add(value))
                    throw new ArgumentException("Module identifiers must be nonempty and unique.", parameter);
            }
            if (result.Count == 0)
                throw new ArgumentException("At least one module identifier is required.", parameter);
            return result;
        }

        private static void RequireAllowed(HashSet<string> allowed, string value, string parameter)
        {
            if (value == null || !allowed.Contains(value))
                throw new ArgumentException("The cosmetic module is not allowlisted.", parameter);
        }
    }
}
