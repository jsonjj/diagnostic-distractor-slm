using System;
using System.Collections.Generic;
using Wayline.Combat.Simulation;

namespace Wayline.Tests.Combat
{
    internal readonly struct CommandPair
    {
        public CommandPair(CombatCommand player, CombatCommand enemy)
        {
            Player = player;
            Enemy = enemy;
        }

        public CombatCommand Player { get; }

        public CombatCommand Enemy { get; }
    }

    internal static class CombatFixtures
    {
        public static CombatWorld NewSplitstaffVsLance(
            int playerHealth = 100,
            int enemyHealth = 100,
            int separationMillimeters = 1600)
        {
            return CombatWorld.CreateGraybox(
                playerHealth,
                enemyHealth,
                -separationMillimeters / 2,
                separationMillimeters / 2);
        }

        public static IReadOnlyList<CommandPair> ThreeSecondExchange()
        {
            var commands = new List<CommandPair>(180);
            for (var tick = 0; tick < 180; tick++)
            {
                var player = tick % 45 == 0
                    ? CombatCommand.LightAttack
                    : tick % 45 < 8
                        ? CombatCommand.None
                        : CombatCommand.MoveRight;
                var enemy = tick % 60 == 20
                    ? CombatCommand.HeavyAttack
                    : tick % 60 >= 12 && tick % 60 < 20
                        ? CombatCommand.Guard
                        : CombatCommand.MoveLeft;
                commands.Add(new CommandPair(player, enemy));
            }

            return commands;
        }

        public static CombatCommand SeededCommand(Random random)
        {
            switch (random.Next(0, 12))
            {
                case 0:
                    return CombatCommand.LightAttack;
                case 1:
                    return CombatCommand.HeavyAttack;
                case 2:
                    return CombatCommand.Parry;
                case 3:
                    return CombatCommand.DodgeBackward;
                case 4:
                case 5:
                    return CombatCommand.Guard;
                case 6:
                case 7:
                    return CombatCommand.MoveLeft;
                case 8:
                case 9:
                    return CombatCommand.MoveRight;
                default:
                    return CombatCommand.None;
            }
        }
    }
}
