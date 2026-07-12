using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.OSXStandalone;
using UnityEngine;
using Wayline.Editor;

namespace Wayline.Tests.Build
{
    public sealed class MacDevelopmentBuildTests
    {
        [Test]
        public void OptionsPinTheSingleArm64DevelopmentAcceptanceInput()
        {
            var options = BuildCommands.CreateMacArm64DevelopmentOptions();

            Assert.That(options.scenes, Is.EqualTo(new[]
            {
                "Assets/_Game/Scenes/Arena_Graybox.unity"
            }));
            Assert.That(options.target, Is.EqualTo(BuildTarget.StandaloneOSX));
            Assert.That(options.targetGroup, Is.EqualTo(BuildTargetGroup.Standalone));
            Assert.That(options.locationPathName, Does.EndWith(
                "Builds/MacDevelopment/Wayline-Development-arm64.app"));
            Assert.That(options.options, Is.EqualTo(BuildOptions.Development));
            Assert.That(options.extraScriptingDefines, Is.Null.Or.Empty);
        }

        [Test]
        public void CanonicalOutputStaysInsideTheIgnoredProjectBuildsDirectory()
        {
            var options = BuildCommands.CreateMacArm64DevelopmentOptions();
            var projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            var buildsRoot = Path.GetFullPath(Path.Combine(projectRoot, "Builds")) +
                             Path.DirectorySeparatorChar;
            var output = Path.GetFullPath(options.locationPathName);

            Assert.That(output, Does.StartWith(buildsRoot));
            Assert.That(
                output,
                Is.EqualTo(Path.GetFullPath(Path.Combine(
                    projectRoot,
                    BuildCommands.DevelopmentOutputPath))));
        }

        [Test]
        public void CanonicalOptionsPassInputValidation()
        {
            Assert.That(
                () => BuildCommands.ValidateAcceptanceBuildInputs(
                    BuildCommands.CreateMacArm64DevelopmentOptions()),
                Throws.Nothing);
        }

        [Test]
        public void InputValidationRejectsAnExtraScene()
        {
            var options = BuildCommands.CreateMacArm64DevelopmentOptions();
            options.scenes = new[]
            {
                BuildCommands.AcceptanceScene,
                "Assets/_Game/Scenes/Unexpected.unity"
            };

            Assert.That(
                () => BuildCommands.ValidateAcceptanceBuildInputs(options),
                Throws.TypeOf<InvalidOperationException>());
        }

        [TestCase(BuildOptions.IncludeTestAssemblies)]
        [TestCase(BuildOptions.AllowDebugging)]
        [TestCase(BuildOptions.ConnectWithProfiler)]
        [TestCase(BuildOptions.EnableDeepProfilingSupport)]
        public void InputValidationRejectsEveryForbiddenBuildFlag(
            BuildOptions forbiddenFlag)
        {
            var options = BuildCommands.CreateMacArm64DevelopmentOptions();
            options.options |= forbiddenFlag;

            Assert.That(
                () => BuildCommands.ValidateAcceptanceBuildInputs(options),
                Throws.TypeOf<InvalidOperationException>());
        }

        [Test]
        public void InputValidationRejectsManualScriptingDefines()
        {
            var options = BuildCommands.CreateMacArm64DevelopmentOptions();
            options.extraScriptingDefines = new[] { "DEVELOPMENT_BUILD" };

            Assert.That(
                () => BuildCommands.ValidateAcceptanceBuildInputs(options),
                Throws.TypeOf<InvalidOperationException>());
        }

        [Test]
        public void InputValidationRejectsOutputPathEscape()
        {
            var options = BuildCommands.CreateMacArm64DevelopmentOptions();
            options.locationPathName = Path.Combine(
                Path.GetTempPath(),
                "Wayline-Development-arm64.app");

            Assert.That(
                () => BuildCommands.ValidateAcceptanceBuildInputs(options),
                Throws.TypeOf<InvalidOperationException>());
        }

        [Test]
        public void InputValidationRejectsAProductionBuild()
        {
            var options = BuildCommands.CreateMacArm64DevelopmentOptions();
            options.options = BuildOptions.None;

            Assert.That(
                () => BuildCommands.ValidateAcceptanceBuildInputs(options),
                Throws.TypeOf<InvalidOperationException>());
        }

