# Wayline Fresh Assisted Route Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unsafe reused-Seal-Trial assisted route with one durable worked example and two fresh, verifier-sealed, reduced-complexity supported MCQs whose answer keys remain server-only until one-shot completion.

**Architecture:** Keep the normal `QuizMachine` and its `3..10` contract unchanged. Build a fresh three-item internal `VerifiedBatchMaterial` at an internal `assisted_route` tier, expose item zero as a worked example and items one and two as keyless supported MCQs, and persist the private material in an isolated hashed `AssistedRouteStore` that generic quiz endpoints cannot address. An immutable `AssistedRouteCompletionEvent` is the authoritative completed state, so completion is replayable without a cross-store state write and its practice-only answers can be retained without entering unassisted procedure, skill, gate, or mastery evidence.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, SQLite, `unittest`, JSON Schema Draft 2020-12, Unity `6000.3.11f1`, C#, Newtonsoft.Json, and Unity Test Framework.

## Global Constraints

- Preserve `PROJECT_CONTEXT.md`, the frozen 140-item holdout, the released `j2ampn/qwen3-4b-distractor-lora-v7` model, and all dirty-worktree changes.
- Do not import `src/buggy_procedures.py` into the product runtime.
- Never expose raw SLM output, a supported MCQ key, a procedure ID, or item correctness before assisted-route completion.
- The public route is exactly one worked example plus two supported MCQs; the worked example may reveal its own trusted answer and steps.
- Both supported MCQs use difficulty `1`; the worked example uses difficulty `2`, matching the approved `0 / -1 / -1` scaffold from a level-2 baseline.
- Every delivered item must be live-verifier sealed or reviewed-cache sealed. Exhausted fresh fallback content returns `safe_content_unavailable`; it never reuses revealed Seal-Trial material.
- Assisted completion clears the world at `0/2`, `1/2`, or `2/2` and never replays a won boss.
- Assisted answers, confidence, selected answer text, compatible procedure hypothesis, and canonical feedback persist locally, but assisted responses never change unassisted procedure status, skill status, secure-skill counts, boss-gate counts, or mastery.
- Completion has one submission and no wrong-count/revision phase.
- Exact preparation and completion replays are idempotent. Reusing a request ID with a different payload is a conflict; a different completion request after world clear is a `409` target conflict.
- The event schema remains `wayline.event.v2`. Existing disposable development profiles containing the old reused-item assisted event are blocked and reset; they are not silently transformed.
- The wire schema remains `wayline.v1`; Python and Unity must accept and reject the same fixtures.
- All SQLite write decisions use `BEGIN IMMEDIATE`, bounded busy timeouts, hash verification, strict ownership checks, and fail-closed decoding.
- Do not make paid calls, download model weights, stage, commit, push, or publish while executing this plan.

---

## File Map

### Create

- `services/wayline_forge/app/assisted_route_machine.py` — pure public projection, sealed scoring, and canonical feedback.
- `services/wayline_forge/app/assisted_route_store.py` — hashed private material, preparation receipts, active-route recovery, and cross-activity serialization.
- `services/wayline_forge/tests/test_assisted_route_machine.py` — key-secrecy and scoring tests.
- `services/wayline_forge/tests/test_assisted_route_store.py` — persistence, tamper, idempotency, concurrency, restart, and cascade tests.

### Modify

- `contracts/wayline/v1/progression-shared.schema.json`
- `contracts/wayline/v1/assisted-route-prepare.schema.json`
- `contracts/wayline/v1/assisted-route-prepared.schema.json`
- `contracts/wayline/v1/assisted-route-complete.schema.json`
- `contracts/wayline/v1/assisted-route-completed.schema.json`
- `contracts/wayline/v1/fixtures/valid/assisted-route-prepare.json`
- `contracts/wayline/v1/fixtures/valid/assisted-route-prepared.json`
- `contracts/wayline/v1/fixtures/valid/assisted-route-complete.json`
- `contracts/wayline/v1/fixtures/valid/assisted-route-completed.json`
- `contracts/wayline/v1/fixtures/invalid/assisted-route-complete-duplicate-item.json`
- `contracts/wayline/v1/fixtures/invalid/assisted-route-completed-correctness-mismatch.json`
- `contracts/wayline/v1/fixtures/invalid/assisted-route-completed-count-mismatch.json`
- `contracts/wayline/v1/fixtures/invalid/assisted-route-prepared-mcq-key-leak.json`
- `contracts/wayline/v1/fixtures/invalid/assisted-route-prepared-world-mismatch.json`
- `services/wayline_forge/app/contracts.py`
- `services/wayline_forge/app/adaptive_planner.py`
- `services/wayline_forge/app/slot_materializer.py`
- `services/wayline_forge/app/batch_material.py`
- `services/wayline_forge/app/orchestrator.py`
- `services/wayline_forge/app/battle_preparation.py`
- `services/wayline_forge/app/events.py`
- `services/wayline_forge/app/evidence_reducer.py`
- `services/wayline_forge/app/profile_store.py`
- `services/wayline_forge/app/progression.py`
- `services/wayline_forge/app/application.py`
- `services/wayline_forge/app/api.py`
- `services/wayline_forge/app/quiz_store.py`
- `services/wayline_forge/tests/test_progression_contracts.py`
- `services/wayline_forge/tests/test_adaptive_planner.py`
- `services/wayline_forge/tests/test_slot_materializer.py`
- `services/wayline_forge/tests/test_batch_material.py`
- `services/wayline_forge/tests/test_orchestrator.py`
- `services/wayline_forge/tests/test_battle_preparation.py`
- `services/wayline_forge/tests/test_event_v2.py`
- `services/wayline_forge/tests/test_evidence_reducer.py`
- `services/wayline_forge/tests/test_legacy_migration.py`
- `services/wayline_forge/tests/test_progression_assisted_store.py`
- `services/wayline_forge/tests/test_progression_commands.py`
- `services/wayline_forge/tests/test_progression_rules.py`
- `services/wayline_forge/tests/test_application_progression_facade.py`
- `services/wayline_forge/tests/test_progression_api.py`
- `services/wayline_forge/tests/test_profile_deletion.py`
- `services/wayline_forge/tests/test_profile_export.py`
- `services/wayline_forge/tests/test_valuehold_application.py`
- `unity/Wayline/Assets/_Game/Scripts/Learning/Contracts/QuizDtos.cs`
- `unity/Wayline/Assets/_Game/Scripts/Learning/Contracts/StrictQuizValidator.cs`
- `unity/Wayline/Assets/_Game/Tests/EditMode/Learning/QuizContractTests.cs`

---

### Task 1: Freeze the Fresh Assisted-Route Wire Contract

**Files:**

- Modify: `services/wayline_forge/app/contracts.py`
- Modify: `contracts/wayline/v1/progression-shared.schema.json`
- Modify: `contracts/wayline/v1/assisted-route-prepare.schema.json`
- Modify: `contracts/wayline/v1/assisted-route-prepared.schema.json`
- Modify: `contracts/wayline/v1/assisted-route-complete.schema.json`
- Modify: `contracts/wayline/v1/assisted-route-completed.schema.json`
- Modify: every assisted-route fixture listed in the file map
- Modify: `services/wayline_forge/tests/test_progression_contracts.py`

**Interfaces:**

- Produces strict Pydantic models `AssistedRoutePrepare`, `AssistedWorkedExample`, `AssistedSupportedItem`, `AssistedRouteBatch`, `AssistedRoutePrepared`, `AssistedSelection`, `AssistedRouteComplete`, `AssistedItemResult`, and `AssistedRouteCompleted`.
- The prepared response contains no `sourceBatchId`, `correctOptionId`, `correctAnswer`, `procedureId`, `possibleError`, `reliableMethod`, or `trustedSteps` inside either supported item.
- The completion result may reveal supported answers and canonical feedback only after the path-owned `route_id` is completed.

- [ ] **Step 1: Replace the assisted contract test matrix with Python/JSON-Schema parity tests**

Add these cases to `test_progression_contracts.py`:

