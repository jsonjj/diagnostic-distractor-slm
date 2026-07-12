# Wayline Opening and Living Atlas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the internal text shell with a skippable story opening and a game-like living-atlas home screen that hands off to the existing campaign without weakening input, accessibility, save, or release boundaries.

**Architecture:** Keep `FlowState.Title` as the campaign authority, but move title presentation into a focused `Wayline.FrontEnd` assembly. A deterministic front-end controller owns opening/menu/settings view state; Unity views render that state and emit actions. A final Higgsfield movie is optional at runtime: an original in-engine meridian animatic is the fail-safe, so a missing or corrupt video can never block play.

**Tech Stack:** Unity `6000.3.11f1`, URP, uGUI, Input System `1.19.0`, Unity VideoPlayer, C#, Unity Test Framework, H.264 1080p24 final cinematic.

## Global Constraints

- macOS Apple Silicon first; mouse, keyboard, and controller are first-class.
- Preserve `VerticalSliceFlowController` as campaign authority and preserve exact resume checkpoints.
- Do not put answer keys, model output, credentials, or learner history in front-end state.
- The opening is skippable, replayable, captioned, and has reduced-motion behavior.
- Missing cinematic media must fall back to an in-engine sequence and still reach the menu.
- The gold meridian route is the only dominant decorative motion.
- No copied game UI, cinematic framing, characters, audio, or title treatment.
- Do not stage, commit, or push without a separate owner instruction.

---

## File map

Create:

- `unity/Wayline/Assets/_Game/Scripts/FrontEnd/Wayline.FrontEnd.asmdef` — isolated front-end runtime assembly.
- `unity/Wayline/Assets/_Game/Scripts/FrontEnd/FrontEndController.cs` — deterministic view-state transitions and action events.
- `unity/Wayline/Assets/_Game/Scripts/FrontEnd/FrontEndMotionEvaluator.cs` — pure opening/menu route-line animation evaluator.
- `unity/Wayline/Assets/_Game/Scripts/FrontEnd/LivingAtlasFrontEnd.cs` — uGUI view, focus order, copy, and world-route projection.
- `unity/Wayline/Assets/_Game/Scripts/FrontEnd/OpeningCinematicPlayer.cs` — final-video/fallback playback, skip, captions, and completion.
- `unity/Wayline/Assets/_Game/Scripts/FrontEnd/OpeningCaptionTrack.cs` — validated timed caption cues rendered by Unity, never burned into generated footage.
- `unity/Wayline/Assets/_Game/Scripts/FrontEnd/FrontEndPresentationPreferences.cs` — nonpersonal reduced-motion, text-scale, caption, audio, and opening-seen settings.
- `unity/Wayline/Assets/_Game/Scripts/FrontEnd/FrontEndPresentationPreferencesStore.cs` — strict atomic local preferences JSON.
- `unity/Wayline/Assets/_Game/Editor/FrontEndCinematicValidator.cs` — build-time codec, resolution, duration, and provenance gate.
- `unity/Wayline/Assets/_Game/Tests/EditMode/FrontEnd/FrontEndControllerTests.cs` — pure transition tests.
- `unity/Wayline/Assets/_Game/Tests/EditMode/FrontEnd/FrontEndMotionEvaluatorTests.cs` — deterministic phase and reduced-motion tests.
- `unity/Wayline/Assets/_Game/Tests/EditMode/FrontEnd/FrontEndPresentationPreferencesTests.cs` — strict persistence and recovery tests.
- `unity/Wayline/Assets/_Game/Tests/PlayMode/FrontEnd/LivingAtlasFrontEndTests.cs` — real mouse, keyboard, and gamepad navigation tests.
- `unity/Wayline/Assets/_Game/Tests/PlayMode/FrontEnd/OpeningCinematicPlayerTests.cs` — skip, fallback, captions, and completion tests.
- `unity/Wayline/Assets/_Game/Tests/PlayMode/FrontEnd/FrontEndHeadfulAcceptanceTests.cs` — title/menu captures at 100%, 125%, and 150% text.

Modify:

- `unity/Wayline/Assets/_Game/Scripts/Flow/Unity/VerticalSliceRuntimeBootstrap.cs` — replace shell title/map presentation with the front-end boundary; keep combat/trial/reward ownership.
- `unity/Wayline/Assets/_Game/Scripts/Flow/Unity/Wayline.Flow.Unity.asmdef` — reference `Wayline.FrontEnd`.
- `unity/Wayline/Assets/_Game/Tests/EditMode/Wayline.EditMode.Tests.asmdef` — reference `Wayline.FrontEnd`.
- `unity/Wayline/Assets/_Game/Tests/PlayMode/Wayline.PlayMode.Tests.asmdef` — reference `Wayline.FrontEnd`.
- `unity/Wayline/Assets/_Game/Editor/BuildCommands.cs` — invoke the final-media validator only for public builds; permit the internal fallback animatic.
- `docs/wayline/WAYLINE_HIGGSFIELD_OPENING_BRIEF.md` — remains the final-media source of truth.
- `docs/ASSET_PROVENANCE.md` — record every final clip, audio file, font, and title asset.

