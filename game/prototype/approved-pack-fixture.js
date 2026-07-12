import { createHash } from "node:crypto";

const glitchFamilies = {
  decimal_drifter: {
    name: "Decimal Drifter",
    personality: "slides digits into the wrong place-value lane",
  },
  factor_faker: {
    name: "Factor Faker",
    personality: "mixes up factors, multiples, GCF, and LCM",
  },
  fraction_forger: {
    name: "Fraction Forger",
    personality: "counterfeits fraction pieces and denominators",
  },
  operation_swapper: {
    name: "Operation Swapper",
    personality: "secretly replaces the operation the problem asks for",
  },
  order_hacker: {
    name: "Order Hacker",
    personality: "scrambles parentheses, powers, and operation order",
  },
  place_value_phantom: {
    name: "Place-Value Phantom",
    personality: "haunts digit positions, conversions, and regrouping",
  },
  reciprocal_rogue: {
    name: "Reciprocal Rogue",
    personality: "flips the wrong fraction during division",
  },
  rounding_rascal: {
    name: "Rounding Rascal",
    personality: "checks the wrong digit before rounding",
  },
  sign_flipper: {
    name: "Sign Flipper",
    personality: "reverses positive and negative rules",
  },
};

function stableJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function resignPack(pack) {
  delete pack.contentHash;
  const digest = createHash("sha256").update(stableJson(pack), "utf8").digest("hex");
  pack.contentHash = `pack:v1:${digest}`;
  return pack;
}

export function makeApprovedPack() {
  const questionHash = `question:v1:${"1".repeat(64)}`;
  const encounter = {
    id: "GR-NUM-006",
    contentStatus: "approved",
    reviewStatus: "approved",
    sourceSplit: "original-game-v1",
    questionHash,
    district: "Decimal Docks",
    roomLabel: "Checkpoint 1 of 1",
    grade: 6,
    difficulty: "easy",
    visualTool: "place_value_grid",
    question: {
      prompt:
        "The pit crew has 1.75 liters of coolant & adds 2.68 liters. How much coolant is there?",
      topic: "Adding and Subtracting with Decimals",
      correctAnswer: "4.43",
      roadEquation: "1.75 + 2.68 = ?",
      trustedSteps: ["Align the decimal points.", "1.75 + 2.68 = 4.43"],
    },
    correctAnswerId: "GR-NUM-006-answer-correct",
    featuredCounterfeitId: "GR-NUM-006-counterfeit-3",
    counterfeits: [
      {
        id: "GR-NUM-006-counterfeit-1",
        answerId: "GR-NUM-006-counterfeit-1-answer",
        answer: "47",
        misconception: "Drops the decimal points before adding",
        computation: "12 + 35 = 47",
        glitchFamilyId: "decimal_drifter",
        glitchName: "Decimal Drifter",
        repairId: "GR-NUM-006-repair-1",
        repairExplanation: "Keep each decimal digit in its original place-value column.",
      },
      {
        id: "GR-NUM-006-counterfeit-2",
        answerId: "GR-NUM-006-counterfeit-2-answer",
        answer: "28.55",
        misconception: "Shifts the second addend's decimal point one place right",
        computation: "1.75 + 26.8 = 28.55",
        glitchFamilyId: "place_value_phantom",
        glitchName: "Place-Value Phantom",
        repairId: "GR-NUM-006-repair-2",
        repairExplanation: "Read 2.68 as two and sixty-eight hundredths without shifting it.",
      },
      {
        id: "GR-NUM-006-counterfeit-3",
        answerId: "GR-NUM-006-counterfeit-3-answer",
        answer: "-0.93",
        misconception: "Subtracts the new coolant instead of adding it",
        computation: "1.75 - 2.68 = -0.93",
        glitchFamilyId: "operation_swapper",
        glitchName: "Operation Swapper",
        repairId: "GR-NUM-006-repair-3",
        repairExplanation: "The word adds requires addition, not subtraction.",
      },
    ],
    repairChoices: [
      {
        id: "GR-NUM-006-repair-1",
        label: "Restore the decimal points.",
        detail: "Align ones, tenths, and hundredths before adding.",
      },
      {
        id: "GR-NUM-006-repair-2",
        label: "Protect the value of 2.68.",
        detail: "Do not shift its decimal point.",
      },
      {
        id: "GR-NUM-006-repair-3",
        label: "Follow the addition action.",
        detail: "Adding coolant increases the total.",
      },
    ],
    provenance: {
      sourceType: "slm-generated",
      sourceQuestionId: "GR-NUM-006",
      sourceCollection: "original-game-v1",
      excludedFromEvaluationHoldout: true,
      questionFingerprint: questionHash,
      modelId: "unsloth/Qwen3-4B-bnb-4bit",
      modelRevision: "a".repeat(40),
      adapterId: "j2ampn/qwen3-4b-distractor-lora-v7",
      adapterRevision: "b".repeat(40),
      generationRunId: "colab-t4-run-001",
      generatedAt: "2026-07-10T20:00:00Z",
      generatorVersion: "glitch-rally-generator-v1",
      generatorSourceSha256: "2".repeat(64),
      backendSourceSha256: "3".repeat(64),
      generationParameters: {
        do_sample: false,
        max_new_tokens: 512,
        enable_thinking: false,
      },
      systemPromptSha256: "4".repeat(64),
      userPromptSha256: "5".repeat(64),
      promptSha256: "6".repeat(64),
      rawResponseSha256: "7".repeat(64),
      candidateHash: `candidate:v1:${"8".repeat(64)}`,
      validatorVersion: "glitch-rally-validator-v1",
      validationReportHash: `validation:v1:${"9".repeat(64)}`,
      reviewPayloadHash: `review-payload:v1:${"a".repeat(64)}`,
      reviewRevision: `review:v1:${"b".repeat(64)}`,
      approvedAt: "2026-07-10T21:00:00Z",
      contentLicense: "original-game-content",
    },
  };

  return resignPack({
    schemaVersion: "glitch-rally-pack-v1",
    packVersion: "glitch-rally-test-v1",
    createdAt: "2026-07-10T21:00:00Z",
    validatorVersion: "glitch-rally-validator-v1",
    contentOrigin: "offline-slm-generated-owner-reviewed",
    questionBankSha256: "c".repeat(64),
    holdoutAssertion: {
      excluded: true,
      recordCount: 140,
      sha256: "47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693",
    },
    encounterIds: [encounter.id],
    encounterCount: 1,
    glitchFamilies: structuredClone(glitchFamilies),
    encounters: [encounter],
  });
}