        [Test]
        public void TransactionSelectsArm64MonoAndRestoresPlayerSettingsWhenBuildThrows()
        {
            var namedTarget = NamedBuildTarget.Standalone;
            var originalBackend = PlayerSettings.GetScriptingBackend(namedTarget);
            var originalArchitecture = PlayerSettings.GetArchitecture(namedTarget);
            var observedBackend = originalBackend;
            var observedArchitecture = originalArchitecture;

            Assert.That(
                () => BuildCommands.RunMacArm64MonoTransaction(() =>
                {
                    observedBackend = PlayerSettings.GetScriptingBackend(namedTarget);
                    observedArchitecture = PlayerSettings.GetArchitecture(namedTarget);
                    throw new TestTransactionException();
                }),
                Throws.TypeOf<TestTransactionException>());

            Assert.That(
                observedBackend,
                Is.EqualTo(ScriptingImplementation.Mono2x));
            Assert.That(
                observedArchitecture,
                Is.EqualTo((int)OSArchitecture.ARM64));
            Assert.That(
                observedArchitecture,
                Is.Not.EqualTo((int)OSArchitecture.x64ARM64));
            Assert.That(
                PlayerSettings.GetScriptingBackend(namedTarget),
                Is.EqualTo(originalBackend));
            Assert.That(
                PlayerSettings.GetArchitecture(namedTarget),
                Is.EqualTo(originalArchitecture));
        }

        [Test]
        public void TransactionSelectsThinArm64BuildProfileAndRestoresItWhenBuildThrows()
        {
            var originalArchitecture = UserBuildSettings.architecture;
            var observedArchitecture = originalArchitecture;

            Assert.That(
                () => BuildCommands.RunMacArm64MonoTransaction(() =>
                {
                    observedArchitecture = UserBuildSettings.architecture;
                    throw new TestTransactionException();
                }),
                Throws.TypeOf<TestTransactionException>());

            Assert.That(
                observedArchitecture,
                Is.EqualTo(OSArchitecture.ARM64));
            Assert.That(
                observedArchitecture,
                Is.Not.EqualTo(OSArchitecture.x64ARM64));
            Assert.That(
                UserBuildSettings.architecture,
                Is.EqualTo(originalArchitecture));
        }

        [Test]
        public void CliBuildMethodExistsWithTheApprovedName()
        {
            Assert.That(
                typeof(BuildCommands).GetMethod(
                    "BuildMacArm64DevelopmentAcceptance"),
                Is.Not.Null);
        }

