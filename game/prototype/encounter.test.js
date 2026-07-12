import assert from "node:assert/strict";
import test from "node:test";

import {
  createInitialEncounterState,
  reduceEncounter,
} from "./encounter.js";

const encounter = {
  correctAnswerId: "answer-correct",
  featuredCounterfeitId: "counterfeit-operation-mimic",
  repairChoices: [
    { id: "repair-equal-pieces" },
    { id: "repair-keep-addition" },
    { id: "repair-read-action" },
  ],
  counterfeits: [
    {
      answerId: "answer-add-denominators",
      id: "counterfeit-denominator-devourer",
      misconception: "Adds numerators and denominators directly",
      repairId: "repair-equal-pieces",
    },
    {
      answerId: "answer-multiply",
      id: "counterfeit-operation-mimic",
      misconception: "Multiplies instead of adding",
      repairId: "repair-keep-addition",
    },
    {
      answerId: "answer-subtract",
      id: "counterfeit-sign-switcher",
      misconception: "Subtracts instead of adding",
      repairId: "repair-read-action",
    },
  ],
};

test("reveals the selected counterfeit after a wrong route is committed", () => {
  let state = createInitialEncounterState(encounter);
  state = reduceEncounter(encounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(encounter, state, { type: "COMMIT_ANSWER" });

  assert.equal(state.phase, "counterbreak");
  assert.equal(state.revealedCounterfeitId, "counterfeit-denominator-devourer");
  assert.equal(state.firstChoiceCorrect, false);
  assert.equal(state.proofBoost, false);
});

test("reveals the encounter's featured counterfeit after a correct route is committed", () => {
  let state = createInitialEncounterState(encounter);
  state = reduceEncounter(encounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-correct",
  });
  state = reduceEncounter(encounter, state, { type: "COMMIT_ANSWER" });

  assert.equal(state.phase, "counterbreak");
  assert.equal(state.revealedCounterfeitId, "counterfeit-operation-mimic");
  assert.equal(state.firstChoiceCorrect, true);
  assert.equal(state.proofBoost, true);
});

test("keeps the player in Counterbreak after the wrong repair", () => {
  let state = createInitialEncounterState(encounter);
  state = reduceEncounter(encounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(encounter, state, { type: "COMMIT_ANSWER" });
  state = reduceEncounter(encounter, state, {
    type: "SELECT_REPAIR",
    repairId: "repair-keep-addition",
  });
  state = reduceEncounter(encounter, state, { type: "COMMIT_REPAIR" });

  assert.equal(state.phase, "counterbreak");
  assert.equal(state.repairAttempts, 1);
  assert.equal(
    state.repairFeedback,
    "Not yet. Trace this move: Adds numerators and denominators directly. Choose the patch that reverses that exact move.",
  );
});

test("ignores a repair selection that is not in the encounter", () => {
  let state = createInitialEncounterState(encounter);
  state = reduceEncounter(encounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(encounter, state, { type: "COMMIT_ANSWER" });

  const nextState = reduceEncounter(encounter, state, {
    type: "SELECT_REPAIR",
    repairId: "repair-does-not-exist",
  });

  assert.equal(nextState, state);
});

test("resolves the encounter after the matching repair", () => {
  let state = createInitialEncounterState(encounter);
  state = reduceEncounter(encounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(encounter, state, { type: "COMMIT_ANSWER" });
  state = reduceEncounter(encounter, state, {
    type: "SELECT_REPAIR",
    repairId: "repair-equal-pieces",
  });
  state = reduceEncounter(encounter, state, { type: "COMMIT_REPAIR" });

  assert.equal(state.phase, "resolved");
  assert.equal(state.repairAttempts, 1);
  assert.equal(state.repairFeedback, "Route repaired.");
});

test("returns to a clean initial state when reset", () => {
  const dirtyState = {
    ...createInitialEncounterState(encounter),
    phase: "resolved",
    selectedAnswerId: "answer-correct",
    proofBoost: true,
  };

  const state = reduceEncounter(encounter, dirtyState, { type: "RESET" });

  assert.deepEqual(state, createInitialEncounterState(encounter));
});
