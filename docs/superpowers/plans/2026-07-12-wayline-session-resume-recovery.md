# Wayline Session Resume and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. The owner has already selected subagent-driven execution.

**Goal:** Persist profile and flow checkpoint atomically, resume won-combat trials without replaying combat, and make fail-once persistence errors retry safely without duplicate campaign mutation.

**Architecture:** A strict no-Unity `RuntimeSessionStore` serializes one validated profile plus one validated stable `FlowCheckpoint` in the same atomic file. The bootstrap loads this snapshot before constructing campaign/flow adapters, restores preserved victories and the stable presentation, and uses stable authority request IDs plus idempotent mutation application for persistence retries.

**Tech Stack:** Unity C#, Newtonsoft JSON, existing `Wayline.Flow`, `Wayline.Flow.Runtime`, `Wayline.Save`, campaign/profile types, and Unity Test Framework.

## Global Constraints

- Do not persist answer keys, quiz selections, confidence, misconception evidence, raw SLM output, or credentials in Unity session state.
- Never serialize the transient `Unavailable` state; retain the last stable trial checkpoint.
- A pending trial cannot be replaced by a new combat. The map must offer resume.
- Profile and checkpoint must be one atomic write with strict duplicate/unknown-field rejection and one backup.
- Do not commit/push or run Unity concurrently with another Unity agent.

---

### Task 1: Atomic profile-plus-checkpoint store

**Files:**
- Create: `unity/Wayline/Assets/_Game/Scripts/Flow/Runtime/RuntimeSessionStore.cs`
- Modify: `unity/Wayline/Assets/_Game/Scripts/Flow/Runtime/Wayline.Flow.Runtime.asmdef`
- Create: `unity/Wayline/Assets/_Game/Tests/EditMode/Flow/RuntimeSessionStoreTests.cs`

**Interfaces:**
- Produces `RuntimeSessionStore.Save(ProfileDataV1, FlowCheckpoint)`.
- Produces `RuntimeSessionStore.Load() -> RuntimeSessionSnapshot`.
- `RuntimeSessionSnapshot` exposes only `Profile` and `Checkpoint`.

- [ ] **Step 1: Write failing round-trip and rejection tests**

Round-trip Title, Map, NormalTrial, SealTrial, AssistedRoute, and Reward checkpoints; require exact battle/reward/committed identities. Corrupt primary and require backup recovery. Reject duplicate JSON keys, unknown members, `Unavailable`, orphan rewards, invalid battle/state combinations, and any payload containing `answerHistory`, `confidence`, `misconception`, `correctAnswer`, `rawResponse`, or secrets.

- [ ] **Step 2: Run RED**

Run `Wayline.Tests.Flow.RuntimeSessionStoreTests` without `-quit`; expect missing store types.

- [ ] **Step 3: Implement strict atomic storage**

Store canonical JSON shaped exactly as:

```json
{
  "schemaVersion":"wayline.runtime-session.v1",
  "profile":{},
  "checkpoint":{
    "stableState":"NormalTrial",
    "worldId":"valuehold",
    "battleId":"valuehold-scout",
    "combatVictoryPreserved":true,
    "committedTrialIds":[],
    "committedRewardIds":[],
    "rewardSourceCompletionId":null,
    "rewardAuthorityReceiptId":null
  }
}
```

Convert checkpoint records through the real `FlowCheckpoint` constructor so all invariants are revalidated. Use temporary write-through, replace, one backup, and cleanup semantics matching `AtomicProfileStore`.

- [ ] **Step 4: Verify GREEN**

Require nonzero discovery and zero failures/errors/skips.

---

### Task 2: Restore the composed runtime

**Files:**
- Modify: `unity/Wayline/Assets/_Game/Scripts/Flow/Unity/VerticalSliceRuntimeBootstrap.cs`
- Create: `unity/Wayline/Assets/_Game/Tests/PlayMode/Flow/VerticalSliceRuntimeBootstrapTests.cs`

**Interfaces:**
- Bootstrap loads an existing `RuntimeSessionSnapshot` instead of always creating a profile.
- Existing combat victories are passed to `RuntimeCampaignFlowAdapter`.
- `VerticalSliceFlowController.Restore` presents the saved stable state.

