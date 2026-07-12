import assert from "node:assert/strict";
import test from "node:test";

import * as content from "./content.js";
import { makeApprovedPack, resignPack } from "./approved-pack-fixture.js";
import { createInitialRunState, reduceRun } from "./encounter.js";
import { renderEncounterMarkup } from "./render.js";
import { createRunViewModel } from "./view-model.js";

test("loads a Python-export-shaped approved pack as frozen encounters", async () => {
  assert.equal(typeof content.loadApprovedPack, "function");

  const source = makeApprovedPack();
  const encounters = await content.loadApprovedPack(source);

  assert.equal(encounters.length, 1);
  assert.equal(encounters[0].id, "GR-NUM-006");
  assert.ok(Object.isFrozen(encounters));
  assert.ok(Object.isFrozen(encounters[0]));
  assert.ok(Object.isFrozen(encounters[0].provenance));
  assert.notEqual(encounters[0], source.encounters[0]);
});

test("brands only the exact encounters returned by a fully verified pack load", async () => {
  assert.equal(typeof content.isVerifiedApprovedEncounter, "function");

  const source = makeApprovedPack();
  assert.equal(content.isVerifiedApprovedEncounter(source.encounters[0]), false);

  const encounters = await content.loadApprovedPack(source);
  assert.equal(content.isVerifiedApprovedEncounter(encounters[0]), true);
  assert.equal(
    content.isVerifiedApprovedEncounter(structuredClone(encounters[0])),
    false,
  );
});

test("runs approved content through loader, wrong and correct routes, repair, and render", async () => {
  const encounters = await content.loadApprovedPack(makeApprovedPack());
  const encounter = encounters[0];
  const initial = createInitialRunState(encounters);
  const initialView = createRunViewModel(encounters, initial);
  const initialMarkup = renderEncounterMarkup(initialView.encounter, initialView.view);
  assert.match(initialMarkup, /coolant &amp; adds/);
  assert.match(initialView.view.prototypeNotice, /offline.*v7\.1.*owner-reviewed/i);

  const chosenWrong = encounter.counterfeits[1];
  let wrongState = reduceRun(encounters, initial, {
    type: "SELECT_ANSWER",
    answerId: chosenWrong.answerId,
  });
  wrongState = reduceRun(encounters, wrongState, { type: "COMMIT_ANSWER" });
  assert.equal(wrongState.encounterState.revealedCounterfeitId, chosenWrong.id);
  wrongState = reduceRun(encounters, wrongState, {
    type: "SELECT_REPAIR",
    repairId: chosenWrong.repairId,
  });
  wrongState = reduceRun(encounters, wrongState, { type: "COMMIT_REPAIR" });
  assert.equal(wrongState.encounterState.phase, "resolved");

  let correctState = reduceRun(encounters, initial, {
    type: "SELECT_ANSWER",
    answerId: encounter.correctAnswerId,
  });
  correctState = reduceRun(encounters, correctState, { type: "COMMIT_ANSWER" });
  assert.equal(
    correctState.encounterState.revealedCounterfeitId,
    encounter.featuredCounterfeitId,
  );
  const featured = encounter.counterfeits.find(
    (counterfeit) => counterfeit.id === encounter.featuredCounterfeitId,
  );
  correctState = reduceRun(encounters, correctState, {
    type: "SELECT_REPAIR",
    repairId: featured.repairId,
  });
  correctState = reduceRun(encounters, correctState, { type: "COMMIT_REPAIR" });
  const resolvedView = createRunViewModel(encounters, correctState);
  const resolvedMarkup = renderEncounterMarkup(
    resolvedView.encounter,
    resolvedView.view,
  );
  assert.equal(correctState.encounterState.phase, "resolved");
  assert.match(resolvedMarkup, new RegExp(featured.glitchName));
  assert.match(resolvedMarkup, /The clean proof/);
});

test("rejects a pack whose frozen holdout assertion is not exact", async () => {
  const pack = makeApprovedPack();
  pack.holdoutAssertion.recordCount = 139;
  resignPack(pack);

  await assert.rejects(
    () => content.loadApprovedPack(pack),
    /frozen holdout assertion/i,
  );
});

