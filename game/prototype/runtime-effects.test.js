import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveRenderEffects,
  updatePersistentAnnouncement,
} from "./runtime-effects.js";

function runState(encounterPhase, runPhase = "playing", encounterState = {}) {
  return {
    phase: runPhase,
    encounterState: { phase: encounterPhase, ...encounterState },
  };
}

test("gates route and patch animation events to their commit transitions", () => {
  assert.deepEqual(
    deriveRenderEffects(
      { type: "COMMIT_ANSWER" },
      runState("choose"),
      runState("counterbreak"),
    ),
    {
      focusTarget: { kind: "id", value: "glitch-name" },
      motionEvent: "route-commit",
      preventScroll: false,
    },
  );
  assert.deepEqual(
    deriveRenderEffects(
      { type: "COMMIT_REPAIR" },
      runState("counterbreak"),
      runState("resolved"),
    ),
    {
      focusTarget: { kind: "id", value: "trusted-proof-title" },
      motionEvent: "patch-commit",
      preventScroll: false,
    },
  );
});

test("does not replay phase motion for same-phase answer or repair interactions", () => {
  const cases = [
    {
      action: { type: "SELECT_ANSWER", answerId: "answer-1" },
      before: runState("choose"),
      after: runState("choose"),
      focusTarget: { kind: "answer", value: "answer-1" },
      preventScroll: true,
    },
    {
      action: { type: "SELECT_REPAIR", repairId: "repair-2" },
      before: runState("counterbreak"),
      after: runState("counterbreak"),
      focusTarget: { kind: "repair", value: "repair-2" },
      preventScroll: true,
    },
    {
      action: { type: "COMMIT_REPAIR" },
      before: runState("counterbreak", "playing", {
        selectedRepairId: "repair-2",
      }),
      after: runState("counterbreak"),
      focusTarget: { kind: "repair", value: "repair-2" },
      preventScroll: true,
    },
  ];

  for (const { action, before, after, focusTarget, preventScroll } of cases) {
    assert.deepEqual(deriveRenderEffects(action, before, after), {
      focusTarget,
      motionEvent: "none",
      preventScroll,
    });
  }
});

test("chooses deterministic focus targets for checkpoint and run transitions", () => {
  assert.deepEqual(
    deriveRenderEffects(
      { type: "ADVANCE_RUN" },
      runState("resolved"),
      runState("choose"),
    ).focusTarget,
    { kind: "id", value: "challenge-title" },
  );
  assert.equal(
    deriveRenderEffects(
      { type: "ADVANCE_RUN" },
      runState("resolved"),
      runState("choose"),
    ).preventScroll,
    false,
  );
  assert.deepEqual(
    deriveRenderEffects(
      { type: "ADVANCE_RUN" },
      runState("resolved"),
      runState("resolved", "complete"),
    ).focusTarget,
    { kind: "id", value: "run-complete-title" },
  );
  assert.deepEqual(
    deriveRenderEffects(
      { type: "RESET_RUN" },
      runState("resolved", "complete"),
      runState("choose"),
    ).focusTarget,
    { kind: "first-answer" },
  );
  assert.equal(
    deriveRenderEffects(
      { type: "RESET_RUN" },
      runState("resolved", "complete"),
      runState("choose"),
    ).preventScroll,
    false,
  );
});

test("updates one persistent announcer only when the status changes", () => {
  const announcer = { textContent: "" };

  assert.equal(updatePersistentAnnouncement(announcer, "Choose a route."), true);
  assert.equal(announcer.textContent, "Choose a route.");
  assert.equal(updatePersistentAnnouncement(announcer, "Choose a route."), false);
  assert.equal(updatePersistentAnnouncement(announcer, "Route repaired."), true);
  assert.equal(announcer.textContent, "Route repaired.");
});
