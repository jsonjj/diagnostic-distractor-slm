import assert from "node:assert/strict";
import test from "node:test";

import {
  createInitialEncounterState,
  createInitialRunState,
  reduceEncounter,
} from "./encounter.js";
import { makeApprovedPack } from "./approved-pack-fixture.js";
import { loadApprovedPack } from "./content.js";
import { prototypeEncounter, prototypeEncounters } from "./sample-encounter.js";
import { renderEncounterMarkup } from "./render.js";
import { createEncounterViewModel, createRunViewModel } from "./view-model.js";

test("renders accessible checkpoint progress for a prototype run", () => {
  const runState = createInitialRunState(prototypeEncounters);
  const { encounter, view } = createRunViewModel(prototypeEncounters, runState);

  const markup = renderEncounterMarkup(encounter, view);

  assert.match(markup, /Checkpoint 1 \/ 3/);
  assert.match(markup, /role="progressbar"/);
  assert.match(markup, /aria-valuenow="1"/);
  assert.match(markup, /aria-valuemax="3"/);
  assert.equal((markup.match(/data-checkpoint-state=/g) ?? []).length, 3);
  assert.equal((markup.match(/data-checkpoint-state="current"/g) ?? []).length, 1);
});

test("renders the active checkpoint equation on the rally road", () => {
  const runState = {
    ...createInitialRunState(prototypeEncounters),
    encounterIndex: 1,
  };
  const { encounter, view } = createRunViewModel(prototypeEncounters, runState);

  const markup = renderEncounterMarkup(encounter, view);

  assert.match(markup, /2\.4 \+ 0\.65 = \?/);
  assert.doesNotMatch(markup, /3\/4&nbsp; \+ &nbsp;1\/8/);
});

test("renders the run's banked Proof Boost count", () => {
  const runState = {
    ...createInitialRunState(prototypeEncounters),
    encounterIndex: 1,
    completedEncounterCount: 1,
    proofBoostCount: 1,
  };
  const { encounter, view } = createRunViewModel(prototypeEncounters, runState);

  const markup = renderEncounterMarkup(encounter, view);

  assert.match(markup, /class="boost-chip is-active">Proof Boost × 1<\/span>/);
});

test("renders an accessible run-complete card with earned totals", () => {
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
  const { encounter, view } = createRunViewModel(prototypeEncounters, runState);

  const markup = renderEncounterMarkup(encounter, view);

  assert.match(markup, /id="run-complete-title" tabindex="-1"/);
  assert.match(markup, /Rally route secured!/);
  assert.match(markup, /<strong>3<\/strong>\s*<span>Checkpoints repaired<\/span>/);
  assert.match(markup, /<strong>2<\/strong>\s*<span>Proof Boosts<\/span>/);
  assert.match(markup, /<strong>5<\/strong>\s*<span>Patch attempts<\/span>/);
  assert.match(markup, /Run rally again/);
});

test("renders an accessible initial Truth Gate", () => {
  const state = createInitialEncounterState(prototypeEncounter);
  const view = createEncounterViewModel(prototypeEncounter, state);
  const markup = renderEncounterMarkup(prototypeEncounter, view);

  assert.match(markup, /A rally battery is 3\/4 charged/);
  assert.equal((markup.match(/data-answer-id=/g) ?? []).length, 4);
  assert.match(markup, /id="challenge-title" tabindex="-1"/);
  assert.match(markup, /data-primary-action[^>]*disabled/);
  assert.match(markup, /Prototype fixture/);
  assert.match(markup, /hand-authored/i);
  assert.match(markup, /not SLM output/i);
});

test("renders the revealed Glitch trace and all repair controls", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_ANSWER" });
  const view = createEncounterViewModel(prototypeEncounter, state);
  const markup = renderEncounterMarkup(prototypeEncounter, view);

  assert.match(markup, /Denominator Devourer/);
  assert.match(markup, /3\/4 \+ 1\/8 = \(3 \+ 1\)\/\(4 \+ 8\) = 4\/12/);
  assert.equal((markup.match(/data-repair-id=/g) ?? []).length, 3);
  assert.match(markup, /Fire Patch Cannon/);
  assert.match(markup, /id="glitch-name" tabindex="-1"/);
  assert.match(markup, /class="game-status" tabindex="-1"/);
  assert.doesNotMatch(
    markup,
    /class="game-status"[^>]*(?:role="status"|aria-live=)/,
  );
  assert.equal(
    (markup.match(/data-answer-id="[^"]+"[\s\S]*?aria-pressed="[^"]+" disabled/g) ?? [])
      .length,
    4,
  );
});

test("renders trusted proof steps after a successful repair", () => {
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
  const markup = renderEncounterMarkup(prototypeEncounter, view);

  assert.match(markup, /3\/4 = 6\/8/);
  assert.match(markup, /6\/8 \+ 1\/8 = 7\/8/);
  assert.match(markup, /Try another route/);
  assert.match(markup, /id="trusted-proof-title" tabindex="-1"/);
});