```python
from services.wayline_forge.app.contracts import (
    AssistedRouteComplete,
    AssistedRouteCompleted,
    AssistedRoutePrepare,
    AssistedRoutePrepared,
)

ASSISTED_MODELS = {
    "assisted-route-prepare.json": AssistedRoutePrepare,
    "assisted-route-prepared.json": AssistedRoutePrepared,
    "assisted-route-complete.json": AssistedRouteComplete,
    "assisted-route-completed.json": AssistedRouteCompleted,
}

def test_assisted_preparation_seals_supported_truth(self) -> None:
    prepared = parse_public_json(
        AssistedRoutePrepared,
        (VALID / "assisted-route-prepared.json").read_text(encoding="utf-8"),
    )
    serialized = json.dumps(
        [item.model_dump(by_alias=True) for item in prepared.batch.items],
        sort_keys=True,
    ).casefold()
    for banned in (
        "sourcebatchid",
        "correctoptionid",
        "correctanswer",
        "procedureid",
        "possibleerror",
        "reliablemethod",
        "trustedsteps",
    ):
        self.assertNotIn(banned, serialized)

def test_assisted_semantic_invalid_fixtures_fail(self) -> None:
    cases = {
        "assisted-route-complete-duplicate-item.json": AssistedRouteComplete,
        "assisted-route-completed-correctness-mismatch.json": AssistedRouteCompleted,
        "assisted-route-completed-count-mismatch.json": AssistedRouteCompleted,
        "assisted-route-prepared-mcq-key-leak.json": AssistedRoutePrepared,
        "assisted-route-prepared-world-mismatch.json": AssistedRoutePrepared,
    }
    for name, model_type in cases.items():
        with self.subTest(name=name), self.assertRaises(ValidationError):
            parse_public_json(model_type, (INVALID / name).read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run the focused contract test and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest services.wayline_forge.tests.test_progression_contracts -v
```

Expected: import failure for the new assisted Pydantic models or fixture validation failures caused by the old `sourceBatchId` shape.

- [ ] **Step 3: Define the exact valid prepared and completed fixtures**

Use this prepared payload:

```json
{
  "schemaVersion": "wayline.v1",
  "requestId": "prepare-assisted-001",
  "worldId": "valuehold",
  "batch": {
    "routeId": "assisted-aaaaaaaaaaaaaaaaaaaaaaaa",
    "worldId": "valuehold",
    "workedExample": {
      "itemId": "item-worked-001",
      "prompt": "What is the value of the 7 in 4,782?",
      "correctAnswer": "700",
      "trustedSteps": ["The 7 is in the hundreds place.", "Seven hundreds equals 700."],
      "reliableMethod": "Name the digit's place, then write its value."
    },
    "items": [
      {
        "itemId": "item-supported-001",
        "prompt": "What is the value of the 6 in 6,241?",
        "options": [
          {"optionId": "opt-supported-001-a", "displayText": "6"},
          {"optionId": "opt-supported-001-b", "displayText": "60"},
          {"optionId": "opt-supported-001-c", "displayText": "600"},
          {"optionId": "opt-supported-001-d", "displayText": "6000"}
        ]
      },
      {
        "itemId": "item-supported-002",
        "prompt": "What is the value of the 3 in 1,305?",
        "options": [
          {"optionId": "opt-supported-002-a", "displayText": "3"},
          {"optionId": "opt-supported-002-b", "displayText": "30"},
          {"optionId": "opt-supported-002-c", "displayText": "300"},
          {"optionId": "opt-supported-002-d", "displayText": "3000"}
        ]
      }
    ]
  }
}
```

Use this completed item shape in `assisted-route-completed.json`:

```json
{
  "itemId": "item-supported-001",
  "selectedOptionId": "opt-supported-001-d",
  "selectedAnswer": "6000",
  "confidence": "leaning",
  "correctOptionId": "opt-supported-001-d",
  "correctAnswer": "6000",
  "isCorrect": true,
  "possibleError": null,
  "reliableMethod": "Name the digit's place, then write its value.",
  "trustedSteps": ["The 6 is in the thousands place.", "Six thousands equals 6000."],
  "canonicalFeedback": [
    "Name the digit's place, then write its value.",
    "The 6 is in the thousands place.",
    "Six thousands equals 6000."
  ]
}
```

The completed top level contains `workedExampleCount: 1`, `supportedMcqCount: 2`, `finalCorrect`, `worldCleared: true`, and exactly two result items.

- [ ] **Step 4: Implement the strict Pydantic models and cross-field validators**

Add these model shapes to `contracts.py`, using the existing `StrictModel`, `Identifier`, `PublicOption`, and `Confidence` types:

```python
class AssistedRoutePrepare(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    session_id: Identifier = Field(alias="sessionId")


class AssistedWorkedExample(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    prompt: str = Field(min_length=1, max_length=1000)
    correct_answer: str = Field(alias="correctAnswer", min_length=1, max_length=256)
    trusted_steps: tuple[str, ...] = Field(alias="trustedSteps", min_length=1, max_length=8)
    reliable_method: str = Field(alias="reliableMethod", min_length=1, max_length=512)


class AssistedSupportedItem(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    prompt: str = Field(min_length=1, max_length=1000)
    options: tuple[PublicOption, PublicOption, PublicOption, PublicOption]

    @model_validator(mode="after")
    def options_are_distinct(self) -> Self:
        option_ids = tuple(option.option_id for option in self.options)
        displays = tuple(normalize_public_display(option.display_text) for option in self.options)
        if len(set(option_ids)) != 4 or len(set(displays)) != 4:
            raise ValueError("supported options must have distinct IDs and displays")
        return self


class AssistedRouteBatch(StrictModel):
    route_id: Identifier = Field(alias="routeId")
    world_id: Identifier = Field(alias="worldId")
    worked_example: AssistedWorkedExample = Field(alias="workedExample")
    items: tuple[AssistedSupportedItem, AssistedSupportedItem]

    @model_validator(mode="after")
    def item_ids_are_distinct(self) -> Self:
        ids = (self.worked_example.item_id, *(item.item_id for item in self.items))
        if len(set(ids)) != 3:
            raise ValueError("all assisted item IDs must be distinct")
        return self


class AssistedRoutePrepared(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    world_id: Identifier = Field(alias="worldId")
    batch: AssistedRouteBatch

    @model_validator(mode="after")
    def world_matches(self) -> Self:
        if self.batch.world_id != self.world_id:
            raise ValueError("batch.worldId must match worldId")
        return self


class AssistedSelection(StrictModel):
    item_id: Identifier = Field(alias="itemId")
    option_id: Identifier = Field(alias="optionId")
    confidence: Confidence = Field(strict=False)


class AssistedRouteComplete(StrictModel):
    schema_version: str = Field(alias="schemaVersion", pattern=r"^wayline\.v1$")
    request_id: Identifier = Field(alias="requestId")
    session_id: Identifier = Field(alias="sessionId")
    selections: tuple[AssistedSelection, AssistedSelection]

    @model_validator(mode="after")
    def item_ids_are_distinct(self) -> Self:
        if self.selections[0].item_id == self.selections[1].item_id:
            raise ValueError("supported selections must target distinct items")
        return self
```

Define `AssistedItemResult` with the exact completed fixture fields. Its validator requires `isCorrect == (selectedOptionId == correctOptionId)`, requires `possibleError is None` for a correct response, and requires `canonicalFeedback` to equal `(possibleError if present, reliableMethod, *trustedSteps)`. Define `AssistedRouteCompleted` so `finalCorrect == sum(item.is_correct)`, there are exactly two distinct item IDs, and `worldCleared` is `Literal[True]`.

- [ ] **Step 5: Update all four schemas with strict nested objects**

Set `additionalProperties: false` at every object level. Use `minItems == maxItems == 2` for supported items and selections, `minItems == maxItems == 4` for options, `workedExampleCount: {"const": 1}`, `supportedMcqCount: {"const": 2}`, and `worldCleared: {"const": true}`. Remove `sourceBatchId` from every definition. The invalid key-leak fixture adds `correctOptionId` to a supported item and must fail schema validation before Pydantic construction.

- [ ] **Step 6: Verify the Python and Draft 2020-12 contract gate is GREEN**

Run:

```bash
.venv-live/bin/python -m unittest services.wayline_forge.tests.test_progression_contracts services.wayline_forge.tests.test_contracts -v
```

