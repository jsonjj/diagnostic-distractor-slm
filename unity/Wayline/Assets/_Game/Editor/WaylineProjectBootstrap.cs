using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;
using Wayline.Combat.Simulation;
using Wayline.Flow.Unity;
using Wayline.Gameplay;

namespace Wayline.Editor
{
    public static class WaylineProjectBootstrap
    {
        private const string SettingsFolder = "Assets/_Game/Settings";
        private const string RendererPath = SettingsFolder + "/WaylineRenderer.asset";
        private const string PipelinePath = SettingsFolder + "/WaylineUrp.asset";
        private const string SceneFolder = "Assets/_Game/Scenes";
        private const string GrayboxFolder = "Assets/_Game/Art/Graybox";
        private const string GrayboxScenePath = SceneFolder + "/Arena_Graybox.unity";

        [MenuItem("Wayline/Bootstrap/Create Render Pipeline")]
        public static void CreateRenderPipeline()
        {
            EnsureSettingsFolder();

            var renderer = AssetDatabase.LoadAssetAtPath<UniversalRendererData>(RendererPath);
            var pipeline = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(PipelinePath);
            if (renderer == null && pipeline != null)
                throw new System.InvalidOperationException("Wayline URP exists without its renderer.");

            if (renderer == null)
            {
                renderer = ScriptableObject.CreateInstance<UniversalRendererData>();
                renderer.name = "Wayline Renderer";
                AssetDatabase.CreateAsset(renderer, RendererPath);
            }

            if (pipeline == null)
            {
                pipeline = UniversalRenderPipelineAsset.Create(renderer);
                pipeline.name = "Wayline URP";
                AssetDatabase.CreateAsset(pipeline, PipelinePath);
            }

            GraphicsSettings.defaultRenderPipeline = pipeline;
            QualitySettings.renderPipeline = pipeline;
            EditorUtility.SetDirty(pipeline);
            AssetDatabase.SaveAssets();
        }

        [MenuItem("Wayline/Bootstrap/Create Graybox Arena")]
        public static void CreateGrayboxScene()
        {
            CreateRenderPipeline();
            EnsureFolder("Assets/_Game", "Scenes");
            EnsureFolder("Assets/_Game", "Art");
            EnsureFolder("Assets/_Game/Art", "Graybox");

            var floorMaterial = CreateMaterial(
                GrayboxFolder + "/GrayboxFloor.mat",
                "Graybox Floor",
                new Color32(90, 94, 100, 255));

            var scene = EditorSceneManager.NewScene(
                NewSceneSetup.EmptyScene,
                NewSceneMode.Single);
            scene.name = "Arena_Graybox";

            var floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
            floor.name = "Combat Floor";
            floor.transform.position = new Vector3(0f, -0.1f, 0f);
            floor.transform.localScale = new Vector3(16f, 0.2f, 4f);
            floor.GetComponent<Renderer>().sharedMaterial = floorMaterial;

            var playerPresenter = CreateFighter(
                "Player Graybox",
                FighterSide.Player,
                new Vector3(-0.8f, 0f, 0f),
                HumanoidPreset.Hero,
                facingRight: true);
            var enemyPresenter = CreateFighter(
                "Enemy Graybox",
                FighterSide.Enemy,
                new Vector3(0.8f, 0f, 0f),
                HumanoidPreset.SurveyorGeneral,
                facingRight: false);

            var cameraObject = new GameObject("Wayline Camera");
            cameraObject.tag = "MainCamera";
            cameraObject.transform.position = new Vector3(0f, 2.6f, -10f);
            cameraObject.transform.LookAt(new Vector3(0f, 1f, 0f));
            var fightCamera = cameraObject.AddComponent<Camera>();
            fightCamera.fieldOfView = 42f;
            fightCamera.nearClipPlane = 0.1f;
            fightCamera.farClipPlane = 100f;
            fightCamera.clearFlags = CameraClearFlags.SolidColor;
            fightCamera.backgroundColor = new Color32(21, 27, 38, 255);
            var cameraController = cameraObject.AddComponent<FightCameraController>();
            cameraController.Configure(fightCamera);

            var lightObject = new GameObject("Key Light");
            lightObject.transform.rotation = Quaternion.Euler(45f, -30f, 0f);
            var keyLight = lightObject.AddComponent<Light>();
            keyLight.type = LightType.Directional;
            keyLight.intensity = 1.4f;
            keyLight.color = new Color32(255, 238, 204, 255);

            RenderSettings.ambientMode = AmbientMode.Flat;
            RenderSettings.ambientLight = new Color32(78, 91, 116, 255);

            var runtimeObject = new GameObject("Combat Runtime");
            var runner = runtimeObject.AddComponent<CombatWorldRunner>();
            var hudObject = new GameObject("Graybox HUD");
            var hud = hudObject.AddComponent<GrayboxHud>();
            hud.Configure(runner);
            runner.ConfigurePresentation(
                playerPresenter,
                enemyPresenter,
                cameraController,
                hud);

            var sliceObject = new GameObject("Vertical Slice Runtime");
            var slice = sliceObject.AddComponent<VerticalSliceRuntimeBootstrap>();
            slice.Configure(runner, deterministicAcceptanceData: true);

            EditorSceneManager.SaveScene(scene, GrayboxScenePath);
            EditorBuildSettings.scenes = new[]
            {
                new EditorBuildSettingsScene(GrayboxScenePath, true)
            };
            AssetDatabase.SaveAssets();
        }

        private static void EnsureSettingsFolder()
        {
            if (!AssetDatabase.IsValidFolder("Assets/_Game"))
                AssetDatabase.CreateFolder("Assets", "_Game");
            if (!AssetDatabase.IsValidFolder(SettingsFolder))
                AssetDatabase.CreateFolder("Assets/_Game", "Settings");
        }

        private static void EnsureFolder(string parent, string child)
        {
            var path = parent + "/" + child;
            if (!AssetDatabase.IsValidFolder(path))
                AssetDatabase.CreateFolder(parent, child);
        }

        private static Material CreateMaterial(string path, string name, Color color)
        {
            var material = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (material != null)
                return material;

            var shader = Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null)
                throw new System.InvalidOperationException("The URP Lit shader is unavailable.");
            material = new Material(shader)
            {
                name = name,
                color = color
            };
            material.SetFloat("_Smoothness", 0.25f);
            AssetDatabase.CreateAsset(material, path);
            return material;
        }

        private static FighterPresenter CreateFighter(
            string name,
            FighterSide side,
            Vector3 position,
            HumanoidPreset preset,
            bool facingRight)
        {
            var fighter = new GameObject(name);
            fighter.name = name;
            fighter.transform.position = position;
            var rig = fighter.AddComponent<HumanoidRig>();
            rig.Configure(
                preset,
                facingRight,
                new Color(0.18f, 0.31f, 0.55f),
                new Color(0.90f, 0.68f, 0.23f));
            var presenter = fighter.AddComponent<FighterPresenter>();
            presenter.ConfigureRig(side, rig);
            return presenter;
        }
    }
}