## Interfaces

```csharp
public enum FrontEndView
{
    Opening,
    PressAnyKey,
    MainMenu,
    Settings,
    Hidden
}

public sealed class FrontEndController
{
    public FrontEndView View { get; }
    public event Action ContinueJourneyRequested;
    public event Action QuitRequested;
    public void Start(bool openingSeen, bool hasJourney);
    public void CompleteOpening();
    public void SkipOpening();
    public void PressAnyKey();
    public void OpenSettings();
    public void CloseSettings();
    public void ReplayOpening();
    public void ContinueJourney();
}

public readonly struct FrontEndMotionState
{
    public float RouteReveal { get; }
    public float AtlasOpacity { get; }
    public float CameraBlend { get; }
    public float SelectedNodePulse { get; }
}

public static class FrontEndMotionEvaluator
{
    public static FrontEndMotionState Evaluate(
        FrontEndView view,
        float elapsedSeconds,
        bool reducedMotion);
}
```

`LivingAtlasFrontEnd` consumes `FrontEndController`, `ProfileDataV1`, and the campaign world definitions. It emits no campaign mutation directly. `VerticalSliceRuntimeBootstrap` subscribes to `ContinueJourneyRequested` and invokes the existing `Flow.EnterMap()` or pending-route resume path.

---

### Task 1: Deterministic front-end state and preferences

- [ ] Write controller tests proving first launch enters `Opening`, returning launch enters `PressAnyKey`, Skip and natural completion converge on the same state, Settings returns to its caller, and Continue fires once.
- [ ] Run the focused EditMode filter and confirm the tests fail because `FrontEndController` does not exist.
- [ ] Implement only the interfaces above, rejecting invalid transitions with `InvalidOperationException` and making repeated Skip/Continue calls idempotent.
- [ ] Add strict preference tests for defaults (`captions=true`, `reducedMotion=false`, `textScale=1f`, `openingSeen=false`), 1.0/1.25/1.5 scale validation, atomic backup recovery, duplicate JSON member rejection, and no unknown members.
- [ ] Implement the preference store at `Application.persistentDataPath/wayline-presentation-preferences-v1.json`; it must never share the campaign session file.
- [ ] Re-run the focused tests and require zero failures.

Focused command:

```bash
/Applications/Unity/Hub/Editor/6000.3.11f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics \
  -projectPath "$PWD/unity/Wayline" \
  -runTests -testPlatform EditMode \
  -testFilter Wayline.Tests.FrontEnd \
  -testResults /tmp/wayline-front-end-edit.xml \
  -logFile /tmp/wayline-front-end-edit.log
```

### Task 2: Living-atlas menu

- [ ] Write PlayMode tests that construct the menu at 1920×1080 and assert this focus order: `Continue journey`, `Choose route`, `Routekeeper`, `Settings`, `Replay opening`, `Quit`.
- [ ] Verify the tests fail before creating the view.
- [ ] Build one screen-space overlay with a real-time Valuehold vista behind it, the hero/atlas silhouette on the left, and a route trace on the right. The selected route endpoint controls the world title and duel preview; do not use floating rounded cards.
- [ ] Render `Continue journey` as the dominant action. Show `Choose route` and `Routekeeper` as honest disabled previews in the one-fight internal build, with explicit `Available in the full Valuehold build` copy.
- [ ] Show progress as repaired route seals and the current opponent endpoint, never as a generic percent bar.
- [ ] Route every state change through `FrontEndController`; clicking and Submit on the selected action must produce the same event.
- [ ] Apply Big Shoulders Display only to `WAYLINE`/world headings, Atkinson Hyperlegible to menu copy, and IBM Plex Mono to route measurements after OFL provenance is recorded.
- [ ] Implement 100%, 125%, and 150% layouts with no clipped action, and a visible noncolor focus edge.
- [ ] Run the focused PlayMode tests and require mouse, keyboard, and gamepad passes.

Signature motion phases:

| Phase | Duration | Primary action |
| --- | ---: | --- |
| Establish | 180 ms | background camera settles; UI remains quiet |
| Trace | 420 ms | one gold line travels from bracer to current route node |
| Resolve | 220 ms | atlas labels and menu actions appear beneath the line |
| Focus | 140 ms | selected endpoint gains one restrained luminosity pulse |

