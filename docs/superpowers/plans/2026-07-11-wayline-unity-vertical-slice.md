# Wayline Unity Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Mac-first Unity client for Wayline: a polished 3D-on-2.5D human weapon fighter with four hero appearances, three worlds, fifteen fights, live verified Route Trials, local progression, and a complete accessible release loop.

**Architecture:** A deterministic 60 Hz combat simulation owns movement, hitboxes, actions, AI inputs, and results; 3D animation, camera, VFX, and audio project that state without deciding gameplay. Unity talks only to the loopback Wayline Forge public API and never scores questions locally. ScriptableObject catalogs define fighters, weapons, actions, worlds, battles, and cosmetics so the three-world slice expands without rewriting flow code.

**Tech Stack:** Unity `6000.3.11f1`, URP, C#, Input System, uGUI/TextMeshPro, Newtonsoft JSON, Unity Test Framework, Blender/FBX, custom fixed-tick simulation, and a packaged Python/llama.cpp sidecar.

## Global Constraints

- Follow the master roadmap and all four authoritative Wayline specifications.
- Create a new `unity/Wayline` project; preserve `game/` and every historical prototype.
- Render fully 3D fighters and arenas but constrain active gameplay to one side-on plane.
- Use no copied code, animations, move data, characters, UI, progression, sound, or visual identity from another fighter.
- Simulation truth never depends on Animator state, root motion, particles, camera, audio, or frame rate.
- Do not run model inference during active combat.
- Unity never receives correct answers or misconception routes before final reveal.
- All math input works without a mouse and never depends only on color.
- Do not import production art before the graybox combat-feel gate passes.
- Do not install packages, import third-party assets, launch builds, stage, commit, or push until separately authorized during execution.

---

### Task 1: Scaffold the Unity project and fixed clock

**Files:**
- Create: `unity/Wayline/Packages/manifest.json`
- Create: `unity/Wayline/Packages/packages-lock.json`
- Create: `unity/Wayline/ProjectSettings/ProjectVersion.txt`
- Create: `unity/Wayline/ProjectSettings/ProjectSettings.asset`
- Create: `unity/Wayline/ProjectSettings/QualitySettings.asset`
- Create: `unity/Wayline/ProjectSettings/GraphicsSettings.asset`
- Create: `unity/Wayline/ProjectSettings/EditorBuildSettings.asset`
- Create: `unity/Wayline/Assets/_Game/Settings/WaylineUrp.asset`
- Create: `unity/Wayline/Assets/_Game/Settings/WaylineRenderer.asset`
- Create: `unity/Wayline/Assets/_Game/Scripts/Core/Wayline.Core.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Core/SimulationClock.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Wayline.EditMode.Tests.asmdef`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/Wayline.PlayMode.Tests.asmdef`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Core/SimulationClockTests.cs`
- Modify: `.gitignore`

**Interfaces:**
- Produces `SimulationClock(int ticksPerSecond, int maxCatchUpTicks)` fixed to 60 Hz.
- Project targets macOS ARM64, Metal, Linear color, IL2CPP release, Mono development, and URP.

- [ ] **Step 1: Write the failing clock tests**

```csharp
using NUnit.Framework;
using Wayline.Core;

namespace Wayline.Tests.Core
{
    public sealed class SimulationClockTests
    {
        [Test]
        public void SixtyNormalFramesProduceSixtyTicks()
        {
            var clock = new SimulationClock(60, 4);
            var emitted = 0;
            for (var i = 0; i < 60; i++)
                emitted += clock.ConsumeFrame(1.0 / 60.0);
            Assert.That(clock.Tick, Is.EqualTo(60));
            Assert.That(emitted, Is.EqualTo(60));
        }

        [Test]
        public void HitchIsCappedAndDropped()
        {
            var clock = new SimulationClock(60, 4);
            Assert.That(clock.ConsumeFrame(1.0), Is.EqualTo(4));
            Assert.That(clock.WasClamped, Is.True);
            Assert.That(clock.ConsumeFrame(0.0), Is.Zero);
        }
    }
}
```

