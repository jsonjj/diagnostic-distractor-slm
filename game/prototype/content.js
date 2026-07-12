export const GLITCH_FAMILIES = deepFreeze({
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
});

const SUPPORTED_GLITCH_FAMILIES = new Set([
  "denominator-devourer",
  "operation-mimic",
  "sign-switcher",
  "wild-fault",
  ...Object.keys(GLITCH_FAMILIES),
]);

const FROZEN_HOLDOUT_ASSERTION = Object.freeze({
  excluded: true,
  recordCount: 140,
  sha256: "47ce1e1b85ebaae0782f0aed32fa12bb6ec0fd4498ed71c75cf3e4aff5135693",
});

const verifiedApprovedEncounters = new WeakSet();

export function isVerifiedApprovedEncounter(encounter) {
  return verifiedApprovedEncounters.has(encounter);
}

const APPROVED_PROVENANCE_FIELDS = Object.freeze([
  "sourceType",
  "sourceQuestionId",
  "sourceCollection",
  "excludedFromEvaluationHoldout",
  "questionFingerprint",
  "modelId",
  "modelRevision",
  "adapterId",
  "adapterRevision",
  "generationRunId",
  "generatedAt",
  "generatorVersion",
  "generatorSourceSha256",
  "backendSourceSha256",
  "generationParameters",
  "systemPromptSha256",
  "userPromptSha256",
  "promptSha256",
  "rawResponseSha256",
  "candidateHash",
  "validatorVersion",
  "validationReportHash",
  "reviewPayloadHash",
  "reviewRevision",
  "approvedAt",
  "contentLicense",
]);

const LOCKED_GENERATION_PARAMETERS = deepFreeze({
  do_sample: false,
  max_new_tokens: 512,
  enable_thinking: false,
});

const APPROVED_PACK_FIELDS = Object.freeze([
  "schemaVersion",
  "packVersion",
  "createdAt",
  "validatorVersion",
  "contentOrigin",
  "questionBankSha256",
  "holdoutAssertion",
  "encounterIds",
  "encounterCount",
  "glitchFamilies",
  "encounters",
  "contentHash",
]);

const APPROVED_ENCOUNTER_FIELDS = Object.freeze([
  "id",
  "contentStatus",
  "reviewStatus",
  "sourceSplit",
  "questionHash",
  "district",
  "roomLabel",
  "grade",
  "difficulty",
  "visualTool",
  "question",
  "correctAnswerId",
  "featuredCounterfeitId",
  "counterfeits",
  "repairChoices",
  "provenance",
]);

const APPROVED_VISUAL_TOOLS = new Set([
  "badge_fraction_array",
  "battery_fraction_bar",
  "decimal_area_model",
  "decimal_equal_groups",
  "decimal_fraction_bar",
  "decimal_number_line",
  "decimal_product_grid",
  "decimal_route_bar",
  "exponent_array",
  "factor_array",
  "fraction_container_model",
  "fraction_number_line",
  "fraction_partition_bar",
  "fraction_route_map",
  "fraction_timeline",
  "integer_elevation_line",
  "integer_number_line",
  "integer_thermometer",
  "model_drone_bar_model",
  "model_drone_decimal_area",
  "model_drone_decimal_grid",
  "model_drone_equal_groups",
  "model_drone_fraction_area",
  "model_drone_fraction_partition",
  "model_drone_fraction_strip",
  "multiple_timeline",
  "operation_order_stack",
  "place_value_grid",
  "place_value_rounding_line",
  "sign_rule_card",
  "signed_counter_array",
  "token_fraction_array",
]);

const APPROVED_QUESTION_FIELDS = Object.freeze([
  "prompt",
  "topic",
  "correctAnswer",
  "roadEquation",
  "trustedSteps",
]);

const APPROVED_COUNTERFEIT_FIELDS = Object.freeze([
  "id",
  "answerId",
  "answer",
  "misconception",
  "computation",
  "glitchFamilyId",
  "glitchName",
  "repairId",
  "repairExplanation",
]);

