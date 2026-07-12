export function deriveRenderEffects(action, previousState, nextState) {
  let focusTarget = { kind: "status" };
  let motionEvent = "none";
  let preventScroll = false;

  if (action.type === "SELECT_ANSWER") {
    focusTarget = { kind: "answer", value: action.answerId };
    preventScroll = true;
  } else if (action.type === "SELECT_REPAIR") {
    focusTarget = { kind: "repair", value: action.repairId };
    preventScroll = true;
  } else if (action.type === "COMMIT_ANSWER") {
    if (
      previousState.encounterState.phase === "choose" &&
      nextState.encounterState.phase === "counterbreak"
    ) {
      focusTarget = { kind: "id", value: "glitch-name" };
      motionEvent = "route-commit";
    }
  } else if (action.type === "COMMIT_REPAIR") {
    if (
      previousState.encounterState.phase === "counterbreak" &&
      nextState.encounterState.phase === "resolved"
    ) {
      focusTarget = { kind: "id", value: "trusted-proof-title" };
      motionEvent = "patch-commit";
    } else if (previousState.encounterState.selectedRepairId) {
      focusTarget = {
        kind: "repair",
        value: previousState.encounterState.selectedRepairId,
      };
      preventScroll = true;
    }
  } else if (action.type === "ADVANCE_RUN") {
    focusTarget = {
      kind: "id",
      value:
        nextState.phase === "complete"
          ? "run-complete-title"
          : "challenge-title",
    };
  } else if (action.type === "RESET_RUN") {
    focusTarget = { kind: "first-answer" };
  }

  return { focusTarget, motionEvent, preventScroll };
}

export function updatePersistentAnnouncement(announcer, status) {
  const nextStatus = String(status ?? "");
  if (!announcer || announcer.textContent === nextStatus) {
    return false;
  }
  announcer.textContent = nextStatus;
  return true;
}