- [ ] **Step 2: Create the minimal package manifest**

Include only URP, Input System, Test Framework, Newtonsoft JSON, and uGUI/TextMeshPro. Open once in Unity `6000.3.11f1`, let the editor resolve compatible exact package versions, review the generated lock, then freeze `packages-lock.json`. Do not add a combat framework, DOTween, Addressables, netcode, FMOD, or behavior-tree package.

- [ ] **Step 3: Run EditMode and verify RED**

Run:

```bash
/Applications/Unity/Hub/Editor/6000.3.11f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics \
  -projectPath "$PWD/unity/Wayline" \
  -runTests -testPlatform EditMode \
  -testFilter Wayline.Tests.Core.SimulationClockTests \
  -testResults /tmp/wayline-clock-red.xml \
  -logFile /tmp/wayline-clock-red.log
```

Expected: compilation failure because `SimulationClock` is missing.

- [ ] **Step 4: Implement the clock**

```csharp
using System;

namespace Wayline.Core
{
    public sealed class SimulationClock
    {
        private readonly double _tickSeconds;
        private readonly int _maxCatchUpTicks;
        private double _accumulator;

        public SimulationClock(int ticksPerSecond, int maxCatchUpTicks)
        {
            if (ticksPerSecond != 60) throw new ArgumentOutOfRangeException(nameof(ticksPerSecond));
            if (maxCatchUpTicks < 1 || maxCatchUpTicks > 8)
                throw new ArgumentOutOfRangeException(nameof(maxCatchUpTicks));
            _tickSeconds = 1.0 / ticksPerSecond;
            _maxCatchUpTicks = maxCatchUpTicks;
        }

        public long Tick { get; private set; }
        public bool Paused { get; set; }
        public bool WasClamped { get; private set; }

        public int ConsumeFrame(double unscaledDeltaSeconds)
        {
            if (Paused) return 0;
            WasClamped = false;
            _accumulator += Math.Max(0.0, unscaledDeltaSeconds);
            var pending = (int)Math.Floor((_accumulator + 1e-12) / _tickSeconds);
            var emitted = Math.Min(pending, _maxCatchUpTicks);
            if (pending > _maxCatchUpTicks)
            {
                _accumulator = 0.0;
                WasClamped = true;
            }
            else _accumulator -= emitted * _tickSeconds;
            Tick += emitted;
            return emitted;
        }
    }
}
```

- [ ] **Step 5: Configure and verify project settings**

Set Apple Silicon, macOS 13 minimum, Linear color, Metal, 60 fps presentation target, no unsafe code, Active Input Handling = Input System, and scenes empty until Task 8. Run the test again; expected zero failures and at least two discovered tests.

- [ ] **Step 6: Protect generated folders**

Add only `unity/Wayline/Library/`, `Temp/`, `Logs/`, `obj/`, `UserSettings/`, and `Builds/` to `.gitignore`. Preserve existing entries.

- [ ] **Step 7: Execution checkpoint**

Report editor/package versions and XML output. Commit only with owner authorization.

---

### Task 2: Freeze public learning DTOs in Unity

**Files:**
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Contracts/Wayline.Learning.Contracts.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Contracts/QuizDtos.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Contracts/StrictQuizValidator.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/TestPaths.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Learning/QuizContractTests.cs`

**Interfaces:**
- Consumes `contracts/wayline/v1/fixtures` from the runtime plan.
- Produces immutable DTOs using the exact camelCase schema aliases.

- [ ] **Step 1: Write fixture tests**

```csharp
using System.IO;
using NUnit.Framework;
using Newtonsoft.Json;
using Wayline.Learning.Contracts;

namespace Wayline.Tests.Learning
{
    public sealed class QuizContractTests
    {
        [Test]
        public void ValidBatchHasNoAnswerKeySurface()
        {
            var json = File.ReadAllText(TestPaths.Contract("valid/three-item-batch.json"));
            var batch = JsonConvert.DeserializeObject<PublicQuizBatch>(json);
            StrictQuizValidator.Validate(batch);
            StringAssert.DoesNotContain("correctAnswer", JsonConvert.SerializeObject(batch));
        }

