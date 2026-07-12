function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderAnswers(view) {
  const locked = view.phase === "choose" ? "" : " disabled";
  return view.answers
    .map(
      (answer, index) => `
        <button
          class="answer-gate tone-${escapeHtml(answer.tone)}${answer.selected ? " is-selected" : ""}"
          type="button"
          data-answer-id="${escapeHtml(answer.id)}"
          aria-pressed="${answer.selected ? "true" : "false"}"${locked}
        >
          <span class="gate-marker" aria-hidden="true">${index + 1}</span>
          <span class="gate-copy">
            <span class="gate-answer">${escapeHtml(answer.display)}</span>
            ${answer.outcome ? `<span class="gate-outcome">${escapeHtml(answer.outcome)}</span>` : ""}
          </span>
          <span class="gate-chevron" aria-hidden="true">→</span>
        </button>`,
    )
    .join("");
}

function renderRepairs(view) {
  if (view.phase !== "counterbreak") {
    return "";
  }

  return `
    <fieldset class="repair-choices">
      <legend>Choose the rule patch</legend>
      ${view.repairs
        .map(
          (repair) => `
            <button
              class="repair-choice${repair.selected ? " is-selected" : ""}"
              type="button"
              data-repair-id="${escapeHtml(repair.id)}"
              aria-pressed="${repair.selected ? "true" : "false"}"
            >
              <span class="repair-icon" aria-hidden="true">✦</span>
              <span>
                <strong>${escapeHtml(repair.label)}</strong>
                <small>${escapeHtml(repair.detail)}</small>
              </span>
            </button>`,
        )
        .join("")}
    </fieldset>`;
}

function renderCounterbreak(view) {
  if (!view.glitch || view.phase === "choose") {
    return "";
  }

  return `
    <section class="counterbreak-card" aria-labelledby="glitch-name">
      <div class="counterbreak-heading">
        <span class="fault-stamp">Counterfeit trace</span>
        <span class="glitch-family">${escapeHtml(view.glitch.familyDisplayName)}</span>
      </div>
      <h2 id="glitch-name" tabindex="-1">${escapeHtml(view.glitch.name)}</h2>
      <p class="misconception">${escapeHtml(view.glitch.misconception)}</p>
      <code class="computation-trace">${escapeHtml(view.glitch.computation)}</code>
      ${
        view.phase === "resolved"
          ? `<p class="repair-explanation">${escapeHtml(view.glitch.repairExplanation)}</p>`
          : ""
      }
    </section>`;
}

function renderTrustedProof(view) {
  if (view.phase !== "resolved") {
    return "";
  }

  return `
    <section class="trusted-proof" aria-labelledby="trusted-proof-title">
      <span class="repair-stamp">Route repaired</span>
      <h2 id="trusted-proof-title" tabindex="-1">The clean proof</h2>
      <ol>
        ${view.trustedSteps.map((step) => `<li><code>${escapeHtml(step)}</code></li>`).join("")}
      </ol>
    </section>`;
}

function renderRunSummary(view) {
  if (!view.runSummary) {
    return "";
  }

  return `
    <section class="run-complete-card" aria-labelledby="run-complete-title">
      <span class="finish-stamp">${escapeHtml(view.contentPresentation.finishStamp)}</span>
      <h2 id="run-complete-title" tabindex="-1">Rally route secured!</h2>
      <p>Every counterfeit trail is patched. Your run record:</p>
      <div class="run-totals">
        <div>
          <strong>${escapeHtml(view.runSummary.checkpointCount)}</strong>
          <span>Checkpoints repaired</span>
        </div>
        <div>
          <strong>${escapeHtml(view.runSummary.proofBoostCount)}</strong>
          <span>Proof Boosts</span>
        </div>
        <div>
          <strong>${escapeHtml(view.runSummary.repairAttemptCount)}</strong>
          <span>Patch attempts</span>
        </div>
      </div>
    </section>`;
}

function renderRunProgress(view) {
  if (!view.run) {
    return "";
  }

  const currentValue = view.run.complete
    ? view.run.totalCheckpoints
    : view.run.currentCheckpoint;
  const checkpointPips = Array.from(
    { length: view.run.totalCheckpoints },
    (_, index) => {
      const checkpointNumber = index + 1;
      const checkpointState =
        view.run.complete || index < view.run.completedEncounterCount
          ? "complete"
          : checkpointNumber === view.run.currentCheckpoint
            ? "current"
            : "upcoming";
      return `<span data-checkpoint-state="${checkpointState}" aria-hidden="true"></span>`;
    },
  ).join("");

  return `
    <div
      class="run-progress"
      role="progressbar"
      aria-label="Rally progress: checkpoint ${escapeHtml(currentValue)} of ${escapeHtml(view.run.totalCheckpoints)}"
      aria-valuemin="1"
      aria-valuenow="${escapeHtml(currentValue)}"
      aria-valuemax="${escapeHtml(view.run.totalCheckpoints)}"
    >
      <strong>Checkpoint ${escapeHtml(currentValue)} / ${escapeHtml(view.run.totalCheckpoints)}</strong>
      <span class="checkpoint-track">${checkpointPips}</span>
    </div>`;
}

