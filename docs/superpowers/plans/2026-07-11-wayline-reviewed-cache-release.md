# Wayline Reviewed-Cache Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish one reviewed-cache SQLite database and its exact manifest as an atomic, content-addressed generation that the learner runtime can fail-closed validate before opening.

**Architecture:** The existing offline builder continues to create and audit a read-only SQLite file plus canonical manifest JSON. A release publisher places both in an immutable generation directory, verifies them, then atomically replaces one small strict pointer; the runtime follows only that pointer, rehashes both artifacts, validates every cache row, and opens the cache read-only. A crash before pointer replacement leaves the prior generation current; a crash after replacement can expose only a pointer to a fully written generation.

**Tech Stack:** Python 3.12 standard library, SQLite, existing `ReviewedCache`, `PinnedSlmManifest`, `QuestionCompiler`, and `unittest`.

## Global Constraints

- Follow `docs/superpowers/plans/2026-07-11-wayline-master-roadmap.md` and `docs/wayline/WAYLINE_LEARNING_AND_RUNTIME_SPEC.md`.
- Never place raw SLM output, learner/profile/session data, answer selections, confidence, secrets, or review notes in a release artifact.
- Hashes are integrity receipts, not signatures; package/code-signing trust remains a separate release gate.
- All paths are absolute, normalized, owner-controlled, non-symlink paths on one filesystem.
- Generations are immutable after publication; publishing a new generation never mutates an existing one.
- Runtime validation is fail-closed: no pointer, malformed pointer, digest mismatch, unexpected file, bad permissions, invalid row, or receipt mismatch means no cache opens.
- Do not install dependencies, invoke Unity, use network/model/TrueFoundry services, stage, commit, push, or publish a real cache.

## File Structure

- Create `services/wayline_forge/app/reviewed_cache_release.py`: strict pointer/manifest parser, generation validator, and read-only runtime handle.
- Modify `services/wayline_forge/app/reviewed_cache.py`: add a trusted-FD learner open that never resolves and reopens an attacker-swappable pathname.
- Create `services/wayline_forge/scripts/publish_reviewed_cache.py`: content-addressed generation writer and atomic pointer switch.
- Create `services/wayline_forge/tests/test_reviewed_cache_release.py`: runtime rejection and eager-row-validation tests.
- Modify `services/wayline_forge/tests/test_reviewed_cache.py`: prove the trusted-FD SQLite connection remains bound across pathname replacement.
- Create `services/wayline_forge/tests/test_publish_reviewed_cache.py`: crash-boundary, durability, and immutable-generation publication tests.
- Reuse `services/wayline_forge/scripts/build_reviewed_cache.py` without changing its learner-safety contracts.

---

### Task 1: Strict runtime generation validation

**Files:**
- Create: `services/wayline_forge/app/reviewed_cache_release.py`
- Create: `services/wayline_forge/tests/test_reviewed_cache_release.py`
- Modify: `services/wayline_forge/app/reviewed_cache.py`
- Modify: `services/wayline_forge/tests/test_reviewed_cache.py`

**Interfaces:**
- Produces `ReviewedCacheRelease.open_current(root, compiler, model_manifest) -> ReviewedCacheRelease`.
- Produces `ReviewedCacheRelease.open_pointer(root, pointer_name, compiler, model_manifest) -> ReviewedCacheRelease` for one safe release-root leaf; `open_current` delegates with `pointer_name="current.json"`.
- Produces `ReviewedCache.open_learner_fd(fd, compiler, manifest) -> ReviewedCache`, opening through a caller-retained trusted descriptor without `Path.resolve()` or pathname reopen.
- Produces `release.cache: ReviewedCache`, `release.generation_id: str`, and `release.close()`/context-manager behavior.
- Pointer schema is exactly `wayline.reviewed-cache-pointer.v1` with `generationId` and `manifestSha256`.
- Generation ID is exactly `generation-<manifestSha256>`.

- [ ] **Step 1: Write the failing happy-path and malformed-pointer tests**

```python
def test_open_current_rehashes_manifest_database_and_every_row(self):
    release = ReviewedCacheRelease.open_current(
        self.release_root,
        compiler=self.verifier.compiler,
        model_manifest=self.verifier.manifest,
    )
    self.assertEqual(release.generation_id, "generation-" + self.manifest_sha256)
    self.assertFalse(release.cache.writable)

def test_unknown_duplicate_or_noncanonical_pointer_fails_closed(self):
    for payload in self.invalid_pointer_payloads():
        self.write_pointer(payload)
        with self.assertRaises(ReviewedCacheReleaseError):
            ReviewedCacheRelease.open_current(
                self.release_root,
                compiler=self.verifier.compiler,
                model_manifest=self.verifier.manifest,
            )
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv/bin/python -m unittest services.wayline_forge.tests.test_reviewed_cache_release -v
```

Expected: import failure because `reviewed_cache_release.py` does not exist.

- [ ] **Step 3: Implement strict pointer and manifest parsing**