        [Test]
        public void MissingConfidenceFailsBeforeTransport()
        {
            var json = File.ReadAllText(TestPaths.Contract("invalid/missing-confidence.json"));
            Assert.Throws<JsonSerializationException>(
                () => StrictQuizValidator.Deserialize<InitialSubmission>(json));
        }
    }
}
```

- [ ] **Step 2: Run and verify RED**

Use the Task 1 Unity command with test filter `Wayline.Tests.Learning.QuizContractTests`. Expected: missing DTOs.

- [ ] **Step 3: Implement DTOs and validation**

Define `Confidence` as `Certain`, `Leaning`, `Guessing` with explicit JSON converters. `StrictQuizValidator.Deserialize<T>` applies `MissingMemberHandling.Error`, rejects duplicate properties before object creation, then calls the type-specific invariant validator. Validate schema version, IDs, counts, required text, unique option IDs, exactly four options per item, item count 3–10, and absence of unknown JSON members. Disallow automatic type coercion at the transport boundary.

- [ ] **Step 4: Run every shared fixture in both runtimes**

Expected: Unity and Python agree on every valid and invalid fixture. Publish fixture hashes in the checkpoint report.

- [ ] **Step 5: Execution checkpoint**

Do not add local scoring helpers. Commit only with owner authorization.

---

### Task 3: Build the deterministic combat simulation

**Files:**
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/Wayline.Combat.Simulation.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Data/Wayline.Combat.Data.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Data/ActionDefinition.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Data/WeaponDefinition.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Data/FighterDefinition.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/CombatCommand.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/CommandBuffer.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/FighterState.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/CombatWorldState.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/CombatWorld.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/KinematicMotor.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/PushboxResolver.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/HitResolver.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Simulation/CombatEvent.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Combat/CombatWorldTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Combat/HitResolverTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Combat/ReplayTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Combat/CombatFixtures.cs`

**Interfaces:**
- Consumes one `CombatCommand` per fighter per tick.
- Produces immutable `CombatEvent` values and deterministic `CombatWorldState` snapshots.
- Coordinates are fixed-plane meters: horizontal `X`, vertical `Y`, fixed gameplay depth `Z=0`.

- [ ] **Step 1: Write deterministic hit and replay tests**

```csharp
using NUnit.Framework;
using Wayline.Combat.Simulation;

namespace Wayline.Tests.Combat
{
    public sealed class CombatWorldTests
    {
        [Test]
        public void SameCommandsProduceByteIdenticalSnapshots()
        {
            var a = CombatFixtures.NewSplitstaffVsLance();
            var b = CombatFixtures.NewSplitstaffVsLance();
            foreach (var pair in CombatFixtures.ThreeSecondExchange())
            {
                a.Step(pair.Player, pair.Enemy);
                b.Step(pair.Player, pair.Enemy);
            }
            Assert.That(a.SerializeSnapshot(), Is.EqualTo(b.SerializeSnapshot()));
        }

        [Test]
        public void VisualAnimatorCannotChangeHitResult()
        {
            var world = CombatFixtures.ContactOnTick(18);
            var events = world.StepTo(18);
            Assert.That(events, Has.Exactly(1).Matches<CombatEvent>(e => e.Kind == CombatEventKind.Hit));
        }
    }
}
```

- [ ] **Step 2: Run and verify RED**

Filter `Wayline.Tests.Combat`. Expected: missing simulation.

- [ ] **Step 3: Define action data**

`ActionDefinition` contains stable ID, total ticks, anticipation/commit/contact/follow/recovery ranges, movement curve samples, cancel windows, invulnerability windows, guard level, damage, guard damage, hit-stop ticks, hitboxes, hurtbox overrides, Focus cost, and AI tags. Validate nonoverlap/order and reject an action whose contact precedes commitment or recovery exceeds total ticks.

- [ ] **Step 4: Implement movement and plane constraints**

Use deterministic numeric state, bounded arena `X`, `Y=0` ground, fixed `Z=0`, pushboxes, facing, walk/dash/crouch/dodge, and no Rigidbody-driven gameplay. Clamp catch-up to four ticks and pause simulation cleanly for Route Trials.