Expected: all valid fixtures pass both validators; all assisted invalid fixtures fail both validators; no existing 3–10 quiz fixture changes behavior.

---

### Task 2: Make the Wire Contract Strictly Unity-Consumable

**Files:**

- Modify: `unity/Wayline/Assets/_Game/Scripts/Learning/Contracts/QuizDtos.cs`
- Modify: `unity/Wayline/Assets/_Game/Scripts/Learning/Contracts/StrictQuizValidator.cs`
- Modify: `unity/Wayline/Assets/_Game/Tests/EditMode/Learning/QuizContractTests.cs`

**Interfaces:**

- Produces C# DTOs matching the Python aliases byte-for-field.
- `StrictQuizValidator.Deserialize<T>` rejects unknown, missing, duplicate, mistyped, cardinality-invalid, identity-invalid, and aggregate-invalid assisted payloads.

- [ ] **Step 1: Add Unity tests that consume the shared assisted fixtures**

Add these exact tests:

```csharp
[Test]
public void AssistedRouteFixturesMatchTheFrozenWireContract()
{
    Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<AssistedRoutePrepare>(
        File.ReadAllText(TestPaths.Contract("valid/assisted-route-prepare.json"))));
    Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<AssistedRoutePrepared>(
        File.ReadAllText(TestPaths.Contract("valid/assisted-route-prepared.json"))));
    Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<AssistedRouteComplete>(
        File.ReadAllText(TestPaths.Contract("valid/assisted-route-complete.json"))));
    Assert.DoesNotThrow(() => StrictQuizValidator.Deserialize<AssistedRouteCompleted>(
        File.ReadAllText(TestPaths.Contract("valid/assisted-route-completed.json"))));
}

[TestCase("assisted-route-complete-duplicate-item.json", typeof(AssistedRouteComplete))]
[TestCase("assisted-route-completed-correctness-mismatch.json", typeof(AssistedRouteCompleted))]
[TestCase("assisted-route-completed-count-mismatch.json", typeof(AssistedRouteCompleted))]
[TestCase("assisted-route-prepared-mcq-key-leak.json", typeof(AssistedRoutePrepared))]
[TestCase("assisted-route-prepared-world-mismatch.json", typeof(AssistedRoutePrepared))]
public void InvalidAssistedRouteFixtureIsRejected(string fixture, System.Type contractType)
{
    var method = typeof(StrictQuizValidator)
        .GetMethod("Deserialize")
        .MakeGenericMethod(contractType);
    Assert.Throws<System.Reflection.TargetInvocationException>(() =>
        method.Invoke(null, new object[] {
            File.ReadAllText(TestPaths.Contract("invalid/" + fixture))
        }));
}

[Test]
public void SupportedItemsHaveNoAnswerKeySurface()
{
    var prepared = StrictQuizValidator.Deserialize<AssistedRoutePrepared>(
        File.ReadAllText(TestPaths.Contract("valid/assisted-route-prepared.json")));
    var json = JsonConvert.SerializeObject(prepared.Batch.Items);
    StringAssert.DoesNotContain("correctOptionId", json);
    StringAssert.DoesNotContain("correctAnswer", json);
    StringAssert.DoesNotContain("procedureId", json);
    StringAssert.DoesNotContain("sourceBatchId", json);
}
```

- [ ] **Step 2: Run the Unity fixture test and verify RED**

Run:

```bash
/Applications/Unity/Hub/Editor/6000.3.11f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics \
  -projectPath "$PWD/unity/Wayline" \
  -runTests -testPlatform EditMode \
  -testFilter Wayline.Tests.Learning.QuizContractTests \
  -testResults /tmp/wayline-assisted-contract-red.xml \
  -logFile /tmp/wayline-assisted-contract-red.log
```

Expected: C# compilation errors because the assisted DTO types do not yet exist.

- [ ] **Step 3: Add opt-in Newtonsoft DTOs with the exact Python field names**

Add `[JsonObject(MemberSerialization.OptIn)]` sealed classes for all nine assisted types. The supported-item DTO is exactly:

```csharp
[JsonObject(MemberSerialization.OptIn)]
public sealed class AssistedSupportedItem
{
    [JsonConstructor]
    public AssistedSupportedItem(
        string itemId,
        string prompt,
        IReadOnlyList<PublicQuizOption> options)
    {
        ItemId = itemId;
        Prompt = prompt;
        Options = options;
    }

    [JsonProperty("itemId", Required = Required.Always)]
    public string ItemId { get; }

    [JsonProperty("prompt", Required = Required.Always)]
    public string Prompt { get; }

    [JsonProperty("options", Required = Required.Always)]
    public IReadOnlyList<PublicQuizOption> Options { get; }
}
```

`AssistedItemResult` declares every completed-result field, including nullable `PossibleError`, `Confidence`, and `IReadOnlyList<string> CanonicalFeedback`. No prepared DTO declares a key or diagnosis property.

- [ ] **Step 4: Add explicit assisted invariant validators**

Add overloads for all assisted root DTOs and dispatch them from `ValidateObject<T>`. The prepared validator must contain these checks:

```csharp
public static void Validate(AssistedRoutePrepared prepared)
{
    Require(prepared, nameof(prepared));
    RequireVersion(prepared.SchemaVersion);
    RequireIdentifier(prepared.RequestId, "requestId");
    RequireIdentifier(prepared.WorldId, "worldId");
    Require(prepared.Batch, "batch");
    Require(prepared.Batch.WorldId == prepared.WorldId, "batch world must match response world");
    RequireIdentifier(prepared.Batch.RouteId, "routeId");
    Require(prepared.Batch.Items, "items");
    Require(prepared.Batch.Items.Count == 2, "assisted route requires two supported items");
    Require(prepared.Batch.WorkedExample, "workedExample");

    var ids = new HashSet<string>(StringComparer.Ordinal);
    Require(ids.Add(prepared.Batch.WorkedExample.ItemId), "worked itemId must be unique");
    foreach (var item in prepared.Batch.Items)
    {
        Require(ids.Add(item.ItemId), "supported itemIds must be unique");
        Require(item.Options.Count == 4, "supported item requires four options");
        var optionIds = new HashSet<string>(StringComparer.Ordinal);
        var displays = new HashSet<string>(StringComparer.Ordinal);
        foreach (var option in item.Options)
        {
            Require(optionIds.Add(option.OptionId), "optionId must be unique");
            Require(displays.Add(NormalizeDisplay(option.DisplayText)), "display must be unique");
        }
    }
}
```

The completion validator recomputes correctness, final count, canonical feedback order, and `worldCleared == true`.

- [ ] **Step 5: Verify Unity contract parity is GREEN**

Re-run the command from Step 2 with result paths ending in `green`. Expected: the complete `QuizContractTests` class passes with zero failures and no existing normal-quiz DTO is widened to two items.

---

### Task 3: Plan Fresh Reduced-Complexity Internal Material

**Files:**

- Modify: `services/wayline_forge/app/adaptive_planner.py`
- Modify: `services/wayline_forge/app/slot_materializer.py`
- Modify: `services/wayline_forge/app/batch_material.py`
- Modify: `services/wayline_forge/tests/test_adaptive_planner.py`
- Modify: `services/wayline_forge/tests/test_slot_materializer.py`
- Modify: `services/wayline_forge/tests/test_batch_material.py`

**Interfaces:**

- Produces `plan_assisted_slots(state, route_plan) -> tuple[SlotIntent, SlotIntent, SlotIntent]`.
- Adds internal tier `assisted_route: 3` without adding it to public `BattleTier`.
- Adds slot kinds `assisted_worked_example` and `assisted_supported_mcq`.
- Assisted items report `valid_for_progression == False` as defense in depth.

- [ ] **Step 1: Write planner tests for exact shape, weakest skills, and all-history exclusions**

Add a test state with observations from two completed batches and assert:

