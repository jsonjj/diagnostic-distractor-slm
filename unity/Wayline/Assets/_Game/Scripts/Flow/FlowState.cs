namespace Wayline.Flow
{
    public enum FlowState
    {
        Title = 0,
        Map = 1,
        Combat = 2,
        NormalTrial = 3,
        SealTrial = 4,
        AssistedRoute = 5,
        Reward = 6,
        Unavailable = 7,
        LossTrial = 8
    }

    public enum FlowCombatOutcome
    {
        Victory = 0,
        Defeat = 1
    }

    public enum FlowTrialStage
    {
        Normal = 0,
        Seal = 1,
        Assisted = 2
    }

    public enum AuthoritativeNextStep
    {
        Reward = 0,
        SealTrial = 1,
        AssistedRoute = 2
    }
}
