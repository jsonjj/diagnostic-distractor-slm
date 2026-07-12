using System.Collections;
using NUnit.Framework;
using UnityEngine.TestTools;
using Wayline.Campaign;
using Wayline.Characters;
using Wayline.Save;

namespace Wayline.Tests.Campaign
{
    public sealed class CampaignFlowTests
    {
        [UnityTest]
        public IEnumerator WonBossPersistsThroughSealAndAssistedRecoveryWithoutReplay()
        {
            var world = CreateWorld();
            var controller = CreateController(world);

            CollectionAssert.AreEqual(
                new[] { 3, 4, 4, 5, 8 },
                new[]
                {
                    controller.TrialFor("valuehold", "valuehold-scout").QuestionCount,
                    controller.TrialFor("valuehold", "valuehold-rival").QuestionCount,
                    controller.TrialFor("valuehold", "valuehold-warden").QuestionCount,
                    controller.TrialFor("valuehold", "valuehold-lieutenant").QuestionCount,
                    controller.TrialFor("valuehold", "valuehold-boss").QuestionCount
                });

            foreach (var battle in world.LeadInBattles)
            {
                controller.RecordCombatVictory(world.Id, battle.Id);
                controller.CompleteStandardBattle(
                    world.Id,
                    battle.Id,
                    new TrialPerformance(0, 0, battle.QuestionCount));
            }

            var locked = controller.EvaluateBossGate(new ServerBossGateState(
                "valuehold",
                unlocked: false,
                leadInWins: 4,
                requiredLeadInWins: 4,
                unmetRequirements: new[] { "latest_ten_first_pass" }));
            Assert.That(locked.CanEnter, Is.False);
            Assert.That(controller.Profile.CombatVictoryCount("valuehold"), Is.EqualTo(4));

            var unlocked = controller.EvaluateBossGate(new ServerBossGateState(
                "valuehold",
                unlocked: true,
                leadInWins: 4,
                requiredLeadInWins: 4,
                unmetRequirements: new string[0]));
            Assert.That(unlocked.CanEnter, Is.True);

            controller.RecordCombatVictory("valuehold", "valuehold-boss");
            Assert.That(controller.Profile.IsWeaponUnlocked("folding-lance"), Is.True);
            controller.EquipWeapon("folding-lance");
            Assert.That(controller.Profile.EquippedWeaponId, Is.EqualTo("folding-lance"));

            var bossMiss = controller.ApplyBossTrial(new BossTrialResolution(
                "valuehold",
                "valuehold-boss",
                finalCorrect: 5,
                itemCount: 8,
                worldCleared: false,
                sealTrialRequired: true));
            Assert.That(bossMiss, Is.EqualTo(CampaignStep.SealTrial));
            Assert.That(controller.Profile.HasCombatVictory("valuehold-boss"), Is.True);

            var firstSeal = controller.ApplySealTrial(new SealTrialResolution(
                "valuehold",
                attemptNumber: 1,
                passed: false,
                worldCleared: false,
                assistedRouteUnlocked: false));
            var secondSeal = controller.ApplySealTrial(new SealTrialResolution(
                "valuehold",
                attemptNumber: 2,
                passed: false,
                worldCleared: false,
                assistedRouteUnlocked: true));
            Assert.That(firstSeal, Is.EqualTo(CampaignStep.SealTrial));
            Assert.That(secondSeal, Is.EqualTo(CampaignStep.AssistedRoute));
            Assert.That(controller.Profile.HasCombatVictory("valuehold-boss"), Is.True);

            var assisted = controller.ApplyAssistedRoute(new AssistedRouteResolution(
                "valuehold",
                finalCorrect: 0,
                itemCount: 2,
                worldCleared: true));
            Assert.That(assisted, Is.EqualTo(CampaignStep.WorldCleared));
            Assert.That(controller.Profile.IsWorldCleared("valuehold"), Is.True);
            Assert.That(controller.BossReplayRequired("valuehold"), Is.False);

            yield return null;
        }

        [UnityTest]
        public IEnumerator SixOfEightServerClearCompletesWorldDirectly()
        {
            var world = CreateWorld();
            var controller = CreateController(world);
            foreach (var battle in world.LeadInBattles)
            {
                controller.RecordCombatVictory(world.Id, battle.Id);
                controller.CompleteStandardBattle(
                    world.Id,
                    battle.Id,
                    new TrialPerformance(0, 0, battle.QuestionCount));
            }
            controller.RecordCombatVictory("valuehold", "valuehold-boss");

            var next = controller.ApplyBossTrial(new BossTrialResolution(
                "valuehold",
                "valuehold-boss",
                finalCorrect: 6,
                itemCount: 8,
                worldCleared: true,
                sealTrialRequired: false));

            Assert.That(next, Is.EqualTo(CampaignStep.WorldCleared));
            Assert.That(controller.Profile.IsWorldCleared("valuehold"), Is.True);
            yield return null;
        }

        private static CampaignController CreateController(WorldDefinition world)
        {
            var appearance = new HeroAppearanceSelection(
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
            return new CampaignController(
                new[] { world },
                profile,
                new RewardController(maxFocusPerTrial: 3));
        }

        private static WorldDefinition CreateWorld()
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
                new[]
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
    }
}