```python
slots = plan_assisted_slots(state, evaluate_world_clear(state, "valuehold").assisted_route_plan)
self.assertEqual(
    tuple(slot.kind for slot in slots),
    ("assisted_worked_example", "assisted_supported_mcq", "assisted_supported_mcq"),
)
self.assertEqual(tuple(slot.skill_id for slot in slots), ("place_value", "place_value", "mental_add_sub"))
self.assertEqual(
    set(slots[0].excluded_question_ids),
    {"question-old-001", "question-old-002"},
)
self.assertEqual(
    set(slots[0].excluded_operand_signatures),
    {"operand-old-001", "operand-old-002"},
)
```

Add a materialization test asserting difficulties `(2, 1, 1)`, three distinct question semantic hashes, three distinct operand signatures, and no prior excluded value appears.

- [ ] **Step 2: Run the planning/material tests and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_adaptive_planner \
  services.wayline_forge.tests.test_slot_materializer \
  services.wayline_forge.tests.test_batch_material -v
```

Expected: missing `plan_assisted_slots`, unsupported tier, or unsupported slot-kind failures.

- [ ] **Step 3: Implement the assisted planner using every prior public observation**

Add:

```python
def plan_assisted_slots(
    state: LearnerState,
    route_plan: AssistedRoutePlan,
) -> tuple[SlotIntent, SlotIntent, SlotIntent]:
    if route_plan.item_count != 3 or len(route_plan.slots) != 3:
        raise ValueError("assisted route plan must contain exactly three slots")
    if state.active_world_id is None:
        raise ValueError("assisted route requires an active world")

    observations = tuple(
        item for item in state.events if isinstance(item, ObservationEvent)
    )
    excluded_items = tuple(dict.fromkeys(item.item_id for item in observations))
    excluded_questions = tuple(dict.fromkeys(item.question_id for item in observations))
    excluded_operands = tuple(dict.fromkeys(item.operand_signature for item in observations))
    latest_templates = _last_batch_exclusions(state)[2]

    intents = []
    for planned in route_plan.slots:
        kind = {
            "worked_example": "assisted_worked_example",
            "supported_mcq": "assisted_supported_mcq",
        }.get(planned.kind)
        if kind is None or not planned.support_provided:
            raise ValueError("assisted route slot is not supported")
        intents.append(SlotIntent(
            kind=kind,
            campaign_world_id=state.active_world_id,
            content_world_id=state.active_world_id,
            skill_id=planned.skill_id,
            excluded_item_ids=excluded_items,
            excluded_question_ids=excluded_questions,
            excluded_template_ids=latest_templates,
            excluded_operand_signatures=excluded_operands,
        ))
    return tuple(intents)  # type: ignore[return-value]
```

- [ ] **Step 4: Add the internal tier and exact difficulty schedule**

Extend the shared internal tables:

```python
QUIZ_LENGTH_BY_TIER["assisted_route"] = 3
DIFFICULTY_SCHEDULE_BY_TIER["assisted_route"] = (2, 1, 1)
SUPPORTED_SLOT_KINDS = SUPPORTED_SLOT_KINDS | frozenset({
    "assisted_worked_example",
    "assisted_supported_mcq",
})
```

Do not add `assisted_route` to the public `BattleTier` enum or `battle-quiz-request.schema.json`.

- [ ] **Step 5: Make assisted material practice-only at the material boundary**

Change the property to:

```python
@property
def valid_for_progression(self) -> bool:
    return self.kind not in {
        "assisted_worked_example",
        "assisted_supported_mcq",
    }
```

Retain the existing semantic/content/operand exclusion checks. A reviewed-cache miss under the new exclusions remains a typed preparation failure.

- [ ] **Step 6: Verify planning, exact difficulty, freshness, and normal tiers are GREEN**

Re-run Step 2. Expected: assisted material is `(2, 1, 1)`, every previous question/operand is excluded, assisted items are practice-only, and all normal tier tests remain unchanged.

---

### Task 4: Implement Pure Assisted Projection and Sealed Scoring

**Files:**

- Create: `services/wayline_forge/app/assisted_route_machine.py`
- Create: `services/wayline_forge/tests/test_assisted_route_machine.py`

**Interfaces:**

- Produces `public_assisted_batch(route_id, material) -> AssistedRouteBatch`.
- Produces `score_assisted_route(route_id, material, selections) -> AssistedRouteScore`.
- `AssistedRouteScore` contains two public item results plus internal selected procedure IDs and provenance receipts.

- [ ] **Step 1: Write key-secrecy and score tests**

Build a verified three-item fixture and assert:

```python
public = public_assisted_batch("assisted-route-001", material)
self.assertEqual(public.worked_example.item_id, material.items[0].item_id)
self.assertEqual(tuple(item.item_id for item in public.items), tuple(
    item.item_id for item in material.items[1:]
))
self.assertTrue(public.worked_example.correct_answer)
supported_json = json.dumps(
    [item.model_dump(by_alias=True) for item in public.items],
    sort_keys=True,
).casefold()
for banned in ("correct", "procedure", "feedback", "reliable", "trusted", "sourcebatch"):
    self.assertNotIn(banned, supported_json)

score = score_assisted_route("assisted-route-001", material, wrong_selections)
self.assertEqual(score.final_correct, 0)
self.assertEqual(len(score.items), 2)
self.assertTrue(all(item.possible_error for item in score.items))
self.assertEqual(
    score.items[0].canonical_feedback,
    (
        score.items[0].possible_error,
        score.items[0].reliable_method,
        *score.items[0].trusted_steps,
    ),
)
```

Also reject wrong item order, duplicate items, forged option IDs, unsupported confidence, a two-item material, a four-item material, wrong tier, wrong slot kinds, non-`(2,1,1)` difficulty, and any unverified source.

- [ ] **Step 2: Run the machine test and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest services.wayline_forge.tests.test_assisted_route_machine -v
```

Expected: import failure because `assisted_route_machine.py` does not exist.

- [ ] **Step 3: Define immutable score records and validate internal material**

Implement:

```python
@dataclass(frozen=True, slots=True)
class AssistedRouteScore:
    route_id: str
    final_correct: int
    items: tuple[AssistedItemResult, AssistedItemResult]
    selected_procedure_ids: tuple[str | None, str | None]
    receipts: tuple[ProvenanceReceipts, ProvenanceReceipts]
    material_sha256: str


def _require_assisted_material(material: VerifiedBatchMaterial) -> None:
    if material.context.battle_tier != "assisted_route":
        raise AssistedRouteMachineError("material_context_mismatch")
    if len(material.items) != 3:
        raise AssistedRouteMachineError("material_count_mismatch")
    if tuple(item.kind for item in material.items) != (
        "assisted_worked_example",
        "assisted_supported_mcq",
        "assisted_supported_mcq",
    ):
        raise AssistedRouteMachineError("material_kind_mismatch")
    if tuple(item.bundle.blueprint.difficulty for item in material.items) != (2, 1, 1):
        raise AssistedRouteMachineError("material_difficulty_mismatch")
```

- [ ] **Step 4: Project only the allowlisted public fields**

Construct the worked example from `material.public_batch.items[0]` and `material.sealed_quiz.items[0]`. Construct supported items from public items one and two only. Never serialize `material.public_batch` as the assisted response, because it contains a third answerable MCQ rather than the worked/supported split.

- [ ] **Step 5: Score only the two supported selections from sealed material**

For each supported pair:

```python
selected_option = next(
    option for option in public_item.options
    if option.option_id == selection.option_id
)
sealed_item = sealed_by_id[selection.item_id]
is_correct = selection.option_id == sealed_item.correct_option_id
possible_error = None if is_correct else dict(sealed_item.possible_errors)[selection.option_id]
canonical_feedback = tuple(
    value for value in (
        possible_error,
        sealed_item.reliable_method,
        *sealed_item.trusted_steps,
    ) if value is not None
)
result = AssistedItemResult(
    itemId=selection.item_id,
    selectedOptionId=selection.option_id,
    selectedAnswer=selected_option.display_text,
    confidence=selection.confidence,
    correctOptionId=sealed_item.correct_option_id,
    correctAnswer=sealed_item.correct_answer,
    isCorrect=is_correct,
    possibleError=possible_error,
    reliableMethod=sealed_item.reliable_method,
    trustedSteps=sealed_item.trusted_steps,
    canonicalFeedback=canonical_feedback,
)
```

