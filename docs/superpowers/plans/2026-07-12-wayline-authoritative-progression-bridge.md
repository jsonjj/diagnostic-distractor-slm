# Wayline Authoritative Progression Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. The owner has already selected subagent-driven execution.

**Goal:** Connect the authenticated Wayline Forge progression responses to Unity's normal, Seal, assisted, reward, and next-world flow without giving Unity any scoring authority.

**Architecture:** `WaylineForgeClient` gains a separate progression interface for path-owned commands. A pure mapper validates every response against the expected battle/stage identities, derives a stable receipt from the validated public response, and returns `AuthoritativeTrialCompletion`; the existing flow controller remains the only state-transition coordinator.

**Tech Stack:** Unity `6000.3.11f1`, C#, Newtonsoft JSON, Unity Test Framework, the existing `Wayline.Learning.Contracts`, `Wayline.Learning.Client`, and `Wayline.Flow` assemblies.

## Global Constraints

- The server remains the sole authority for correct answers, exact wrong counts, Seal outcomes, assisted completion, world clear, and successor activation.
- Unity must reject response/request/path identity mismatches before mutating campaign or save state.
- Deterministic acceptance content must remain entirely inside `UNITY_EDITOR || DEVELOPMENT_BUILD`; production fails closed.
- Do not add answer-key, local scoring, misconception-evidence, or mastery logic to Unity.
- Do not stage, commit, push, download models, or run a second Unity process while another agent owns the project lock.

---

### Task 1: Add the path-owned progression client

**Files:**
- Create: `unity/Wayline/Assets/_Game/Scripts/Learning/Client/IWaylineProgressionClient.cs`
- Modify: `unity/Wayline/Assets/_Game/Scripts/Learning/Client/WaylineForgeClient.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/EditMode/Learning/WaylineForgeClientTests.cs`

**Interfaces:**
- Produces `IWaylineProgressionClient.CompleteBattleAsync`, `PrepareSealTrialAsync`, `CompleteSealTrialAsync`, and `ActivateWorldAsync`.
- Each method accepts path-owned IDs separately from the body DTO and validates the returned identities before returning.

- [ ] **Step 1: Write the failing path and identity tests**

Add tests which enqueue strict DTO JSON and assert these exact routes:

```csharp
CollectionAssert.AreEqual(
    new[]
    {
        "/v1/worlds/valuehold/battles/valuehold_route_1/quiz-batches/batch-001/completion",
        "/v1/worlds/valuehold/seal-trials",
        "/v1/worlds/valuehold/seal-trials/seal-batch-001/completion",
        "/v1/worlds/valuehold/successors/decimara/activation"
    },
    transport.Requests.ConvertAll(request => request.RelativePath));
```

Also replace one response `worldId`, `battleId`, `batchId`, or `requestId` at a time and require `WaylineClientException.Code == "integrity_failure"`.

- [ ] **Step 2: Run the focused EditMode filter and verify RED**

Run without `-quit`:

```bash
/Applications/Unity/Hub/Editor/6000.3.11f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics \
  -projectPath "$PWD/unity/Wayline" \
  -runTests -testPlatform EditMode \
  -testFilter Wayline.Tests.Learning.WaylineForgeClientTests \
  -testResults /tmp/wayline-progression-client-red.xml \
  -logFile /tmp/wayline-progression-client-red.log
```

Expected: compilation/test failure because `IWaylineProgressionClient` and the four methods do not exist.

- [ ] **Step 3: Add the interface and minimal transport implementation**

Use these exact signatures:

```csharp
public interface IWaylineProgressionClient
{
    Task<BattleCompleted> CompleteBattleAsync(
        string worldId, string battleId, string batchId,
        BattleComplete request, CancellationToken cancellationToken);

    Task<SealTrialPrepared> PrepareSealTrialAsync(
        string worldId, SealTrialPrepare request,
        CancellationToken cancellationToken);

    Task<SealTrialCompleted> CompleteSealTrialAsync(
        string worldId, string batchId, SealTrialComplete request,
        CancellationToken cancellationToken);

    Task<WorldActivated> ActivateWorldAsync(
        string completedWorldId, string nextWorldId,
        WorldActivate request, CancellationToken cancellationToken);
}
```

Make `WaylineForgeClient` implement both client interfaces. Call `StrictQuizValidator.Validate` before transport, build only the four paths asserted above, pass the body command's `SessionId`, deserialize with the existing strict helper, then compare all path/request identities before returning.

- [ ] **Step 4: Run the focused filter and verify GREEN**

Expected: nonzero discovery, zero failures/errors/skips, and no request body containing path-owned world, battle, batch, or successor overrides.

---

### Task 2: Map validated responses to flow authority

**Files:**
- Create: `unity/Wayline/Assets/_Game/Scripts/Flow/Authority/Wayline.Flow.Authority.asmdef`
- Create: `unity/Wayline/Assets/_Game/Scripts/Flow/Authority/AuthoritativeProgressionMapper.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Flow/AuthoritativeProgressionMapperTests.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/EditMode/Wayline.EditMode.Tests.asmdef`

**Interfaces:**
- Consumes already strict-deserialized `BattleCompleted`, `SealTrialCompleted`, and `AssistedRouteCompleted` values plus the expected path/stage context.
- Produces only a validated `AuthoritativeTrialCompletion`.

- [ ] **Step 1: Write the complete branch table as failing tests**

Cover these exact mappings:

