# Wayline Production Package and Runtime Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use strict test-driven development for every production behavior. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Wayline Forge package eligible for live composition only when it contains a bundled digest-matched GGUF, a strict descriptor-binding receipt tied to the exact model/server/runtime adapter, a valid reviewed-cache release, and the concrete packaged runtime entrypoint.

**Architecture:** The package auditor remains the first authority and binds every immutable artifact. A small production spawn adapter satisfies the existing exact-child callback contract. A new production composition root validates the frozen package before opening learner stores or constructing the managed worker, while the generic launcher keeps its `runtime_factory=None` fail-closed behavior.

**Tech Stack:** Python 3.12, unittest, PyInstaller onedir, SQLite, llama.cpp loopback worker, existing Wayline verifier/cache/runtime contracts.

## Global Constraints

- Edit only `services/wayline_forge/**`; do not touch Unity or Git.
- Do not create model, cache, parity, review, or descriptor receipts for absent production artifacts.
- Do not use credentials, paid services, network downloads, or model downloads.
- Raw/unverified model output must never become learner-facing.
- The final result must not claim live readiness without real external artifacts and live validation.

---

### Task 1: Bind the descriptor receipt and bundled GGUF into the package

**Files:**
- Modify: `services/wayline_forge/app/macos_worker_runtime.py`
- Modify: `services/wayline_forge/scripts/build_mac_sidecar.py`
- Modify: `services/wayline_forge/tests/test_packaged_layout.py`
- Create: `services/wayline_forge/tests/test_descriptor_binding_receipt.py`

**Interfaces:**
- Produces: `parse_descriptor_binding_release_receipt(payload) -> DescriptorBindingReleaseReceipt` and canonical `DescriptorBindingReleaseReceipt.to_json()`.
- Produces: package input `descriptor_binding_receipt: Path`; `gguf: Path` is required rather than optional.

- [x] Write tests proving duplicate/unknown/noncanonical receipt JSON fails, then run them and observe import/behavior failures.
- [x] Implement strict canonical JSON parsing for exactly `schemaVersion`, `binarySha256`, `modelSha256`, `llamaCppRevision`, `osName`, `architecture`, `readinessProtocolRevision`, and `spawnAdapterSha256`.
- [x] Run receipt tests and require all to pass.
- [x] Add package tests proving a missing receipt, missing GGUF, mismatched server/model/revision, or wrong platform/adapter digest fails closed.
- [x] Run package tests and observe failures because the current auditor permits those states.
- [x] Require `resources/descriptor_binding_release_receipt_v1.json`, require `models/<manifest ggufFileName>`, bind receipt hashes to package entries and the model manifest, and make both assembler CLI inputs mandatory.
- [x] Run receipt/package tests and require all to pass.

### Task 2: Supply the concrete exact-child production spawn adapter

**Files:**
- Create: `services/wayline_forge/app/production_spawn.py`
- Create: `services/wayline_forge/tests/test_production_spawn.py`

**Interfaces:**
- Produces: `ProductionPopenFactory`, callable with the existing `wayline_child_created` callback contract.
- Produces: `PRODUCTION_SPAWN_ADAPTER_SHA256`, a domain-separated identity digest used by the descriptor receipt and package audit.

- [x] Write tests requiring the adapter to advertise the callback contract, publish the exact child immediately, and terminate/kill/reap a child if ownership publication raises.
- [x] Run tests and observe the missing-module failure.
- [x] Implement the minimal wrapper over `subprocess.Popen`, including bounded exact-child cleanup and immutable adapter identity.
- [x] Run focused adapter and macOS worker tests and require all to pass.

### Task 3: Inject a validated production factory only in the bundled entrypoint

**Files:**
- Create: `services/wayline_forge/app/production_runtime.py`
- Create: `services/wayline_forge/app/packaged_launcher.py`
- Modify: `services/wayline_forge/WaylineForge.spec`
- Modify: `services/wayline_forge/scripts/build_mac_sidecar.py`
- Create: `services/wayline_forge/tests/test_production_runtime.py`
- Modify: `services/wayline_forge/tests/test_packaged_layout.py`
- Modify: `services/wayline_forge/docs/runtime_release_boundary.md`

**Interfaces:**
- Produces: `build_production_runtime(settings, *, package_root) -> RuntimeBundle`.
- Produces: `packaged_launcher.main(argv)` that passes only the concrete production factory to `launcher.main`.
- Consumes: validated package manifest, model manifest, descriptor receipt, reviewed-cache generation, curriculum, registry, writable private runtime root, and the concrete production spawn adapter.

- [x] Write tests proving package validation happens before profile-store mutation and that an invalid/missing cache, receipt, model, or package manifest returns one stable composition failure.
- [x] Write a successful construction test using only locally generated test fixtures whose model/cache/receipt hashes genuinely agree; do not launch a worker.
- [x] Write a packaged-entrypoint test proving it injects the concrete factory while direct `launcher.main()` remains `live_runtime_unavailable`.
- [x] Run focused tests and observe missing production modules/factory failures.
- [x] Implement validated resource loading, shared compiler/verifier/manifest construction, reviewed-cache open, managed-worker/provider/orchestrator/application assembly, current-session resolution, and reverse-order cleanup.
- [x] Change the PyInstaller analysis entrypoint to `packaged_launcher.py`; copy the four digest-pinned authored resources into the frozen module-relative resource location as well as the audited package resource root.
- [x] Run focused tests and require all to pass.

### Task 4: Verification and honest release record

**Files:**
- Modify: `services/wayline_forge/docs/runtime_release_boundary.md`

- [x] Run the focused receipt, package, spawn, launcher, worker, provider, reviewed-cache, and production-runtime tests.
- [x] Run the full Forge suite with `.venv-live/bin/python -m unittest discover -s services/wayline_forge/tests -v` and require nonzero discovery and zero failures.
- [x] Run `python -m compileall` on Wayline Forge, `git diff --check`, and secret/research/source scans without staging or committing.
- [x] Record that real GGUF, model/parity receipts, Mac llama-server, reviewed cache, artifact-specific descriptor receipt, live soak, final package, and clean-Mac smoke remain external gates.

## Self-review

- Spec coverage: bundled GGUF, descriptor receipt, package binding, concrete spawn adapter, factory injection, cache validation, lifecycle cleanup, tests, and honest external gates are represented.
- Placeholder scan: no deferred implementation markers are present.
- Type consistency: the package path and receipt names are shared across Tasks 1–3; the generic launcher remains unchanged and fail closed.