- [ ] **Step 5: Implement defense and hit resolution**

Resolve invulnerability, parry, guard, guard break, hit, stagger, knockdown, and knockout in a documented order. Hitboxes are action-data shapes evaluated at the fixed tick. Presentation hit-stop is emitted as an event; it cannot create another hit.

- [ ] **Step 6: Create graybox splitstaff and lance catalogs**

Implement idle/locomotion, three light attacks, two heavies, crouching attack, dash attack, parry counter, one Focus technique, guard, parry, dodge, reactions, knockout, and victory. Use the exact baseline frame phases in the art/animation bible for the first feel pass.

- [ ] **Step 7: Verify GREEN and fuzz**

Replay 10,000 seeded command sequences. Expected: deterministic hashes, no NaN, no plane escape, no negative health, no multi-hit outside declared windows, and no fighter overlap after resolution.

- [ ] **Step 8: Execution checkpoint**

Record balance constants and replay hashes. Do not tune from animation appearance alone.

---

### Task 4: Add input, AI, camera, and animation presentation

**Files:**
- Create: `unity/Wayline/Assets/_Game/Input/FighterControls.inputactions`
- Create: `unity/Wayline/Assets/_Game/Scripts/Input/Wayline.Input.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Input/PlayerCombatInput.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/AI/Wayline.AI.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/AI/AiProfile.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/AI/FighterAiController.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Camera/FightCameraController.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Camera/CameraImpulseMixer.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Presentation/Wayline.Combat.Presentation.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Presentation/FighterPresenter.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Presentation/ActionPhasePresenter.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Combat/Presentation/ContactIkPresenter.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/AI/AiControllerTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/Combat/CameraPlaneTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/Combat/AnimationContactTests.cs`

**Interfaces:**
- Input and AI both emit the same `CombatCommand`.
- Presenters read simulation snapshots/events only.
- Camera may orbit up to 20 degrees while input is locked, then must return to gameplay framing before control resumes.

- [ ] **Step 1: Write AI, camera, and contact tests**

Test that AI cannot read future player commands, the gameplay camera keeps both fighters inside safe frame, cinematic orbit is rejected while input is active, reduced-motion clamps impulse, contact error is ≤8 cm for showcase actions, and planted-foot error meets the asset bible.

- [ ] **Step 2: Run and verify RED**

Run EditMode AI and PlayMode combat filters. Expected: missing controllers/presenters.

- [ ] **Step 3: Implement keyboard/controller actions**

Bind move, crouch, light, heavy, guard/parry, dodge, technique, pause, confirm, cancel, and UI navigation. Store remaps per local profile. Buffer attacks for a bounded number of simulation ticks; never use rendered frames for command timing.

- [ ] **Step 4: Implement fair finite-state AI**

AI selects only from current distance, self/opponent public state, telegraph memory, cooldowns, and seeded temperament. Difficulty changes reaction delay, option weights, and pattern complexity—not input reading, damage multipliers, or hidden immunity.

- [ ] **Step 5: Implement side-on camera**

Frame the midpoint and horizontal extent, clamp distance/FOV, preserve the combat plane, damp only presentation, and use short event-driven impulse. Introductions/supers/victories lock input, run authored camera phases, and settle back before control.

- [ ] **Step 6: Implement phase-driven animation presentation**

Map simulation `action_id`, phase, and normalized phase time to authored poses/clips. Root motion remains presentation-only. Use contact IK, foot locks, delayed mantle/hair/chain motion, and motion-derived trails. VFX/audio/camera trigger from semantic events, not Animator events that decide gameplay.

- [ ] **Step 7: Pass the graybox combat-feel gate**

Record three minutes of splitstaff-versus-lance combat with VFX disabled and review anticipation, silhouette, spacing, contact, follow-through, recovery, foot lock, and camera. Then add restrained VFX and confirm they reinforce rather than hide the poses.

- [ ] **Step 8: Execution checkpoint**

Do not request production character assets until this gate passes.

---