test("escapes generated text instead of interpreting it as markup", () => {
  const unsafeEncounter = structuredClone(prototypeEncounter);
  unsafeEncounter.question.prompt = '<img src=x onerror="alert(1)">';
  const state = createInitialEncounterState(unsafeEncounter);
  const view = createEncounterViewModel(unsafeEncounter, state);
  const markup = renderEncounterMarkup(unsafeEncounter, view);

  assert.doesNotMatch(markup, /<img src=x/);
  assert.match(markup, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
});

test("shows route outcomes in text instead of relying on answer colors", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  state = reduceEncounter(prototypeEncounter, state, { type: "COMMIT_ANSWER" });
  const markup = renderEncounterMarkup(
    prototypeEncounter,
    createEncounterViewModel(prototypeEncounter, state),
  );

  assert.equal((markup.match(/class="gate-outcome"/g) ?? []).length, 4);
  assert.equal((markup.match(/>True route<\/span>/g) ?? []).length, 1);
  assert.equal((markup.match(/>Counterfeit route<\/span>/g) ?? []).length, 3);
});

test("ties a visual four-lane road fork to ordered answer routes", () => {
  let state = createInitialEncounterState(prototypeEncounter);
  state = reduceEncounter(prototypeEncounter, state, {
    type: "SELECT_ANSWER",
    answerId: "answer-add-denominators",
  });
  const view = createEncounterViewModel(prototypeEncounter, state);
  const markup = renderEncounterMarkup(prototypeEncounter, view);

  assert.match(markup, /class="route-fork" aria-hidden="true"/);
  assert.equal((markup.match(/class="road-route[^\"]*"/g) ?? []).length, 4);
  view.answers.forEach((answer, index) => {
    assert.match(
      markup,
      new RegExp(
        `data-road-answer-id="${answer.id}"[\\s\\S]*?<b>${index + 1}<\\/b>[\\s\\S]*?<small>${answer.display.replace("/", "\\/")}<\\/small>`,
      ),
    );
  });
  assert.match(
    markup,
    /class="road-route[^\"]*is-selected[^\"]*"[^>]*data-road-answer-id="answer-add-denominators"/,
  );
  assert.match(
    markup,
    new RegExp(`class="game-shell phase-choose route-choice-${view.selectedRouteIndex}"`),
  );
});

test("renders a friendly verified family label and approved SLM copy", async () => {
  const [approvedEncounter] = await loadApprovedPack(makeApprovedPack());
  let encounterState = createInitialEncounterState(approvedEncounter);
  encounterState = reduceEncounter(approvedEncounter, encounterState, {
    type: "SELECT_ANSWER",
    answerId: approvedEncounter.counterfeits[0].answerId,
  });
  encounterState = reduceEncounter(approvedEncounter, encounterState, {
    type: "COMMIT_ANSWER",
  });
  const runState = {
    ...createInitialRunState([approvedEncounter]),
    phase: "complete",
    completedEncounterCount: 1,
    encounterState: { ...encounterState, phase: "resolved" },
  };
  const { encounter, view } = createRunViewModel([approvedEncounter], runState);
  const markup = renderEncounterMarkup(encounter, view);

  assert.match(markup, /class="glitch-family">Decimal Drifter<\/span>/);
  assert.doesNotMatch(markup, /class="glitch-family">[^<]*_/);
  assert.match(markup, /Glitch Forge · reviewed SLM/);
  assert.match(markup, /Reviewed SLM run complete/);
  assert.match(markup, /SLM-powered checkpoint:/);
  assert.doesNotMatch(markup, /prototype|in production|will come|future/i);
});

test("renders a concise phase summary without fake stability points", () => {
  const state = createInitialEncounterState(prototypeEncounter);
  const view = createEncounterViewModel(prototypeEncounter, state);
  const markup = renderEncounterMarkup(prototypeEncounter, view);

  assert.match(markup, /class="stage-status"/);
  assert.match(markup, />Routes open<\/strong>/);
  assert.match(markup, /Four routes branch from 3\/4 \+ 1\/8 = \?\./);
  assert.doesNotMatch(markup, /Rally stability|meter-pips|stability points/i);
});

test("the skip link targets the focusable challenge heading", () => {
  const state = createInitialEncounterState(prototypeEncounter);
  const markup = renderEncounterMarkup(
    prototypeEncounter,
    createEncounterViewModel(prototypeEncounter, state),
  );

  assert.match(
    markup,
    /<a class="skip-link" href="#challenge-title">Skip to the math challenge<\/a>/,
  );
  assert.match(markup, /<h1 id="challenge-title" tabindex="-1">/);
});