```text
normal non-boss or cleared boss -> Reward
normal uncleared boss with sealTrialRequired -> SealTrial
passed Seal -> Reward
first missed Seal -> SealTrial
missed Seal with assistedRouteUnlocked -> AssistedRoute
completed assisted route at 0/2, 1/2, or 2/2 -> Reward
```

Require rejection for request, world, battle, batch, route, attempt, item-count, and impossible-flag mismatches. A changed material response field must change the receipt.

- [ ] **Step 2: Run the mapper filter and verify RED**

Use the Task 1 Unity command with `-testFilter Wayline.Tests.Flow.AuthoritativeProgressionMapperTests` and `/tmp/wayline-progression-mapper-red.xml`.

Expected: missing mapper/assembly.

- [ ] **Step 3: Implement the pure mapper**

Expose these exact methods:

```csharp
public static AuthoritativeTrialCompletion FromBattle(
    FlowBattle expectedBattle, string expectedBatchId,
    BattleComplete command, BattleCompleted response);

public static AuthoritativeTrialCompletion FromSeal(
    FlowBattle expectedBattle, int expectedAttemptNumber,
    string expectedBatchId, SealTrialComplete command,
    SealTrialCompleted response);

public static AuthoritativeTrialCompletion FromAssisted(
    FlowBattle expectedBattle, string expectedRouteId,
    AssistedRouteComplete command, AssistedRouteCompleted response);
```

Use `response.RequestId` as `CompletionId` only after it exactly matches the command. Derive `AuthorityReceiptId` as `wayline.progression.v1:` plus lowercase SHA-256 of a versioned, length-prefixed serialization of every validated public response field. This digest is a stable integrity identity, not a server signature; document that distinction in the class comment.

- [ ] **Step 4: Verify mapper GREEN and all flow tests**

Expected: every branch and mismatch test passes, followed by `Wayline.Tests.Flow` with zero failures.

---

### Task 3: Prove the development-content boundary

**Files:**
- Modify: `unity/Wayline/Assets/_Game/Scripts/Flow/Unity/DevelopmentDeterministicAcceptanceQuizClient.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/EditMode/Flow/DeterministicAcceptanceGateTests.cs`

**Interfaces:**
- Acceptance fixtures exist only when `UNITY_EDITOR || DEVELOPMENT_BUILD` is defined.
- Non-development bootstrap construction returns an unavailable presentation and never creates a fixture client.

- [ ] **Step 1: Add a source-boundary regression test**

Read the acceptance-client source as text and require the first nonblank directive to be exactly `#if UNITY_EDITOR || DEVELOPMENT_BUILD`, the final directive to be `#endif`, and the visible label to contain `NOT LIVE SLM`. Also require `VerticalSliceRuntimeBootstrap` to have an explicit non-development fail-closed branch.

- [ ] **Step 2: Witness RED, then wrap the whole implementation**

If the source is not already fully wrapped, run the flow filter and record the intended failure. Wrap imports, namespace, class, fixture builders, and answer-bearing data—not only a runtime selector.

- [ ] **Step 3: Verify GREEN**

Run `Wayline.Tests.Flow` EditMode. Expected: nonzero discovery and zero failures/errors/skips.

---

### Task 4: Compose authoritative branches and reload recovery

**Files:**
- Modify: `unity/Wayline/Assets/_Game/Scripts/Flow/Unity/VerticalSliceRuntimeBootstrap.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/PlayMode/Flow/VerticalSliceHeadfulAcceptanceTests.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/Flow/AuthoritativeBranchPlayModeTests.cs`

**Interfaces:**
- Uses the existing combat, normal quiz, assisted quiz, campaign adapter, reward, and profile/save components.
- Production completion events enter `VerticalSliceFlowController` only through the mapper from Task 2.

- [ ] **Step 1: Add failing branch tests**

Exercise normal-to-reward, normal-to-Seal, first Seal miss, second Seal miss-to-assisted, and assisted 0/2-to-reward. Destroy and recreate the coordinator at each stable boundary and require the exact checkpoint to resume without duplicate trial or reward commits.

- [ ] **Step 2: Verify RED before coordinator changes**

Run the new PlayMode class to `/tmp/wayline-authoritative-branches-red.xml`. Expected: the missing production coordinator prevents branch completion.

- [ ] **Step 3: Wire the minimal coordinator**

Translate only validated response DTOs with `AuthoritativeProgressionMapper`, pass the result to `CompleteTrial`, persist each stable `FlowCheckpoint`, and call `CompleteReward` once. On transport, validation, or persistence failure, show retry/return-to-map and preserve the won combat.

- [ ] **Step 4: Verify all branch and headful acceptance tests**

Run the new branch class, then the existing headful class. Require nonzero discovery, zero failures/errors/skips, the visible `NOT LIVE SLM` label in development acceptance, and a screenshot showing combat, trial, reward, and map states.

---

## Self-review

- Spec coverage: normal, Seal, assisted, reward, activation transport, identity binding, receipt semantics, fail-closed development boundary, and reload idempotency each map to a task.
- Placeholder scan: no TBD/TODO steps remain.
- Type consistency: all signatures use existing DTO and flow type names; `WorldActivate` maps only to profile activation and is deliberately not converted into a trial completion.
- Explicitly deferred: Second Wind transport/coordinator work, full next-world profile mutation, production GGUF/cache packaging, and clean-Mac smoke remain separate plan units.
