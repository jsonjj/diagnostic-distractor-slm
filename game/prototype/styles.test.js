import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { makeApprovedPack } from "./approved-pack-fixture.js";

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function cssVariable(name) {
  const match = css.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, "i"));
  assert.ok(match, `Expected --${name} to be a six-digit hex color.`);
  return match[1];
}

function luminance(hex) {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    .map((channel) => Number.parseInt(channel, 16) / 255)
    .map((channel) =>
      channel <= 0.04045
        ? channel / 12.92
        : ((channel + 0.055) / 1.055) ** 2.4,
    );
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(first, second) {
  const light = Math.max(luminance(first), luminance(second));
  const dark = Math.min(luminance(first), luminance(second));
  return (light + 0.05) / (dark + 0.05);
}

function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `Expected a CSS rule for ${selector}.`);
  return match[1];
}

test("uses a dual-color keyboard focus indicator with 3:1 fallback contrast", () => {
  const focusDark = cssVariable("focus-dark");
  const focusLight = cssVariable("focus-light");

  assert.ok(contrast(focusDark, cssVariable("paper")) >= 3);
  assert.ok(contrast(focusLight, cssVariable("graphite")) >= 3);
  assert.match(css, /outline:\s*3px solid var\(--focus-light\)/);
  assert.match(css, /box-shadow:\s*0 0 0 6px var\(--focus-dark\)/);
});

test("visually hides the persistent screen-reader announcer", () => {
  const body = ruleBody(".live-announcer");

  assert.match(body, /position:\s*absolute/);
  assert.match(body, /width:\s*1px/);
  assert.match(body, /height:\s*1px/);
  assert.match(body, /clip-path:\s*inset\(50%\)/);
});

test("keeps resolved primary-action text at 4.5:1 contrast or better", () => {
  const actionColor = cssVariable("confirmed-action");

  assert.ok(contrast(actionColor, "#ffffff") >= 4.5);
  assert.match(
    ruleBody(".phase-resolved .primary-action"),
    /background:\s*var\(--confirmed-action\)/,
  );
});

test("styles all canonical Glitch families with deterministic vehicle accents", () => {
  const families = Object.keys(makeApprovedPack().glitchFamilies);

  families.forEach((familyId) => {
    const body = ruleBody(`.glitch-car[data-family="${familyId}"]`);
    assert.match(body, /--glitch-body:\s*#[0-9a-f]{6}/i);
    assert.match(body, /--glitch-accent:\s*#[0-9a-f]{6}/i);
  });
});

test("lays out a selected four-lane route fork on the proof road", () => {
  assert.match(ruleBody(".route-fork"), /grid-template-columns:\s*repeat\(4,/);
  assert.match(ruleBody(".road-route"), /border:/);
  assert.match(ruleBody(".road-route.is-selected"), /--route-marker:/);
});

test("drives the player car toward the selected route lane", () => {
  for (let index = 0; index < 4; index += 1) {
    const body = ruleBody(`.route-choice-${index}`);
    assert.match(body, /--route-finish-x:/);
    assert.match(body, /--route-overshoot-x:/);
    assert.match(body, /--route-tilt:/);
  }
  assert.match(
    ruleBody(".phase-counterbreak .player-car"),
    /translate\(var\(--route-finish-x\), -70px\)/,
  );
  assert.match(css, /translate\(var\(--route-overshoot-x\), -82px\)/);
});

test("gates one-shot rally motion on semantic app events", () => {
  assert.doesNotMatch(ruleBody(".glitch-car.is-visible"), /animation:/);
  assert.doesNotMatch(ruleBody(".phase-counterbreak .road-seam"), /animation:/);
  assert.doesNotMatch(ruleBody(".phase-counterbreak .player-car"), /animation:/);
  assert.doesNotMatch(ruleBody(".phase-resolved .patch-beam"), /animation:/);
  assert.match(ruleBody(".glitch-car.is-visible"), /opacity:\s*1/);
  assert.match(ruleBody(".phase-resolved .patch-beam"), /width:\s*46%/);
  assert.match(
    ruleBody('#app[data-motion-event="route-commit"] .glitch-car.is-visible'),
    /animation:\s*glitch-unfold/,
  );
  assert.match(
    ruleBody(
      '#app[data-motion-event="route-commit"] .phase-counterbreak .road-seam',
    ),
    /animation:\s*crack-in/,
  );
  assert.match(
    ruleBody(
      '#app[data-motion-event="route-commit"] .phase-counterbreak .player-car',
    ),
    /animation:\s*commit-lane/,
  );
  assert.match(
    ruleBody('#app[data-motion-event="patch-commit"] .phase-resolved .patch-beam'),
    /animation:\s*patch-fire/,
  );
});