Read the internal procedure ID with `material.items[index].route_for_option(selection.option_id)`. Never accept client feedback, answer text, correctness, or procedure identity.

- [ ] **Step 6: Verify pure projection and scoring are GREEN**

Re-run Step 2. Expected: zero supported-key surface before scoring, exact canonical feedback afterward, and `0/2` is a valid score.

---

### Task 5: Add the Dedicated Hashed Assisted Route Store

**Files:**

- Create: `services/wayline_forge/app/assisted_route_store.py`
- Create: `services/wayline_forge/tests/test_assisted_route_store.py`
- Modify: `services/wayline_forge/app/quiz_store.py`
- Modify: `services/wayline_forge/tests/test_quiz_store.py`

**Interfaces:**

- `AssistedRouteStore(path, compiler, manifest)` uses the same profile database file through its own connection.
- Produces `load_preparation`, `load_active`, `create_prepared`, `load`, and `active_route_id`.
- Completion state is derived from the profile event log; private material is never marked completed in a second transaction.

- [ ] **Step 1: Write persistence, replay, tamper, and cross-activity tests**

Cover these exact outcomes:

```python
stored = store.create_prepared(
    route_id="assisted-route-001",
    profile_id=profile_id,
    source_session_id=session_id,
    world_id="valuehold",
    preparation_request_id="prepare-assisted-001",
    preparation_payload_sha256="1" * 64,
    event_head_ordinal=4,
    event_head_hash="2" * 64,
    plan_sha256="3" * 64,
    material=material,
)
self.assertEqual(store.load("assisted-route-001", profile_id=profile_id), stored)
self.assertEqual(
    store.load_preparation("prepare-assisted-001", profile_id=profile_id),
    stored,
)
self.assertEqual(store.active_route_id(profile_id), "assisted-route-001")
```

Then test same-request/same-payload replay, same-request/different-payload conflict, different-request/same-active-route alias receipt, private JSON tamper, digest tamper, route/profile mismatch, stale event head, one active normal quiz blocking route creation, and one active route blocking `QuizStore.create_prepared`.

- [ ] **Step 2: Run store tests and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_assisted_route_store \
  services.wayline_forge.tests.test_quiz_store -v