Use duplicate-rejecting standard JSON, reject NaN/infinity/BOM/unknown fields, bound pointer to 4 KiB and manifest to 16 MiB, require lowercase SHA-256, require `generationId == "generation-" + manifestSha256`, and reject any generation path or file reached through a symlink. Require exactly `reviewed_cache.sqlite3` and `reviewed_cache_manifest.json` in the generation directory.

- [ ] **Step 4: Validate the complete generation before exposing the cache**

Require private owner-controlled directories, generation files regular/single-link/read-only, manifest bytes matching the pointer digest, database bytes/size matching manifest, manifest runtime receipts matching the supplied compiler/model manifest, no SQLite sidecars, `PRAGMA integrity_check == ok`, exact cache schema/indexes, and eager `_validate_row` over every row. Bind every manifest item’s `cacheContentSha256` to that exact row’s `rowSha256`; require exact row count and aggregate hashes. `open_pointer` accepts only a non-dot leaf without separators and uses the same release-root directory descriptor as `open_current`. Retain the exact validated database descriptor for the release-handle lifetime and open SQLite through `ReviewedCache.open_learner_fd`; a pathname inode swap must not change the opened database.

- [ ] **Step 5: Add tamper and path-race tests**

Test pointer/manifest/database byte changes, missing/extra generation files, wrong generation name, symlinked pointer/generation/file, hardlinked files, writable files, wrong receipt, wrong per-item row binding, corrupt row, sidecars, and pointer replacement during open. Every case returns one non-sensitive `ReviewedCacheReleaseError` code and no open cache.

- [ ] **Step 6: Verify GREEN**

Run Task 1 tests. Expected: all pass with no leaked artifact content in exception text or repr.

---

### Task 2: Atomic content-addressed generation publication

**Files:**
- Create: `services/wayline_forge/scripts/publish_reviewed_cache.py`
- Create: `services/wayline_forge/tests/test_publish_reviewed_cache.py`

**Interfaces:**
- Produces `publish_reviewed_cache(input_path, release_root, compiler, model_manifest) -> PublishedCacheGeneration`.
- The result carries generation ID, pointer SHA-256, manifest SHA-256, database SHA-256, and item count.
- Layout is `release_root/generations/generation-<manifestSha256>/{reviewed_cache.sqlite3,reviewed_cache_manifest.json}` plus `release_root/current.json`.

- [ ] **Step 1: Write the failing publication and crash-boundary tests**

```python
def test_publish_writes_generation_before_switching_pointer(self):
    result = publish_reviewed_cache(
        self.input_path,
        self.release_root,
        compiler=self.verifier.compiler,
        model_manifest=self.verifier.manifest,
    )
    self.assertEqual(self.events, [
        "database-fsync", "manifest-fsync", "staging-directory-fsync",
        "generation-rename",
        "generation-parent-fsync", "pointer-fsync", "pointer-replace",
        "release-root-fsync", "runtime-reopen",
    ])
    with ReviewedCacheRelease.open_current(
        self.release_root,
        compiler=self.verifier.compiler,
        model_manifest=self.verifier.manifest,
    ) as opened:
        self.assertEqual(opened.generation_id, result.generation_id)

def test_failure_before_pointer_replace_preserves_previous_generation(self):
    previous = self.publish_first_generation()
    self.inject_failure("pointer-replace")
    with self.assertRaises(CachePublicationError):
        self.publish_second_generation()
    self.assertEqual(self.read_current_generation_id(), previous.generation_id)
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv/bin/python -m unittest services.wayline_forge.tests.test_publish_reviewed_cache -v
```

Expected: import failure because `publish_reviewed_cache.py` does not exist.

- [ ] **Step 3: Acquire publication authority and build a private staging generation**

Require an existing owner-private release root, `generations` directory, and provisioned 0600 `.publish.lock`. Open the lock through the release-root directory descriptor without truncation, acquire nonblocking cross-process `fcntl.flock(LOCK_EX | LOCK_NB)`, and re-attest that its name still identifies the locked regular owner/single-link inode after locking and before pointer switch. Hold that FD across the entire build/promote/switch/reopen/cleanup transaction. Create one random 0700 staging directory under `generations`, call `build_reviewed_cache` for `reviewed_cache.sqlite3`, write its exact returned `manifest_json` to `reviewed_cache_manifest.json` with exclusive 0600 creation, chmod and fsync both files at 0400, chmod the staging directory 0500 while retaining its FD, then fsync that directory so both entries and the immutable mode transition are durable before promotion. Require one filesystem device across release root/generations/staging/files and reject sidecars/extras before promotion.

- [ ] **Step 4: Promote the immutable generation**

Name the final directory from the manifest digest. If absent, atomically rename staging to that name and fsync `generations`. If present, require byte-identical validated contents and discard staging; never mutate or replace an existing generation. Write a same-root temporary candidate pointer and validate the promoted generation through `ReviewedCacheRelease.open_pointer` before the current pointer can change.

- [ ] **Step 5: Atomically switch the pointer**