Reduced motion uses a single 180 ms opacity transition and no camera displacement or pulse.

### Task 3: Skippable story opening with fail-safe fallback

- [ ] Write tests for final-video playback, missing-video fallback, corrupt-video fallback, Skip after the first rendered frame, replay from Settings, captions independent of audio, and identical final controller state under reduced motion.
- [ ] Verify the tests fail before adding the player.
- [ ] Implement the seven semantic beats from `WAYLINE_HIGGSFIELD_OPENING_BRIEF.md`: line wakes, connected world, break, hero commits, guarded routes, first duel, route read/reconnection.
- [ ] Use the locked narration/captions in the brief. The guarded-route beat must show that champions closed routes for safety and duels earn access; the final beat must imply route reading before reconnection.
- [ ] For internal builds with no final movie, render a 24-second in-engine animatic using the current environment, meridian line, controlled camera moves, authored captions, and noncopying graybox poses.
- [ ] For final media, play one editor-assembled H.264 file; do not ask Unity to sequence seven raw generated clips at runtime.
- [ ] On any media failure, log only `opening_media_unavailable`, start the fallback, and never block `PressAnyKey`.
- [ ] Run focused tests, then capture standard and reduced-motion openings.

### Task 4: Final cinematic asset gate

- [ ] Generate clips only after the hero turnaround, face sheet, splitstaff sheet, atlas-bracer close-up, palette board, and three world references pass the asset-bible review.
- [ ] Generate at least four takes per shot with a locked character/reference seed; reject morphing hands, weapons, armor, handedness, faces, and route-line color.
- [ ] Edit outside Unity to 1920×1080, 24 fps, 34–38 seconds, H.264 video plus 48 kHz stereo AAC, with the real vector title but no burned-in captions.
- [ ] Author the three locked lines in `OpeningCaptionTrack` with exact cue times; Unity renders them in Atkinson Hyperlegible so captions can be toggled independently of sound.
- [ ] Record prompts, seeds, generation date, source images, selected takes, model/tool terms, audio sources, and final hashes in `docs/ASSET_PROVENANCE.md`.
- [ ] Make `FrontEndCinematicValidator` reject wrong duration, resolution, frame rate, missing provenance hash, generated lettering, or an unapproved filename before a public build.

### Task 5: Flow integration and regression gate

- [ ] Write a PlayMode test that starts at `FlowState.Title`, completes/skips the opening, selects Continue, reaches the map, starts combat, returns after defeat, and preserves initial menu focus.
- [ ] Verify it fails while the bootstrap still owns the old title shell.
- [ ] Replace only title/map shell construction in `VerticalSliceRuntimeBootstrap`; leave combat, trial, reward, authority, and persistence code unchanged.
- [ ] Keep the development boundary as a quiet footer: `Development questions — live model off`. Public/live builds must derive their label from validated runtime mode, not a hardcoded optimistic string.
- [ ] Run the focused integration test, all 303+ EditMode tests, all 55+ PlayMode tests, and the three headful acceptance paths.
- [ ] Build, sign, launch, and manually verify the real Mac app with mouse, Return, and one connected controller.

### Task 6: Public-demo dependency gates

The front end can ship in the internal app with fallback animation, but it is not a public-demo release by itself. Before calling the game a public demo, also require:

- [ ] receipt-bound merged Q4_K_M GGUF, pinned ARM64 `llama-server`, production model manifest, live parity report, reviewed cache, and packaged live-process smoke;
- [ ] five finished Valuehold fights plus five each for Decimara and Fracture;
- [ ] final hero, three bosses, four weapons, three arena kits, complete combat animation/VFX/audio, and provenance;
- [ ] target-age usability, full input remapping, captions, text scaling, reduced motion/flashes/hit-stop, privacy/export/delete UI, clean-Mac resume, and 60 fps reports;
- [ ] 1,000 live generations with zero unverified learner-visible output and a verified same-skill fallback for every failure.

## Self-review

- Spec coverage: story motivation, duel-plus-route logic, living-atlas identity, input parity, accessibility, fallback behavior, media provenance, flow integration, and live-SLM honesty each have an explicit task and gate.
- Placeholder scan: every implementation and failure behavior is concrete; no deferred-fill markers remain.
- Type consistency: the controller, view enum, motion evaluator, and integration event names are defined once above and reused unchanged.
- Scope: this plan does not redesign combat, scoring, evidence, or the sidecar; those remain governed by the Wayline master roadmap and runtime plans.