```

Expected: missing store module and missing QuizStore cross-activity guard.

- [ ] **Step 3: Create schema version 1 in one immediate transaction**

Use this exact table shape:

```sql
CREATE TABLE IF NOT EXISTS assisted_route_store_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS assisted_route_material (
    route_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    event_head_ordinal INTEGER NOT NULL CHECK (event_head_ordinal >= 0),
    event_head_hash TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    material_json TEXT NOT NULL,
    material_sha256 TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE (profile_id, world_id),
    FOREIGN KEY (profile_id) REFERENCES local_profiles(profile_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assisted_route_preparation_receipts (
    profile_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    PRIMARY KEY (profile_id, request_id),
    FOREIGN KEY (route_id) REFERENCES assisted_route_material(route_id) ON DELETE CASCADE
);
```

Validate exact columns, keys, foreign keys, indexes, and `schema_version == 1` on every open. Configure `foreign_keys=ON`, WAL compatibility, bounded busy timeout, and a maximum private material size equal to the existing batch-material limit.

- [ ] **Step 4: Reconstruct and revalidate private material on every load**

Read `material_json`, verify its SHA-256 with `hmac.compare_digest`, call `VerifiedBatchMaterial.from_private_json(compiler, manifest)`, run `_require_assisted_material`, and verify route/profile/world/session/plan bindings. Derive the public response from reconstructed material; do not trust a stored public JSON copy.

- [ ] **Step 5: Make preparation receipts exact and alias-safe**

An exact request replay returns the same route only when payload and output hashes match. A new request ID for the same active `(profile, world)` inserts a new receipt pointing to the existing route after verifying the current assisted plan hash. A reused request ID with any changed profile, session, world, or payload raises `AssistedRouteStoreError("idempotency_conflict")`.

- [ ] **Step 6: Serialize normal-quiz and assisted-route creation across the shared database**

Inside `AssistedRouteStore.create_prepared`, after `BEGIN IMMEDIATE`, reject any `quiz_machines` row for the profile whose state is not `closed`. Inside `QuizStore._require_live_batch_slot_in_transaction`, if the assisted tables exist, reject a route row for which no matching completion event exists:

```sql
SELECT route_id
FROM assisted_route_material AS route
WHERE route.profile_id = ?
  AND NOT EXISTS (
      SELECT 1
      FROM event_log AS event
      WHERE event.profile_id = route.profile_id
        AND event.event_type = 'assisted_route_completion'
        AND event.semantic_key = 'assisted_route_completion:' || route.world_id
  )
LIMIT 1
```

Because both creation paths hold `BEGIN IMMEDIATE`, concurrent normal/assisted preparation can persist only one active activity.

- [ ] **Step 7: Verify store recovery and cross-activity guards are GREEN**

Re-run Step 2. Expected: byte-stable restart replay, fail-closed tamper handling, one active activity per profile, and no change to normal QuizStore item-count constraints.

---

### Task 6: Reuse the Existing Verifier/Cache Pipeline Without Persisting a Normal Quiz

**Files:**

- Modify: `services/wayline_forge/app/orchestrator.py`
- Modify: `services/wayline_forge/app/battle_preparation.py`
- Modify: `services/wayline_forge/tests/test_orchestrator.py`
- Modify: `services/wayline_forge/tests/test_battle_preparation.py`

**Interfaces:**

- Produces `BatchPreparationOrchestrator.build_verified_material(*, context: BatchContext, intents: tuple[SlotIntent, ...], batch_seed: int, batch_id: str) -> VerifiedBatchMaterial`.
- Produces `BattlePreparationService.prepare_assisted_route(*, request_id: str, profile_id: str, current_session_id: str, world_id: str) -> AssistedRouteBatch`, avoiding a dependency from battle preparation back into progression command types.
- Normal `prepare()` preserves its store-replay-before-dependency behavior.

- [ ] **Step 1: Write a material-only orchestration test**

Assert one call creates a complete assisted material without a `quiz_machines` row:

```python
material = await orchestrator.build_verified_material(
    context=assisted_context,
    intents=assisted_intents,
    batch_seed=731,
    batch_id="batch_assisted_internal_001",
)
self.assertEqual(material.context.battle_tier, "assisted_route")
self.assertEqual(tuple(item.bundle.blueprint.difficulty for item in material.items), (2, 1, 1))
self.assertIsNone(store.resumable_batch_id(profile_id))
```

Add a fallback test proving that a reviewed row matching a previously exposed operand signature is rejected and an exhausted fresh cache raises `fallback_unavailable` rather than accepting old content.

- [ ] **Step 2: Run orchestration/preparation tests and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_orchestrator \
  services.wayline_forge.tests.test_battle_preparation -v
```

Expected: missing `build_verified_material` and assisted preparation service methods.

- [ ] **Step 3: Extract the bounded in-memory build from normal preparation**

Implement this public internal-runtime seam:

```python
async def build_verified_material(
    self,
    *,
    context: BatchContext,
    intents: tuple[SlotIntent, ...],
    batch_seed: int,
    batch_id: str,
) -> VerifiedBatchMaterial:
    started_at = self._monotonic()
    live_deadline = started_at + LIVE_WINDOW_SECONDS
    preparation_deadline = started_at + PREPARATION_WINDOW_SECONDS
    planned_slots = materialize_slots(
        intents,
        context.battle_tier,
        batch_seed,
        self._compiler,
    )
    builder = BatchMaterialBuilder(
        batch_id=batch_id,
        context=context,
        planned_slots=planned_slots,
        item_id_factory=self._item_id_factory,
    )
    if await self._begin_provider_preparation(live_deadline):
        await self._fill_live(builder, batch_seed, live_deadline, preparation_deadline)
    if builder.next_slot is not None:
        while self._monotonic() < live_deadline:
            await self._sleeper(live_deadline - self._monotonic())
        await self._fill_reviewed_cache(builder, preparation_deadline)
    self._require_before(preparation_deadline)
    return builder.finalize()
```

Keep the normal preparation replay lookup before calling this method, then atomically write its result with `QuizStore.create_prepared`.

- [ ] **Step 4: Add authenticated fresh assisted preparation**

`BattlePreparationService.prepare_assisted_route(*, request_id, profile_id, current_session_id, world_id) -> AssistedRouteBatch` performs this sequence:

1. Authenticate the current profile/session.
2. Drain normal quiz observations and load current learner state.
3. Require active-world equality and an unlocked, incomplete assisted plan.
4. Check an exact preparation receipt or an already-active route before touching planner, clocks, provider, cache, or ID factories.
5. Capture the current event-head ordinal/hash.
6. Call `plan_assisted_slots` and `build_verified_material` with context battle ID `<world>_assisted_route` and tier `assisted_route`.
7. Re-authenticate and recheck the event head and gate.
8. Persist through `AssistedRouteStore.create_prepared`.
9. Return `public_assisted_batch` with the caller's request ID.

Use a deterministic seed over catalog receipt, curriculum receipt, profile, world, plan hash, event-head hash, and preparation request identity.

- [ ] **Step 5: Make stale generation and safe-content failures explicit**

If the event head changes while generation is in flight, discard the unpersisted material and return `target_in_progress` after reevaluation. Translate compiler, verifier, cache, or bounded-deadline exhaustion to `safe_content_unavailable`; never fall back to old Seal-Trial material.

- [ ] **Step 6: Verify orchestration and authenticated preparation are GREEN**

Re-run Step 2. Expected: no raw or partial batch writes, exact preparation replay touches no provider/cache/clock, and safe cache exhaustion is typed and bounded.

---

### Task 7: Persist Practice Answers Without Inflating Unassisted Evidence

**Files:**

- Modify: `services/wayline_forge/app/events.py`
- Modify: `services/wayline_forge/app/evidence_reducer.py`
- Modify: `services/wayline_forge/app/profile_store.py`
- Modify: `services/wayline_forge/tests/test_event_v2.py`
- Modify: `services/wayline_forge/tests/test_evidence_reducer.py`
- Modify: `services/wayline_forge/tests/test_legacy_migration.py`
- Modify: `services/wayline_forge/tests/test_profile_export.py`

**Interfaces:**

- Replaces the old reused-source fields in `AssistedRouteCompletionEvent` with fresh route/material/question/answer/feedback/provenance fields.
- `reduce_events` includes two assisted `AnswerRecord` values but derives procedures, skills, gates, and mastery only from `ObservationEvent`.

- [ ] **Step 1: Write event round-trip and evidence-isolation tests**

Create a `wayline.event.v2` assisted event with two wrong supported answers. Assert canonical JSON round-trip equality and:

```python
before = reduce_events(events_before_completion)
after = reduce_events((*events_before_completion, assisted_event))
self.assertEqual(len(after.answers), len(before.answers) + 2)
self.assertEqual(after.procedures, before.procedures)
self.assertEqual(after.skills, before.skills)
self.assertEqual(
    after.world("valuehold").valid_item_count,
    before.world("valuehold").valid_item_count,
)
self.assertEqual(after.answers[-2].first_confidence, "leaning")
self.assertEqual(after.answers[-2].explanations_shown, assisted_event.canonical_feedback[0])
```

Add an export test proving selected answer text, confidence, compatible procedure, and feedback appear in the local event payload.

- [ ] **Step 2: Run event/evidence/migration tests and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_event_v2 \
  services.wayline_forge.tests.test_evidence_reducer \
  services.wayline_forge.tests.test_legacy_migration \
  services.wayline_forge.tests.test_profile_export -v
```

Expected: old assisted event fields do not satisfy the fresh event test and assisted answers are absent from `LearnerState.answers`.

- [ ] **Step 3: Define the fresh v2 completion event**

Use these required fields:

```python
@dataclass(frozen=True, slots=True)
class AssistedRouteCompletionEvent(EventBase):
    route_revision: str
    route_id: str
    material_sha256: str
    worked_example_item_id: str
    supported_item_ids: tuple[str, str]
    supported_question_ids: tuple[str, str]
    selected_option_ids: tuple[str, str]
    selected_answers: tuple[str, str]
    correct_option_ids: tuple[str, str]
    correct_answers: tuple[str, str]
    confidences: tuple[str, str]
    correctness: tuple[bool, bool]
    selected_procedure_ids: tuple[str | None, str | None]
    possible_errors: tuple[str | None, str | None]
    reliable_methods: tuple[str, str]
    trusted_steps: tuple[tuple[str, ...], tuple[str, ...]]
    canonical_feedback: tuple[tuple[str, ...], tuple[str, ...]]
    receipts: tuple[ProvenanceReceipts, ProvenanceReceipts]
    final_correct: int
    item_count: int

    EVENT_TYPE: ClassVar[str] = "assisted_route_completion"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {OUTCOME_EVENT_SCHEMA_VERSION}
    )
```

Require `route_revision == "fresh-assisted-v1"`, two distinct items/questions, correctness derived from option IDs, procedure present exactly when wrong, possible error present exactly when wrong, canonical feedback derived from possible error/method/steps, `item_count == 2`, and exact final count. Keep semantic key `assisted_route_completion:<world_id>`.

- [ ] **Step 4: Extend strict event decoding for nested tuples and receipts**

In `event_from_dict`, convert every list field to its exact tuple type and rebuild both `ProvenanceReceipts` values before constructing the event. Reject extra/missing event fields through the dataclass constructor and ProfileStore canonical replay checks.

- [ ] **Step 5: Add assisted answers to history only**

After building observation-derived answers, append two `AnswerRecord` values per assisted event with `batch_id=route_id`, `first_option_id == final_option_id`, identical first/final confidence and correctness, and `explanations_shown=canonical_feedback[index]`. Do not add assisted events to the `observations` tuple used by procedure, skill, world-item, gate, transfer, secure, or mastery reducers.

- [ ] **Step 6: Block old reused-item assisted events instead of transforming them**

During the existing profile schema audit, detect `event_type='assisted_route_completion'` rows whose canonical JSON lacks `"route_revision":"fresh-assisted-v1"`. Record the owning profile in `legacy_outcome_profiles` with marker `wayline.assisted-reused.v0`; `load_state` then raises the existing `LegacyOutcomeProfileError`. The release migration report instructs deletion/reset of those disposable development profiles. Do not synthesize fresh question or answer history from an event whose supported keys had already been exposed.

- [ ] **Step 7: Verify event v2, answer retention, isolation, and migration are GREEN**

Re-run Step 2. Expected: byte-stable event replay, two retained practice answers, unchanged unassisted evidence, strict export, and fail-closed old-profile detection.

---

### Task 8: Integrate Authoritative Preparation, Completion, and Replay

**Files:**

- Modify: `services/wayline_forge/app/progression.py`
- Modify: `services/wayline_forge/app/application.py`
- Modify: `services/wayline_forge/tests/test_progression_assisted_store.py`
- Modify: `services/wayline_forge/tests/test_progression_commands.py`
- Modify: `services/wayline_forge/tests/test_progression_rules.py`
- Modify: `services/wayline_forge/tests/test_application_progression_facade.py`
- Modify: `services/wayline_forge/tests/test_valuehold_application.py`

**Interfaces:**

- `ProgressionCommandService.prepare_assisted_route` and `WaylineApplication.prepare_assisted_route` become async.
- Completion loads only fresh `AssistedRouteStore` material; `_derive_assisted_route` is removed.
- Exact concurrent replay returns the same result; mismatches fail with stable codes.

- [ ] **Step 1: Replace the old reuse test with a fresh production-store test**

The test must persist two missed Seal Trials, then assert:

```python
prepared = await progression.prepare_assisted_route(request)
old_item_ids = {
    item.item_id
    for material in missed_trial_materials
    for item in material.items
}
self.assertTrue(old_item_ids.isdisjoint({
    prepared.batch.worked_example.item_id,
    *(item.item_id for item in prepared.batch.items),
}))
self.assertEqual(provider.verified_generation_count + cache.hit_count, 3)
self.assertIsNone(quizzes.resumable_batch_id(profile_id))
```

Complete with two wrong answers and assert world clear, `final_correct == 0`, one fresh completion event, no procedure/skill delta, and exact replay after closing/reopening all stores.

- [ ] **Step 2: Run progression/application tests and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_progression_assisted_store \
  services.wayline_forge.tests.test_progression_commands \
  services.wayline_forge.tests.test_progression_rules \
  services.wayline_forge.tests.test_application_progression_facade \
  services.wayline_forge.tests.test_valuehold_application -v
```

Expected: the current service still reuses Seal material and preparation is synchronous.

- [ ] **Step 3: Inject AssistedRouteStore and correct the special-preparer protocol**

Pass the route store into `BattlePreparationService`, `ProgressionCommandService`, and `WaylineApplication`. Move `prepare_second_wind` back onto `_SpecialBattlePreparer` and add the exact async assisted preparation method; remove the accidental method nesting under `_AssistedRouteAuthority`. Remove the old public projection dataclasses `AssistedRouteWorkedExample`, `AssistedRouteMcq`, `AssistedRouteBatch`, and `AssistedRouteItemResult` from `progression.py`; import their strict replacements from `contracts.py`. Retain only transport-neutral command request/result records in `progression.py`, with completion selections normalized to the strict `AssistedSelection` contract type.

- [ ] **Step 4: Replace preparation derivation with the fresh async service**

Delete `_derive_assisted_route`. `ProgressionCommandService.prepare_assisted_route` authenticates, rejects already-cleared or locked states, then awaits `BattlePreparationService.prepare_assisted_route`. It validates exact response type, world, one worked item, two supported items, and route ownership before returning.

- [ ] **Step 5: Complete against sealed route material and append the fresh event**

Load the route by path-owned route ID and profile, call `score_assisted_route`, then build `AssistedRouteCompletionEvent` only from score/material/store authority. Append it even when `final_correct == 0`. Build `AssistedRouteCompleted` from the event and require `evaluate_world_clear(self._state(request.profile_id), request.world_id).cleared` afterward.

- [ ] **Step 6: Make append races idempotent**

Before scoring, scan existing completion events:

- same request ID and byte-identical route/selections/confidence returns `_assisted_result(existing)`;
- same request ID with any difference raises `idempotency_conflict`;
- different request after a completion for the world raises `target_already_completed`.

If append raises an idempotency or semantic conflict, reload events once and apply the same comparison. This makes two simultaneous exact requests return the same result while two different requests yield one success and one target conflict.

- [ ] **Step 7: Preserve boss victory and clear regardless of score**

Keep `evaluate_world_clear` based on presence of a post-boss assisted completion event, not its score. Assert `boss_replay_required == False`, `combat_victory_preserved == True`, and `assisted_route_plan is None` after completion.

- [ ] **Step 8: Verify authoritative progression and restart replay are GREEN**

Re-run Step 2. Expected: fresh verified material only, `0/2` clear, no normal quiz machine, exact restart replay, and no provider/cache/clock calls during replay.

---

### Task 9: Publish the Two Assisted API Routes Without Widening Generic Quiz Access

**Files:**

- Modify: `services/wayline_forge/app/api.py`
- Modify: `services/wayline_forge/tests/api_fixtures.py`
- Modify: `services/wayline_forge/tests/test_progression_api.py`

**Interfaces:**

- `POST /v1/worlds/{world_id}/assisted-routes` returns `201 AssistedRoutePrepared`.
- `POST /v1/worlds/{world_id}/assisted-routes/{route_id}/completion` returns `200 AssistedRouteCompleted`.
- Profile, world, route, and current session are server/path-owned; bodies cannot override them.

- [ ] **Step 1: Replace the expected 404 with full route lifecycle API tests**

Add tests for successful preparation/completion, supported-key absence, path binding, forged route/option, duplicate JSON members, current-session enforcement, exact replay, a new post-completion request returning `409`, and generic lookup isolation:

```python
prepared = await client.post(
    "/v1/worlds/valuehold/assisted-routes",
    json=self.common("prepare-assisted-001"),
    headers=self.fixture.public_headers(session=True),
)
self.assertEqual(prepared.status_code, 201)
supported = json.dumps(prepared.json()["batch"]["items"]).casefold()
for banned in ("correctoptionid", "correctanswer", "procedureid", "sourcebatchid"):
    self.assertNotIn(banned, supported)

route_id = prepared.json()["batch"]["routeId"]
generic = await client.get(
    f"/v1/quiz-batches/{route_id}",
    headers=self.fixture.public_headers(session=True),
)
self.assertEqual(generic.status_code, 404)
```

- [ ] **Step 2: Run the progression API test and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest services.wayline_forge.tests.test_progression_api -v
```

Expected: preparation still returns `route_not_found` because the public route is absent.

- [ ] **Step 3: Extend the facade protocol and method allowlist**

Add async `prepare_assisted_route` and synchronous `complete_assisted_route` to `WaylineApiFacade` and `_FACADE_METHODS`. Keep stable error translation: `safe_content_unavailable -> 503`, `target_in_progress -> 409`, `idempotency_conflict -> 409`, `target_already_completed -> 409`, and unknown internal errors -> redacted `500 integrity_failure`.

- [ ] **Step 4: Implement strict path-owned endpoints**

Preparation parses `AssistedRoutePrepare`, checks the header-resolved session, constructs `AssistedRoutePreparationRequest` with server-resolved profile and path world, awaits the facade, and validates exact output identity.

Completion parses `AssistedRouteComplete`, binds profile/session/world/route from authority, and never accepts those fields in the body. Validate every result identity and aggregate through `AssistedRouteCompleted` before JSON serialization.

- [ ] **Step 5: Verify no generic endpoint can address the route store**

Do not add AssistedRouteStore fallback to `GET /v1/quiz-batches/{batch_id}`, initial submit, revision submit, or quiz snapshot. An assisted route ID remains `404 batch_unavailable` on those paths even for its owning profile.

- [ ] **Step 6: Verify API lifecycle and redaction are GREEN**

Re-run Step 2. Expected: strict `201/200` lifecycle, no precompletion supported key, path authority, stable `409/503` errors, and generic route isolation.

---

### Task 10: Prove Quit/Reload, Deletion, and Concurrency Safety

**Files:**

- Modify: `services/wayline_forge/app/profile_store.py`
- Modify: `services/wayline_forge/tests/test_assisted_route_store.py`
- Modify: `services/wayline_forge/tests/test_profile_deletion.py`
- Modify: `services/wayline_forge/tests/test_progression_assisted_store.py`
- Modify: `services/wayline_forge/tests/test_valuehold_application.py`

**Interfaces:**

- A new current session and new preparation request recover the same active route.
- Profile deletion removes private route material and preparation receipts through verified foreign-key cascade.
- Concurrent prepare/complete operations preserve one route and one completion event.

- [ ] **Step 1: Write restart and new-session recovery tests**

Prepare a route, close all connections without completing, reopen stores, close the old session, create a new session, and call preparation with a new request ID. Assert identical route ID, worked example, supported options, and material digest; assert zero provider/cache/ID-factory calls during recovery.

- [ ] **Step 2: Write deletion-cascade tests**

Prepare a route, delete the authenticated profile, then assert direct SQL counts are zero for both assisted tables. Reopen `AssistedRouteStore` and assert route lookup fails with `profile_not_found` without leaking whether a route previously existed.

- [ ] **Step 3: Write true concurrent creation and completion tests**

Use two store connections and `asyncio.gather`:

- two exact preparations produce one material row and two valid receipts;
- assisted preparation racing normal quiz preparation produces exactly one active activity;
- two exact completion requests produce identical public results and one event;
- two different completion requests produce one success and one target conflict;
- completion racing a new normal quiz causes the quiz creation to wait, then proceed only after the completion event makes the route inactive.

- [ ] **Step 4: Run lifecycle tests and verify RED**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_assisted_route_store \
  services.wayline_forge.tests.test_profile_deletion \
  services.wayline_forge.tests.test_progression_assisted_store \
  services.wayline_forge.tests.test_valuehold_application -v
```

Expected: at least one missing recovery alias, cascade assertion, or concurrency replay assertion before the lifecycle logic is complete.

- [ ] **Step 5: Make active-route recovery profile-owned rather than session-locked**

Retain `source_session_id` for audit, but authenticate recovery through the current session and owning profile. A new current session may receive the existing immutable route; a different profile always receives a redacted not-found/authorization error.

- [ ] **Step 6: Verify foreign-key cascade before reporting deletion success**

ProfileStore already enables foreign keys. Keep the route tables referencing `local_profiles ON DELETE CASCADE`. In `ProfileStore.delete_profile`, after deleting the profile and before committing, query both assisted tables when they exist and raise `IdentityStoreCorruptionError` if either still contains the profile ID. `ProfileDeletionService.delete` translates that failure to `integrity_failure`.

- [ ] **Step 7: Verify lifecycle and concurrency are GREEN**

Re-run Step 4. Expected: durable reload, cross-session recovery, complete cascade, serialized activity creation, and stable exact completion replay.

---

### Task 11: Run Focused, Full, Security, and Cross-Runtime Release Gates

**Files:**

- Verify only; no new production file is introduced by this task.

**Interfaces:**

- Produces release evidence for contracts, verifier-only content, state recovery, evidence isolation, deletion, Python packaging compatibility, and Unity parity.

- [ ] **Step 1: Run the complete assisted/progression focus set**

Run:

```bash
.venv-live/bin/python -m unittest \
  services.wayline_forge.tests.test_progression_contracts \
  services.wayline_forge.tests.test_assisted_route_machine \
  services.wayline_forge.tests.test_assisted_route_store \
  services.wayline_forge.tests.test_adaptive_planner \
  services.wayline_forge.tests.test_slot_materializer \
  services.wayline_forge.tests.test_batch_material \
  services.wayline_forge.tests.test_orchestrator \
  services.wayline_forge.tests.test_battle_preparation \
  services.wayline_forge.tests.test_event_v2 \
  services.wayline_forge.tests.test_evidence_reducer \
  services.wayline_forge.tests.test_progression_assisted_store \
  services.wayline_forge.tests.test_progression_commands \
  services.wayline_forge.tests.test_progression_rules \
  services.wayline_forge.tests.test_progression_api \
  services.wayline_forge.tests.test_profile_deletion \
  services.wayline_forge.tests.test_profile_export \
  services.wayline_forge.tests.test_valuehold_application -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the complete Wayline Forge suite**

Run:

```bash
.venv-live/bin/python -m unittest discover -s services/wayline_forge/tests -v
```

Expected: zero failures and zero errors.

- [ ] **Step 3: Compile every Python service module**

Run:

```bash
.venv-live/bin/python -m compileall -q services/wayline_forge/app services/wayline_forge/tests
```

Expected: exit code `0` with no syntax errors.

- [ ] **Step 4: Run the Unity EditMode contract suite**

Run:

```bash
/Applications/Unity/Hub/Editor/6000.3.11f1/Unity.app/Contents/MacOS/Unity \
  -batchmode -nographics \
  -projectPath "$PWD/unity/Wayline" \
  -runTests -testPlatform EditMode \
  -testFilter Wayline.Tests.Learning.QuizContractTests \
  -testResults /tmp/wayline-assisted-contract-final.xml \
  -logFile /tmp/wayline-assisted-contract-final.log
```

Expected: zero failed Unity tests and a well-formed NUnit XML result.

- [ ] **Step 5: Run the prepared-payload security scan**

Run:

```bash
.venv-live/bin/python -c 'import json, pathlib; p=json.loads(pathlib.Path("contracts/wayline/v1/fixtures/valid/assisted-route-prepared.json").read_text()); s=json.dumps(p["batch"]["items"]).casefold(); banned=("sourcebatchid","correctoptionid","correctanswer","procedureid","possibleerror","reliablemethod","trustedsteps"); assert not any(x in s for x in banned); assert len(p["batch"]["items"]) == 2'
```

Expected: exit code `0` and no output.

- [ ] **Step 6: Prove the unsafe reuse path is absent**

Run:

```bash
rg -n '_derive_assisted_route|Reuse three verifier-sealed missed items|sourceBatchId' \
  services/wayline_forge/app \
  contracts/wayline/v1/assisted-route-prepared.schema.json \
  contracts/wayline/v1/fixtures/valid/assisted-route-prepared.json
```

Expected: exit code `1` with no matches.

- [ ] **Step 7: Prove the core quiz cardinality stayed unchanged**

Run:

```bash
rg -n 'between 3 and 10|BETWEEN 3 AND 10|ge=3, le=10' \
  services/wayline_forge/app/quiz_machine.py \
  services/wayline_forge/app/quiz_store.py \
  services/wayline_forge/app/contracts.py
```

Expected: the existing normal-quiz `3..10` guards remain present; no generic two-item exception exists.

---

## Migration Policy

1. Create `AssistedRouteStore` schema version `1` transactionally. There is no earlier assisted-store schema to transform.
2. Do not change QuizStore's `3..10` item-count schema or normal QuizMachine serialization.
3. Keep new completion events at `wayline.event.v2` with required `route_revision="fresh-assisted-v1"`.
4. Detect old `assisted_route_completion` rows without that revision and mark their profiles with `wayline.assisted-reused.v0` in `legacy_outcome_profiles`.
5. Block those disposable development profiles through the existing legacy-profile error path and reset them explicitly. Do not fabricate fresh material, answers, feedback, or mastery from old exposed-key routes.
6. Keep public contracts at `wayline.v1`; Python and Unity ship the assisted schema change together.
7. Profile deletion cascades through both new assisted tables before returning success.

## Recovery Matrix

| Interruption | Required recovery |
| --- | --- |
| Before material persistence | No row exists; a retry may safely rebuild. |
| After persistence, before prepare response | Exact or new-request preparation returns the same stored route without generation. |
| After route display, before completion | New current session receives the same route and options. |
| During completion scoring | No event exists; exact retry scores the same immutable material. |
| After event append, before response | Exact retry reconstructs the identical result from the event. |
| Concurrent exact completion | One append wins; the other reloads and returns the same result. |
| Concurrent different completion | One append wins; the other returns target conflict. |
| Profile deletion | Foreign-key cascade removes material and receipts; subsequent lookup is redacted. |

## Self-Review Against `WAYLINE_LEARNING_AND_RUNTIME_SPEC.md`

- **Model boundary:** Covered by Tasks 3, 4, and 6. Trusted questions remain compiler-owned; distractors remain SLM-proposed and verifier-sealed; scoring and progression remain deterministic.
- **No raw learner-facing generation:** Covered by Tasks 4, 6, 9, and 11. Only reconstructed verified material reaches the public projection.
- **Sealed public API:** Covered by Tasks 1, 2, 4, and 9. Supported keys and diagnoses are absent until completion, and generic quiz endpoints cannot address route material.
- **Reduced operand complexity:** Covered by Task 3 with exact internal difficulty `(2,1,1)` and two level-1 supported MCQs.
- **Assisted route after two Seal misses:** Existing deterministic gate remains authoritative; Tasks 6 and 8 consume its plan and preserve boss victory.
- **Completion regardless of score:** Covered by Tasks 4, 7, and 8, including explicit `0/2` tests.
- **Answers, confidence, hypotheses, and explanations retained:** Covered by Task 7 and profile-export verification.
- **No mastery inflation:** Covered by Tasks 3 and 7. Assisted events are answer-history records only, never observations used by procedure, skill, secure, gate, or mastery reducers.
- **Idempotency and reload:** Covered by Tasks 5, 8, and 10, with an explicit interruption and concurrency matrix.
- **Security and privacy:** Covered by separate storage, allowlisted projection, strict path ownership, hash verification, bounded bodies, redacted errors, deletion cascade, and Task 11 scans.
- **Cross-runtime contract parity:** Covered by Tasks 1 and 2 using the same JSON fixtures in Python and Unity.
- **Freshness and reviewed fallback:** Covered by Tasks 3 and 6. All prior item/question/operand exposures are excluded; cache exhaustion fails closed.
- **Migration safety:** Covered by Task 7 and the migration policy. Previously exposed routes are blocked, not relabeled.

Self-review found no uncovered requirement, undefined cross-task interface, or intentionally deferred implementation. The plan introduces no generic two-item quiz path and no alternate scoring authority.
