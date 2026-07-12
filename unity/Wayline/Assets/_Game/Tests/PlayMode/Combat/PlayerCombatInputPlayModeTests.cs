using NUnit.Framework;
using UnityEngine;
using UnityEngine.InputSystem;
using Wayline.Combat.Simulation;
using Wayline.Gameplay;

namespace Wayline.Tests.Combat
{
    public sealed class PlayerCombatInputPlayModeTests : InputTestFixture
    {
        [Test]
        public void KeyboardLightPressFeedsTheFixedTickRunner()
        {
            var keyboard = InputSystem.AddDevice<Keyboard>();
            var runnerObject = new GameObject("Input Runner");
            var runner = runnerObject.AddComponent<CombatWorldRunner>();
            runner.RunAutomatically = false;
            runner.SetCommandSources(new PlayerCombatInput(), new NoneSource());

            Press(keyboard.jKey);
            runner.AdvanceFrame(1.0 / 60.0);

            Assert.That(runner.State.Tick, Is.EqualTo(1));
            Assert.That(
                runner.State.Player.CurrentAction,
                Is.EqualTo(CombatAction.LightAttack));
            Object.DestroyImmediate(runnerObject);
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
