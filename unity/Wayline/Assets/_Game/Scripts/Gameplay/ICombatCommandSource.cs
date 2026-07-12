using Wayline.Combat.Simulation;

namespace Wayline.Gameplay
{
    public interface ICombatCommandSource
    {
        CombatCommand NextCommand(CombatWorldState state, FighterSide side);
    }
}