const APPROVED_REPAIR_FIELDS = Object.freeze(["id", "label", "detail"]);

function parseNumericAnswer(value) {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim().replaceAll("−", "-");
  const fraction = normalized.match(
    /^(-?\d+(?:\.\d+)?)\s*\/\s*(-?\d+(?:\.\d+)?)$/,
  );
  if (fraction) {
    const numerator = Number(fraction[1]);
    const denominator = Number(fraction[2]);
    return denominator === 0 ? null : numerator / denominator;
  }

  if (/^-?\d+(?:\.\d+)?$/.test(normalized)) {
    return Number(normalized);
  }

  return null;
}

function numericallyEquivalent(left, right) {
  const parsedLeft = parseNumericAnswer(left);
  const parsedRight = parseNumericAnswer(right);
  return (
    parsedLeft !== null &&
    parsedRight !== null &&
    Math.abs(parsedLeft - parsedRight) <= 1e-9
  );
}

function unique(values) {
  return new Set(values).size === values.length;
}

function boundedText(errors, label, value, maximum) {
  if (typeof value !== "string" || value.trim().length === 0) {
    errors.push(`${label} must be non-empty text.`);
  } else if (value.length > maximum) {
    errors.push(`${label} must be at most ${maximum} characters.`);
  }
}

function isSafeDisplayText(value, maximum) {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= maximum &&
    value === value.trim() &&
    !/[<>]/.test(value) &&
    !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(value)
  );
}

function isSimpleAnswerText(value) {
  return (
    isSafeDisplayText(value, 64) &&
    /^-?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]+)?|(?:0|[1-9][0-9]*)\/(?:[1-9][0-9]*))$/.test(
      value,
    )
  );
}