### Task 5: Integrate the sidecar and truthful Route Trial UI

**Files:**
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Client/Wayline.Learning.Client.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Client/IWaylineForgeClient.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Client/WaylineForgeClient.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Client/UnityWebRequestTransport.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Client/WaylineForgeProcessHost.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Quiz/QuizState.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Quiz/QuizController.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/UI/AtlasTrialPanel.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/UI/QuestionPage.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/UI/ConfidenceControl.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/UI/WrongCountPanel.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/UI/FinalFeedbackPanel.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Platform/Mac/MacTextToSpeech.cs`
- Create: `unity/Wayline/Assets/_Game/Plugins/macOS/WaylineTextToSpeech.mm`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Learning/QuizControllerTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/Learning/RouteTrialFlowTests.cs`

**Interfaces:**
- `IWaylineForgeClient.PrepareBatchAsync`, `SubmitInitialAsync`, `SubmitRevisionAsync`, and `GetBossGateAsync` mirror the public API.
- `QuizController` never calculates correctness and has no answer-key field.

- [ ] **Step 1: Write state-machine tests with a fake client**

Test required confidence, no preselection, exact nonzero count, neutral all-item revision, zero-wrong skip, one revision, duplicate-click idempotency, reload resume, full final feedback, keyboard-only completion, and no item correctness before reveal.

- [ ] **Step 2: Run and verify RED**

Run `Wayline.Tests.Learning` EditMode/PlayMode. Expected: missing client/controller/UI.

- [ ] **Step 3: Implement sidecar launch and transport**

Launch the packaged relative executable, pass runtime root and ephemeral token out of command-line process listings through a protected inherited file/pipe where available, wait for authenticated health, and terminate the process tree on game exit. Do not ship `.env` or TrueFoundry credentials.

- [ ] **Step 4: Implement quiz controller states**

Use `Loading`, `Answering`, `SubmittingInitial`, `Reviewing`, `SubmittingRevision`, `Revealed`, and `Complete`. Disable only the initiating control during an in-flight request, preserve selections locally for UI recovery, and trust the server state after reconnect.

- [ ] **Step 5: Build the atlas UI**

Implement the meridian reading arc, four answer fields, explicit confidence labels/notches, progress, exact-count center moment, final first/review comparison, reliable method, and possible-error wording. Neutral revision uses no correctness animation. Reduced motion crossfades in 180 ms.

- [ ] **Step 6: Add accessibility**

Support keyboard/controller focus, visible focus shape, macOS text-to-speech, subtitles, 125/150% type, high contrast, non-color icons/labels, reduced motion/flashes/hit-stop, pause, and no math timer.

- [ ] **Step 7: Verify GREEN**

Run all learning tests against fake recorded API fixtures, then one local sidecar smoke. Expected: exact state behavior and no answer-key string in the Unity serialized state or logs.

- [ ] **Step 8: Execution checkpoint**

Record a complete 3-question zero-wrong and nonzero-revision capture for owner review.

---

### Task 6: Build campaign, boss gates, Second Wind, customization, and save

**Files:**
- Create: `unity/Wayline/Assets/_Game/Scripts/Campaign/Wayline.Campaign.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Campaign/WorldDefinition.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Campaign/BattleDefinition.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Campaign/CampaignController.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Campaign/BossGateController.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Campaign/SecondWindController.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Campaign/RewardController.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Characters/HeroAppearanceDefinition.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Characters/HeroCustomizer.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Save/ProfileDataV1.cs`
- Create: `unity/Wayline/Assets/_Game/Scripts/Save/AtomicProfileStore.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Save/ProfileStoreTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/Campaign/CampaignFlowTests.cs`

**Interfaces:**
- Unity stores presentation/campaign checkpoint data; the sidecar stores learning events and gate truth.
- Rewards never subtract for mistakes and essential weapons remain combat-earned.

- [ ] **Step 1: Write campaign/save tests**

Test exact `3/4/4/5/8` requests, locked boss before server gate, four preserved battle wins after a failed gate, three-item Seal Trial, assisted route after two misses, 6/8 clear, Second Wind substitution, 35% revive plus capped shield, all four hero appearances, any-unlocked-weapon selection, atomic save, corrupt-save backup, and resume at every boundary.

