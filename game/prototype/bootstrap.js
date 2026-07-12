import { loadApprovedPack } from "./content.js";

const PACK_ID_PATTERN = /^[a-z0-9](?:[a-z0-9.-]{1,62}[a-z0-9])$/;
const MAX_PACK_BYTES = 1_000_000;
const SAFE_LOAD_ERROR = "Approved content could not be loaded.";

export function getContentSourceTitle(kind) {
  if (kind === "prototype") {
    return "Mathbreakers: Glitch Rally — Prototype";
  }
  if (kind === "approved") {
    return "Mathbreakers: Glitch Rally — Reviewed SLM Run";
  }
  throw new Error("Unknown game content source.");
}

function invalidSelection() {
  return new Error("Approved content selection is invalid.");
}

export function parseApprovedPackSelection(pageUrl) {
  let page;
  try {
    page = new URL(pageUrl);
  } catch {
    throw invalidSelection();
  }

  if (!page.searchParams.has("pack")) {
    return null;
  }

  const values = page.searchParams.getAll("pack");
  const packId = values[0] ?? "";
  if (
    values.length !== 1 ||
    !PACK_ID_PATTERN.test(packId) ||
    packId.includes("..") ||
    packId.endsWith(".json")
  ) {
    throw invalidSelection();
  }

  const packUrl = new URL(`../content/packs/${packId}.json`, page);
  if (packUrl.origin !== page.origin) {
    throw invalidSelection();
  }

  return { packId, url: packUrl.href };
}

function responseIsExpectedJson(response, expectedUrl) {
  if (!response?.ok || response.redirected) {
    return false;
  }

  try {
    if (response.url && new URL(response.url).href !== expectedUrl) {
      return false;
    }
  } catch {
    return false;
  }

  const contentType = response.headers?.get?.("content-type") ?? "";
  return /^(?:application\/json|[^;]+\+json)(?:\s*;|$)/i.test(contentType);
}

export async function loadEncounterSource({
  pageUrl,
  prototypeEncounters,
  fetchImpl = globalThis.fetch,
}) {
  const selection = parseApprovedPackSelection(pageUrl);
  if (selection === null) {
    return Object.freeze({
      kind: "prototype",
      packId: null,
      encounters: prototypeEncounters,
    });
  }

  try {
    if (typeof fetchImpl !== "function") {
      throw new TypeError("Fetch is unavailable.");
    }
    const response = await fetchImpl(selection.url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      method: "GET",
      redirect: "error",
    });
    if (!responseIsExpectedJson(response, selection.url)) {
      throw new Error("Unexpected approved-pack response.");
    }

    const declaredLength = Number(response.headers.get("content-length"));
    if (
      Number.isFinite(declaredLength) &&
      declaredLength > MAX_PACK_BYTES
    ) {
      throw new Error("Approved pack exceeds the size limit.");
    }

    const text = await response.text();
    if (text.length === 0 || text.length > MAX_PACK_BYTES) {
      throw new Error("Approved pack exceeds the size limit.");
    }
    const encounters = await loadApprovedPack(JSON.parse(text));
    return Object.freeze({
      kind: "approved",
      packId: selection.packId,
      encounters,
    });
  } catch (error) {
    throw new Error(SAFE_LOAD_ERROR, { cause: error });
  }
}

export function renderBootErrorMarkup() {
  return `
    <main class="game-shell boot-error" role="alert" aria-labelledby="boot-error-title">
      <section class="challenge-panel">
        <span class="fault-stamp">Run stopped safely</span>
        <h1 id="boot-error-title">Approved content unavailable</h1>
        <p>
          This approved run could not be verified, so no encounter was started and
          no substitute content was loaded.
        </p>
        <p>
          <a class="primary-action" href="./">Open the clearly labeled prototype</a>
        </p>
      </section>
    </main>`;
}
