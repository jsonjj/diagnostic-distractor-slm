# Wayline Forge runtime release boundary

This record describes implemented package/runtime behavior. It is not evidence that a live production package exists.

## Implemented fail-closed seam

- `build_mac_sidecar.py` requires an Apple-Silicon `llama-server`, production model manifest, exact bundled GGUF, immutable reviewed-cache release, and canonical `descriptor_binding_release_receipt_v1.json`.
- The package audit recomputes every file digest and requires the descriptor receipt to bind the packaged server digest, GGUF digest, llama.cpp revision, Darwin/arm64 readiness protocol, and the reviewed production spawn-adapter identity.
- PyInstaller staging accepts only the executable plus `_internal` roots. The package scanner accepts one size-bounded, unprefixed, untrailed, comment-free `base_library.zip` with a bounded canonical central directory and stored safe `.pyc` members. It rejects Windows/drive/traversal names, compressed secrets, source/research members, nested or prefixed archives, gzip/xz/bzip payloads, encrypted members, and every other opaque archive output.
- Reviewed-cache staging copies only the canonical pointer and its exact current generation. Package audit rejects stale generations, publisher locks, and every other cache extra.
- An omitted GGUF is no longer a valid production-package mode. `optionalModel.bundled` remains in the v1 manifest encoding for compatibility, but the only accepted value is `true`.
- The generic `launcher.py` still fails with `live_runtime_unavailable` when no factory is injected.
- The PyInstaller entrypoint is `packaged_launcher.py`. It derives the immutable package root from `sys.executable`, keeps `--runtime-root` as separate writable learner state, and injects only `build_production_runtime`.
- Production composition validates the complete package, exact `0500` package-directory permissions, single-read authored resource digests/parsing, manifest-bound model/receipt bytes, and every reviewed-cache row before it creates profile or worker state. It then constructs the managed worker, strict stdlib transport, verifier, orchestrator, application facade, and current-session resolver without starting llama.cpp.
- The driver must advertise the reviewed descriptor-binding contract before worker construction. Worker HOME and TMPDIR are private runtime-owned directories; ambient Metal, HOME, and temporary-directory paths are not inherited.
- Runtime shutdown shields asynchronous cleanup to completion, preserves exact cancellation and process-control exceptions across the launcher, gives the worker and exact driver independent deadlines, lets later driver control-flow override an ordinary worker failure, and retains a separately registered driver backstop before closing assisted, quiz, profile, and reviewed-cache resources.
- The current PyInstaller spec produces an arm64 onedir executable with the four module-relative authored JSON resources. Its fail-closed packaged smoke reaches the production entrypoint, but this is build validation rather than a final sidecar.

`DescriptorBindingReleaseReceipt` is an explicit owner attestation bound by package hashes. It is not a digital signature. `PRODUCTION_SPAWN_ADAPTER_SHA256` identifies the reviewed callback/cleanup policy; it is not a hash of Python source bytes.

## Still absent and therefore blocked

No checked-in or generated test fixture is production authority. Live mode remains blocked until all of these external artifacts and validations exist:

- the parity-accepted `Q4_K_M` GGUF and production `model_manifest_v1.json`;
- the matching Apple-Silicon llama.cpp binary built from the manifest's exact revision, plus license/notices;
- proof that the server is self-contained, or an audited set of every required Mach-O dylib and Metal companion resource;
- a production reviewed-cache generation with coverage for every launch planner fallback slot;
- an artifact-specific descriptor-binding owner receipt containing the real server/model digests;
- a final sidecar assembled from those exact inputs;
- macOS fault injection proving executable-launch failure cannot orphan a child;
- cold-start hashing within the Unity launch deadline, a live-GGUF 1,000-item privacy/fallback soak, packaged Unity smoke, and clean-Mac validation.

The synthetic package in automated tests proves composition and cleanup contracts only. It never launches a model and must not be distributed or described as live inference.
