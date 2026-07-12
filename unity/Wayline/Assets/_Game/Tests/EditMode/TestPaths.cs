using System.IO;
using UnityEngine;

namespace Wayline.Tests
{
    internal static class TestPaths
    {
        public static string Contract(string relativeFixturePath)
        {
            var unityProject = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            var repository = Path.GetFullPath(Path.Combine(unityProject, "..", ".."));
            return Path.Combine(
                repository,
                "contracts",
                "wayline",
                "v1",
                "fixtures",
                relativeFixturePath);
        }
    }
}