- [ ] **Step 2: Run and verify RED**

Expected: missing campaign/save modules.

- [ ] **Step 3: Implement data-driven campaign definitions**

World assets declare world ID, launch skills, five battle IDs, arena, faction, boss, introduced weapon, route color, and required gate. Battle assets declare tier, question count, AI profile, opponent, rewards, and narrative card.

- [ ] **Step 4: Implement fair rewards and failure paths**

Grant base Route Marks for completion. Grant capped Focus for first-pass correct and wrong-to-correct answers. Never remove rewards. Preserve won combat and offer Seal Trial. Second Wind replaces the post-fight quiz and gives equal-weight `Retry now`/`Second Wind` choices.

- [ ] **Step 5: Implement four shared-rig hero appearances**

Allow head, hair, mantle, two dye channels, and inlay color. Validate every combination against allowed module IDs. Appearance changes never alter hurtbox, reach, speed, or stats.

- [ ] **Step 6: Implement atomic local save**

Write versioned JSON to a temporary file, flush, replace, retain one backup, and store only campaign/presentation state plus sidecar profile UUID. Offer export/delete. Do not duplicate answer history in Unity.

- [ ] **Step 7: Verify GREEN**

Complete and resume a fake three-world run; compare server gate state and Unity campaign state at every boundary.

- [ ] **Step 8: Execution checkpoint**

Record save-version and migration rules. Commit only with owner authorization.

---

### Task 7: Produce the three-world content and presentation

**Files:**
- Create: `unity/Wayline/Assets/_Game/Scenes/Bootstrap.unity`
- Create: `unity/Wayline/Assets/_Game/Scenes/Title.unity`
- Create: `unity/Wayline/Assets/_Game/Scenes/WorldMap.unity`
- Create: `unity/Wayline/Assets/_Game/Scenes/Arena_Graybox.unity`
- Create: `unity/Wayline/Assets/_Game/Scenes/Arena_Valuehold.unity`
- Create: `unity/Wayline/Assets/_Game/Scenes/Arena_Decimara.unity`
- Create: `unity/Wayline/Assets/_Game/Scenes/Arena_Fracture.unity`
- Create: `unity/Wayline/Assets/_Game/Art/Bespoke/`
- Create: `unity/Wayline/Assets/_Game/Art/ThirdParty/`
- Create: `unity/Wayline/Assets/_Game/Audio/`
- Create: `unity/Wayline/Assets/_Game/Data/Campaign/`
- Create: `unity/Wayline/Assets/_Game/Data/Combat/`
- Create: `unity/Wayline/Assets/_Game/Data/Characters/`
- Create: `unity/Wayline/Assets/_Game/Data/Weapons/`
- Create: `docs/ASSET_PROVENANCE.md`
- Create: `unity/Wayline/Assets/_Game/Editor/AssetImportAudit.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Presentation/AssetAuditTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/FullThreeWorldSmokeTests.cs`

**Interfaces:**
- Consumes only assets accepted under the Wayline art/animation bible.
- Produces fifteen battle definitions and three complete world routes.

- [ ] **Step 1: Write asset and full-flow tests before import**

Audit scale, axes, humanoid avatar, sockets, material count, texture size, LODs, missing references, duplicate IDs, battle counts, weapon unlocks, world routes, scene list, and provenance record. The full smoke uses fakes to clear fifteen fights and every trial branch.

- [ ] **Step 2: Run and verify RED**

Expected: missing scenes/assets/data.

- [ ] **Step 3: Finish internal Valuehold slice first**

Create five Valuehold fights, one Decimara exhibition, and one Fracture exhibition using graybox art. Pass complete title→profile→world map→fight→trial→reward→gate→boss→save flow before bespoke import.

- [ ] **Step 4: Import the hero and first boss kit**

Follow exact topology, rig, socket, material, LOD, texture, and animation requirements in the asset bible. Normalize every third-party background asset to the Wayline material/palette system and record license/provenance.

