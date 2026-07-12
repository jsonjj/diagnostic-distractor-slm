using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Wayline.Tests.Combat
{
    public sealed class GrayboxSceneTests
    {
        private const string ScenePath = "Assets/_Game/Scenes/Arena_Graybox.unity";

        [TearDown]
        public void ResetScene()
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        }

        [Test]
        public void GrayboxArenaPreservesTheSideOnCombatPlane()
        {
            Assert.That(AssetDatabase.LoadAssetAtPath<SceneAsset>(ScenePath), Is.Not.Null);
            EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);

            var floor = GameObject.Find("Combat Floor");
            var player = GameObject.Find("Player Graybox");
            var enemy = GameObject.Find("Enemy Graybox");
            var fightCamera = GameObject.Find("Wayline Camera").GetComponent<Camera>();

            Assert.That(floor, Is.Not.Null);
            Assert.That(floor.transform.localScale, Is.EqualTo(new Vector3(16f, 0.2f, 4f)));
            Assert.That(player.transform.position.z, Is.Zero.Within(0.0001f));
            Assert.That(enemy.transform.position.z, Is.Zero.Within(0.0001f));
            Assert.That(player.transform.position.x, Is.LessThan(enemy.transform.position.x));
            Assert.That(fightCamera.orthographic, Is.False);
            Assert.That(fightCamera.transform.position.z, Is.LessThan(-5f));
        }
    }
}