- [ ] **Step 1: Add failing restore tests**

Use an injectable temporary session path. Save a profile with rewards/victory and a NormalTrial checkpoint, construct the bootstrap, and require the same profile values plus `FlowState.NormalTrial` with no combat restart. Save a Reward checkpoint and require reward presentation without reapplying its trial mutation.

- [ ] **Step 2: Witness RED**

Run only the new PlayMode class. Expected: bootstrap overwrites the fixture with a new profile/title.

- [ ] **Step 3: Implement load-or-create**

Use a unique temporary path only for batch acceptance; manual Editor/development app sessions use `Application.persistentDataPath/wayline-runtime-session-v1.json`. On load, construct campaign from the loaded profile, derive preserved victories from known world battle definitions, initialize the adapter with the checkpoint, call `Flow.Restore`, and do not call `ShowTitle` unless the stable state is Title.

- [ ] **Step 4: Verify GREEN**

Require the existing headful new-profile flow and both restore cases to pass.

---

### Task 3: Preserve pending trials on the map

**Files:**
- Modify: `unity/Wayline/Assets/_Game/Scripts/Flow/VerticalSliceFlowController.cs`
- Modify: `unity/Wayline/Assets/_Game/Scripts/Flow/Unity/VerticalSliceRuntimeBootstrap.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/EditMode/Flow/VerticalSliceFlowControllerTests.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/PlayMode/Flow/VerticalSliceHeadfulAcceptanceTests.cs`

- [ ] **Step 1: Write failing pending-resume tests**

After `SuspendTrial` then `ReturnToMapFromUnavailable`, require `HasPendingTrial == true`; `StartCombat` must throw and retain the pending checkpoint. The map button must read `RESUME ROUTE TRIAL`, call `ResumePending`, and return to the same stage/battle without recording another combat victory.

- [ ] **Step 2: Witness RED**

Expected: StartCombat currently replaces the pending checkpoint.

- [ ] **Step 3: Implement minimal guard and UI route**

Expose read-only `HasPendingTrial`. Reject combat while true. Have the existing map primary action resume when pending and start the selected battle otherwise; use a stable text reference to update its label.

- [ ] **Step 4: Verify GREEN**

Run Flow EditMode and the focused headful pending route.

---

### Task 4: Retry fail-once persistence without duplicate mutation

**Files:**
- Modify: `unity/Wayline/Assets/_Game/Scripts/Flow/Runtime/CampaignControllerMutations.cs`
- Modify: `unity/Wayline/Assets/_Game/Scripts/Flow/Unity/VerticalSliceRuntimeBootstrap.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/EditMode/Flow/RuntimeCampaignFlowAdapterTests.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/PlayMode/Flow/VerticalSliceRuntimeBootstrapTests.cs`

- [ ] **Step 1: Add failing retry tests**

With persistence that fails once, require won combat to retry the same transition. For trial completion, require the same completion request ID/receipt on retry, campaign mutation applied at most once, then one persisted Reward checkpoint. Include Seal-pass and Assisted-clear mutations, whose pending preconditions make duplicate application observable.

- [ ] **Step 2: Witness RED**

Expected: `_combatResolved`/`_trialCommitted` wedge and/or a duplicate mutation registration error.

- [ ] **Step 3: Implement stable retry identity and idempotent application**

Set `_combatResolved` and `_trialCommitted` only after successful flow transition. Cache one progression request ID for the active completed trial until success. Make registration idempotent only for the same completion ID and battle; never replace the original action. Track successfully applied completion IDs so persistence retry does not reapply campaign state.

- [ ] **Step 4: Verify GREEN and full regressions**

Run focused retry tests, all Flow/Learning EditMode, full PlayMode, then headful acceptance. Require zero duplicate rewards, no replayed combat, and no lost stable checkpoint.

---

## Self-review

- Spec coverage: atomic profile/checkpoint persistence, restore, pending-trial map behavior, and persistence-failure retry each map to explicit tests.
- Privacy: the session schema contains campaign/flow identities only; learning history remains in Forge.
- Explicitly deferred: exact mid-question/revision recovery still depends on the server-owned snapshot API and batch identity; this plan restores the trial stage without inventing learner state.
