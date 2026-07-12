import assert from "node:assert/strict";
import test from "node:test";

import { makeApprovedPack } from "./approved-pack-fixture.js";
import { loadApprovedPack } from "./content.js";
import { createInitialEncounterState, reduceEncounter } from "./encounter.js";
import { createInitialRunState } from "./encounter.js";
import { prototypeEncounter, prototypeEncounters } from "./sample-encounter.js";
import { createEncounterViewModel } from "./view-model.js";
import * as viewModels from "./view-model.js";

test("builds progress for the active checkpoint in a prototype run", () => {
  assert.equal(typeof viewModels.createRunViewModel, "function");
  const runState = createInitialRunState(prototypeEncounters);

  const { encounter, view } = viewModels.createRunViewModel(
    prototypeEncounters,
    runState,
  );

  assert.equal(encounter, prototypeEncounter);
  assert.deepEqual(view.run, {
    currentCheckpoint: 1,
    totalCheckpoints: 3,
    completedEncounterCount: 0,
    isFinalCheckpoint: false,
    complete: false,
    proofBoostCount: 0,
    repairAttemptCount: 0,
  });
});

test("uses topic-neutral route guidance across the encounter pack", () => {
  const runState = {
    ...createInitialRunState(prototypeEncounters),
    encounterIndex: 1,
  };

  const { view } = viewModels.createRunViewModel(prototypeEncounters, runState);

  assert.equal(view.instruction, "Choose the route that keeps the rally math true.");
});

test("keeps answer order stable per encounter while varying the correct gate", () => {
  const correctPositions = prototypeEncounters.map((encounter) => {
    const first = createEncounterViewModel(
      encounter,
      createInitialEncounterState(encounter),
    );
    const afterReset = createEncounterViewModel(
      encounter,
      createInitialEncounterState(encounter),
    );

    assert.deepEqual(
      first.answers.map((answer) => answer.id),
      afterReset.answers.map((answer) => answer.id),
    );
    return first.answers.findIndex((answer) => answer.id === encounter.correctAnswerId);
  });

  assert.equal(new Set(correctPositions).size, 3);
});

test("varies featured Glitches and independently orders their repair buttons", () => {
  const featuredIndexes = [];
  const repairPositions = prototypeEncounters.map((encounter) => {
    const featured = encounter.counterfeits.find(
      (counterfeit) => counterfeit.id === encounter.featuredCounterfeitId,
    );
    featuredIndexes.push(encounter.counterfeits.indexOf(featured));

    const first = createEncounterViewModel(encounter, createInitialEncounterState());
    const afterReset = createEncounterViewModel(
      encounter,
      createInitialEncounterState(),
    );
    assert.deepEqual(
      first.repairs.map((repair) => repair.id),
      afterReset.repairs.map((repair) => repair.id),
    );
    return first.repairs.findIndex((repair) => repair.id === featured.repairId);
  });

  assert.equal(new Set(featuredIndexes).size, 3);
  assert.equal(new Set(repairPositions).size, 3);
});

test("offers the next checkpoint after a non-final repair", () => {
  const initial = createInitialRunState(prototypeEncounters);
  const runState = {
    ...initial,
    encounterState: {
      ...createInitialEncounterState(),
      phase: "resolved",
    },
  };

  const { view } = viewModels.createRunViewModel(prototypeEncounters, runState);

  assert.equal(view.primaryAction.label, "Next checkpoint");
});

test("offers to finish the rally after the final repair", () => {
  const runState = {
    ...createInitialRunState(prototypeEncounters),
    encounterIndex: 2,
    completedEncounterCount: 2,
    encounterState: {
      ...createInitialEncounterState(),
      phase: "resolved",
    },
  };

  const { view } = viewModels.createRunViewModel(prototypeEncounters, runState);

  assert.equal(view.primaryAction.label, "Finish rally");
});

test("presents a completed-run summary and restart action", () => {
  const runState = {
    ...createInitialRunState(prototypeEncounters),
    phase: "complete",
    encounterIndex: 2,
    completedEncounterCount: 3,
    proofBoostCount: 2,
    repairAttemptCount: 5,
    encounterState: {
      ...createInitialEncounterState(),
      phase: "resolved",
    },
  };

  const { view } = viewModels.createRunViewModel(prototypeEncounters, runState);

  assert.equal(view.primaryAction.label, "Run rally again");
  assert.match(view.instruction, /every Glitch/i);
  assert.deepEqual(view.runSummary, {
    checkpointCount: 3,
    proofBoostCount: 2,
    repairAttemptCount: 5,
  });
});

test("presents four neutral routes and disables commitment before selection", () => {
  const state = createInitialEncounterState(prototypeEncounter);
  const view = createEncounterViewModel(prototypeEncounter, state);

  assert.equal(view.phase, "choose");
  assert.deepEqual(
    view.answers.map((answer) => answer.display).sort(),
    ["4/12", "7/8", "3/32", "5/8"].sort(),
  );
  assert.ok(view.answers.every((answer) => answer.tone === "neutral"));
  assert.equal(view.primaryAction.disabled, true);
  assert.equal(view.primaryAction.label, "Lock route");
});

test("tracks the selected gate's displayed lane for rally motion", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });

  const view = createEncounterViewModel(prototypeEncounter, state);

  assert.equal(
    view.selectedRouteIndex,
    view.answers.findIndex((answer) => answer.id === "answer-add-denominators"),
  );
});

