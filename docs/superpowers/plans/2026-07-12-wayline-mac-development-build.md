# Wayline Mac Development Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. The owner has already selected subagent-driven execution.

**Goal:** Produce a playable, ad-hoc-signed Apple-Silicon development `.app` for the current one-battle Valuehold acceptance slice without implying that live SLM packaging is complete.

**Architecture:** An Editor-only build command validates the one-scene input, temporarily selects ARM64 Mono, invokes Unity with only `BuildOptions.Development`, restores project settings in `finally`, and audits the emitted bundle for forbidden research/model/sidecar payloads. The app retains the visible `NOT LIVE SLM` development label and production builds remain fail closed.

**Tech Stack:** Unity `6000.3.11f1`, UnityEditor BuildPipeline, macOS ARM64 Mono development player, Metal, and Unity Test Framework.

## Global Constraints

- Wait until the authority-adoption agent releases the Unity project lock.
- Build exactly `Assets/_Game/Scenes/Arena_Graybox.unity` to `Builds/MacDevelopment/Wayline-Development-arm64.app`.
- Use `BuildOptions.Development` only; prohibit `AllowDebugging`, profiler connection, deep profiling, test assemblies, and manual scripting defines.
- Do not package `.env`, Python source, research data, GGUF files, Wayline Forge, reviewed caches, credentials, or external receipts.
- The artifact is an internal development acceptance build, not a public live-SLM release, Developer-ID-signed build, or notarized product.
- Do not stage, commit, push, or alter the saved release backend/architecture after the build completes.

---

### Task 1: Add testable build options and input validation

**Files:**
- Create: `unity/Wayline/Assets/_Game/Editor/BuildCommands.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Build/MacDevelopmentBuildTests.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/EditMode/Wayline.EditMode.Tests.asmdef`

**Interfaces:**
- Produces `BuildCommands.CreateMacArm64DevelopmentOptions()`.
- Produces `BuildCommands.ValidateAcceptanceBuildInputs(BuildPlayerOptions options)`.
- Produces `BuildCommands.DevelopmentOutputPath`.

- [ ] **Step 1: Write the failing option tests**

Assert the returned value has exactly:

```csharp
Assert.That(options.scenes, Is.EqualTo(new[]
{
    "Assets/_Game/Scenes/Arena_Graybox.unity"
}));
Assert.That(options.target, Is.EqualTo(BuildTarget.StandaloneOSX));
Assert.That(options.locationPathName, Does.EndWith(
    "Builds/MacDevelopment/Wayline-Development-arm64.app"));
Assert.That(options.options, Is.EqualTo(BuildOptions.Development));
Assert.That(options.extraScriptingDefines, Is.Null.Or.Empty);
```

Also require the canonical output to remain beneath the project's ignored `Builds` directory and reject any extra scene, `IncludeTestAssemblies`, `AllowDebugging`, `ConnectWithProfiler`, `EnableDeepProfilingSupport`, or path escape.

- [ ] **Step 2: Run the focused EditMode test and verify RED**

Run without `-quit`:

```bash
/Applications/Unity/Hub/Editor/6000.3.11f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics \
  -projectPath "$PWD/unity/Wayline" \
  -runTests -testPlatform EditMode \
  -testFilter Wayline.Tests.Build.MacDevelopmentBuildTests \
  -testResults /tmp/wayline-mac-build-red.xml \
  -logFile /tmp/wayline-mac-build-red.log
```

Expected: missing `BuildCommands` compilation failure.

- [ ] **Step 3: Implement minimal option construction and validation**

Use a public Editor-only static class and these exact members:

```csharp
public const string AcceptanceScene =
    "Assets/_Game/Scenes/Arena_Graybox.unity";
public const string DevelopmentOutputPath =
    "Builds/MacDevelopment/Wayline-Development-arm64.app";

public static BuildPlayerOptions CreateMacArm64DevelopmentOptions();
public static void ValidateAcceptanceBuildInputs(BuildPlayerOptions options);
```

Resolve the output with `Path.GetFullPath(Path.Combine(projectRoot, DevelopmentOutputPath))`, require its relative path to round-trip exactly, require the scene file and `.meta` to exist, and require `EditorBuildSettings.scenes` to contain exactly that enabled scene.

