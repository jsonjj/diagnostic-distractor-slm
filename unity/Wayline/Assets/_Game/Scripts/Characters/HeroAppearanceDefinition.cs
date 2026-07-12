using System;
using Newtonsoft.Json;

namespace Wayline.Characters
{
    public sealed class HeroAppearanceDefinition
    {
        public HeroAppearanceDefinition(
            string id,
            string faceId,
            string skinToneId,
            string defaultHairId,
            string defaultMantleId)
        {
            Id = Require(id, nameof(id));
            FaceId = Require(faceId, nameof(faceId));
            SkinToneId = Require(skinToneId, nameof(skinToneId));
            DefaultHairId = Require(defaultHairId, nameof(defaultHairId));
            DefaultMantleId = Require(defaultMantleId, nameof(defaultMantleId));
        }

        public string Id { get; }

        public string FaceId { get; }

        public string SkinToneId { get; }

        public string DefaultHairId { get; }

        public string DefaultMantleId { get; }

        private static string Require(string value, string parameter)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("A module identifier is required.", parameter);
            return value;
        }
    }

    [JsonObject(MemberSerialization.OptIn)]
    public sealed class HeroAppearanceSelection
    {
        [JsonConstructor]
        public HeroAppearanceSelection(
            string appearanceId,
            string hairId,
            string mantleId,
            string primaryDyeId,
            string secondaryDyeId,
            string inlayColorId)
        {
            AppearanceId = Require(appearanceId, nameof(appearanceId));
            HairId = Require(hairId, nameof(hairId));
            MantleId = Require(mantleId, nameof(mantleId));
            PrimaryDyeId = Require(primaryDyeId, nameof(primaryDyeId));
            SecondaryDyeId = Require(secondaryDyeId, nameof(secondaryDyeId));
            InlayColorId = Require(inlayColorId, nameof(inlayColorId));
        }

        [JsonProperty("appearanceId", Required = Required.Always)]
        public string AppearanceId { get; }

        [JsonProperty("hairId", Required = Required.Always)]
        public string HairId { get; }

        [JsonProperty("mantleId", Required = Required.Always)]
        public string MantleId { get; }

        [JsonProperty("primaryDyeId", Required = Required.Always)]
        public string PrimaryDyeId { get; }

        [JsonProperty("secondaryDyeId", Required = Required.Always)]
        public string SecondaryDyeId { get; }

        [JsonProperty("inlayColorId", Required = Required.Always)]
        public string InlayColorId { get; }

        private static string Require(string value, string parameter)
        {
            if (string.IsNullOrWhiteSpace(value))
                throw new ArgumentException("A cosmetic module identifier is required.", parameter);
            return value;
        }
    }
}
