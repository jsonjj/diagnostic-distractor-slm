# Wayline model export runbook

This produces a Mac-targeted `Q4_K_M` GGUF from the released Wayline adapter. It uses free Colab, creates no paid service, and never publishes or commits weights. The notebook fails closed: `model_manifest_v1.json does not exist unless every gate passes`.

## Before Colab

1. In Terminal, create the exact input bundle from the repository root:

   ```bash
   cd /Users/jonat/Projects/diagnostic-distractor-slm
   zip -X -FS wayline_export_inputs_v1.zip \
     data/game/questions_v1.jsonl \
     data/game/work/review_decisions_owner_v1.jsonl \
     data/game/work/reviewed_v1.jsonl \
     data/processed/eval_heldout.jsonl \
     data/wayline/runtime/reference_prompts_v1.jsonl \
     services/wayline_forge/app/curriculum.py \
     services/wayline_forge/app/distractor_verifier.py \
     services/wayline_forge/app/model_manifest.py \
     services/wayline_forge/app/procedure_registry.py \
     services/wayline_forge/app/providers/__init__.py \
     services/wayline_forge/app/providers/distractor.py \
     services/wayline_forge/app/question_kernel.py \
     services/wayline_forge/app/safe_numeric.py \
     services/wayline_forge/app/slm_prompt.py \
     services/wayline_forge/model_manifest.schema.json \
     services/wayline_forge/resources/curriculum_v1.json \
     services/wayline_forge/resources/procedure_registry_v1.json \
     services/wayline_forge/scripts/legacy_review_audit.py \
     src/__init__.py src/game_candidate_generation.py \
     src/game_colab_backend.py src/prompts.py
   ```

2. Obtain one verified, immutable 40-character llama.cpp commit SHA from the official `ggml-org/llama.cpp` repository: open the official GitHub commit page, choose the commit you intend to audit, use **Copy full SHA**, and keep that page URL with your run notes. The notebook deliberately has no default. Do not paste a branch, tag, `main`, or abbreviated SHA.

3. Make roughly 13 GB available in the free Google Drive quota and keep at least 20 GB of Colab local disk free. No storage purchase is required if that space is available: Hub snapshots, llama.cpp, the temporary F16 GGUF, and package staging stay on Colab’s ephemeral disk; Drive holds only resumable checkpoints and final downloads. A Hugging Face account is optional while both repositories remain public. If access requires a token, create a read-only token; it will be entered through a masked prompt and is never stored in a cell or output.

## Run in free Colab

4. Upload `notebooks/export_wayline_gguf_colab.ipynb` to Google Colab. Choose **Runtime → Change runtime type → T4 GPU**. A T4 or better is required; free Colab availability and session limits are outside the project’s control.

5. Run the install and configuration cells. Leave Google Drive enabled for resumability. Paste the 40-character llama.cpp commit SHA at the ordinary nonsecret prompt. Enter the Hugging Face token only at the masked prompt, or press Enter for public access.

6. When prompted, upload exactly `wayline_export_inputs_v1.zip`. The notebook rejects extra, missing, duplicated, path-traversing, symlinked, encrypted, oversized, or hash-mismatched members before importing any uploaded code.

7. Continue in order. The notebook verifies the adapter at `dd30dcea2755b7a2659faa908714e31335349408`, checks its recorded base ID, then downloads the base only at `cad0bedfdd862093a12af478cb974ab2addd0e0a`. It runs the original adapter before merging, performs no training, builds the exact llama.cpp commit, converts to F16 GGUF, and quantizes exactly `Q4_K_M`.

8. Let parity finish. It runs the same deterministic contract through the original adapter and GGUF for 60 Wayline reference prompts plus six owner-approved legacy encounters. Every approved answer→misconception mapping must remain unchanged. Exactly-three, distinct-answer, key-safe, and current product-verifier rates may not regress by more than five percentage points.

9. Download `wayline_live_forge_q4_k_m.zip`, `model_manifest_v1.json`, `wayline_parity_report_v1.json`, and `package_receipt_v1.json`. Keep all four outside Git. Verify the ZIP’s SHA-256 against the package receipt and verify the internal `SHA256SUMS` before moving anything into a runtime bundle. If the browser cannot transfer the multi-gigabyte ZIP, download it from the notebook’s Google Drive output folder. Do not commit, upload, publish, or redistribute the GGUF without a separate license review and owner approval.

## Resume after a disconnect

10. Reopen the notebook with the same Google Drive mounted and the same immutable revisions. Run from the first cell again. Enter the same llama.cpp SHA. The notebook re-hashes every completed input and artifact and resumes missing original/GGUF completions by case ID. A conflicting configuration, stale checkpoint, corrupt output, or unreceipted artifact stops the run instead of overwriting it.

11. If free Colab deletes the VM, the merged model, Q4 GGUF, inference checkpoints, and receipts remain in the Drive work root; Hub snapshots and llama.cpp are rebuilt locally, while a receipted Q4 does not require rebuilding the ephemeral F16 GGUF. If Drive quota or Colab runtime limits interrupt conversion, free space and resume; do not delete receipts while retaining their artifacts. If you intentionally change any pin, use a new Drive folder.

## Expected output

The ZIP contains the hashed GGUF, runtime `model_manifest_v1.json`, detailed export and parity receipts, source license/card files, `THIRD_PARTY_NOTICES.md`, and `SHA256SUMS`. The F16 GGUF and merged model remain checkpoint artifacts and are not packaged. A failed run leaves a `wayline_parity_report_FAILED_v1.json` for diagnosis but no production manifest.
