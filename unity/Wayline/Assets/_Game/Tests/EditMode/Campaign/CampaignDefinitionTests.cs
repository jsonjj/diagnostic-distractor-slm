using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using NUnit.Framework;
using Wayline.Campaign;
using Wayline.Characters;
using Wayline.Save;

namespace Wayline.Tests.Campaign
{
    public sealed class CampaignDefinitionTests
    {
        [Test]
        public void FiveBattleWorldUsesTheLockedQuestionCadence()
        {
            var world = Fixtures.Valuehold();

            Assert.That(world.Battles.Count, Is.EqualTo(5));
            CollectionAssert.AreEqual(
                new[] { 3, 4, 4, 5, 8 },
                Array.ConvertAll(world.Battles.ToArray(), battle => battle.QuestionCount));
            CollectionAssert.AreEqual(
                new[]
                {
                    BattleTier.Scout,
                    BattleTier.Rival,
                    BattleTier.Warden,
                    BattleTier.Lieutenant,
                    BattleTier.Boss
                },
                Array.ConvertAll(world.Battles.ToArray(), battle => battle.Tier));
        }

        [Test]
        public void InvalidBattleCadenceIsRejectedAtTheDataBoundary()
        {
            var battles = new List<BattleDefinition>(Fixtures.Valuehold().Battles);
            battles[3] = new BattleDefinition(
                "valuehold-extra-rival",
                BattleTier.Rival,
                "ai-rival",
                "opponent-rival",
                12,
                "card-rival");

            Assert.Throws<ArgumentException>(() => Fixtures.Valuehold(battles));
        }

        [Test]
        public void SecondWindAlwaysRevivesAndOnlyCorrectAnswersAddCappedShield()
        {
            var controller = new SecondWindController();

            Assert.That(controller.Choices.Count, Is.EqualTo(2));
            Assert.That(controller.Choices[0].VisualWeight,
                Is.EqualTo(controller.Choices[1].VisualWeight));
            Assert.That(controller.Choices[0].Kind, Is.EqualTo(KnockoutChoiceKind.RetryNow));
            Assert.That(controller.Choices[1].Kind, Is.EqualTo(KnockoutChoiceKind.SecondWind));

            var noCorrect = controller.Resolve(finalCorrect: 0, itemCount: 3);
            var allCorrect = controller.Resolve(finalCorrect: 3, itemCount: 3);

            Assert.That(noCorrect.ReviveHealthPercent, Is.EqualTo(35));
            Assert.That(noCorrect.ShieldPercent, Is.Zero);
            Assert.That(allCorrect.ReviveHealthPercent, Is.EqualTo(35));
            Assert.That(allCorrect.ShieldPercent, Is.EqualTo(15));
            Assert.Throws<ArgumentOutOfRangeException>(() =>
                controller.Resolve(finalCorrect: 4, itemCount: 3));
            Assert.Throws<ArgumentException>(() =>
                controller.Resolve(finalCorrect: 2, itemCount: 4));
        }

        [Test]
        public void RewardsNeverSubtractForMistakesAndFocusIsCapped()
        {
            var appearance = Fixtures.AppearanceCatalog().CreateSelection(
                "routekeeper-amber",
                "hair-braid",
                "mantle-scout",
                "dye-lapis",
                "dye-oxide",
                "inlay-gold");
            var profile = ProfileDataV1.CreateNew(
                "profile-local-001",
                "valuehold",
                "splitstaff",
                appearance);
            var rewards = new RewardController(maxFocusPerTrial: 3);
            var first = Fixtures.Valuehold().Battles[0];
            var second = Fixtures.Valuehold().Battles[1];

            var strong = rewards.Grant(
                profile,
                first,
                new TrialPerformance(firstPassCorrect: 2, selfCorrected: 1, itemCount: 3));
            var mistakes = rewards.Grant(
                profile,
                second,
                new TrialPerformance(firstPassCorrect: 0, selfCorrected: 0, itemCount: 4));

            Assert.That(strong.RouteMarks, Is.EqualTo(first.BaseRouteMarks));
            Assert.That(strong.Focus, Is.EqualTo(3));
            Assert.That(mistakes.RouteMarks, Is.EqualTo(second.BaseRouteMarks));
            Assert.That(mistakes.Focus, Is.Zero);
            Assert.That(profile.RouteMarks,
                Is.EqualTo(first.BaseRouteMarks + second.BaseRouteMarks));
            Assert.That(profile.Focus, Is.EqualTo(3));
        }

