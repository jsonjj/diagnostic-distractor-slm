import {
  GLITCH_FAMILIES,
  isVerifiedApprovedEncounter,
} from "./content.js";

function findRevealedGlitch(encounter, state) {
  if (!state.revealedCounterfeitId) {
    return null;
  }
  return (
    encounter.counterfeits.find(
      (counterfeit) => counterfeit.id === state.revealedCounterfeitId,
    ) ?? null
  );
}

function answerTone(answerId, encounter, state) {
  if (state.phase === "choose") {
    return "neutral";
  }
  if (answerId === encounter.correctAnswerId) {
    return "confirmed";
  }
  if (answerId === state.selectedAnswerId && state.firstChoiceCorrect === false) {
    return "fault";
  }
  return "muted";
}

function stableHash(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function stablePermutation(items, encounterId, channel, keyForItem) {
  return items
    .map((item, index) => ({
      item,
      index,
      score: stableHash(`${channel}|${encounterId}|${keyForItem(item)}`),
    }))
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .map(({ item }) => item);
}

function familyDisplayName(familyId) {
  if (Object.hasOwn(GLITCH_FAMILIES, familyId)) {
    return GLITCH_FAMILIES[familyId].name;
  }

  return familyId
    .split(/[-_]+/)
    .filter(Boolean)
    .map((word) => `${word.charAt(0).toUpperCase()}${word.slice(1)}`)
    .join(" ");
}

function createContentPresentation(encounter) {
  if (isVerifiedApprovedEncounter(encounter)) {
    return {
      mode: "approved",
      forgeLabel: "Glitch Forge · reviewed SLM",
      finishStamp: "Reviewed SLM run complete",
      footerTitle: "SLM-powered checkpoint:",
      footerBody:
        "Reviewed distractor answers form the counterfeit routes; their computations power Glitch attacks, and their misconception families shape the rival vehicles.",
    };
  }

  return {
    mode: "prototype",
    forgeLabel: "Glitch Forge · prototype",
    finishStamp: "Prototype run complete",
    footerTitle: "Where the SLM fits:",
    footerBody:
      "In a reviewed SLM pack, each distractor answer becomes a route, its computation powers the attack, and its misconception family shapes the rival vehicle.",
  };
}

function createStageSummary(encounter, state, revealedGlitch, run) {
  if (run?.complete) {
    return {
      label: "Rally secured",
      summary: `${run.completedEncounterCount} checkpoints repaired. The Proof Road reaches the finish.`,
    };
  }

  if (state.phase === "counterbreak") {
    return {
      label: "Glitch engaged",
      summary: `${revealedGlitch.answer} is counterfeit. Trace ${revealedGlitch.glitchName}'s rule.`,
    };
  }

  if (state.phase === "resolved") {
    return {
      label: "Road repaired",
      summary: `${encounter.question.correctAnswer} is the true route. Proof Road repaired.`,
    };
  }

  return {
    label: "Routes open",
    summary: `Four routes branch from ${encounter.question.roadEquation ?? "this equation"}. Choose the true one.`,
  };
}

export function createEncounterViewModel(encounter, state, run = null) {
  const revealedGlitch = findRevealedGlitch(encounter, state);
  const contentPresentation = createContentPresentation(encounter);
  const stage = createStageSummary(encounter, state, revealedGlitch, run);
  const orderedAnswers = stablePermutation(
    [
      ...encounter.counterfeits,
      {
        answerId: encounter.correctAnswerId,
        answer: encounter.question.correctAnswer,
      },
    ].filter(Boolean),
    encounter.id,
    "answers-v1",
    (answer) => answer.answerId,
  );

  const answers = orderedAnswers.map((answer) => ({
    id: answer.answerId,
    display: answer.answer,
    selected: state.selectedAnswerId === answer.answerId,
    tone: answerTone(answer.answerId, encounter, state),
    outcome:
      state.phase === "choose"
        ? ""
        : answer.answerId === encounter.correctAnswerId
          ? "True route"
          : "Counterfeit route",
  }));
  const selectedRouteIndex = answers.findIndex((answer) => answer.selected);

  const repairs = stablePermutation(
    encounter.repairChoices,
    encounter.id,
    "patch-options-v1",
    (repair) => repair.id,
  ).map((repair) => ({
    ...repair,
    selected: state.selectedRepairId === repair.id,
  }));

  let instruction = "Choose the route that keeps the rally math true.";
  let status = "Four routes ahead. Three are counterfeits.";
  let primaryAction = {
    label: "Lock route",
    disabled: state.selectedAnswerId === null,
  };
  let runSummary = null;

  if (state.phase === "counterbreak") {
    instruction = state.firstChoiceCorrect
      ? "You found the clean route. Now disable the counterfeit chasing you."
      : "Trace the counterfeit, then choose the patch that breaks its trick.";
    status =
      state.repairFeedback ||
      (state.firstChoiceCorrect
        ? "Clean route locked. Counterfeit pursuit detected."
        : "Counterfeit route detected. A repair can recover your momentum.");
    primaryAction = {
      label: "Fire Patch Cannon",
      disabled: state.selectedRepairId === null,
    };
  }

  if (state.phase === "resolved") {
    instruction = "The Proof Road is stable again.";
    status = state.proofBoost
      ? "Route repaired. Proof Boost preserved."
      : "Route repaired. Comeback boost charged.";
    primaryAction = {
      label: run
        ? run.isFinalCheckpoint
          ? "Finish rally"
          : "Next checkpoint"
        : "Try another route",
      disabled: false,
    };
  }

  if (run?.complete) {
    instruction =
      "Every Glitch has been countered. Your proof trail reaches the finish.";
    status = `${run.completedEncounterCount} checkpoints repaired. ${run.proofBoostCount} Proof Boosts earned across ${run.repairAttemptCount} Patch Cannon attempts.`;
    primaryAction = { label: "Run rally again", disabled: false };
    runSummary = {
      checkpointCount: run.completedEncounterCount,
      proofBoostCount: run.proofBoostCount,
      repairAttemptCount: run.repairAttemptCount,
    };
  }

  return {
    phase: state.phase,
    answers,
    selectedRouteIndex,
    repairs,
    glitch: revealedGlitch
      ? {
          id: revealedGlitch.id,
          familyId: revealedGlitch.glitchFamilyId,
          familyDisplayName: familyDisplayName(revealedGlitch.glitchFamilyId),
          name: revealedGlitch.glitchName,
          misconception: revealedGlitch.misconception,
          computation: revealedGlitch.computation,
          repairExplanation: revealedGlitch.repairExplanation,
        }
      : null,
    instruction,
    status,
    primaryAction,
    proofBoost: state.proofBoost,
    proofBoostCount: run
      ? run.proofBoostCount + (!run.complete && state.proofBoost ? 1 : 0)
      : state.proofBoost
        ? 1
        : 0,
    repairAttempts: state.repairAttempts,
    trustedSteps:
      state.phase === "resolved" ? encounter.question.trustedSteps : [],
    contentPresentation,
    stageStatusLabel: stage.label,
    stageSummary: stage.summary,
    prototypeNotice:
      contentPresentation.mode === "approved"
        ? "Approved encounter — its three counterfeits were generated offline by the v7.1 diagnostic SLM and owner-reviewed. Nothing is generated live during play."
        : "Prototype fixture — hand-authored interaction content, not SLM output or approved gameplay content. Production counterfeits will come from reviewed SLM output.",
    run,
    runSummary,
  };
}

export function createRunViewModel(encounters, runState) {
  const encounter = encounters[runState.encounterIndex];
  const run = {
    currentCheckpoint: runState.encounterIndex + 1,
    totalCheckpoints: encounters.length,
    completedEncounterCount: runState.completedEncounterCount,
    isFinalCheckpoint: runState.encounterIndex === encounters.length - 1,
    complete: runState.phase === "complete",
    proofBoostCount: runState.proofBoostCount,
    repairAttemptCount: runState.repairAttemptCount,
  };

  return {
    encounter,
    view: createEncounterViewModel(encounter, runState.encounterState, run),
  };
}
