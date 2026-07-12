# Wayline Live SLM — Go-Live Runbook (final step)

The three-world demo is fully playable today in the deterministic acceptance mode
(`DETERMINISTIC LOCAL ACCEPTANCE DATA — NOT LIVE SLM`). Turning on live Qwen
generation is the one remaining step and it is gated on a single owner-provided
artifact: the merged, quantized model produced by the Colab export.

Everything the live path needs on this machine is prepared:

- Apple-Silicon `llama-server` built from the exact pinned commit
  `6b4dc2116a92c5c8f2782bfe51fabe5ee66fb5ef`
  at `.wayline-build/llama.cpp/build/bin/llama-server`
  (sha recorded in `.wayline-build/llama_server_sha256.txt`).
- Forge runtime, verifier, orchestrator, reviewed-cache fallback, and packaging
  are implemented and green (`services/wayline_forge`, 1,179 tests).
- Export inputs bundle already built and receipt-bound at
  `data/wayline/runtime/wayline_export_inputs_v1.bundle/`.

## What is still required from the owner

Run `notebooks/export_wayline_gguf_colab.ipynb` on a Colab GPU (T4/L4):

1. Runtime → Change runtime type → T4 GPU.
2. Run all cells.
3. When prompted, paste the llama.cpp pin
   `6b4dc2116a92c5c8f2782bfe51fabe5ee66fb5ef`, enter the Hugging Face token,
   and upload `wayline_export_inputs_v1.zip`.
4. Let merge → Q4_K_M conversion → 60+6 parity finish.
5. Download `wayline_live_forge_q4_k_m.zip` and provide its local path.

That ZIP contains the merged `Q4_K_M` GGUF, `model_manifest_v1.json`, and the
parity report — the artifacts the runtime binds.

## Why live cannot be fabricated before then

The reviewed-cache release and descriptor-binding receipt are cryptographically
bound to the exported GGUF's SHA-256 (`PinnedSlmManifest.from_model_manifest`),
so the production runtime is fail-closed until the real model hash exists. This
is deliberate: no unverified model output can reach a learner. A genuine live run
therefore requires the real GGUF; there is no honest shortcut.

## Bringing it online once the GGUF is provided

1. Unzip the export next to the runtime and confirm the manifest hash matches the
   GGUF (`shasum -a 256`).
2. Start the local worker with the pinned server + GGUF on IPv4 loopback.
3. Compose the Forge runtime against that worker (the verifier and reviewed-cache
   fallback stay in the path so every distractor is checked) and point the Unity
   `WaylineForgeClient` at the loopback port; the runtime-derived banner then
   reads live instead of the deterministic label.
4. Validate: at least one battle's distractors are generated live and pass
   `DistractorVerifier`, with a same-skill fallback on any miss.

Until step 1's artifact exists, the shipped internal build stays in the honest
deterministic mode and never claims live inference.
