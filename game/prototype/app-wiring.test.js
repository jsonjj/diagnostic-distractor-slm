import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appUrl = new URL("./app.js", import.meta.url);
const pageUrl = new URL("./index.html", import.meta.url);

test("boots either explicit prototype content or a verified approved source", async () => {
  const source = await readFile(appUrl, "utf8");

  assert.match(source, /await loadEncounterSource\(/);
  assert.match(source, /createInitialRunState\(encounters\)/);
  assert.match(source, /reduceRun\(encounters, state, action\)/);
  assert.match(source, /renderBootErrorMarkup\(\)/);
  assert.match(source, /document\.title = getContentSourceTitle\(source\.kind\)/);
  assert.doesNotMatch(
    source,
    /createInitialRunState\(prototypeEncounters\)/,
    "prototype fixtures must not be a silent runtime fallback",
  );
});

test("keeps one live region outside the replaceable application root", async () => {
  const [source, page] = await Promise.all([
    readFile(appUrl, "utf8"),
    readFile(pageUrl, "utf8"),
  ]);

  const announcerIndex = page.indexOf('id="game-announcer"');
  const appIndex = page.indexOf('id="app"');
  assert.ok(announcerIndex >= 0 && announcerIndex < appIndex);
  assert.match(page, /id="game-announcer"[^>]*aria-live="polite"/);
  assert.match(source, /updatePersistentAnnouncement\(announcer, view\.status\)/);
});

test("passes explicit commit-only motion events into the rendered root", async () => {
  const source = await readFile(appUrl, "utf8");

  assert.match(source, /deriveRenderEffects\(action, previousState, state\)/);
  assert.match(source, /root\.dataset\.motionEvent = motionEvent/);
});

test("preserves scroll only for same-control focus restoration", async () => {
  const source = await readFile(appUrl, "utf8");

  assert.match(source, /function focusAfterRender\(target, preventScroll\)/);
  assert.match(source, /focusAfterRender\(focusTarget, preventScroll\)/);
  assert.match(source, /element\.focus\(\{ preventScroll: true \}\)/);
  assert.match(source, /element\.focus\(\);/);
});