test("rejects an unsupported approved-pack schema", async () => {
  const pack = makeApprovedPack();
  pack.schemaVersion = "glitch-rally-pack-v2";
  resignPack(pack);

  await assert.rejects(
    () => content.loadApprovedPack(pack),
    /schemaVersion/i,
  );
});

test("rejects an approved pack whose canonical content hash is stale", async () => {
  const pack = makeApprovedPack();
  pack.encounters[0].question.prompt += " changed";

  await assert.rejects(
    () => content.loadApprovedPack(pack),
    /contentHash/i,
  );
});

test("requires the Python export's exact pack metadata", async () => {
  const mutations = [
    ["packVersion", (pack) => (pack.packVersion = "Bad Version")],
    ["createdAt", (pack) => (pack.createdAt = "2026-07-10")],
    ["validatorVersion", (pack) => (pack.validatorVersion = "old-validator")],
    ["contentOrigin", (pack) => (pack.contentOrigin = "manual")],
    ["questionBankSha256", (pack) => (pack.questionBankSha256 = "not-a-hash")],
    ["encounterIds", (pack) => (pack.encounterIds = ["GR-NUM-999"])],
    ["encounterCount", (pack) => (pack.encounterCount = 2)],
  ];

  for (const [field, mutate] of mutations) {
    const pack = makeApprovedPack();
    mutate(pack);
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(field, "i"),
    );
  }
});

test("requires the exact approved Glitch family registry", async () => {
  const pack = makeApprovedPack();
  delete pack.glitchFamilies.sign_flipper;
  resignPack(pack);

  await assert.rejects(
    () => content.loadApprovedPack(pack),
    /glitchFamilies/i,
  );
});

test("rejects encounters that are not approved at both trust boundaries", async () => {
  for (const field of ["contentStatus", "reviewStatus"]) {
    const pack = makeApprovedPack();
    pack.encounters[0][field] = "rejected";
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(field, "i"),
    );
  }
});

test("requires every sanitized provenance field from the Python export", async () => {
  const requiredFields = Object.keys(makeApprovedPack().encounters[0].provenance);

  for (const field of requiredFields) {
    const pack = makeApprovedPack();
    delete pack.encounters[0].provenance[field];
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(`provenance.*${field}`, "i"),
    );
  }
});

test("validates every approved provenance binding and immutable revision", async () => {
  const mutations = [
    ["sourceType", (value) => (value.sourceType = "manual")],
    ["sourceQuestionId", (value) => (value.sourceQuestionId = "GR-NUM-999")],
    ["sourceCollection", (value) => (value.sourceCollection = "other")],
    ["excludedFromEvaluationHoldout", (value) => (value.excludedFromEvaluationHoldout = false)],
    ["questionFingerprint", (value) => (value.questionFingerprint = `question:v1:${"0".repeat(64)}`)],
    ["modelId", (value) => (value.modelId = "other/model")],
    ["modelRevision", (value) => (value.modelRevision = "main")],
    ["adapterId", (value) => (value.adapterId = "other/adapter")],
    ["adapterRevision", (value) => (value.adapterRevision = "main")],
    ["generationRunId", (value) => (value.generationRunId = "")],
    ["generatorVersion", (value) => (value.generatorVersion = "old-generator")],
    ["generatorSourceSha256", (value) => (value.generatorSourceSha256 = "bad")],
    ["backendSourceSha256", (value) => (value.backendSourceSha256 = "bad")],
    ["generationParameters", (value) => (value.generationParameters.max_new_tokens = 256)],
    ["systemPromptSha256", (value) => (value.systemPromptSha256 = "bad")],
    ["userPromptSha256", (value) => (value.userPromptSha256 = "bad")],
    ["promptSha256", (value) => (value.promptSha256 = "bad")],
    ["rawResponseSha256", (value) => (value.rawResponseSha256 = "bad")],
    ["candidateHash", (value) => (value.candidateHash = "bad")],
    ["validatorVersion", (value) => (value.validatorVersion = "old-validator")],
    ["validationReportHash", (value) => (value.validationReportHash = "bad")],
    ["reviewPayloadHash", (value) => (value.reviewPayloadHash = "bad")],
    ["reviewRevision", (value) => (value.reviewRevision = "bad")],
    ["approvedAt", (value) => (value.approvedAt = "yesterday")],
    ["contentLicense", (value) => (value.contentLicense = "unknown")],
  ];

  for (const [field, mutate] of mutations) {
    const pack = makeApprovedPack();
    mutate(pack.encounters[0].provenance);
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(field, "i"),
    );
  }
});

