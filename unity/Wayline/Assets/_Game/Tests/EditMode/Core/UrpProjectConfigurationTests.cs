using System.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace Wayline.Tests.Core
{
    public sealed class UrpProjectConfigurationTests
    {
        [Test]
        public void ProjectUsesThePinnedWaylineUniversalRenderer()
        {
            const string pipelinePath = "Assets/_Game/Settings/WaylineUrp.asset";
            const string rendererPath = "Assets/_Game/Settings/WaylineRenderer.asset";
            var pipeline = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(
                pipelinePath);
            var renderer = AssetDatabase.LoadAssetAtPath<UniversalRendererData>(rendererPath);

            Assert.That(pipeline, Is.Not.Null);
            Assert.That(renderer, Is.Not.Null);
            Assert.That(GraphicsSettings.defaultRenderPipeline, Is.SameAs(pipeline));
            Assert.That(
                AssetDatabase.GetDependencies(pipelinePath).Contains(rendererPath),
                Is.True);
        }
    }
}