export function assertValidEncounter(encounter, options = {}) {
  const errors = [];
  const holdoutHashes = options.holdoutHashes ?? new Set();

  if (!encounter || typeof encounter !== "object") {
    throw new TypeError("Encounter validation failed: encounter must be an object.");
  }

  boundedText(errors, "Encounter ID", encounter.id, 80);
  boundedText(errors, "Question hash", encounter.questionHash, 160);
  boundedText(errors, "Question prompt", encounter.question?.prompt, 360);
  boundedText(errors, "Question topic", encounter.question?.topic, 120);
  boundedText(errors, "Trusted answer", encounter.question?.correctAnswer, 48);

  if (holdoutHashes.has(encounter.questionHash)) {
    errors.push("Question hash belongs to the frozen evaluation holdout.");
  }

  if (!Array.isArray(encounter.question?.trustedSteps) || encounter.question.trustedSteps.length === 0) {
    errors.push("Question must include at least one trusted repair step.");
  } else {
    encounter.question.trustedSteps.forEach((step, index) =>
      boundedText(errors, `Trusted step ${index + 1}`, step, 160),
    );
  }

  const repairs = Array.isArray(encounter.repairChoices)
    ? encounter.repairChoices
    : [];
  const repairIds = repairs.map((repair) => repair.id);
  if (!unique(repairIds)) {
    errors.push("Repair choice IDs must be unique.");
  }
  repairs.forEach((repair, index) => {
    boundedText(errors, `Repair ${index + 1} ID`, repair.id, 80);
    boundedText(errors, `Repair ${index + 1} label`, repair.label, 120);
  });

  const counterfeits = Array.isArray(encounter.counterfeits)
    ? encounter.counterfeits
    : [];
  if (counterfeits.length !== 3) {
    errors.push("Encounter must contain exactly three counterfeits.");
  }

  const counterfeitIds = counterfeits.map((counterfeit) => counterfeit.id);
  const answerIds = counterfeits.map((counterfeit) => counterfeit.answerId);
  if (!unique(counterfeitIds)) {
    errors.push("Counterfeit IDs must be unique.");
  }
  if (!unique(answerIds)) {
    errors.push("Counterfeit answer IDs must be unique.");
  }

  const numericAnswers = [];
  counterfeits.forEach((counterfeit, index) => {
    const prefix = `Counterfeit ${index + 1}`;
    boundedText(errors, `${prefix} ID`, counterfeit.id, 80);
    boundedText(errors, `${prefix} answer ID`, counterfeit.answerId, 80);
    boundedText(errors, `${prefix} answer`, counterfeit.answer, 48);
    boundedText(errors, `${prefix} misconception`, counterfeit.misconception, 180);
    boundedText(errors, `${prefix} computation`, counterfeit.computation, 260);

    if (!SUPPORTED_GLITCH_FAMILIES.has(counterfeit.glitchFamilyId)) {
      errors.push(`${prefix} uses an unsupported Glitch family.`);
    }
    if (!repairIds.includes(counterfeit.repairId)) {
      errors.push(`${prefix} repairId must reference a repair choice.`);
    }
    if (numericallyEquivalent(counterfeit.answer, encounter.question?.correctAnswer)) {
      errors.push(`${prefix} counterfeit must not equal the trusted answer.`);
    }

    const lastEquals =
      typeof counterfeit.computation === "string"
        ? counterfeit.computation.lastIndexOf("=")
        : -1;
    const finalValue =
      lastEquals >= 0
        ? counterfeit.computation.slice(lastEquals + 1).trim()
        : counterfeit.computation?.trim();
    if (!numericallyEquivalent(finalValue, counterfeit.answer)) {
      errors.push(`${prefix} computation must end in its claimed answer.`);
    }

    const numericAnswer = parseNumericAnswer(counterfeit.answer);
    if (numericAnswer !== null) {
      if (numericAnswers.some((value) => Math.abs(value - numericAnswer) <= 1e-9)) {
        errors.push("Counterfeit answers must be numerically distinct.");
      }
      numericAnswers.push(numericAnswer);
    }
  });

  if (!counterfeitIds.includes(encounter.featuredCounterfeitId)) {
    errors.push("featuredCounterfeitId must reference a counterfeit.");
  }

  const sourceType = encounter.provenance?.sourceType;
  if (encounter.contentStatus === "prototype-placeholder") {
    if (sourceType !== "prototype-placeholder") {
      errors.push("Prototype content must identify itself as a prototype placeholder.");
    }
  } else if (encounter.contentStatus === "approved") {
    errors.push(
      "Prototype validator cannot approve production content; use the offline Glitch Forge approval pipeline.",
    );
  } else {
    errors.push("contentStatus must be prototype-placeholder or approved.");
  }

  if (errors.length > 0) {
    throw new Error(`Encounter validation failed:\n- ${errors.join("\n- ")}`);
  }

  return encounter;
}

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value).forEach((item) => deepFreeze(item));
    Object.freeze(value);
  }
  return value;
}

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

function isSha256(value) {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function isUtcTimestamp(value) {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(value) &&
    !Number.isNaN(Date.parse(value))
  );
}

function isPrefixedHash(value, prefix) {
  return (
    typeof value === "string" &&
    new RegExp(`^${prefix}[0-9a-f]{64}$`).test(value)
  );
}

function rejectProvenance(field) {
  throw new Error(`Approved pack validation failed: provenance ${field} is invalid.`);
}