test("enforces generation, approval, and pack release chronology", async () => {
  const mutations = [
    ["generatedAt", (pack) => (pack.encounters[0].provenance.generatedAt = "not-a-time")],
    [
      "generatedAt",
      (pack) => (pack.encounters[0].provenance.generatedAt = "2026-07-10T22:00:00Z"),
    ],
    [
      "approvedAt",
      (pack) => (pack.encounters[0].provenance.approvedAt = "2026-07-10T22:00:00Z"),
    ],
  ];

  for (const [field, mutate] of mutations) {
    const pack = makeApprovedPack();
    mutate(pack);
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(field, "i"),
    );
  }
});

test("rejects a generation run ID outside the public lowercase contract", async () => {
  const pack = makeApprovedPack();
  pack.encounters[0].provenance.generationRunId = "bad@Run";
  resignPack(pack);

  await assert.rejects(
    () => content.loadApprovedPack(pack),
    /generationRunId/i,
  );
});

test("rejects reviewer identity and raw model response leakage anywhere in a pack", async () => {
  for (const field of ["reviewer", "rawResponse"]) {
    const pack = makeApprovedPack();
    pack[field] = field === "reviewer" ? "owner@example.test" : "raw model text";
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(field, "i"),
    );
  }
});

test("requires the exact approved encounter field contract", async () => {
  for (const mutate of [
    (encounter) => delete encounter.district,
    (encounter) => (encounter.bonusPayload = "unexpected"),
  ]) {
    const pack = makeApprovedPack();
    mutate(pack.encounters[0]);
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      /encounter.*(?:district|bonusPayload)/i,
    );
  }
});

test("rejects an approved encounter with an unknown visual tool", async () => {
  const pack = makeApprovedPack();
  pack.encounters[0].visualTool = "unregistered_visual_tool";
  resignPack(pack);

  await assert.rejects(
    () => content.loadApprovedPack(pack),
    /visualTool/i,
  );
});

test("requires the exact nested question contract and trusted steps", async () => {
  for (const mutate of [
    (question) => delete question.trustedSteps,
    (question) => (question.rawResponse = "leaked"),
  ]) {
    const pack = makeApprovedPack();
    mutate(pack.encounters[0].question);
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      /question.*(?:trustedSteps|rawResponse)/i,
    );
  }
});

test("rejects unsafe or unsupported approved question text", async () => {
  const mutations = [
    ["prompt", (question) => (question.prompt = "<img src=x>")],
    ["topic", (question) => (question.topic = " Decimal topic ")],
    ["correctAnswer", (question) => (question.correctAnswer = "four")],
    ["roadEquation", (question) => (question.roadEquation = "")],
    ["trustedSteps", (question) => (question.trustedSteps[0] = "<b>unsafe</b>")],
  ];

  for (const [field, mutate] of mutations) {
    const pack = makeApprovedPack();
    mutate(pack.encounters[0].question);
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(field, "i"),
    );
  }
});

test("validates approved encounter identity and gameplay metadata", async () => {
  const mutations = [
    ["district", (encounter) => (encounter.district = "<unsafe>")],
    ["roomLabel", (encounter) => (encounter.roomLabel = "Checkpoint 9 of 9")],
    ["grade", (encounter) => (encounter.grade = 5)],
    ["difficulty", (encounter) => (encounter.difficulty = "expert")],
    ["correctAnswerId", (encounter) => (encounter.correctAnswerId = "answer")],
  ];

  for (const [field, mutate] of mutations) {
    const pack = makeApprovedPack();
    mutate(pack.encounters[0]);
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(field, "i"),
    );
  }
});