- [ ] **Step 5: Complete Valuehold, Decimara, and Fracture**

Build three arena lighting states, Surveyor-General, Tide Marshal, Chain Warden, splitstaff, lance, pivot sabers, chain, world HUD variants, route-restoration sequences, VFX, and audio. Keep gameplay floor static and clear.

- [ ] **Step 6: Build the owner-only audit view**

`Behind the Meridian` shows live/cache provenance, pinned model/adapter/GGUF, prompt/verifier/registry receipts, rejection/fallback code, and canonical procedure mapping only after reveal. It is excluded from child builds and never shows credentials or learner identity beside raw output.

- [ ] **Step 7: Verify GREEN**

Run asset audits, animation contact tests, and the complete three-world smoke. Expected: zero missing references, invalid provenance, contact violations, or flow failures.

- [ ] **Step 8: Execution checkpoint**

Capture the three arena gameplay views, hero customization, each boss intro/defeat, and both quiz branches for owner review.

---

### Task 8: Complete Mac accessibility, performance, and release gates

**Files:**
- Create: `unity/Wayline/Assets/_Game/Editor/BuildCommands.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/Accessibility/AccessibilitySmokeTests.cs`
- Create: `docs/wayline/WAYLINE_ACCESSIBILITY_MATRIX.md`
- Create: `docs/wayline/WAYLINE_TARGET_AGE_PLAYTEST.md`
- Create: `docs/wayline/WAYLINE_PERFORMANCE_REPORT.md`
- Create: `docs/wayline/WAYLINE_ORIGINALITY_REVIEW.md`
- Create: `docs/wayline/WAYLINE_MAC_CLEAN_MACHINE_CHECKLIST.md`
- Create: `docs/wayline/WAYLINE_PUBLIC_DEMO_MANIFEST.md`

**Interfaces:**
- Produces a reproducible Apple-Silicon development and closed-alpha build.

- [ ] **Step 1: Write automated accessibility and build smoke tests**

Test keyboard/controller-only completion, focus visibility, text at 150%, screen speech, captions, high contrast, reduced motion/flashes/hit-stop, pause, profile deletion, service unavailable, model timeout, and cache fallback.

- [ ] **Step 2: Run full automated suites**

Run all Unity EditMode/PlayMode tests and all Wayline Forge tests. Fail on nonzero exit, missing XML, zero discovered tests, XML failure/error, or leaked secret scan.

- [ ] **Step 3: Profile combat and transitions**

At 1920×1080 Medium on the owner Mac, record CPU/GPU p50/p95/p99, memory, draw calls, triangles, GC allocations, scene loads, sidecar RSS, and inference pressure. Require 60 fps combat, no per-frame managed allocations in combat, and no active inference during a fight.

- [ ] **Step 4: Conduct target-age playtests**

Observe whether ages 10–13 can start a fight, read boss telegraphs, complete confidence input, explain the exact-count review rule, revise once, understand `This answer can come from`, recover through Seal Trial, and resume a saved run. Remove shame, confusion, and fatigue sources before asking about enjoyment.

- [ ] **Step 5: Complete originality and provenance review**

Compare characters, silhouettes, weapons, moves, UI, arena composition, progression, audio, title, and marketing language against cited references. Replace any element that creates substantial similarity. Verify every dependency/model/font/asset license.

- [ ] **Step 6: Build the Apple-Silicon closed alpha**

Build IL2CPP ARM64, assemble sidecar/resources, ad-hoc sign nested binaries consistently, and test on a clean macOS account. Document Gatekeeper opening steps. Do not claim notarization; that requires an Apple Developer membership outside the zero-dollar plan.

- [ ] **Step 7: Final acceptance run**

From a new profile, clear all three worlds, exercise zero/nonzero revision, fail and pass a Seal Trial, use Second Wind, change appearance/weapon, quit/resume at every boundary, delete the profile, and verify no learner data was sent to Sonnet.

- [ ] **Step 8: Execution checkpoint**

Report build hash, size, test summaries, performance, known limitations, and the exact next gate. Publish only with explicit owner approval.
