import assert from "node:assert/strict";
import test from "node:test";

import * as bootstrap from "./bootstrap.js";
import { makeApprovedPack } from "./approved-pack-fixture.js";
import {
  loadEncounterSource,
  parseApprovedPackSelection,
  renderBootErrorMarkup,
} from "./bootstrap.js";
import { prototypeEncounters } from "./sample-encounter.js";

const PAGE_URL = "https://mathbreakers.test/prototype/?pack=glitch-rally-v1";

function jsonResponse(pack, url = "https://mathbreakers.test/content/packs/glitch-rally-v1.json") {
  return {
    ok: true,
    redirected: false,
    status: 200,
    url,
    headers: new Headers({ "content-type": "application/json" }),
    text: async () => JSON.stringify(pack),
  };
}

test("labels prototype and reviewed SLM runs truthfully in the document title", () => {
  assert.equal(typeof bootstrap.getContentSourceTitle, "function");
  assert.equal(
    bootstrap.getContentSourceTitle("prototype"),
    "Mathbreakers: Glitch Rally — Prototype",
  );
  assert.equal(
    bootstrap.getContentSourceTitle("approved"),
    "Mathbreakers: Glitch Rally — Reviewed SLM Run",
  );
});

test("keeps the no-selection boot explicitly on prototype encounters", async () => {
  let fetched = false;
  const result = await loadEncounterSource({
    pageUrl: "https://mathbreakers.test/prototype/",
    prototypeEncounters,
    fetchImpl: async () => {
      fetched = true;
      throw new Error("must not fetch");
    },
  });

  assert.equal(result.kind, "prototype");
  assert.equal(result.packId, null);
  assert.equal(result.encounters, prototypeEncounters);
  assert.equal(fetched, false);
});

test("maps one narrow pack ID to the same-origin released-pack directory", () => {
  assert.deepEqual(parseApprovedPackSelection(PAGE_URL), {
    packId: "glitch-rally-v1",
    url: "https://mathbreakers.test/content/packs/glitch-rally-v1.json",
  });
});

test("rejects empty, repeated, path-like, URL-like, and ambiguous pack selections", () => {
  const invalidSelections = [
    "?pack=",
    "?pack=a",
    "?pack=../secret",
    "?pack=..%2Fsecret",
    "?pack=folder%2Fpack",
    "?pack=folder%5Cpack",
    "?pack=https%3A%2F%2Fevil.test%2Fpack",
    "?pack=glitch-rally-v1.json",
    "?pack=glitch..rally",
    "?pack=glitch-rally-v1&pack=other-pack",
    "?pack=UPPERCASE",
  ];

  for (const search of invalidSelections) {
    assert.throws(
      () =>
        parseApprovedPackSelection(
          `https://mathbreakers.test/prototype/${search}`,
        ),
      /approved content selection is invalid/i,
      search,
    );
  }
});

test("fetches and verifies an explicitly selected approved pack without cloning its encounters", async () => {
  const calls = [];
  const result = await loadEncounterSource({
    pageUrl: PAGE_URL,
    prototypeEncounters,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return jsonResponse(makeApprovedPack());
    },
  });

  assert.equal(result.kind, "approved");
  assert.equal(result.packId, "glitch-rally-v1");
  assert.equal(result.encounters.length, 1);
  assert.equal(result.encounters[0].id, "GR-NUM-006");
  assert.deepEqual(calls, [
    {
      url: "https://mathbreakers.test/content/packs/glitch-rally-v1.json",
      options: {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        method: "GET",
        redirect: "error",
      },
    },
  ]);
});

test("fails closed instead of returning prototype content when approved loading fails", async () => {
  const failures = [
    async () => {
      throw new Error("network details must stay private");
    },
    async () => ({ ...jsonResponse(makeApprovedPack()), ok: false, status: 404 }),
    async () => ({ ...jsonResponse(makeApprovedPack()), text: async () => "{" }),
    async () => {
      const pack = makeApprovedPack();
      pack.contentHash = `pack:v1:${"0".repeat(64)}`;
      return jsonResponse(pack);
    },
  ];

  for (const fetchImpl of failures) {
    await assert.rejects(
      () =>
        loadEncounterSource({ pageUrl: PAGE_URL, prototypeEncounters, fetchImpl }),
      /approved content could not be loaded/i,
    );
  }
});

test("rejects redirected or non-JSON approved-pack responses", async () => {
  const redirected = jsonResponse(
    makeApprovedPack(),
    "https://elsewhere.test/content/packs/glitch-rally-v1.json",
  );
  redirected.redirected = true;
  const html = jsonResponse(makeApprovedPack());
  html.headers = new Headers({ "content-type": "text/html" });

  for (const response of [redirected, html]) {
    await assert.rejects(
      () =>
        loadEncounterSource({
          pageUrl: PAGE_URL,
          prototypeEncounters,
          fetchImpl: async () => response,
        }),
      /approved content could not be loaded/i,
    );
  }
});

test("renders a static safe boot error without reflecting error or path text", () => {
  const markup = renderBootErrorMarkup(
    new Error('<img src=x onerror="alert(1)"> /private/pack.json'),
  );

  assert.match(markup, /Approved content unavailable/);
  assert.match(markup, /prototype/i);
  assert.doesNotMatch(markup, /onerror|private|pack\.json|alert\(1\)/i);
  assert.match(markup, /role="alert"/);
});