test("reveals the matching Glitch and generated trace during Counterbreak", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_ANSWER" });

  const view = createEncounterViewModel(prototypeEncounter, state);

  assert.equal(view.phase, "counterbreak");
  assert.equal(view.glitch.name, "Denominator Devourer");
  assert.equal(view.glitch.computation, "3/4 + 1/8 = (3 + 1)/(4 + 8) = 4/12");
  assert.match(view.status, /counterfeit route detected/i);
  assert.equal(view.primaryAction.label, "Fire Patch Cannon");
  assert.equal(view.primaryAction.disabled, true);
});

test("uses mastery language after the player chooses the true route", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-correct",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_ANSWER" });

  const view = createEncounterViewModel(prototypeEncounter, state);

  assert.equal(view.proofBoost, true);
  assert.match(view.status, /clean route/i);
  assert.match(view.instruction, /disable the counterfeit/i);
});

test("keeps earned Proof Boosts visible across checkpoints", () => {
  const runState = {
    ...createInitialRunState(prototypeEncounters),
    encounterIndex: 1,
    completedEncounterCount: 1,
    proofBoostCount: 1,
  };

  const { view } = viewModels.createRunViewModel(prototypeEncounters, runState);

  assert.equal(view.proofBoostCount, 1);
});

test("shows trusted reasoning and reset action after repair", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_ANSWER" });
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_REPAIR",
    repairId: "repair-equal-pieces",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_REPAIR" });

  const view = createEncounterViewModel(prototypeEncounter, state);

  assert.equal(view.phase, "resolved");
  assert.deepEqual(view.trustedSteps, ["3/4 = 6/8", "6/8 + 1/8 = 7/8"]);
  assert.equal(view.primaryAction.label, "Try another route");
  assert.equal(view.primaryAction.disabled, false);
  assert.match(view.status, /route repaired/i);
});

test("labels every committed route outcome in words", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_ANSWER" });

  const view = createEncounterViewModel(prototypeEncounter, state);
  const outcomes = Object.fromEntries(
    view.answers.map((answer) => [answer.id, answer.outcome]),
  );

  assert.equal(outcomes[prototypeEncounter.correctAnswerId], "True route");
  for (const counterfeit of prototypeEncounter.counterfeits) {
    assert.equal(outcomes[counterfeit.answerId], "Counterfeit route");
  }
});

test("formats a verified approved Glitch family as a friendly display name", async () => {
  const [approvedEncounter] = await loadApprovedPack(makeApprovedPack());
  let state = createInitialEncounterState(approvedEncounter);
  state = reduceEncounter(approvedEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: approvedEncounter.counterfeits[0].answerId,
  });
  state = reduceEncounter(approvedEncounter, state, { type: "COMMIT_ANSWER" });

  const view = createEncounterViewModel(approvedEncounter, state);

  assert.equal(view.glitch.familyDisplayName, "Decimal Drifter");
  assert.doesNotMatch(view.glitch.familyDisplayName, /[_-]/);
});

test("keeps approved and prototype presentation copy source-truthful", async () => {
  const prototypeView = createEncounterViewModel(
    prototypeEncounter,
    createInitialEncounterState(prototypeEncounter),
  );
  const [approvedEncounter] = await loadApprovedPack(makeApprovedPack());
  const approvedView = createEncounterViewModel(
    approvedEncounter,
    createInitialEncounterState(approvedEncounter),
  );

  assert.equal(prototypeView.contentPresentation.mode, "prototype");
  assert.match(prototypeView.contentPresentation.forgeLabel, /prototype/i);
  assert.match(prototypeView.contentPresentation.finishStamp, /prototype/i);

  assert.equal(approvedView.contentPresentation.mode, "approved");
  assert.match(approvedView.contentPresentation.forgeLabel, /reviewed SLM/i);
  assert.match(approvedView.contentPresentation.finishStamp, /reviewed SLM/i);
  assert.match(approvedView.contentPresentation.footerTitle, /SLM-powered/i);
  assert.doesNotMatch(
    Object.values(approvedView.contentPresentation).join(" "),
    /prototype|in production|will come|future/i,
  );

  const unverifiedClone = structuredClone(approvedEncounter);
  const unverifiedView = createEncounterViewModel(
    unverifiedClone,
    createInitialEncounterState(unverifiedClone),
  );
  assert.notEqual(unverifiedView.contentPresentation.mode, "approved");
  assert.doesNotMatch(
    unverifiedView.contentPresentation.forgeLabel,
    /reviewed SLM/i,
  );
});

test("summarizes the active stage from its equation and encounter phase", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  let view = createEncounterViewModel(prototypeEncounter, state);
  assert.equal(view.stageStatusLabel, "Routes open");
  assert.match(view.stageSummary, /four routes/i);
  assert.match(view.stageSummary, /3\/4 \+ 1\/8 = \?/);

  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_ANSWER" });
  view = createEncounterViewModel(prototypeEncounter, state);
  assert.equal(view.stageStatusLabel, "Glitch engaged");
  assert.match(view.stageSummary, /4\/12.*counterfeit/i);
  assert.match(view.stageSummary, /Denominator Devourer/);

  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_REPAIR",
    repairId: "repair-equal-pieces",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_REPAIR" });
  view = createEncounterViewModel(prototypeEncounter, state);
  assert.equal(view.stageStatusLabel, "Road repaired");
  assert.match(view.stageSummary, /7\/8.*true route/i);
});
