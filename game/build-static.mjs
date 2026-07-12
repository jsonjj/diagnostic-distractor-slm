import {
  copyFile,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { loadApprovedPack } from "./prototype/content.js";

const RUNTIME_FILES = Object.freeze([
  "index.html",
  "app.js",
  "bootstrap.js",
  "content.js",
  "encounter.js",
  "render.js",
  "runtime-effects.js",
  "sample-encounter.js",
  "styles.css",
  "view-model.js",
]);
const PACK_FILENAME_PATTERN =
  /^[a-z0-9](?:[a-z0-9.-]{1,62}[a-z0-9])\.json$/;

async function copyReleasedPacks(sourceDir, temporaryOutDir) {
  const sourcePackDir = join(sourceDir, "content", "packs");
  const outputPackDir = join(temporaryOutDir, "content", "packs");
  await mkdir(outputPackDir, { recursive: true });

  const entries = await readdir(sourcePackDir, { withFileTypes: true });
  const packFiles = [];
  for (const entry of entries) {
    if (!entry.name.endsWith(".json")) {
      continue;
    }
    if (
      !entry.isFile() ||
      !PACK_FILENAME_PATTERN.test(entry.name) ||
      entry.name.includes("..")
    ) {
      throw new Error(`Invalid released pack filename: ${entry.name}`);
    }
    packFiles.push(entry.name);
  }
  packFiles.sort();

  await Promise.all(
    packFiles.map(async (filename) => {
      const bytes = await readFile(join(sourcePackDir, filename));
      let pack;
      try {
        pack = JSON.parse(bytes.toString("utf8"));
      } catch (error) {
        throw new Error(
          `Approved pack validation failed: ${filename} is not valid JSON.`,
          { cause: error },
        );
      }
      await loadApprovedPack(pack);
      await writeFile(
        join(outputPackDir, filename),
        `${JSON.stringify(pack)}\n`,
        "utf8",
      );
    }),
  );
  return packFiles.length;
}

export async function buildStaticSite({ sourceDir, outDir }) {
  const resolvedSource = resolve(sourceDir);
  const resolvedOut = resolve(outDir);
  if (resolvedSource === resolvedOut) {
    throw new Error("Static output directory must differ from its source.");
  }

  const temporaryOut = `${resolvedOut}.tmp-${process.pid}-${Date.now()}`;
  await rm(temporaryOut, { recursive: true, force: true });
  try {
    await mkdir(join(temporaryOut, "prototype"), { recursive: true });
    await copyFile(
      join(resolvedSource, "index.html"),
      join(temporaryOut, "index.html"),
    );
    await Promise.all(
      RUNTIME_FILES.map((filename) =>
        copyFile(
          join(resolvedSource, "prototype", filename),
          join(temporaryOut, "prototype", filename),
        ),
      ),
    );
    const packCount = await copyReleasedPacks(resolvedSource, temporaryOut);

    await rm(resolvedOut, { recursive: true, force: true });
    await rename(temporaryOut, resolvedOut);
    return Object.freeze({
      outDir: resolvedOut,
      packCount,
      runtimeFileCount: RUNTIME_FILES.length,
    });
  } catch (error) {
    await rm(temporaryOut, { recursive: true, force: true });
    throw error;
  }
}

const modulePath = fileURLToPath(import.meta.url);
if (process.argv[1] && resolve(process.argv[1]) === modulePath) {
  const sourceDir = dirname(modulePath);
  try {
    const result = await buildStaticSite({
      sourceDir,
      outDir: join(sourceDir, "dist"),
    });
    console.log(
      `Built ${result.runtimeFileCount} prototype files and ${result.packCount} approved packs in ${result.outDir}`,
    );
  } catch (error) {
    console.error("Static build failed.", error);
    process.exitCode = 1;
  }
}