Write canonical pointer JSON to a same-directory exclusive 0600 temporary file, chmod 0400, fsync, atomically replace `current.json`, and fsync the release root. Reopen through `ReviewedCacheRelease.open_current` before returning. On any pre-replace failure, remove only owned staging/temp artifacts and leave the previous pointer unchanged. If release-root fsync fails after pointer replacement, report `pointer_durability_uncertain`; either the old or new pointer may survive a crash, and both name previously validated durable generations. If the post-switch runtime reopen fails after a durable switch, leave the new pointer installed and return `published_release_unavailable`; runtime remains fail-closed and rollback is a separate explicit recovery operation.

- [ ] **Step 6: Add adversarial publication tests**

Cover existing foreign generation, duplicate publication idempotency, immutable-generation permissions, pointer/manifest mismatch, same-size mutation, symlink/hardlink/path replacement, failure at every fsync/rename/replace seam, KeyboardInterrupt/SystemExit cleanup, and concurrent publisher exclusion. Assert no raw generation, learner identity, secrets, answers, or confidence enter either artifact.

- [ ] **Step 7: Verify GREEN**

Run Task 2 tests, Task 1 tests, and `test_build_reviewed_cache`. Expected: all pass.

---

### Task 3: Remove the direct-cache settings bypass

**Files:**
- Modify: `services/wayline_forge/app/settings.py`
- Modify: `services/wayline_forge/tests/test_settings.py`

**Interfaces:**
- Replaces `Settings.cache_path` with `Settings.reviewed_cache_release_root`.
- `Settings.from_environment()` preserves a lexically validated absolute `WAYLINE_RUNTIME_ROOT` without `Path.resolve()`; trusted descriptor walking remains the loader’s authority.

- [ ] **Step 1: Write the failing settings tests**

```python
def test_runtime_settings_expose_release_root_not_direct_cache_path(self):
    settings = Settings.for_tests(Path("/tmp/wayline-runtime"))
    self.assertEqual(
        settings.reviewed_cache_release_root,
        Path("/tmp/wayline-runtime/resources/reviewed_cache_release_v1"),
    )
    self.assertFalse(hasattr(settings, "cache_path"))

def test_environment_root_must_already_be_absolute_and_normalized(self):
    for root in ("relative", "/tmp/runtime/../runtime", "/tmp/runtime/"):
        with self.subTest(root=root), patch.dict(
            os.environ, {"WAYLINE_RUNTIME_ROOT": root}, clear=True
        ):
            with self.assertRaises(ValueError):
                Settings.from_environment()
```

- [ ] **Step 2: Run and verify RED**

```bash
.venv/bin/python -m unittest services.wayline_forge.tests.test_settings -v
```

Expected: missing `reviewed_cache_release_root` and current direct `cache_path` assertions fail.

- [ ] **Step 3: Implement lexical root validation and release-root settings**

Require a nonempty absolute string with no NUL and `os.path.normpath(raw) == raw`; do not expand, resolve, or follow filesystem links. Derive `resources/reviewed_cache_release_v1`. Remove the direct reviewed-cache SQLite setting so future composition has only the release-loader path.

- [ ] **Step 4: Verify GREEN and scan for bypasses**

Run settings tests, then:

```bash
rg -n "settings\.cache_path|reviewed_cache_v1\.sqlite" services/wayline_forge/app
```

Expected: tests pass and the scan returns no production match.

---

### Task 4: Full service regression and handoff

**Files:**
- Verify only; no additional production file is required.

**Interfaces:**
- Proves the publication layer and settings boundary preserve all existing provider, verifier, cache, quiz, and profile behavior.

- [ ] **Step 1: Run the full service suite under a hard deadline**

```bash
/usr/bin/perl -e 'alarm 90; exec @ARGV' \
  .venv/bin/python -m unittest discover -s services/wayline_forge/tests -q
```

Expected: zero failures and no alarm.

- [ ] **Step 2: Compile all runtime, script, and test modules**

```bash
.venv/bin/python -m compileall -q \
  services/wayline_forge/app \
  services/wayline_forge/scripts \
  services/wayline_forge/tests
```

Expected: exit 0 and no output.

- [ ] **Step 3: Independent adversarial review**

The reviewer verifies pointer atomicity, generation immutability, exact manifest/database binding, per-row receipt binding, crash boundaries, cleanup ownership, secret exclusion, and that no success claim extends to a real production cache or signed package.

- [ ] **Step 4: Execution checkpoint**

Report changed files, RED/GREEN evidence, full-suite count, compile result, and remaining owner gates. Do not stage or commit.

## Self-Review

- Spec coverage: this plan closes the runtime plan’s missing durable `reviewed_cache_manifest_v1.json`/cache binding and makes learner-mode cache selection fail-closed.
- Placeholder scan: no deferred implementation placeholder is used; real cache population and package signing remain explicit external release gates, not omitted code steps.
- Type consistency: Task 2 consumes the exact `ReviewedCacheRelease.open_current` API produced by Task 1 and the existing `CacheBuildResult` fields.
