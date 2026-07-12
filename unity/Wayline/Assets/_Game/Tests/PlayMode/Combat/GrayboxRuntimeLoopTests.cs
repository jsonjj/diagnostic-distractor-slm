using System.Collections;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Wayline.Combat.Simulation;
using Wayline.Gameplay;

namespace Wayline.Tests.Combat
{
    public sealed class GrayboxRuntimeLoopTests
    {
        [UnityTest]
        public IEnumerator StartupSceneFeedsPlayerCommandsAndDeterministicAi()
        {
            yield return LoadGrayboxScene();
            var runner = Object.FindFirstObjectByType<CombatWorldRunner>();
            var ai = new DeterministicFighterAi(seed: 7411, reactionTicks: 12);
            runner.RunAutomatically = false;
            runner.SetCommandSources(new AggressiveSource(), ai);
            var startingTick = runner.State.Tick;

            for (var frame = 0; frame < 120; frame++)
                runner.AdvanceFrame(1.0 / 60.0);

            Assert.That(runner.State.Tick - startingTick, Is.EqualTo(120));
            Assert.That(runner.State.Enemy.Health, Is.LessThan(100));
            Assert.That(ai.DecisionsMade, Is.GreaterThan(0));
        }

        [UnityTest]
        public IEnumerator PresentersAndCameraKeepBothFightersOnTheSafePlane()
        {
            yield return LoadGrayboxScene();
            var runner = Object.FindFirstObjectByType<CombatWorldRunner>();
            runner.RunAutomatically = false;
            runner.SetCommandSources(new MoveTowardSource(), new MoveTowardSource());

            for (var frame = 0; frame < 120; frame++)
                runner.AdvanceFrame(1.0 / 60.0);

            Assert.That(runner.PlayerPresenter.transform.position.z, Is.Zero.Within(0.0001f));
            Assert.That(runner.EnemyPresenter.transform.position.z, Is.Zero.Within(0.0001f));
            Assert.That(SceneContainsAnimator(), Is.False);

            var playerView = runner.FightCamera.Camera.WorldToViewportPoint(
                runner.PlayerPresenter.transform.position + Vector3.up);
            var enemyView = runner.FightCamera.Camera.WorldToViewportPoint(
                runner.EnemyPresenter.transform.position + Vector3.up);
            Assert.That(playerView.z, Is.GreaterThan(0f));
            Assert.That(enemyView.z, Is.GreaterThan(0f));
            Assert.That(playerView.x, Is.InRange(0.1f, 0.9f));
            Assert.That(enemyView.x, Is.InRange(0.1f, 0.9f));
        }

        [UnityTest]
        public IEnumerator LoadedSceneKeepsBothFighterMeshesRenderable()
        {
            yield return LoadGrayboxScene();
            var runner = Object.FindFirstObjectByType<CombatWorldRunner>();
            runner.RunAutomatically = false;

            AssertRenderableFighter(runner.PlayerPresenter, runner.FightCamera.Camera);
            AssertRenderableFighter(runner.EnemyPresenter, runner.FightCamera.Camera);
            AssertSingleRuntimeBody(runner.PlayerPresenter);
            AssertSingleRuntimeBody(runner.EnemyPresenter);
        }

        [UnityTest]
        public IEnumerator KnockoutHudAndRestartRestoreAPlayableWorld()
        {
            yield return LoadGrayboxScene();
            var runner = Object.FindFirstObjectByType<CombatWorldRunner>();
            runner.RunAutomatically = false;
            runner.SetCommandSources(new AggressiveSource(), new NoneSource());

            for (var frame = 0;
                 frame < 1000 && runner.State.Result == CombatResult.InProgress;
                 frame++)
            {
                runner.AdvanceFrame(1.0 / 60.0);
            }

            Assert.That(runner.State.Result, Is.EqualTo(CombatResult.PlayerWon));
            Assert.That(runner.Hud.IsRestartVisible, Is.True);
            Assert.That(runner.Hud.StatusText, Does.Contain("KNOCKOUT"));

            runner.RestartCombat();

            Assert.That(runner.State.Result, Is.EqualTo(CombatResult.InProgress));
            Assert.That(runner.State.Player.Health, Is.EqualTo(100));
            Assert.That(runner.State.Enemy.Health, Is.EqualTo(100));
            Assert.That(runner.Hud.IsRestartVisible, Is.False);
        }

        [UnityTest]
        public IEnumerator RenderFrameScheduleAndPresentationCannotChangeSimulationTruth()
        {
            yield return LoadGrayboxScene();
            var sceneRunner = Object.FindFirstObjectByType<CombatWorldRunner>();
            sceneRunner.RunAutomatically = false;

            var firstObject = new GameObject("Thirty FPS Runner");
            var first = firstObject.AddComponent<CombatWorldRunner>();
            first.RunAutomatically = false;
            first.SetCommandSources(new AggressiveSource(), new NoneSource());

            var secondObject = new GameObject("Sixty FPS Runner");
            var second = secondObject.AddComponent<CombatWorldRunner>();
            second.RunAutomatically = false;
            second.SetCommandSources(new AggressiveSource(), new NoneSource());

            for (var frame = 0; frame < 300; frame++)
                first.AdvanceFrame(1.0 / 30.0);
            for (var frame = 0; frame < 600; frame++)
                second.AdvanceFrame(1.0 / 60.0);

            CollectionAssert.AreEqual(first.SerializeSnapshot(), second.SerializeSnapshot());
            Assert.That(HasAnimatorComponent(firstObject), Is.False);
            Assert.That(HasAnimatorComponent(secondObject), Is.False);

            Object.Destroy(firstObject);
            Object.Destroy(secondObject);
        }

