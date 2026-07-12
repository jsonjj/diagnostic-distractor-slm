namespace Wayline.Flow.Unity
{
    public static class DeterministicAcceptanceGate
    {
        public static bool CanSelect(bool unityEditor, bool developmentBuild)
        {
            return unityEditor || developmentBuild;
        }

        public static bool IsAvailable
        {
            get
            {
#if UNITY_EDITOR || DEVELOPMENT_BUILD
                return true;
#else
                return false;
#endif
            }
        }
    }
}