        [Test]
        public void MinimalRegularAppBundlePassesStructuralAudit()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.Nothing);
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsAFatMachOAnywhereInTheBundle()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var nativeImage = OpaqueRuntimePath(app);
                WriteFatMachOMagic(nativeImage);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsAnX64MachOAnywhereInTheBundle()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteMinimalMachODylib(
                    OpaqueRuntimePath(app),
                    0x01000007U,
                    0x32U,
                    PackVersion(13, 0, 0));

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsATruncatedArm64MachOAnywhereInTheBundle()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var nativeImage = OpaqueRuntimePath(app);
                using (var writer = new BinaryWriter(File.Create(nativeImage)))
                {
                    writer.Write(0xFEEDFACFU);
                    writer.Write(0x0100000CU);
                    writer.Write(0U);
                    writer.Write(6U);
                    writer.Write(1U);
                    writer.Write(72U);
                    writer.Write(0U);
                    writer.Write(0U);
                }

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsAMachOBuiltForMacOSNewerThanThirteen()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteMinimalMachODylib(
                    OpaqueRuntimePath(app),
                    0x0100000CU,
                    0x32U,
                    PackVersion(13, 1, 0));

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsALegacyMachOBuiltForMacOSNewerThanThirteen()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteMinimalMachODylib(
                    OpaqueRuntimePath(app),
                    0x0100000CU,
                    0x24U,
                    PackVersion(14, 0, 0));

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditAcceptsAThinArm64MachOAtMacOSThirteen()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteMinimalMachODylib(
                    OpaqueRuntimePath(app),
                    0x0100000CU,
                    0x32U,
                    PackVersion(13, 0, 0));

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.Nothing);
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void TextToSpeechBuildRecipePinsThinArm64AndMacOSThirteen()
        {
            var script = File.ReadAllText(ProjectFile(
                "Assets",
                "_Game",
                "Plugins",
                "macOS",
                "build_macos_plugin.sh"));

            StringAssert.Contains("  -arch arm64 \\", script);
            StringAssert.DoesNotContain("-arch x86_64", script);
            StringAssert.Contains(
                "  -mmacosx-version-min=13.0 \\",
                script);
            StringAssert.Contains(
                "ARCHS=$(lipo -archs \"$OUTPUT\")",
                script);
            StringAssert.Contains("test \"$ARCHS\" = \"arm64\"", script);
        }

        [Test]
        public void AuthoredTextToSpeechPluginPassesNativeBundleAudit()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var plugins = Path.Combine(app, "Contents", "PlugIns");
                Directory.CreateDirectory(plugins);
                File.Copy(
                    ProjectFile(
                        "Assets",
                        "_Game",
                        "Plugins",
                        "macOS",
                        "libWaylineTextToSpeech.dylib"),
                    Path.Combine(plugins, "libWaylineTextToSpeech.dylib"));

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.Nothing);
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [TestCase(".env")]
        [TestCase(".env.local")]
        [TestCase(".env.production")]
        [TestCase("weights.gguf")]
        [TestCase("weights.safetensors")]
        [TestCase("WaylineForge")]
        [TestCase("wayline_forge")]
        [TestCase("Wayline Forge")]
        [TestCase("wayline.forge")]
        [TestCase("Wayline•Forge")]
        [TestCase("llama-server")]
        [TestCase("llama.server")]
        [TestCase("reviewed_cache")]
        [TestCase("reviewed.cache")]
        [TestCase("train_v7.jsonl")]
        [TestCase("eval_heldout.jsonl")]
        [TestCase("raw-eedi-export.csv")]
        [TestCase("training-notebook.ipynb")]
        [TestCase("runtime.py")]
        [TestCase("model_manifest_v1.json")]
        [TestCase("descriptor_binding_release_receipt_v1.json")]
        [TestCase("package_receipt_v1.json")]
        [TestCase("wayline_parity_report_v1.json")]
        [TestCase("package_manifest_v1.json")]
        public void BundleAuditRejectsForbiddenPayloadNames(string forbiddenName)
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var resources = Path.Combine(app, "Contents", "Resources");
                Directory.CreateDirectory(resources);
                File.WriteAllText(Path.Combine(resources, forbiddenName), "forbidden");

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsAFifoResourceEntry()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var resources = Path.Combine(app, "Contents", "Resources");
                Directory.CreateDirectory(resources);
                var fifo = Path.Combine(resources, "innocent-resource");
                Assert.That(MakeFifo(fifo, 420), Is.Zero);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        [Timeout(3000)]
        public void BundleAuditRejectsAnExecutableFifoWithoutBlockingOnOpen()
        {
            var root = CreateTemporaryRoot();
            Task unblock = null;
            try
            {
                var app = CreateMinimalBundle(root);
                var player = PlayerPath(app);
                File.Delete(player);
                Assert.That(MakeFifo(player, 493), Is.Zero);
                unblock = Task.Run(() =>
                {
                    Thread.Sleep(500);
                    var descriptor = Open(
                        player,
                        OpenReadWrite | OpenNonBlocking);
                    if (descriptor >= 0)
                        Close(descriptor);
                });

                var stopwatch = Stopwatch.StartNew();
                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
                stopwatch.Stop();
                Assert.That(stopwatch.ElapsedMilliseconds, Is.LessThan(250));
                Assert.That(unblock.Wait(2000), Is.True);
            }
            finally
            {
                if (unblock != null)
                    unblock.Wait(2000);
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditNormalizesDirectorySeparatorsBeforePayloadChecks()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var nestedPayload = Path.Combine(
                    app,
                    "Contents",
                    "Resources",
                    "wayline",
                    "forge");
                Directory.CreateDirectory(Path.GetDirectoryName(nestedPayload));
                File.WriteAllText(nestedPayload, "forbidden");

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsSymlinksWithoutFollowingThem()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var outside = Path.Combine(root, "outside.txt");
                File.WriteAllText(outside, "outside");
                var link = Path.Combine(app, "Contents", "ResourcesLink");
                Assert.That(Symlink(outside, link), Is.Zero);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsASymlinkedOutputParentComponent()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var realParent = Path.Combine(root, "real-parent");
                Directory.CreateDirectory(realParent);
                var realApp = CreateMinimalBundle(realParent);
                var linkedParent = Path.Combine(root, "linked-parent");
                Assert.That(Symlink(realParent, linkedParent), Is.Zero);
                var appThroughLink = Path.Combine(
                    linkedParent,
                    Path.GetFileName(realApp));

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(appThroughLink),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void PathComponentValidationRejectsASymlinkWhoseNameContainsBackslash()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var realParent = Path.Combine(root, "real-parent");
                Directory.CreateDirectory(realParent);
                var realApp = CreateMinimalBundle(realParent);
                var linkedParent = Path.Combine(root, "linked\\parent");
                Assert.That(Symlink(realParent, linkedParent), Is.Zero);
                var appThroughLink = Path.Combine(
                    linkedParent,
                    Path.GetFileName(realApp));

                Assert.That(
                    InvokePathComponentValidator(root, appThroughLink),
                    Is.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsMissingMacExecutableDirectory()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = Path.Combine(root, "Wayline-Development-arm64.app");
                Directory.CreateDirectory(Path.Combine(app, "Contents"));

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsInfoPlistWithoutExecutableKey()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteInfoPlistDictionary(
                    app,
                    "<key>CFBundleName</key><string>Wayline</string>");

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsNonStringExecutableValue()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteInfoPlistDictionary(
                    app,
                    "<key>CFBundleExecutable</key><integer>1</integer>");

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsDuplicateMixedTypeExecutableKeys()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteInfoPlistDictionary(
                    app,
                    "<key>CFBundleExecutable</key>" +
                    "<string>Wayline-Development-arm64</string>" +
                    "<key>CFBundleExecutable</key><integer>1</integer>");

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void CleanupValidationRejectsAChildSymlinkWithoutTouchingExternalData()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var staleApp = Path.Combine(root, "stale.app");
                Directory.CreateDirectory(staleApp);
                var external = Path.Combine(root, "external");
                Directory.CreateDirectory(external);
                var sentinel = Path.Combine(external, "sentinel.txt");
                File.WriteAllText(sentinel, "preserve me");
                Assert.That(
                    Symlink(external, Path.Combine(staleApp, "child-link")),
                    Is.Zero);

                Assert.That(
                    InvokeCleanupTreeValidator(staleApp),
                    Is.TypeOf<InvalidOperationException>());
                Assert.That(File.ReadAllText(sentinel), Is.EqualTo("preserve me"));
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void CleanupValidationRejectsADanglingChildSymlink()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var staleApp = Path.Combine(root, "stale.app");
                Directory.CreateDirectory(staleApp);
                Assert.That(
                    Symlink(
                        Path.Combine(root, "missing-target"),
                        Path.Combine(staleApp, "dangling-link")),
                    Is.Zero);

                Assert.That(
                    InvokeCleanupTreeValidator(staleApp),
                    Is.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsMissingInfoPlist()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                File.Delete(Path.Combine(app, "Contents", "Info.plist"));

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsMissingPlistExecutableBinding()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteInfoPlist(app, "Missing-Player");

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [TestCase("../Outside-Player")]
        [TestCase("Nested/Player")]
        [TestCase("Nested\\Player")]
        public void BundleAuditRejectsUnsafePlistExecutableNames(string executableName)
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                WriteInfoPlist(app, executableName);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsAPlayerWithoutExecutablePermission()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var player = PlayerPath(app);
                Assert.That(Chmod(player, 420), Is.Zero);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsExecutableTextMasqueradingAsAPlayer()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var player = PlayerPath(app);
                File.WriteAllText(player, "not a Mach-O executable");
                Assert.That(Chmod(player, 493), Is.Zero);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsAnExecutableX64MachOPlayer()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var player = PlayerPath(app);
                WriteMinimalMachOExecutable(player, 0x01000007U);
                Assert.That(Chmod(player, 493), Is.Zero);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsAHeaderOnlyArm64MachOPlayer()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var player = PlayerPath(app);
                WriteMachOHeaderOnly(player, 0x0100000CU);
                Assert.That(Chmod(player, 493), Is.Zero);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        [Test]
        public void BundleAuditRejectsATruncatedArm64MachOHeader()
        {
            var root = CreateTemporaryRoot();
            try
            {
                var app = CreateMinimalBundle(root);
                var player = PlayerPath(app);
                using (var writer = new BinaryWriter(File.Create(player)))
                {
                    writer.Write(0xFEEDFACFU);
                    writer.Write(0x0100000CU);
                }
                Assert.That(Chmod(player, 493), Is.Zero);

                Assert.That(
                    () => BuildCommands.ValidateBuiltBundle(app),
                    Throws.TypeOf<InvalidOperationException>());
            }
            finally
            {
                DeleteTemporaryRoot(root);
            }
        }

        private static string CreateTemporaryRoot()
        {
            var root = Path.Combine(
                Path.GetTempPath(),
                "wayline-mac-build-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            return root;
        }

        private static string ProjectFile(params string[] components)
        {
            var path = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            foreach (var component in components)
                path = Path.Combine(path, component);
            return path;
        }

        private static string CreateMinimalBundle(string root)
        {
            var app = Path.Combine(root, "Wayline-Development-arm64.app");
            var macDirectory = Path.Combine(app, "Contents", "MacOS");
            Directory.CreateDirectory(macDirectory);
            WriteInfoPlist(app, "Wayline-Development-arm64");
            var player = PlayerPath(app);
            WriteMinimalMachOExecutable(player, 0x0100000CU);
            Assert.That(Chmod(player, 493), Is.Zero);
            return app;
        }

        private static string PlayerPath(string app)
        {
            return Path.Combine(
                app,
                "Contents",
                "MacOS",
                "Wayline-Development-arm64");
        }

        private static string OpaqueRuntimePath(string app)
        {
            var directory = Path.Combine(
                app,
                "Contents",
                "Resources",
                "Data");
            Directory.CreateDirectory(directory);
            return Path.Combine(directory, "opaque-runtime");
        }

        private static void WriteInfoPlist(string app, string executableName)
        {
            WriteInfoPlistDictionary(
                app,
                "<key>CFBundleExecutable</key><string>" + executableName +
                "</string>");
        }

        private static void WriteInfoPlistDictionary(string app, string dictionaryXml)
        {
            File.WriteAllText(
                Path.Combine(app, "Contents", "Info.plist"),
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" +
                "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" " +
                "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n" +
                "<plist version=\"1.0\"><dict>" + dictionaryXml +
                "</dict></plist>\n");
        }

        private static void WriteMachOHeaderOnly(string path, uint cpuType)
        {
            using (var writer = new BinaryWriter(File.Create(path)))
            {
                writer.Write(0xFEEDFACFU);
                writer.Write(cpuType);
                writer.Write(0U);
                writer.Write(2U);
                writer.Write(0U);
                writer.Write(0U);
                writer.Write(0U);
                writer.Write(0U);
            }
        }

        private static void WriteMinimalMachOExecutable(string path, uint cpuType)
        {
            const uint headerSize = 32;
            const uint segmentCommandSize = 72;
            const uint mainCommandSize = 24;
            const uint loadCommandsSize = segmentCommandSize + mainCommandSize;
            const uint entryOffset = headerSize + loadCommandsSize;
            const uint fileSize = entryOffset + 4;

            using (var writer = new BinaryWriter(File.Create(path)))
            {
                writer.Write(0xFEEDFACFU);
                writer.Write(cpuType);
                writer.Write(0U);
                writer.Write(2U);
                writer.Write(2U);
                writer.Write(loadCommandsSize);
                writer.Write(0U);
                writer.Write(0U);

                writer.Write(0x19U);
                writer.Write(segmentCommandSize);
                var segmentName = new byte[16];
                segmentName[0] = (byte)'_';
                segmentName[1] = (byte)'_';
                segmentName[2] = (byte)'T';
                segmentName[3] = (byte)'E';
                segmentName[4] = (byte)'X';
                segmentName[5] = (byte)'T';
                writer.Write(segmentName);
                writer.Write(0UL);
                writer.Write((ulong)fileSize);
                writer.Write(0UL);
                writer.Write((ulong)fileSize);
                writer.Write(5U);
                writer.Write(5U);
                writer.Write(0U);
                writer.Write(0U);

                writer.Write(0x80000028U);
                writer.Write(mainCommandSize);
                writer.Write((ulong)entryOffset);
                writer.Write(0UL);

                writer.Write(0xD65F03C0U);
            }
        }

        private static void WriteFatMachOMagic(string path)
        {
            using (var writer = new BinaryWriter(File.Create(path)))
            {
                writer.Write(0xBEBAFECAU);
                writer.Write(0x01000000U);
            }
        }

        private static void WriteMinimalMachODylib(
            string path,
            uint cpuType,
            uint versionCommand,
            uint minimumVersion)
        {
            const uint headerSize = 32;
            const uint segmentCommandSize = 72;
            var versionCommandSize = versionCommand == 0x32U ? 24U : 16U;
            var loadCommandsSize = segmentCommandSize + versionCommandSize;
            var fileSize = headerSize + loadCommandsSize + 4;

            using (var writer = new BinaryWriter(File.Create(path)))
            {
                writer.Write(0xFEEDFACFU);
                writer.Write(cpuType);
                writer.Write(0U);
                writer.Write(6U);
                writer.Write(2U);
                writer.Write(loadCommandsSize);
                writer.Write(0U);
                writer.Write(0U);

                writer.Write(0x19U);
                writer.Write(segmentCommandSize);
                var segmentName = new byte[16];
                segmentName[0] = (byte)'_';
                segmentName[1] = (byte)'_';
                segmentName[2] = (byte)'T';
                segmentName[3] = (byte)'E';
                segmentName[4] = (byte)'X';
                segmentName[5] = (byte)'T';
                writer.Write(segmentName);
                writer.Write(0UL);
                writer.Write((ulong)fileSize);
                writer.Write(0UL);
                writer.Write((ulong)fileSize);
                writer.Write(5U);
                writer.Write(5U);
                writer.Write(0U);
                writer.Write(0U);

                writer.Write(versionCommand);
                writer.Write(versionCommandSize);
                if (versionCommand == 0x32U)
                    writer.Write(1U);
                writer.Write(minimumVersion);
                writer.Write(minimumVersion);
                if (versionCommand == 0x32U)
                    writer.Write(0U);

                writer.Write(0xD65F03C0U);
            }
        }

        private static uint PackVersion(uint major, uint minor, uint patch)
        {
            return (major << 16) | (minor << 8) | patch;
        }

        private static void DeleteTemporaryRoot(string root)
        {
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "symlink")]
        private static extern int Symlink(string target, string linkPath);

        [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "chmod")]
        private static extern int Chmod(string path, uint mode);

        private const int OpenReadWrite = 2;
        private const int OpenNonBlocking = 4;

        [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "mkfifo")]
        private static extern int MakeFifo(string path, uint mode);

        [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "open")]
        private static extern int Open(string path, int flags);

        [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "close")]
        private static extern int Close(int descriptor);

        private static Exception InvokeCleanupTreeValidator(string path)
        {
            var method = typeof(BuildCommands).GetMethod(
                "ValidateCleanupTreeForDeletion",
                BindingFlags.Public | BindingFlags.Static);
            if (method == null)
                return new MissingMethodException(
                    "ValidateCleanupTreeForDeletion is missing.");

            try
            {
                method.Invoke(null, new object[] { path });
                return null;
            }
            catch (TargetInvocationException exception)
            {
                return exception.InnerException;
            }
        }

        private static Exception InvokePathComponentValidator(
            string trustedBoundary,
            string path)
        {
            var method = typeof(BuildCommands).GetMethod(
                "RequireNoSymlinkedPathComponents",
                BindingFlags.NonPublic | BindingFlags.Static);
            if (method == null)
            {
                return new MissingMethodException(
                    "RequireNoSymlinkedPathComponents is missing.");
            }

            try
            {
                method.Invoke(null, new object[] { trustedBoundary, path });
                return null;
            }
            catch (TargetInvocationException exception)
            {
                return exception.InnerException;
            }
        }

        private sealed class TestTransactionException : Exception
        {
        }
    }
}