function renderRouteFork(view) {
  return `
    <div class="route-fork" aria-hidden="true">
      ${view.answers
        .map(
          (answer, index) => `
            <span
              class="road-route tone-${escapeHtml(answer.tone)}${answer.selected ? " is-selected" : ""}"
              data-road-answer-id="${escapeHtml(answer.id)}"
            >
              <b>${index + 1}</b>
              <small>${escapeHtml(answer.display)}</small>
            </span>`,
        )
        .join("")}
    </div>`;
}

function renderRallyStage(encounter, view) {
  const glitchVisible = view.glitch !== null && view.phase !== "choose";
  return `
    <section class="rally-stage" aria-label="Papercraft rally checkpoint">
      <div class="stage-sky" aria-hidden="true">
        <span class="paper-cloud cloud-one"></span>
        <span class="paper-cloud cloud-two"></span>
        <span class="factory-shape factory-one"></span>
        <span class="factory-shape factory-two"></span>
      </div>
      <div class="stage-sign">
        <span>${escapeHtml(encounter.district)}</span>
        <small>${escapeHtml(encounter.roomLabel)}</small>
      </div>
      <div class="proof-road" aria-hidden="true">
        <div class="road-seam"></div>
        ${renderRouteFork(view)}
        <div class="road-equation">
          ${escapeHtml(encounter.question.roadEquation ?? "?")}
        </div>
        <div class="toy-car player-car">
          <span class="car-cabin"></span>
          <span class="wheel wheel-left"></span>
          <span class="wheel wheel-right"></span>
          <span class="car-flag">MB</span>
        </div>
        <div class="toy-car glitch-car${glitchVisible ? " is-visible" : ""}" data-family="${escapeHtml(view.glitch?.familyId ?? "hidden")}">
          <span class="glitch-jaw"></span>
          <span class="wheel wheel-left"></span>
          <span class="wheel wheel-right"></span>
          <span class="glitch-eye"></span>
        </div>
        <div class="patch-beam"></div>
      </div>
      <div class="stage-status">
        <span>Stage status</span>
        <strong>${escapeHtml(view.stageStatusLabel)}</strong>
      </div>
      <p class="stage-caption">${escapeHtml(view.stageSummary)}</p>
    </section>`;
}

export function renderEncounterMarkup(encounter, view) {
  const disabled = view.primaryAction.disabled ? " disabled" : "";
  const boostActive = view.proofBoostCount > 0 ? " is-active" : "";
  const boostCount = view.run ? ` × ${escapeHtml(view.proofBoostCount)}` : "";
  const routeChoiceClass =
    Number.isInteger(view.selectedRouteIndex) &&
    view.selectedRouteIndex >= 0 &&
    view.selectedRouteIndex < 4
      ? ` route-choice-${view.selectedRouteIndex}`
      : "";
  return `
    <a class="skip-link" href="#challenge-title">Skip to the math challenge</a>
    <main class="game-shell phase-${escapeHtml(view.phase)}${routeChoiceClass}" data-phase="${escapeHtml(view.phase)}">
      <header class="topbar">
        <div class="brand-lockup">
          <span class="brand-mark" aria-hidden="true">M</span>
          <div>
            <strong>Mathbreakers</strong>
            <span>Glitch Rally</span>
          </div>
        </div>
        <div class="run-readout">
          ${renderRunProgress(view)}
          <span class="forge-chip">${escapeHtml(view.contentPresentation.forgeLabel)}</span>
          <span class="boost-chip${boostActive}">Proof Boost${boostCount}</span>
        </div>
      </header>

      <div class="encounter-grid">
        ${renderRallyStage(encounter, view)}

        <section class="challenge-panel" id="challenge" aria-labelledby="challenge-title">
          <div class="challenge-kicker">
            <span>Truth Gate</span>
            <span>${escapeHtml(encounter.question.topic)}</span>
          </div>
          <h1 id="challenge-title" tabindex="-1">${escapeHtml(encounter.question.prompt)}</h1>
          <p class="instruction">${escapeHtml(view.instruction)}</p>

          <div class="answer-grid" aria-label="Route answers">
            ${renderAnswers(view)}
          </div>

          ${renderCounterbreak(view)}
          ${renderRepairs(view)}
          ${renderTrustedProof(view)}
          ${renderRunSummary(view)}

          <div class="action-dock">
            <p class="game-status" tabindex="-1">${escapeHtml(view.status)}</p>
            <button class="primary-action" type="button" data-primary-action${disabled}>
              <span>${escapeHtml(view.primaryAction.label)}</span>
              <span aria-hidden="true">➜</span>
            </button>
          </div>
        </section>
      </div>

      <footer class="content-note">
        <strong>${escapeHtml(view.contentPresentation.footerTitle)}</strong>
        ${escapeHtml(view.contentPresentation.footerBody)}
        <span>${escapeHtml(view.prototypeNotice)}</span>
      </footer>
    </main>`;
}