test("requires exactly three counterfeits and three repair choices", async () => {
  for (const field of ["counterfeits", "repairChoices"]) {
    const pack = makeApprovedPack();
    pack.encounters[0][field].pop();
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(`${field}.*exactly three`, "i"),
    );
  }
});

test("requires exact camelCase counterfeit and repair choice fields", async () => {
  const mutations = [
    ["counterfeit", (encounter) => delete encounter.counterfeits[0].glitchName],
    ["counterfeit", (encounter) => (encounter.counterfeits[0].raw_response = "leaked")],
    ["repair", (encounter) => delete encounter.repairChoices[0].detail],
    ["repair", (encounter) => (encounter.repairChoices[0].reviewer = "owner")],
  ];

  for (const [label, mutate] of mutations) {
    const pack = makeApprovedPack();
    mutate(pack.encounters[0]);
    resignPack(pack);

    await assert.rejects(
      () => content.loadApprovedPack(pack),
      new RegExp(label, "i"),
    );
  }
});

test("rejects duplicate IDs and broken counterfeit references", async () => {
  const mutations = [
    [
      /counterfeit.*ID/i,
      (encounter) => (encounter.counterfeits[1].id = encounter.counterfeits[0].id),
    ],
    [
      /answer.*ID/i,
      (encounter) =>
        (encounter.counterfeits[1].answerId = encounter.counterfeits[0].answerId),
    ],
    [
      /repair.*ID/i,
      (encounter) => (encounter.repairChoices[1].id = encounter.repairChoices[0].id),
    ],
    [
      /repairId/i,
      (encounter) => (encounter.counterfeits[0].repairId = "missing-repair"),
    ],
    [
      /featuredCounterfeitId/i,
      (encounter) => (encounter.featuredCounterfeitId = "missing-counterfeit"),
    ],
  ];

  for (const [pattern, mutate] of mutations) {
    const pack = makeApprovedPack();
    mutate(pack.encounters[0]);
    resignPack(pack);

    await assert.rejects(() => content.loadApprovedPack(pack), pattern);
  }
});

test("requires a one-to-one repair mapping for the three counterfeits", async () => {
  const pack = makeApprovedPack();
  pack.encounters[0].counterfeits[1].repairId =
    pack.encounters[0].counterfeits[0].repairId;
  resignPack(pack);

  await assert.rejects(
    () => content.loadApprovedPack(pack),
    /repairId.*one-to-one/i,
  );
});

test("rejects invalid counterfeit math, family metadata, and display text", async () => {
  const mutations = [
    [/answer/i, (encounter) => (encounter.counterfeits[0].answer = "forty-seven")],
    [
      /trusted answer/i,
      (encounter) => {
        encounter.counterfeits[0].answer = "4.43";
        encounter.counterfeits[0].computation = "1.75 + 2.68 = 4.43";
      },
    ],
    [
      /numerically distinct/i,
      (encounter) => {
        encounter.counterfeits[1].answer = "47.0";
        encounter.counterfeits[1].computation = "47.0 = 47.0";
      },
    ],
    [
      /computation/i,
      (encounter) => (encounter.counterfeits[0].computation = "12 + 35 = 46"),
    ],
    [
      /glitchFamilyId/i,
      (encounter) => (encounter.counterfeits[0].glitchFamilyId = "mystery_family"),
    ],
    [
      /glitchName/i,
      (encounter) => (encounter.counterfeits[0].glitchName = "Mystery Car"),
    ],
    [
      /misconception/i,
      (encounter) => (encounter.counterfeits[0].misconception = "<unsafe>") ,
    ],
    [
      /repair.*detail/i,
      (encounter) => (encounter.repairChoices[0].detail = "<unsafe>"),
    ],
  ];

  for (const [pattern, mutate] of mutations) {
    const pack = makeApprovedPack();
    mutate(pack.encounters[0]);
    resignPack(pack);

    await assert.rejects(() => content.loadApprovedPack(pack), pattern);
  }
});
