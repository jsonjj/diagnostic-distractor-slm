import assert from "node:assert/strict";
import test from "node:test";

import * as encounterState from "./encounter.js";

function makeEncounter(id) {
  return {
    id,
    correctAnswerId: `${id}-correct`,
    featuredCounterfeitId: `${id}-counterfeit`,
    counterfeits: [
      {
        id: `${id}-counterfeit`,
        answerId: `${id}-wrong`,
        misconception: "Uses the wrong operation",
        repairId: `${id}-repair`,
      },
    ],
    repairChoices: [{ id: `${id}-repair` }],
  };
}

const encounters = [
  makeEncounter("checkpoint-one"),
  makeEncounter("checkpoint-two"),
  makeEncounter("checkpoint-three"),
];

test("starts a run at the first checkpoint with clean totals", () => {
  assert.equal(typeof encounterState.createInitialRunState, "function");

  const state = encounterState.createInitialRunState(encounters);

  assert.equal(state.phase, "playing");
  assert.equal(state.encounterIndex, 0);
  assert.deepEqual(state.encounterState, encounterState.createInitialEncounterState());
  assert.equal(state.completedEncounterCount, 0);
  assert.equal(state.proofBoostCount, 0);
  assert.equal(state.repairAttemptCount, 0);
});

test("refuses to start a run without any encounters", () => {
  assert.throws(
    () => encounterState.createInitialRunState([]),
    /at least one encounter/i,
  );
});

test("routes encounter actions to the active checkpoint", () => {
  assert.equal(typeof encounterState.reduceRun, "function");
  const initial = encounterState.createInitialRunState(encounters);

  const state = encounterState.reduceRun(encounters, initial, {
    type: "SELECT_ANSWER",
    answerId: "checkpoint-one-wrong",
  });

  assert.equal(state.encounterIndex, 0);
  assert.equal(state.encounterState.selectedAnswerId, "checkpoint-one-wrong");
});

test("advances from a resolved checkpoint and banks its run totals", () => {
  let state = encounterState.createInitialRunState(encounters);
  state = encounterState.reduceRun(encounters, state, {
    type: "SELECT_ANSWER",
    answerId: "checkpoint-one-correct",
  });
  state = encounterState.reduceRun(encounters, state, { type: "COMMIT_ANSWER" });
  state = encounterState.reduceRun(encounters, state, {
    type: "SELECT_REPAIR",
    repairId: "checkpoint-one-repair",
  });
  state = encounterState.reduceRun(encounters, state, { type: "COMMIT_REPAIR" });
  assert.equal(state.encounterState.phase, "resolved");

  state = encounterState.reduceRun(encounters, state, { type: "ADVANCE_RUN" });

  assert.equal(state.phase, "playing");
  assert.equal(state.encounterIndex, 1);
  assert.deepEqual(state.encounterState, encounterState.createInitialEncounterState());
  assert.equal(state.completedEncounterCount, 1);
  assert.equal(state.proofBoostCount, 1);
  assert.equal(state.repairAttemptCount, 1);
});

test("completes the run after the final resolved checkpoint", () => {
  const initial = encounterState.createInitialRunState(encounters);
  const finalResolved = {
    ...initial,
    encounterIndex: 2,
    completedEncounterCount: 2,
    proofBoostCount: 1,
    repairAttemptCount: 2,
    encounterState: {
      ...encounterState.createInitialEncounterState(),
      phase: "resolved",
      repairAttempts: 2,
    },
  };

  const state = encounterState.reduceRun(encounters, finalResolved, {
    type: "ADVANCE_RUN",
  });

  assert.equal(state.phase, "complete");
  assert.equal(state.encounterIndex, 2);
  assert.equal(state.completedEncounterCount, 3);
  assert.equal(state.proofBoostCount, 1);
  assert.equal(state.repairAttemptCount, 4);
});

test("resets a completed run to the first clean checkpoint", () => {
  const completed = {
    ...encounterState.createInitialRunState(encounters),
    phase: "complete",
    encounterIndex: 2,
    completedEncounterCount: 3,
    proofBoostCount: 2,
    repairAttemptCount: 5,
    encounterState: {
      ...encounterState.createInitialEncounterState(),
      phase: "resolved",
    },
  };

  const state = encounterState.reduceRun(encounters, completed, {
    type: "RESET_RUN",
  });

  assert.deepEqual(state, encounterState.createInitialRunState(encounters));
});

test("ignores encounter actions after a run is complete", () => {
  const completed = {
    ...encounterState.createInitialRunState(encounters),
    phase: "complete",
    encounterIndex: 2,
    completedEncounterCount: 3,
    encounterState: {
      ...encounterState.createInitialEncounterState(),
      phase: "resolved",
    },
  };

  const state = encounterState.reduceRun(encounters, completed, {
    type: "RESET",
  });

  assert.equal(state, completed);
});

test("maps the primary button to the action allowed by each run phase", () => {
  assert.equal(typeof encounterState.getRunPrimaryAction, "function");
  const initial = encounterState.createInitialRunState(encounters);
  const cases = [
    [initial, "COMMIT_ANSWER"],
    [
      {
        ...initial,
        encounterState: { ...initial.encounterState, phase: "counterbreak" },
      },
      "COMMIT_REPAIR",
    ],
    [
      {
        ...initial,
        encounterState: { ...initial.encounterState, phase: "resolved" },
      },
      "ADVANCE_RUN",
    ],
    [{ ...initial, phase: "complete" }, "RESET_RUN"],
  ];

  cases.forEach(([state, expectedType]) => {
    assert.deepEqual(encounterState.getRunPrimaryAction(state), {
      type: expectedType,
    });
  });
});