- [ ] **Step 4: Verify focused GREEN**

Expected: nonzero discovery and zero failures/errors/skips.

---

### Task 2: Build transaction and bundle audit

**Files:**
- Modify: `unity/Wayline/Assets/_Game/Editor/BuildCommands.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/EditMode/Build/MacDevelopmentBuildTests.cs`

**Interfaces:**
- Produces `BuildCommands.BuildMacArm64DevelopmentAcceptance()` for `-executeMethod`.
- Produces `BuildCommands.ValidateBuiltBundle(string appPath)`.

- [ ] **Step 1: Add failing transaction/audit tests**

Require the build method's testable settings helper to select `ScriptingImplementation.Mono2x` and ARM64, while the transaction restores the prior scripting backend and architecture even when the injected build action throws. Feed `ValidateBuiltBundle` fake bundle trees containing each forbidden name and require rejection:

```text
.env, *.gguf, WaylineForge, llama-server, reviewed_cache, train_v7.jsonl,
eval_heldout.jsonl, *.py, model_manifest_v1.json
```

Require a minimal valid `.app/Contents/MacOS/Wayline-Development-arm64` tree to pass the structural validator.

- [ ] **Step 2: Verify RED before implementation**

Run the focused class again. Expected: missing transaction/audit behavior.

- [ ] **Step 3: Implement the build transaction**

Capture the existing `PlayerSettings.GetScriptingBackend(NamedBuildTarget.Standalone)` and standalone architecture. In `try`, set Mono and ARM64, call `BuildPipeline.BuildPlayer`, require `BuildResult.Succeeded` with zero errors, then validate the bundle. In `finally`, restore both settings and call `AssetDatabase.SaveAssets()`.

Never add scripting defines manually. Create/clean only the exact ignored output directory. Reject symlinks and forbidden filenames during the recursive bundle audit; do not follow symlinks.

- [ ] **Step 4: Verify transaction GREEN**

Run focused EditMode tests and confirm the serialized project still reports its original release backend/architecture afterward.

---

### Task 3: Build and inspect the actual app

**Files:**
- Generate only: `unity/Wayline/Builds/MacDevelopment/Wayline-Development-arm64.app`

- [ ] **Step 1: Run full Unity tests before building**

Run complete EditMode and PlayMode suites without `-quit`; require XML files, nonzero discovery, and zero failures/errors/skips.

- [ ] **Step 2: Execute the build**

```bash
/Applications/Unity/Hub/Editor/6000.3.11f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics -quit \
  -projectPath "$PWD/unity/Wayline" \
  -executeMethod Wayline.Editor.BuildCommands.BuildMacArm64DevelopmentAcceptance \
  -logFile /tmp/wayline-mac-development-build.log
```

Expected: exit 0 and an app at the exact output path.

- [ ] **Step 3: Verify the emitted bundle externally**

Run:

```bash
file unity/Wayline/Builds/MacDevelopment/Wayline-Development-arm64.app/Contents/MacOS/*
codesign --verify --deep --strict --verbose=2 \
  unity/Wayline/Builds/MacDevelopment/Wayline-Development-arm64.app
find unity/Wayline/Builds/MacDevelopment/Wayline-Development-arm64.app \
  -type f -o -type l
```

Require arm64 player output, successful ad-hoc signature verification, no symlinks escaping the bundle, and no forbidden model/research/sidecar filenames. `spctl` rejection is expected because there is no Developer ID/notarization and is not a failure of this internal artifact.

- [ ] **Step 4: Launch smoke**

Launch the executable directly with a temporary log, wait for the scene/player process to appear, capture the title or reward frame, and terminate it cleanly. Confirm the visible label says `DETERMINISTIC LOCAL ACCEPTANCE DATA — NOT LIVE SLM`.

---

## Self-review

- Spec coverage: exact scene, ARM64 Mono development mode, setting restoration, forbidden flags, bundle exclusion, external architecture/signature checks, and honest labeling each map to a test or build step.
- Placeholder scan: no TBD/TODO steps remain.
- Explicitly deferred: production sidecar assembly, real GGUF inference, notarization, public distribution, and clean-Mac product smoke remain external release gates.
