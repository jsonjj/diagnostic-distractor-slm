import assert from "node:assert/strict";
import {
  access,
  cp,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

import { buildStaticSite } from "./build-static.mjs";
import { makeApprovedPack } from "./prototype/approved-pack-fixture.js";

const gameRoot = dirname(fileURLToPath(import.meta.url));

test("root entrypoint selects the reviewed pack", async () => {
  const html = await readFile(join(gameRoot, "index.html"), "utf8");

  assert.match(
    html,
    /<meta\s+http-equiv="refresh"\s+content="0; url=\.\/prototype\/\?pack=glitch-rally-v1"\s*\/>/,
  );
  assert.match(
    html,
    /<a\s+href="\.\/prototype\/\?pack=glitch-rally-v1">[^<]+<\/a>/,
  );
});

async function makeFixture() {
  const temporaryRoot = await mkdtemp(join(tmpdir(), "glitch-rally-build-"));
  const sourceDir = join(temporaryRoot, "game");
  const outDir = join(temporaryRoot, "dist");
  await cp(gameRoot, sourceDir, {
    recursive: true,
    filter: (source) =>
      !source.includes(`${join(gameRoot, "dist")}`) &&
      !source.includes(`${join(gameRoot, "node_modules")}`),
  });
  return { temporaryRoot, sourceDir, outDir };
}

test("builds a dependency-free playable artifact with reviewed pack JSON", async (t) => {
  const fixture = await makeFixture();
  t.after(() => rm(fixture.temporaryRoot, { recursive: true, force: true }));
  const existingPackCount = (
    await readdir(join(fixture.sourceDir, "content", "packs"), {
      withFileTypes: true,
    })
  ).filter((entry) => entry.isFile() && entry.name.endsWith(".json")).length;
  const packPath = join(
    fixture.sourceDir,
    "content",
    "packs",
    "glitch-rally-test-v1.json",
  );
  const packText = `${JSON.stringify(makeApprovedPack())}\n`;
  await writeFile(packPath, packText, "utf8");

  const result = await buildStaticSite({
    sourceDir: fixture.sourceDir,
    outDir: fixture.outDir,
  });

  assert.equal(result.packCount, existingPackCount + 1);
  assert.match(
    await readFile(join(fixture.outDir, "index.html"), "utf8"),
    /url=\.\/prototype\//,
  );
  for (const relativePath of [
    "prototype/index.html",
    "prototype/app.js",
    "prototype/bootstrap.js",
    "prototype/content.js",
    "prototype/encounter.js",
    "prototype/render.js",
    "prototype/runtime-effects.js",
    "prototype/sample-encounter.js",
    "prototype/styles.css",
    "prototype/view-model.js",
  ]) {
    await access(join(fixture.outDir, relativePath));
  }
  assert.equal(
    await readFile(
      join(
        fixture.outDir,
        "content",
        "packs",
        "glitch-rally-test-v1.json",
      ),
      "utf8",
    ),
    packText,
  );
  await assert.rejects(access(join(fixture.outDir, "prototype", "bootstrap.test.js")));
  await assert.rejects(
    access(join(fixture.outDir, "prototype", "approved-pack-fixture.js")),
  );
});

test("refuses to publish a validly named JSON file that is not an approved pack", async (t) => {
  const fixture = await makeFixture();
  t.after(() => rm(fixture.temporaryRoot, { recursive: true, force: true }));
  await writeFile(
    join(
      fixture.sourceDir,
      "content",
      "packs",
      "glitch-rally-malformed-v1.json",
    ),
    '{"schemaVersion":"glitch-rally-pack-v1","reviewerEmail":"private@example.test"}',
    "utf8",
  );

  await assert.rejects(
    () =>
      buildStaticSite({ sourceDir: fixture.sourceDir, outDir: fixture.outDir }),
    /approved pack validation/i,
  );
  await assert.rejects(access(fixture.outDir));
});

test("canonicalizes a verified pack so shadowed duplicate values cannot ship", async (t) => {
  const fixture = await makeFixture();
  t.after(() => rm(fixture.temporaryRoot, { recursive: true, force: true }));
  const pack = makeApprovedPack();
  const approvedOrigin = '"contentOrigin":"offline-slm-generated-owner-reviewed"';
  const sourceText = `${JSON.stringify(pack).replace(
    approvedOrigin,
    `"contentOrigin":"private-reviewer@example.test",${approvedOrigin}`,
  )}\n`;
  await writeFile(
    join(
      fixture.sourceDir,
      "content",
      "packs",
      "glitch-rally-shadowed-v1.json",
    ),
    sourceText,
    "utf8",
  );

  await buildStaticSite({ sourceDir: fixture.sourceDir, outDir: fixture.outDir });
  const published = await readFile(
    join(
      fixture.outDir,
      "content",
      "packs",
      "glitch-rally-shadowed-v1.json",
    ),
    "utf8",
  );
  assert.doesNotMatch(published, /private-reviewer/);
  assert.equal(JSON.parse(published).contentOrigin, "offline-slm-generated-owner-reviewed");
});

test("emits every relative JavaScript module imported by the playable artifact", async (t) => {
  const fixture = await makeFixture();
  t.after(() => rm(fixture.temporaryRoot, { recursive: true, force: true }));
  await buildStaticSite({ sourceDir: fixture.sourceDir, outDir: fixture.outDir });

  const runtimeFiles = [
    "app.js",
    "bootstrap.js",
    "content.js",
    "encounter.js",
    "render.js",
    "runtime-effects.js",
    "sample-encounter.js",
    "view-model.js",
  ];
  for (const filename of runtimeFiles) {
    const builtPath = join(fixture.outDir, "prototype", filename);
    const source = await readFile(builtPath, "utf8");
    const imports = source.matchAll(/from\s+["'](\.\/[^"']+)["']/g);
    for (const match of imports) {
      const importedPath = fileURLToPath(
        new URL(match[1], pathToFileURL(builtPath)),
      );
      await access(importedPath);
    }
  }
});

test("fails the build for a JSON pack outside the public filename contract", async (t) => {
  for (const filename of ["Bad Pack.json", "glitch..rally.json"]) {
    await t.test(filename, async (subtest) => {
      const fixture = await makeFixture();
      subtest.after(() =>
        rm(fixture.temporaryRoot, { recursive: true, force: true }),
      );
      await writeFile(
        join(fixture.sourceDir, "content", "packs", filename),
        "{}",
        "utf8",
      );

      await assert.rejects(
        () =>
          buildStaticSite({
            sourceDir: fixture.sourceDir,
            outDir: fixture.outDir,
          }),
        /pack filename/i,
      );
    });
  }
});

test("uses the dependency-free static builder as the default package build", async () => {
  const packageJson = JSON.parse(
    await readFile(join(gameRoot, "package.json"), "utf8"),
  );
  assert.equal(packageJson.scripts.build, "node build-static.mjs");
  assert.equal(
    packageJson.scripts.test,
    "node --test build-static.test.js prototype/*.test.js",
  );
});
