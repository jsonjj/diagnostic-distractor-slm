import {
  createInitialRunState,
  getRunPrimaryAction,
  reduceRun,
} from "./encounter.js";
import {
  getContentSourceTitle,
  loadEncounterSource,
  renderBootErrorMarkup,
} from "./bootstrap.js";
import { renderEncounterMarkup } from "./render.js";
import {
  deriveRenderEffects,
  updatePersistentAnnouncement,
} from "./runtime-effects.js";
import { prototypeEncounters } from "./sample-encounter.js";
import { createRunViewModel } from "./view-model.js";

const root = document.querySelector("#app");
const announcer = document.querySelector("#game-announcer");
if (!(root instanceof HTMLElement) || !(announcer instanceof HTMLElement)) {
  throw new Error("Mathbreakers could not find its required application roots.");
}

let encounters;
let state;

function findFocusTarget(target) {
  if (!target) {
    return null;
  }
  if (target.kind === "status") {
    return root.querySelector(".game-status");
  }
  if (target.kind === "first-answer") {
    return root.querySelector("[data-answer-id]");
  }
  if (target.kind === "id") {
    return [...root.querySelectorAll("[id]")].find(
      (element) => element.id === target.value,
    );
  }
  const attribute =
    target.kind === "answer"
      ? "data-answer-id"
      : target.kind === "repair"
        ? "data-repair-id"
        : null;
  if (!attribute) {
    return null;
  }
  return [...root.querySelectorAll(`[${attribute}]`)].find(
    (element) => element.getAttribute(attribute) === target.value,
  );
}

function focusAfterRender(target, preventScroll) {
  if (!target) {
    return;
  }
  requestAnimationFrame(() => {
    const element = findFocusTarget(target);
    if (element instanceof HTMLElement) {
      if (preventScroll) {
        element.focus({ preventScroll: true });
      } else {
        element.focus();
      }
    }
  });
}

function dispatch(action) {
  const previousState = state;
  state = reduceRun(encounters, state, action);
  render(deriveRenderEffects(action, previousState, state));
}

function bindAnswerGates() {
  root.querySelectorAll("[data-answer-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const answerId = button.getAttribute("data-answer-id");
      if (answerId) {
        dispatch({ type: "SELECT_ANSWER", answerId });
      }
    });
  });
}

function bindRepairChoices() {
  root.querySelectorAll("[data-repair-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const repairId = button.getAttribute("data-repair-id");
      if (repairId) {
        dispatch({ type: "SELECT_REPAIR", repairId });
      }
    });
  });
}

function bindPrimaryAction() {
  const button = root.querySelector("[data-primary-action]");
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }

  button.addEventListener("click", () => {
    dispatch(getRunPrimaryAction(state));
  });
}

function render({
  focusTarget = null,
  motionEvent = "none",
  preventScroll = false,
} = {}) {
  const { encounter, view } = createRunViewModel(encounters, state);
  document.documentElement.dataset.encounterPhase = view.phase;
  document.documentElement.dataset.runPhase = state.phase;
  root.dataset.motionEvent = motionEvent;
  root.innerHTML = renderEncounterMarkup(encounter, view);
  const visualStatus = root.querySelector(".game-status");
  visualStatus?.removeAttribute("role");
  visualStatus?.removeAttribute("aria-live");
  updatePersistentAnnouncement(announcer, view.status);
  bindAnswerGates();
  bindRepairChoices();
  bindPrimaryAction();
  focusAfterRender(focusTarget, preventScroll);
}

async function boot() {
  try {
    const source = await loadEncounterSource({
      pageUrl: window.location.href,
      prototypeEncounters,
    });
    encounters = source.encounters;
    state = createInitialRunState(encounters);
    document.documentElement.dataset.contentSource = source.kind;
    document.title = getContentSourceTitle(source.kind);
    render();
  } catch (error) {
    document.documentElement.dataset.contentSource = "error";
    document.title = "Mathbreakers: Glitch Rally — Content unavailable";
    root.dataset.motionEvent = "none";
    root.innerHTML = renderBootErrorMarkup();
    updatePersistentAnnouncement(
      announcer,
      "Approved content unavailable. No encounter was started.",
    );
    console.error("Mathbreakers stopped an unverified content load.", error);
  }
}

await boot();