        [Test]
        public void FourAuthoredAppearancesShareOneRigAndRejectUnknownModules()
        {
            var customizer = Fixtures.AppearanceCatalog();

            Assert.That(customizer.AvailableAppearances.Count, Is.EqualTo(4));
            Assert.That(customizer.SharedRigId, Is.EqualTo("routekeeper-shared-rig-v1"));
            var selection = customizer.CreateSelection(
                "routekeeper-amber",
                "hair-braid",
                "mantle-scout",
                "dye-lapis",
                "dye-oxide",
                "inlay-gold");

            Assert.That(selection.AppearanceId, Is.EqualTo("routekeeper-amber"));
            Assert.That(selection.HairId, Is.EqualTo("hair-braid"));
            Assert.Throws<ArgumentException>(() => customizer.CreateSelection(
                "routekeeper-amber",
                "hair-unknown",
                "mantle-scout",
                "dye-lapis",
                "dye-oxide",
                "inlay-gold"));
        }

        [Test]
        public void RestoredPendingWorldMustExistInCampaignCatalog()
        {
            var profile = CreateProfile();
            profile.SetPending(CampaignStep.SealTrial.ToString(), "missing-world");
            var before = JsonConvert.SerializeObject(profile);

            Assert.Throws<ArgumentException>(() => new CampaignController(
                new[] { Fixtures.Valuehold() },
                profile,
                new RewardController(maxFocusPerTrial: 3)));
            Assert.That(JsonConvert.SerializeObject(profile), Is.EqualTo(before));
        }

        [Test]
        public void SealTrialCannotResolveWithoutItsMatchingPendingStage()
        {
            var world = Fixtures.Valuehold();
            var profile = CreateProfile();
            var controller = CreateController(profile, world);
            controller.RecordCombatVictory(world.Id, world.BossBattle.Id);
            var before = JsonConvert.SerializeObject(profile);

            Assert.Throws<InvalidOperationException>(() => controller.ApplySealTrial(
                new SealTrialResolution(
                    world.Id,
                    attemptNumber: 1,
                    passed: false,
                    worldCleared: false,
                    assistedRouteUnlocked: false)));
            Assert.That(JsonConvert.SerializeObject(profile), Is.EqualTo(before));
        }

        [Test]
        public void SealTrialCannotResolveAnotherPendingWorld()
        {
            var valuehold = Fixtures.Valuehold();
            var frontier = Fixtures.FractionFrontier();
            var profile = CreateProfile();
            var controller = CreateController(profile, valuehold, frontier);
            controller.RecordCombatVictory(frontier.Id, frontier.BossBattle.Id);
            profile.SetPending(CampaignStep.SealTrial.ToString(), valuehold.Id);
            var before = JsonConvert.SerializeObject(profile);

            Assert.Throws<InvalidOperationException>(() => controller.ApplySealTrial(
                new SealTrialResolution(
                    frontier.Id,
                    attemptNumber: 1,
                    passed: false,
                    worldCleared: false,
                    assistedRouteUnlocked: false)));
            Assert.That(JsonConvert.SerializeObject(profile), Is.EqualTo(before));
        }

        [Test]
        public void AssistedRouteCannotResolveWithoutItsMatchingPendingStage()
        {
            var world = Fixtures.Valuehold();
            var profile = CreateProfile();
            var controller = CreateController(profile, world);
            controller.RecordCombatVictory(world.Id, world.BossBattle.Id);
            var before = JsonConvert.SerializeObject(profile);

            Assert.Throws<InvalidOperationException>(() => controller.ApplyAssistedRoute(
                new AssistedRouteResolution(
                    world.Id,
                    finalCorrect: 0,
                    itemCount: 2,
                    worldCleared: true)));
            Assert.That(JsonConvert.SerializeObject(profile), Is.EqualTo(before));
        }

        [Test]
        public void AssistedRouteCannotResolveAnotherPendingWorld()
        {
            var valuehold = Fixtures.Valuehold();
            var frontier = Fixtures.FractionFrontier();
            var profile = CreateProfile();
            var controller = CreateController(profile, valuehold, frontier);
            controller.RecordCombatVictory(frontier.Id, frontier.BossBattle.Id);
            profile.SetPending(CampaignStep.AssistedRoute.ToString(), valuehold.Id);
            var before = JsonConvert.SerializeObject(profile);

            Assert.Throws<InvalidOperationException>(() => controller.ApplyAssistedRoute(
                new AssistedRouteResolution(
                    frontier.Id,
                    finalCorrect: 0,
                    itemCount: 2,
                    worldCleared: true)));
            Assert.That(JsonConvert.SerializeObject(profile), Is.EqualTo(before));
        }

