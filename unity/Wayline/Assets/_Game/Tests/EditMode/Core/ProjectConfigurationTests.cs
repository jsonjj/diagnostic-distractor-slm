using System.IO;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

namespace Wayline.Tests.Core
{
    public sealed class ProjectConfigurationTests
    {
        [Test]
        public void MacProjectSettingsArePinnedForTheDeterministicSlice()
        {
            Assert.That(PlayerSettings.colorSpace, Is.EqualTo(ColorSpace.Linear));
            Assert.That(
                PlayerSettings.GetGraphicsAPIs(BuildTarget.StandaloneOSX),
                Is.EqualTo(new[] { GraphicsDeviceType.Metal }));

            var root = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            var playerSettings = File.ReadAllText(
                Path.Combine(root, "ProjectSettings", "ProjectSettings.asset"));
            StringAssert.Contains("macOSTargetOSVersion: 13.0", playerSettings);
            StringAssert.Contains("allowUnsafeCode: 0", playerSettings);
            StringAssert.Contains("activeInputHandler: 1", playerSettings);
            StringAssert.Contains("platformArchitecture:\n    Standalone: 1", playerSettings);
            StringAssert.Contains("scriptingBackend:\n    Standalone: 1", playerSettings);

            var buildSettings = File.ReadAllText(
                Path.Combine(root, "ProjectSettings", "EditorBuildSettings.asset"));
            StringAssert.Contains("enabled: 1", buildSettings);
            StringAssert.Contains(
                "path: Assets/_Game/Scenes/Arena_Graybox.unity",
                buildSettings);
        }
    }
}