function assertExactFields(value, expectedFields, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Approved pack validation failed: ${label} must be an object.`);
  }
  for (const field of expectedFields) {
    if (!Object.hasOwn(value, field)) {
      throw new Error(`Approved pack validation failed: ${label} is missing ${field}.`);
    }
  }
  for (const field of Object.keys(value)) {
    if (!expectedFields.includes(field)) {
      throw new Error(
        `Approved pack validation failed: ${label} has unexpected field ${field}.`,
      );
    }
  }
}

function validateApprovedProvenance(
  encounter,
  packValidatorVersion,
  packCreatedAt,
) {
  const value = encounter.provenance;
  if (value.sourceType !== "slm-generated") rejectProvenance("sourceType");
  if (value.sourceQuestionId !== encounter.id) rejectProvenance("sourceQuestionId");
  if (
    value.sourceCollection !== "original-game-v1" ||
    value.sourceCollection !== encounter.sourceSplit
  ) {
    rejectProvenance("sourceCollection");
  }
  if (value.excludedFromEvaluationHoldout !== true) {
    rejectProvenance("excludedFromEvaluationHoldout");
  }
  if (
    !isPrefixedHash(value.questionFingerprint, "question:v1:") ||
    value.questionFingerprint !== encounter.questionHash
  ) {
    rejectProvenance("questionFingerprint");
  }
  if (value.modelId !== "unsloth/Qwen3-4B-bnb-4bit") {
    rejectProvenance("modelId");
  }
  if (!/^[0-9a-f]{40}$/.test(value.modelRevision)) {
    rejectProvenance("modelRevision");
  }
  if (value.adapterId !== "j2ampn/qwen3-4b-distractor-lora-v7") {
    rejectProvenance("adapterId");
  }
  if (!/^[0-9a-f]{40}$/.test(value.adapterRevision)) {
    rejectProvenance("adapterRevision");
  }
  if (!/^[a-z0-9][a-z0-9._-]{2,79}$/.test(value.generationRunId)) {
    rejectProvenance("generationRunId");
  }
  if (!isUtcTimestamp(value.generatedAt)) rejectProvenance("generatedAt");
  if (value.generatorVersion !== "glitch-rally-generator-v1") {
    rejectProvenance("generatorVersion");
  }
  for (const field of [
    "generatorSourceSha256",
    "backendSourceSha256",
    "systemPromptSha256",
    "userPromptSha256",
    "promptSha256",
    "rawResponseSha256",
  ]) {
    if (!isSha256(value[field])) rejectProvenance(field);
  }
  if (
    stableJson(value.generationParameters) !==
    stableJson(LOCKED_GENERATION_PARAMETERS)
  ) {
    rejectProvenance("generationParameters");
  }
  if (!isPrefixedHash(value.candidateHash, "candidate:v1:")) {
    rejectProvenance("candidateHash");
  }
  if (
    value.validatorVersion !== "glitch-rally-validator-v1" ||
    value.validatorVersion !== packValidatorVersion
  ) {
    rejectProvenance("validatorVersion");
  }
  if (!isPrefixedHash(value.validationReportHash, "validation:v1:")) {
    rejectProvenance("validationReportHash");
  }
  if (!isPrefixedHash(value.reviewPayloadHash, "review-payload:v1:")) {
    rejectProvenance("reviewPayloadHash");
  }
  if (!isPrefixedHash(value.reviewRevision, "review:v1:")) {
    rejectProvenance("reviewRevision");
  }
  if (!isUtcTimestamp(value.approvedAt)) rejectProvenance("approvedAt");
  if (Date.parse(value.generatedAt) > Date.parse(value.approvedAt)) {
    rejectProvenance("generatedAt chronology");
  }
  if (Date.parse(value.approvedAt) > Date.parse(packCreatedAt)) {
    rejectProvenance("approvedAt chronology");
  }
  if (value.contentLicense !== "original-game-content") {
    rejectProvenance("contentLicense");
  }
}

async function sha256Text(value) {
  if (!globalThis.crypto?.subtle) {
    throw new Error("Approved pack validation failed: Web Crypto is unavailable.");
  }
  const bytes = new TextEncoder().encode(value);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export async function loadApprovedPack(pack) {
  if (!pack || typeof pack !== "object" || Array.isArray(pack)) {
    throw new TypeError("Approved pack validation failed: pack must be an object.");
  }
  const candidate = structuredClone(pack);

  for (const field of Object.keys(candidate)) {
    if (!APPROVED_PACK_FIELDS.includes(field)) {
      throw new Error(
        `Approved pack validation failed: unexpected top-level field ${field}.`,
      );
    }
  }
  for (const field of APPROVED_PACK_FIELDS) {
    if (!Object.hasOwn(candidate, field)) {
      throw new Error(
        `Approved pack validation failed: missing top-level field ${field}.`,
      );
    }
  }

  if (candidate.schemaVersion !== "glitch-rally-pack-v1") {
    throw new Error("Approved pack validation failed: schemaVersion is not supported.");
  }

  const holdout = candidate.holdoutAssertion;
  if (
    !holdout ||
    Object.keys(holdout).length !== 3 ||
    holdout.excluded !== FROZEN_HOLDOUT_ASSERTION.excluded ||
    holdout.recordCount !== FROZEN_HOLDOUT_ASSERTION.recordCount ||
    holdout.sha256 !== FROZEN_HOLDOUT_ASSERTION.sha256
  ) {
    throw new Error("Approved pack validation failed: frozen holdout assertion mismatch.");
  }

  if (!/^[a-z0-9][a-z0-9.-]{2,80}$/.test(candidate.packVersion)) {
    throw new Error("Approved pack validation failed: packVersion is malformed.");
  }
  if (!isUtcTimestamp(candidate.createdAt)) {
    throw new Error("Approved pack validation failed: createdAt must be a UTC timestamp.");
  }
  if (candidate.validatorVersion !== "glitch-rally-validator-v1") {
    throw new Error("Approved pack validation failed: validatorVersion is not current.");
  }
  if (candidate.contentOrigin !== "offline-slm-generated-owner-reviewed") {
    throw new Error("Approved pack validation failed: contentOrigin is not approved.");
  }
  if (!isSha256(candidate.questionBankSha256)) {
    throw new Error("Approved pack validation failed: questionBankSha256 is malformed.");
  }
  if (!Array.isArray(candidate.encounters) || candidate.encounters.length === 0) {
    throw new Error("Approved pack validation failed: encounterCount requires encounters.");
  }
  const actualIds = candidate.encounters.map((encounter) => encounter?.id);
  if (
    !Array.isArray(candidate.encounterIds) ||
    candidate.encounterIds.length !== actualIds.length ||
    candidate.encounterIds.some((id, index) => id !== actualIds[index])
  ) {
    throw new Error("Approved pack validation failed: encounterIds do not match encounters.");
  }
  if (candidate.encounterCount !== candidate.encounters.length) {
    throw new Error("Approved pack validation failed: encounterCount does not match encounters.");
  }
  if (stableJson(candidate.glitchFamilies) !== stableJson(GLITCH_FAMILIES)) {
    throw new Error("Approved pack validation failed: glitchFamilies registry mismatch.");
  }
  const seenEncounterIds = new Set();
  const seenQuestionHashes = new Set();
  candidate.encounters.forEach((encounter, index) => {
    assertExactFields(
      encounter,
      APPROVED_ENCOUNTER_FIELDS,
      `encounter ${index + 1}`,
    );
    assertExactFields(
      encounter.question,
      APPROVED_QUESTION_FIELDS,
      `encounter ${index + 1} question`,
    );
    if (!/^GR-NUM-[0-9]{3}$/.test(encounter.id)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} id is malformed.`,
      );
    }
    if (seenEncounterIds.has(encounter.id)) {
      throw new Error("Approved pack validation failed: encounter id is duplicated.");
    }
    seenEncounterIds.add(encounter.id);
    if (encounter.sourceSplit !== "original-game-v1") {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} sourceSplit is invalid.`,
      );
    }
    if (!isPrefixedHash(encounter.questionHash, "question:v1:")) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} questionHash is malformed.`,
      );
    }
    if (seenQuestionHashes.has(encounter.questionHash)) {
      throw new Error("Approved pack validation failed: questionHash is duplicated.");
    }
    seenQuestionHashes.add(encounter.questionHash);
    if (!isSafeDisplayText(encounter.district, 80)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} district is unsafe.`,
      );
    }
    if (
      encounter.roomLabel !==
      `Checkpoint ${index + 1} of ${candidate.encounters.length}`
    ) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} roomLabel is incorrect.`,
      );
    }
    if (encounter.grade !== 6) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} grade must be 6.`,
      );
    }
    if (!new Set(["easy", "medium", "hard"]).has(encounter.difficulty)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} difficulty is invalid.`,
      );
    }
    if (encounter.correctAnswerId !== `${encounter.id}-answer-correct`) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} correctAnswerId is invalid.`,
      );
    }
    if (
      !Array.isArray(encounter.counterfeits) ||
      encounter.counterfeits.length !== 3
    ) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} counterfeits must contain exactly three items.`,
      );
    }
    if (
      !Array.isArray(encounter.repairChoices) ||
      encounter.repairChoices.length !== 3
    ) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} repairChoices must contain exactly three items.`,
      );
    }
    encounter.counterfeits.forEach((counterfeit, counterfeitIndex) =>
      assertExactFields(
        counterfeit,
        APPROVED_COUNTERFEIT_FIELDS,
        `encounter ${index + 1} counterfeit ${counterfeitIndex + 1}`,
      ),
    );
    encounter.repairChoices.forEach((repair, repairIndex) =>
      assertExactFields(
        repair,
        APPROVED_REPAIR_FIELDS,
        `encounter ${index + 1} repair ${repairIndex + 1}`,
      ),
    );
    const counterfeitIds = encounter.counterfeits.map((item) => item.id);
    const counterfeitAnswerIds = encounter.counterfeits.map(
      (item) => item.answerId,
    );
    const repairIds = encounter.repairChoices.map((item) => item.id);
    if (!unique(counterfeitIds)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} counterfeit IDs must be unique.`,
      );
    }
    if (!unique(counterfeitAnswerIds)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} answer IDs must be unique.`,
      );
    }
    if (!unique(repairIds)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} repair IDs must be unique.`,
      );
    }
    if (!counterfeitIds.includes(encounter.featuredCounterfeitId)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} featuredCounterfeitId is invalid.`,
      );
    }
    encounter.counterfeits.forEach((counterfeit) => {
      if (!repairIds.includes(counterfeit.repairId)) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} counterfeit repairId is invalid.`,
        );
      }
    });
    const referencedRepairIds = encounter.counterfeits.map(
      (item) => item.repairId,
    );
    if (!unique(referencedRepairIds)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} repairId mapping must be one-to-one.`,
      );
    }
    const correctValue = parseNumericAnswer(encounter.question.correctAnswer);
    const counterfeitValues = [];
    encounter.counterfeits.forEach((counterfeit, counterfeitIndex) => {
      if (!isSimpleAnswerText(counterfeit.answer)) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} counterfeit ${counterfeitIndex + 1} answer is unsupported.`,
        );
      }
      const answerValue = parseNumericAnswer(counterfeit.answer);
      if (Math.abs(answerValue - correctValue) <= 1e-9) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} counterfeit equals the trusted answer.`,
        );
      }
      if (
        counterfeitValues.some(
          (value) => Math.abs(value - answerValue) <= 1e-9,
        )
      ) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} counterfeit answers must be numerically distinct.`,
        );
      }
      counterfeitValues.push(answerValue);
      const lastEquals = counterfeit.computation.lastIndexOf("=");
      const computationResult =
        lastEquals >= 0
          ? counterfeit.computation.slice(lastEquals + 1).trim()
          : counterfeit.computation;
      if (!numericallyEquivalent(computationResult, counterfeit.answer)) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} counterfeit computation must end in its answer.`,
        );
      }
      if (!Object.hasOwn(GLITCH_FAMILIES, counterfeit.glitchFamilyId)) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} counterfeit glitchFamilyId is unknown.`,
        );
      }
      if (
        counterfeit.glitchName !==
        GLITCH_FAMILIES[counterfeit.glitchFamilyId].name
      ) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} counterfeit glitchName does not match its family.`,
        );
      }
      for (const [field, maximum] of [
        ["misconception", 240],
        ["computation", 320],
        ["glitchName", 120],
        ["repairExplanation", 320],
      ]) {
        if (!isSafeDisplayText(counterfeit[field], maximum)) {
          throw new Error(
            `Approved pack validation failed: encounter ${index + 1} counterfeit ${field} is unsafe.`,
          );
        }
      }
    });
    encounter.repairChoices.forEach((repair, repairIndex) => {
      if (!isSafeDisplayText(repair.label, 160)) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} repair ${repairIndex + 1} label is unsafe.`,
        );
      }
      if (!isSafeDisplayText(repair.detail, 320)) {
        throw new Error(
          `Approved pack validation failed: encounter ${index + 1} repair ${repairIndex + 1} detail is unsafe.`,
        );
      }
    });
    if (
      !Array.isArray(encounter.question.trustedSteps) ||
      encounter.question.trustedSteps.length < 1 ||
      encounter.question.trustedSteps.length > 6
    ) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} question trustedSteps must contain 1-6 steps.`,
      );
    }
    if (!isSafeDisplayText(encounter.question.prompt, 420)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} question prompt is unsafe.`,
      );
    }
    if (!isSafeDisplayText(encounter.question.topic, 120)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} question topic is unsafe.`,
      );
    }
    if (!isSimpleAnswerText(encounter.question.correctAnswer)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} question correctAnswer is unsupported.`,
      );
    }
    if (!isSafeDisplayText(encounter.question.roadEquation, 200)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} question roadEquation is unsafe.`,
      );
    }
    if (
      encounter.question.trustedSteps.some(
        (step) => !isSafeDisplayText(step, 240),
      )
    ) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} question trustedSteps contain unsafe text.`,
      );
    }
    if (encounter?.contentStatus !== "approved") {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} contentStatus is not approved.`,
      );
    }
    if (encounter.reviewStatus !== "approved") {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} reviewStatus is not approved.`,
      );
    }
    if (!APPROVED_VISUAL_TOOLS.has(encounter.visualTool)) {
      throw new Error(
        `Approved pack validation failed: encounter ${index + 1} visualTool is unknown.`,
      );
    }
    const provenance = encounter.provenance;
    const provenanceKeys =
      provenance && typeof provenance === "object" && !Array.isArray(provenance)
        ? Object.keys(provenance)
        : [];
    for (const field of APPROVED_PROVENANCE_FIELDS) {
      if (!provenanceKeys.includes(field)) {
        throw new Error(
          `Approved pack validation failed: provenance is missing ${field}.`,
        );
      }
    }
    const unexpectedProvenance = provenanceKeys.filter(
      (field) => !APPROVED_PROVENANCE_FIELDS.includes(field),
    );
    if (unexpectedProvenance.length > 0) {
      throw new Error(
        `Approved pack validation failed: provenance has unexpected field ${unexpectedProvenance[0]}.`,
      );
    }
    validateApprovedProvenance(
      encounter,
      candidate.validatorVersion,
      candidate.createdAt,
    );
  });

  if (!/^pack:v1:[0-9a-f]{64}$/.test(candidate.contentHash)) {
    throw new Error("Approved pack validation failed: contentHash is malformed.");
  }
  const hashPayload = structuredClone(candidate);
  delete hashPayload.contentHash;
  const expectedHash = `pack:v1:${await sha256Text(stableJson(hashPayload))}`;
  if (candidate.contentHash !== expectedHash) {
    throw new Error("Approved pack validation failed: contentHash does not match the pack.");
  }

  candidate.encounters.forEach((encounter) =>
    verifiedApprovedEncounters.add(encounter),
  );
  return deepFreeze(candidate.encounters);
}