        private static CampaignController CreateController(
            ProfileDataV1 profile,
            params WorldDefinition[] worlds)
        {
            return new CampaignController(
                worlds,
                profile,
                new RewardController(maxFocusPerTrial: 3));
        }

        private static ProfileDataV1 CreateProfile()
        {
            var appearance = Fixtures.AppearanceCatalog().CreateSelection(
                "routekeeper-amber",
                "hair-braid",
                "mantle-scout",
                "dye-lapis",
                "dye-oxide",
                "inlay-gold");
            return ProfileDataV1.CreateNew(
                "profile-local-001",
                "valuehold",
                "splitstaff",
                appearance);
        }

        internal static class Fixtures
        {
            public static WorldDefinition Valuehold(IReadOnlyList<BattleDefinition> battles = null)
            {
                return new WorldDefinition(
                    "valuehold",
                    "Valuehold Reach",
                    new[] { "place_value", "mental_add_sub" },
                    "arena-valuehold",
                    "surveyors",
                    "surveyor-general",
                    "folding-lance",
                    "#E6AF3B",
                    battles ?? new[]
                    {
                        new BattleDefinition("valuehold-scout", BattleTier.Scout,
                            "ai-scout", "scout", 10, "card-scout"),
                        new BattleDefinition("valuehold-rival", BattleTier.Rival,
                            "ai-rival", "rival", 12, "card-rival"),
                        new BattleDefinition("valuehold-warden", BattleTier.Warden,
                            "ai-warden", "warden", 14, "card-warden"),
                        new BattleDefinition("valuehold-lieutenant", BattleTier.Lieutenant,
                            "ai-lieutenant", "lieutenant", 16, "card-lieutenant"),
                        new BattleDefinition("valuehold-boss", BattleTier.Boss,
                            "ai-boss", "surveyor-general", 25, "card-boss")
                    });
            }

            public static WorldDefinition FractionFrontier()
            {
                return new WorldDefinition(
                    "fraction-frontier",
                    "Fraction Frontier",
                    new[] { "fraction_equivalence", "fraction_operations" },
                    "arena-fraction-frontier",
                    "dividers",
                    "divider-general",
                    "ratio-blade",
                    "#2D7F83",
                    new[]
                    {
                        new BattleDefinition("fraction-frontier-scout", BattleTier.Scout,
                            "ai-scout", "scout", 10, "card-scout"),
                        new BattleDefinition("fraction-frontier-rival", BattleTier.Rival,
                            "ai-rival", "rival", 12, "card-rival"),
                        new BattleDefinition("fraction-frontier-warden", BattleTier.Warden,
                            "ai-warden", "warden", 14, "card-warden"),
                        new BattleDefinition("fraction-frontier-lieutenant", BattleTier.Lieutenant,
                            "ai-lieutenant", "lieutenant", 16, "card-lieutenant"),
                        new BattleDefinition("fraction-frontier-boss", BattleTier.Boss,
                            "ai-boss", "divider-general", 25, "card-boss")
                    });
            }

            public static HeroCustomizer AppearanceCatalog()
            {
                return new HeroCustomizer(
                    new[]
                    {
                        new HeroAppearanceDefinition("routekeeper-amber", "face-amber",
                            "skin-deep", "hair-braid", "mantle-scout"),
                        new HeroAppearanceDefinition("routekeeper-cobalt", "face-cobalt",
                            "skin-light", "hair-crop", "mantle-scout"),
                        new HeroAppearanceDefinition("routekeeper-verdant", "face-verdant",
                            "skin-medium", "hair-coil", "mantle-ranger"),
                        new HeroAppearanceDefinition("routekeeper-oxide", "face-oxide",
                            "skin-warm", "hair-wave", "mantle-ranger")
                    },
                    new[] { "hair-braid", "hair-crop", "hair-coil", "hair-wave" },
                    new[] { "mantle-scout", "mantle-ranger" },
                    new[] { "dye-lapis", "dye-oxide", "dye-teal", "dye-limestone" },
                    new[] { "inlay-gold", "inlay-teal", "inlay-oxide" });
            }
        }
    }
}
