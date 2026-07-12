export function createInitialEncounterState() {
  return {
    phase: "choose",
    selectedAnswerId: null,
    selectedRepairId: null,
    revealedCounterfeitId: null,
    firstChoiceCorrect: null,
    proofBoost: false,
    repairAttempts: 0,
    repairFeedback: "",
  };
}

export function createInitialRunState(encounters) {
  if (!Array.isArray(encounters) || encounters.length === 0) {
    throw new TypeError("A run needs at least one encounter.");
  }

  return {
    phase: "playing",
    encounterIndex: 0,
    encounterState: createInitialEncounterState(),
    completedEncounterCount: 0,
    proofBoostCount: 0,
    repairAttemptCount: 0,
  };
}

export function getRunPrimaryAction(state) {
  if (state.phase === "complete") {
    return { type: "RESET_RUN" };
  }

  const actionByEncounterPhase = {
    choose: "COMMIT_ANSWER",
    counterbreak: "COMMIT_REPAIR",
    resolved: "ADVANCE_RUN",
  };
  return { type: actionByEncounterPhase[state.encounterState.phase] };
}

export function reduceRun(encounters, state, action) {
  if (action.type === "RESET_RUN") {
    return createInitialRunState(encounters);
  }

  if (state.phase === "complete") {
    return state;
  }

  if (
    action.type === "ADVANCE_RUN" &&
    state.phase === "playing" &&
    state.encounterState.phase === "resolved"
  ) {
    const finalCheckpoint = state.encounterIndex === encounters.length - 1;
    return {
      ...state,
      phase: finalCheckpoint ? "complete" : "playing",
      encounterIndex: finalCheckpoint
        ? state.encounterIndex
        : state.encounterIndex + 1,
      encounterState: finalCheckpoint
        ? state.encounterState
        : createInitialEncounterState(),
      completedEncounterCount: state.completedEncounterCount + 1,
      proofBoostCount:
        state.proofBoostCount + (state.encounterState.proofBoost ? 1 : 0),
      repairAttemptCount:
        state.repairAttemptCount + state.encounterState.repairAttempts,
    };
  }

  const encounter = encounters[state.encounterIndex];
  return {
    ...state,
    encounterState: reduceEncounter(encounter, state.encounterState, action),
  };
}

export function reduceEncounter(encounter, state, action) {
  if (action.type === "RESET") {
    return createInitialEncounterState();
  }

  if (action.type === "SELECT_ANSWER" && state.phase === "choose") {
    const validAnswerIds = new Set([
      encounter.correctAnswerId,
      ...encounter.counterfeits.map((counterfeit) => counterfeit.answerId),
    ]);

    return validAnswerIds.has(action.answerId)
      ? { ...state, selectedAnswerId: action.answerId }
      : state;
  }

  if (action.type === "COMMIT_ANSWER" && state.phase === "choose") {
    if (state.selectedAnswerId === null) {
      return state;
    }

    const firstChoiceCorrect = state.selectedAnswerId === encounter.correctAnswerId;
    const revealedCounterfeit = firstChoiceCorrect
      ? encounter.counterfeits.find(
          (counterfeit) => counterfeit.id === encounter.featuredCounterfeitId,
        )
      : encounter.counterfeits.find(
          (counterfeit) => counterfeit.answerId === state.selectedAnswerId,
        );

    if (!revealedCounterfeit) {
      return state;
    }

    return {
      ...state,
      phase: "counterbreak",
      revealedCounterfeitId: revealedCounterfeit.id,
      firstChoiceCorrect,
      proofBoost: firstChoiceCorrect,
      selectedRepairId: null,
      repairFeedback: firstChoiceCorrect
        ? "Clean route. Now disable the counterfeit chasing you."
        : "Counterfeit route detected. Read its trick and patch the rule.",
    };
  }

  if (action.type === "SELECT_REPAIR" && state.phase === "counterbreak") {
    const validRepairIds = new Set(
      encounter.repairChoices.map((repair) => repair.id),
    );
    return validRepairIds.has(action.repairId)
      ? { ...state, selectedRepairId: action.repairId, repairFeedback: "" }
      : state;
  }

  if (action.type === "COMMIT_REPAIR" && state.phase === "counterbreak") {
    if (state.selectedRepairId === null || state.revealedCounterfeitId === null) {
      return state;
    }

    const revealedCounterfeit = encounter.counterfeits.find(
      (counterfeit) => counterfeit.id === state.revealedCounterfeitId,
    );
    if (!revealedCounterfeit) {
      return state;
    }

    const repaired = state.selectedRepairId === revealedCounterfeit.repairId;
    return {
      ...state,
      phase: repaired ? "resolved" : "counterbreak",
      repairAttempts: state.repairAttempts + 1,
      repairFeedback: repaired
        ? "Route repaired."
        : `Not yet. Trace this move: ${revealedCounterfeit.misconception}. Choose the patch that reverses that exact move.`,
    };
  }

  return state;
}