        [UnityTest]
        public IEnumerator SameAiSeedAndPublicStateProduceTheSameFight()
        {
            yield return LoadGrayboxScene();
            var sceneRunner = Object.FindFirstObjectByType<CombatWorldRunner>();
            sceneRunner.RunAutomatically = false;

            var firstObject = new GameObject("First AI Replay");
            var first = firstObject.AddComponent<CombatWorldRunner>();
            first.RunAutomatically = false;
            first.SetCommandSources(
                new AggressiveSource(),
                new DeterministicFighterAi(7411, 12));

            var secondObject = new GameObject("Second AI Replay");
            var second = secondObject.AddComponent<CombatWorldRunner>();
            second.RunAutomatically = false;
            second.SetCommandSources(
                new AggressiveSource(),
                new DeterministicFighterAi(7411, 12));

            for (var frame = 0; frame < 600; frame++)
            {
                first.AdvanceFrame(1.0 / 60.0);
                second.AdvanceFrame(1.0 / 60.0);
            }

            CollectionAssert.AreEqual(first.SerializeSnapshot(), second.SerializeSnapshot());
            Object.Destroy(firstObject);
            Object.Destroy(secondObject);
        }

        private static IEnumerator LoadGrayboxScene()
        {
            var operation = SceneManager.LoadSceneAsync("Arena_Graybox", LoadSceneMode.Single);
            while (!operation.isDone)
                yield return null;
            yield return null;
        }

        private static bool SceneContainsAnimator()
        {
            foreach (var gameObject in Object.FindObjectsByType<GameObject>(
                         FindObjectsSortMode.None))
            {
                if (HasAnimatorComponent(gameObject))
                    return true;
            }

            return false;
        }

        private static void AssertRenderableFighter(
            FighterPresenter presenter,
            Camera fightCamera)
        {
            var renderer = presenter.GetComponentInChildren<Renderer>();
            Assert.That(renderer, Is.Not.Null);
            Assert.That(renderer.enabled, Is.True);
            Assert.That(renderer.gameObject.activeInHierarchy, Is.True);
            Assert.That(
                renderer.bounds.size.y,
                Is.GreaterThan(1f),
                $"{presenter.name} has no visible height. " +
                $"localScale={renderer.transform.localScale}, " +
                $"lossyScale={renderer.transform.lossyScale}, " +
                $"bounds={renderer.bounds}.");
            Assert.That(
                GeometryUtility.TestPlanesAABB(
                    GeometryUtility.CalculateFrustumPlanes(fightCamera),
                    renderer.bounds),
                Is.True,
                $"{presenter.name} is outside the fight-camera frustum.");
        }

        private static bool HasAnimatorComponent(GameObject gameObject)
        {
            foreach (var component in gameObject.GetComponents<Component>())
            {
                if (component != null && component.GetType().Name == "Animator")
                    return true;
            }

            return false;
        }

        private static void AssertSingleRuntimeBody(FighterPresenter presenter)
        {
            var rig = presenter.GetComponent<HumanoidRig>();
            Assert.That(rig, Is.Not.Null);
            var bodies = 0;
            for (var index = 0; index < presenter.transform.childCount; index++)
            {
                if (presenter.transform.GetChild(index).name == "Body")
                    bodies++;
            }
            Assert.That(
                bodies,
                Is.EqualTo(1),
                $"{presenter.name} must have exactly one runtime Body; duplicate " +
                "serialized/runtime rigs make attacks look like a clone stepping out.");
        }

        private sealed class AggressiveSource : ICombatCommandSource
        {
            public CombatCommand NextCommand(CombatWorldState state, FighterSide side)
            {
                var fighter = side == FighterSide.Player ? state.Player : state.Enemy;
                return fighter.CurrentAction == CombatAction.None &&
                       fighter.StunTicksRemaining == 0
                    ? CombatCommand.LightAttack
                    : CombatCommand.None;
            }
        }

        private sealed class MoveTowardSource : ICombatCommandSource
        {
            public CombatCommand NextCommand(CombatWorldState state, FighterSide side)
            {
                var fighter = side == FighterSide.Player ? state.Player : state.Enemy;
                if (fighter.CurrentAction != CombatAction.None || fighter.StunTicksRemaining > 0)
                    return CombatCommand.None;
                return side == FighterSide.Player
                    ? CombatCommand.MoveRight
                    : CombatCommand.MoveLeft;
            }
        }

        private sealed class NoneSource : ICombatCommandSource
        {
            public CombatCommand NextCommand(CombatWorldState state, FighterSide side)
            {
                return CombatCommand.None;
            }
        }
    }
}
