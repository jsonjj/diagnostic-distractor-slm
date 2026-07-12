using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using Wayline.Combat.Simulation;

namespace Wayline.Gameplay
{
    public sealed class PlayerCombatInput : ICombatCommandSource
    {
        public CombatCommand NextCommand(CombatWorldState state, FighterSide side)
        {
            if (side != FighterSide.Player || state.Result != CombatResult.InProgress)
                return CombatCommand.None;

            var keyboard = Keyboard.current;
            var gamepad = Gamepad.current;
            if (Pressed(keyboard?.spaceKey, gamepad?.buttonEast))
                return CombatCommand.DodgeBackward;
            if (Pressed(keyboard?.lKey, gamepad?.rightShoulder))
                return CombatCommand.Parry;
            if (Pressed(keyboard?.kKey, gamepad?.buttonWest))
                return CombatCommand.HeavyAttack;
            if (Pressed(keyboard?.jKey, gamepad?.buttonSouth))
                return CombatCommand.LightAttack;
            if (Held(keyboard?.leftShiftKey, gamepad?.leftShoulder))
                return CombatCommand.Guard;

            var horizontal = 0f;
            if (keyboard != null)
            {
                if (keyboard.aKey.isPressed || keyboard.leftArrowKey.isPressed)
                    horizontal -= 1f;
                if (keyboard.dKey.isPressed || keyboard.rightArrowKey.isPressed)
                    horizontal += 1f;
            }
            if (gamepad != null && System.Math.Abs(gamepad.leftStick.x.ReadValue()) > 0.35f)
                horizontal = gamepad.leftStick.x.ReadValue();
            if (horizontal < -0.35f)
                return CombatCommand.MoveLeft;
            if (horizontal > 0.35f)
                return CombatCommand.MoveRight;
            return CombatCommand.None;
        }

        private static bool Pressed(ButtonControl keyboard, ButtonControl gamepad)
        {
            return (keyboard != null && keyboard.wasPressedThisFrame) ||
                   (gamepad != null && gamepad.wasPressedThisFrame);
        }

        private static bool Held(ButtonControl keyboard, ButtonControl gamepad)
        {
            return (keyboard != null && keyboard.isPressed) ||
                   (gamepad != null && gamepad.isPressed);
        }
    }
}
