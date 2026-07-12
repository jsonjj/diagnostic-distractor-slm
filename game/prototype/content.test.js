import assert from "node:assert/strict";
import test from "node:test";

import { assertValidEncounter } from "./content.js";
import * as prototypeFixtures from "./sample-encounter.js";
import { prototypeEncounter } from "./sample-encounter.js";

function makeEncounter() {
  return {
    id: "prototype-fraction-foundry-001",
    contentStatus: "prototype-placeholder",
    sourceSplit: "prototype",
    questionHash: "prototype:beacon-three-fourths-plus-one-eighth",
    question: {
      prompt:
        "A rally battery is 3/4 charged. The pit crew adds another 1/8 of a full charge. How full is it now?",
      topic: "Adding and Subtracting with Fractions",
      correctAnswer: "7/8",
      trustedSteps: ["3/4 = 6/8", "6/8 + 1/8 = 7/8"],
    },
    correctAnswerId: "answer-correct",
    featuredCounterfeitId: "counterfeit-denominator-devourer",
    repairChoices: [
      { id: "repair-equal-pieces", label: "Make equal-sized pieces first." },
      { id: "repair-keep-addition", label: "Keep addition, then multiply." },
      { id: "repair-read-action", label: "Follow the action in the story." },
    ],
    counterfeits: [
      {
        id: "counterfeit-denominator-devourer",
        answerId: "answer-add-denominators",
        answer: "4/12",
        misconception: "Adds numerators and denominators directly",
        computation: "3/4 + 1/8 = (3 + 1)/(4 + 8) = 4/12",
        glitchFamilyId: "denominator-devourer",
        repairId: "repair-equal-pieces",
      },
      {
        id: "counterfeit-operation-mimic",
        answerId: "answer-multiply",
        answer: "3/32",
        misconception: "Multiplies instead of adding",
        computation: "3/4 x 1/8 = 3/32",
        glitchFamilyId: "operation-mimic",
        repairId: "repair-keep-addition",
      },
      {
        id: "counterfeit-sign-switcher",
        answerId: "answer-subtract",
        answer: "5/8",
        misconception: "Subtracts instead of adding",
        computation: "3/4 - 1/8 = 6/8 - 1/8 = 5/8",
        glitchFamilyId: "sign-switcher",
        repairId: "repair-read-action",
      },
    ],
    provenance: {
      sourceType: "prototype-placeholder",
      note: "Hand-authored interaction fixture; not an SLM generation.",
    },
  };
}

test("accepts a well-formed prototype encounter without making an SLM claim", () => {
  const encounter = makeEncounter();

  assert.equal(assertValidEncounter(encounter), encounter);
  assert.equal(encounter.provenance.sourceType, "prototype-placeholder");
});

test("ships an explicitly labeled and valid prototype encounter", () => {
  assert.equal(assertValidEncounter(prototypeEncounter), prototypeEncounter);
  assert.equal(prototypeEncounter.contentStatus, "prototype-placeholder");
  assert.equal(prototypeEncounter.question.topic, "Adding and Subtracting Fractions");
  assert.match(prototypeEncounter.provenance.note, /not an SLM generation/i);
});

test("exports the prototype encounter pack", () => {
  assert.ok(Array.isArray(prototypeFixtures.prototypeEncounters));
  assert.equal(prototypeFixtures.prototypeEncounters[0], prototypeEncounter);
});

test("ships three distinct validated hand-authored prototype checkpoints", () => {
  const pack = prototypeFixtures.prototypeEncounters;

  assert.equal(pack.length, 3);
  assert.equal(new Set(pack.map((encounter) => encounter.id)).size, 3);
  assert.equal(new Set(pack.map((encounter) => encounter.questionHash)).size, 3);
  pack.forEach((encounter) => {
    assert.equal(assertValidEncounter(encounter), encounter);
    assert.equal(encounter.contentStatus, "prototype-placeholder");
    assert.match(encounter.provenance.note, /hand-authored/i);
    assert.match(encounter.provenance.note, /not an SLM generation/i);
  });
});

test("rejects a mathematically equivalent correct-answer counterfeit", () => {
  const encounter = makeEncounter();
  encounter.counterfeits[0].answer = "14/16";
  encounter.counterfeits[0].computation = "14/16";

  assert.throws(
    () => assertValidEncounter(encounter),
    /counterfeit must not equal the trusted answer/i,
  );
});

test("rejects a computation whose final value does not match its answer", () => {
  const encounter = makeEncounter();
  encounter.counterfeits[0].computation = "3/4 + 1/8 = 5/12";

  assert.throws(
    () => assertValidEncounter(encounter),
    /computation must end in its claimed answer/i,
  );
});

test("rejects a counterfeit whose repair does not exist", () => {
  const encounter = makeEncounter();
  encounter.counterfeits[0].repairId = "repair-missing";

  assert.throws(
    () => assertValidEncounter(encounter),
    /repairId must reference a repair choice/i,
  );
});

test("rejects a question whose normalized hash belongs to the frozen holdout", () => {
  const encounter = makeEncounter();
  const holdoutHashes = new Set([encounter.questionHash]);

  assert.throws(
    () => assertValidEncounter(encounter, { holdoutHashes }),
    /frozen evaluation holdout/i,
  );
});

test("rejects duplicate repairs and unsupported Glitch families", () => {
  const encounter = makeEncounter();
  encounter.repairChoices[1].id = encounter.repairChoices[0].id;
  encounter.counterfeits[0].glitchFamilyId = "mystery-car";

  assert.throws(
    () => assertValidEncounter(encounter),
    /repair choice IDs must be unique.*unsupported Glitch family/is,
  );
});

test("refuses to promote prototype data into approved gameplay content", () => {
  const encounter = makeEncounter();
  encounter.contentStatus = "approved";
  encounter.provenance.sourceType = "slm-generation";

  assert.throws(
    () => assertValidEncounter(encounter),
    /prototype validator cannot approve production content/i,
  );
});
