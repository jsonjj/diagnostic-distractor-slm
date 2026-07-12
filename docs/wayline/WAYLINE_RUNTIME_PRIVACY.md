# Wayline Runtime Privacy Record

**Status:** implementation record for the Mac-first vertical slice, 2026-07-12

**Scope:** local Wayline Forge runtime, local learner profiles, reviewed content, and the Unity vertical-slice client

**Not a public privacy notice:** this record makes no claim of compliance with any child-privacy, education, or consumer-privacy law. A public release requires a separate legal and product review.

## Privacy boundary

Wayline v1 is designed around local, pseudonymous profiles. A profile UUID is not a real-world account, but it is still a stable identifier and is not anonymous while it remains linked to a learner's history on the Mac. There is no cloud account, analytics service, social feature, advertising SDK, or telemetry pipeline in the implemented runtime.

Deterministic code owns questions, answer keys, scoring, evidence, adaptation, and progression. The Qwen distractor SLM is local and receives only a trusted question, trusted correct answer, topic, and generation receipt data. It does not receive a profile or session ID, a learner's selection, confidence, correctness, evidence state, or name. Raw model text is handled transiently, hashed, verified, and discarded; accepted canonical question material and provenance receipts are stored instead.

“Sealed” means hidden behind the service and quiz-state boundary until reveal. It does **not** mean encrypted at rest. A person or process able to read the local SQLite file can read the stored private quiz material.

## Data flow

| Component | Data handled | Boundary |
| --- | --- | --- |
| Unity client | Public question text, opaque item/option IDs, selections, confidence, wrong count, worked example, and final reveal | Typed loopback client/controllers are implemented and tested; packaged live-process smoke remains gated |
| Wayline Forge | Profile/session IDs, quiz truth, submissions, learning events, evidence, gates, and receipts | Local process and local SQLite |
| Local Qwen/`llama.cpp` | Trusted question, correct answer, topic, deterministic prompt, and generated distractor proposal | HTTP loopback only; production GGUF and binary are pending |
| Reviewed cache | Verified question truth, canonical distractors/feedback, provenance, and review receipt | Read-only learner resource; schema has no learner/profile/session field and stores no raw model response |
| Sonnet/TrueFoundry | Nothing in the current learner runtime | Disabled; authored templates are the production default |

The implemented loopback policy permits only `127.0.0.1`, uses a fresh 256-bit bearer token per launch, requires the exact configured Unity origin whenever an `Origin` header is present (native Unity requests may omit that header), requires the current session ID where applicable, caps request bodies at 64 KiB, and disables documentation and access logging. The FastAPI adapter and macOS private-descriptor Unity launch handoff are implemented and tested, including cancellable startup and exact-child reaping. A Python 3.12 hash lock, arm64 PyInstaller onedir build, validated production runtime factory, and fail-closed package assembler/auditor now exist, but that onedir output is not a release package. A packaged live-process smoke still requires an artifact-specific descriptor-binding receipt, model manifest, GGUF, pinned `llama-server`, reviewed-cache release, and a fully assembled package manifest.

## Local data inventory

### Identity and sessions

The implemented profile contract currently stores no name, email, age, school, parent identity, IP address, or device identifier. Although the product specification allows an optional local display name later, the current profile contract contains only a request ID and the stored profile contains only:

- `schema_version`, `profile_id`, and `created_at`.

Each local session stores:

- `schema_version`, `session_id`, `profile_id`, `client_build`, `opened_at`, `closed_at`, `active_world_id`, `campaign_catalog_sha256`, `event_ordinal_at_opening`, and `event_hash_at_opening`.

Idempotency and replay support also store command request IDs, action, request-payload hash, profile ID, optional session ID, canonical response, and response hash. The immutable session-opening log stores profile/session IDs, opening ordinal/time, active world, event ordinal/hash at opening, previous opening hash, and opening hash.

### Learning events

Every event stores the common fields `schema_version`, `event_id`, `idempotency_id`, `ordinal`, `profile_id`, `session_id`, `world_id`, `battle_id`, `occurred_at`, and `event_type`.

Every revealed quiz item stores one observation with:

- `batch_id`, `item_id`, `question_id`, `template_id`, `content_version_id`, `skill_id`, legacy `world_core_subskill_ids`, `operand_signature`, and `context_id`;
- `first_option_id`, `final_option_id`, `first_confidence`, `final_confidence`, `first_correct`, `final_correct`, `choice_changed`, and `self_corrected`;
- `first_procedure_id`, `final_procedure_id`, `targeted_procedure_ids`, `is_transfer`, `is_changed_context_transfer`, and `valid_for_progression`;
- `batch_wrong_count`, `canonical_feedback`, and `optional_wording_shown`; and
- `receipts.generator`, `receipts.model`, `receipts.adapter`, `receipts.gguf`, `receipts.verifier`, `receipts.registry`, and `receipts.cache`.

The only stored confidence values are `certain`, `leaning`, and `guessing`.

Campaign events additionally store `core_subskill_ids` and `curriculum_receipt`; `won` and `is_lead_in`; `combat_won`, `final_correct`, `item_count`, and `is_campaign_finale`; or `attempt_number`, `passed`, `final_correct`, and `item_count`.

A fresh assisted-route completion stores its route/material receipt, one worked-example
item ID, two supported item/question IDs, the two selected and correct options/answers,
confidence, correctness, compatible procedure hypotheses, possible-error wording,
trusted methods/steps, canonical feedback, and provenance. Those two answers enter local
answer history only; reducers exclude them from unassisted procedure, skill, gate, secure,
and mastery evidence.

The rebuildable learner projection duplicates the canonical events and derives:

- answer records with event/world/battle/batch/item/question IDs, first/final option IDs, confidence, correctness, and explanations shown;
- procedure status, associated world/skill, distinct question/template counts, priority, ambiguity, targeted-transfer count, evidence event IDs, and last ordinal;
- skill status, exposures, first-pass-correct count, self-correction count, changed-context-transfer count, and last ordinal;
- world core skills, valid-item count, lead-in wins, curriculum receipt, activation/last ordinals; and
- active world and ambiguous procedure pairs.

A selected distractor is stored as evidence for a hypothesis, never as proof of what a child thought.

### Private quiz state

The SQLite quiz store retains resumable state, not just the final learning event. It stores:

- batch owner, state, version, item IDs and opaque option layouts;
- complete initial and revision submissions: request ID, batch/item counts, selected item/option IDs, and confidence;
- the initial wrong-count result, final per-item correctness and first/final selections, correct option and answer, trusted steps, possible-error wording, reliable method, and self-correction flag;
- transition/preparation receipts, request/payload/output hashes, and the durable observation outbox;
- batch context: profile, session, world, battle, core-subskill IDs, content version, and battle tier; and
- private verified material: compile request and seed, prompt, operands, exact answer, trusted steps, allowed procedures, holdout-exclusion receipt, four options, sealed option-to-procedure bindings, canonical labels/computations/feedback/methods, source/reviewer proofs, and model/verifier/registry/cache hashes.

Raw Qwen response text is deliberately absent from the verified bundle, reviewed-cache schema, and quiz database. Its SHA-256 and provenance receipts remain.

Fresh assisted material is stored in separate hash-verified tables with profile/route
foreign-key ownership, exact preparation receipts, and cascade deletion. Before
completion, its public projection contains one worked solution and two supported MCQs
without supported keys or diagnoses. Generic quiz endpoints cannot address assisted
route IDs.

## Files and database behavior

`Settings` currently resolves learner storage to these packaged-runtime-relative paths:

- `profiles/wayline_profiles_v1.sqlite` for profiles, sessions, quiz state, events, and projections;
- `resources/reviewed_cache_release_v1/current.json`, which selects one immutable generation containing `reviewed_cache.sqlite3` and `reviewed_cache_manifest.json`, for approved fallback questions; and
- `resources/model_manifest_v1.json` for the pinned local-model receipt.

The launcher accepts and validates an absolute runtime root, and Unity passes it through a private macOS process handoff. The final Application Support location and clean packaged install are not yet release-verified.

SQLite uses foreign keys, `secure_delete=ON`, and WAL journaling. The main database may therefore have `-wal` and `-shm` sidecars. Before a schema migration, the store requests a full WAL checkpoint and creates a whole-database sibling backup named `wayline_profiles_v1.sqlite.backup-v<old-version>`. After a committed migration it removes matching migration artifacts on a best-effort basis and retries stale artifacts on the next store open; a failed migration intentionally retains its recovery backup.

Profile deletion uses a stricter WAL sequence:

1. Before beginning the delete transaction, the store requires `wal_checkpoint(TRUNCATE)` to complete. If an existing reader keeps the WAL busy, deletion fails before row mutation; the deletion service reports the redacted, retryable `storage_busy` code. This checkpoint moves committed frames into the main database and truncates the prior WAL; it is an ordering guard, not erasure on its own.
2. The store begins one `IMMEDIATE` transaction, revalidates the expected current open session inside that transaction, unlinks every matching `wayline_profiles_v1.sqlite.backup-v*` artifact, deletes the profile-owned rows, and commits. A SQL failure rolls the row changes back. An unlink failure occurs before row deletion and also leaves the rows intact.
3. After commit, the store attempts `VACUUM` and, even if that raises, attempts a non-required final truncating WAL checkpoint. Post-commit maintenance errors are deliberately suppressed because the logical deletion is already durable.

A migration backup contains every profile in that database. It cannot preserve another profile while selectively removing one profile's old pages, so deleting any profile removes all matching migration backups and sacrifices migration recovery coverage for every other profile in the same database. Filesystem unlink is not part of the SQLite transaction: a crash or later transactional failure after one or more successful unlinks can leave the profile rows present while those recovery artifacts are already gone.

## Export and deletion

The implemented `ProfileStore.export_profile(profile_id)` creates a strict `wayline.profile-export.v1` object after revalidating identity, campaign history, timestamps, canonical events, per-event hashes, and the terminal event chain. It contains:

- profile ID and creation time;
- campaign catalog hash, active world, and campaign ordinal;
- session IDs, client build, and open/close times; and
- every canonical learning event, its ordinal and SHA-256, plus the terminal chain hash.

The portable export does not include an unrevealed/resumable quiz snapshot, private batch material, identity command receipts, the derived projection, or the reviewed cache. The export service and authenticated FastAPI route are implemented; a Unity export control, file chooser, and user-facing destination are pending. Any copy the user saves outside the runtime is outside profile deletion and must be managed separately.

The implemented deletion service requires the exact current open session for the profile. It checks that session before invoking the store, and the store repeats the current-session authorization under the same `IMMEDIATE` transaction that performs the row deletion. That transaction removes the profile's projection, canonical events, pending observation outbox, quiz transition and preparation receipts, private normal and assisted material, quiz machines, identity receipts, session-opening records, sessions, and profile row while leaving other profiles' rows in place. It verifies the assisted-table cascade before reporting success.

On the clean success path, tests verify that the deleted marker is absent from the main database and WAL sidecar and that migration backups are gone. This is not a forensic-erasure or crash-erasure guarantee. A crash before the row commit leaves SQLite to recover the uncommitted profile data, while already-unlinked migration backups may remain lost. A crash after commit but before `VACUUM` and the final checkpoint leaves the profile logically deleted, but old bytes may remain in the main database or sidecars. The same residual-byte risk applies when best-effort post-commit maintenance fails. Deletion does not remove shared reviewed content/model resources, user-created exports, Time Machine or filesystem snapshots, crash artifacts, or copies made outside the app.

The export/deletion services and authenticated public API routes exist, but the Unity export/delete UI does not.

## Sonnet, credentials, and internet use

Authored, linted story templates are the current default. No TrueFoundry narrative provider is implemented, so Sonnet is disabled and receives no learner data.

If an optional provider is added later, its allowed outbound request is limited to enumerated style, setting, reading-level, and story-frame IDs plus the literal placeholders `{A}`, `{B}`, and `{UNIT}`. It must not contain a name, profile/session ID, question operands, answer, selection, confidence, correctness, evidence, procedure result, IP address, raw Qwen response, or credential. Deterministic code inserts trusted numbers only after the returned text passes the story linter. The existing research TrueFoundry configuration may log provider requests, so public learner use remains prohibited until logging, retention, cost, authentication, and child-privacy terms are approved.

`TFY_BASE_URL`, `TFY_MODEL`, and `TFY_API_KEY` may be read from the sidecar process environment; the key is excluded from object representations. Credentials must not be placed in Unity, the app bundle, source control, model receipts, or logs. The local Qwen worker needs no Hugging Face or TrueFoundry credential at inference time. The package assembler now rejects credential-like names/content, research data, Python source, symlinks, unexpected files, and digest or permission mismatches. The same audit has not yet run against a final assembled package because the required release artifacts are absent.

## Logs and retention

The current Python runtime has no learner analytics or general application-log pipeline. Public error boundaries return stable redacted codes, and security rejections contain no attacker-controlled text. Uvicorn access logging is disabled, and Unity request/response/process representations redact bodies, sessions, and launch tokens. Normal release logs must not contain prompts, raw Qwen output, correct answers, selections, confidence, profile/session IDs, launch tokens, or provider credentials. The production `llama-server` output, crash reports, and final packaged log paths remain unaudited because the pinned release artifacts do not yet exist.

There is no implemented time-based retention schedule. A local profile, revealed events, resumable quiz state, and derived evidence persist until profile deletion or app data removal. Shared reviewed content persists with the installed runtime and contains no learner records. A public retention period and behavior for abandoned profiles, exports, OS backups, crash artifacts, and upgrades require owner and child-privacy review before release.

## Integrity and threat model

Canonical JSON, SHA-256 row receipts, event chains, session-opening chains, model/content receipts, strict schemas, and replay checks detect many accidental corruptions, stale/partial writes, and partial authority rewrites. Loopback binding and a per-launch bearer token reduce exposure to unrelated network clients.

These hashes are unkeyed. An attacker who can rewrite all relevant SQLite authority rows can forge an internally consistent learner history by changing the payloads and recomputing the row, receipt, event-chain, session-opening-chain, and chain-head hashes. Preventing that class of forgery would require an integrity secret outside the database, such as a macOS Keychain-backed key used for an HMAC root or equivalent signature design; none is implemented. The hashes also do not protect against a compromised local process or administrator, filesystem copies made before deletion, or an attacker who captures the live launch token. No database encryption is implemented, and the use of constant-time launch-token comparison is not an HMAC storage design.

This is currently an integrity-and-recovery design for a local prototype, not a tamper-proof learner record system.

## Child privacy and public-release prerequisites

The intended audience is ages 10–13, so a technically local design is not sufficient by itself for public distribution. Before real learners use a public build, the release owner must complete and approve:

- a child-privacy/data-flow review and user-facing privacy notice, including whether parental notice or consent is required;
- a purpose-specific retention and deletion policy, export UX, incident-response path, and clean-account deletion test;
- a decision on local display names, OS backups, crash reporting, diagnostics, encryption at rest, and whether a Keychain/HMAC integrity root is necessary;
- a provider review proving that learner-mode Sonnet is either absent or uses approved logging/retention terms through an authenticated, funded proxy;
- a no-silent-upload rule and separate architecture/migration plan before any cloud account or sync feature; and
- target-age testing of privacy language and the distinction between a possible error pattern and a diagnosis.

## Release checklist

- [x] Pseudonymous local profile/session identity and strict creation contracts are implemented.
- [x] Canonical learning events, deterministic projections, profile export, and current-session deletion services are implemented.
- [x] Reviewed-cache schemas exclude learner identity and raw Qwen responses.
- [x] Authored narrative is the default; the outbound Sonnet request shape excludes learner data.
- [x] Loopback security and redacted error primitives are implemented.
- [x] FastAPI endpoints, launcher, disabled access logging, and authenticated Unity transport contracts are implemented and test-verified.
- [x] The Python 3.12 hash lock, PyInstaller specification, local arm64 onedir sidecar build, immutable package-manifest/audit code, and package secret/research/source exclusion tests are implemented.
- [x] Reviewed-cache build, immutable-generation publication, pointer validation, and learner-mode reopen code are implemented and test-verified.
- [ ] Production GGUF, pinned model manifest, and parity acceptance are pending.
- [ ] A pinned `llama-server` Apple-Silicon binary, license record, artifact-specific descriptor-binding release receipt, and packaged-log audit are pending; the process driver and validated production runtime factory are implemented.
- [ ] The production reviewed-cache generation and pointer are pending. The current dry-run migration accepts zero records because the Wayline bundle index and production model manifest are absent, and the six legacy prompts are not directly representable by the current compiler.
- [x] A 1,000-item recorded-provider verifier/fallback soak displayed zero unverified items.
- [ ] A live-GGUF 1,000-item generation/privacy soak is pending.
- [x] Unity campaign/presentation save, backup recovery, export-to-file, and delete primitives are implemented.
- [ ] Unity profile API, learner-facing export/delete controls, and clean-Mac validation are pending.
- [ ] Final Application Support paths, package contents, credentials scan, and delete-after-upgrade behavior are pending.
- [ ] Public retention, provider logging, child-privacy, and privacy-notice review are pending.

## Implementation references

- `docs/wayline/WAYLINE_LEARNING_AND_RUNTIME_SPEC.md`
- `docs/superpowers/plans/2026-07-11-wayline-learning-runtime.md`
- `docs/superpowers/plans/2026-07-11-wayline-unity-vertical-slice.md`
- `services/wayline_forge/app/events.py`
- `services/wayline_forge/app/evidence_reducer.py`
- `services/wayline_forge/app/profile_store.py`
- `services/wayline_forge/app/profile_deletion.py`
- `services/wayline_forge/app/quiz_store.py`
- `services/wayline_forge/app/assisted_route_store.py`
- `services/wayline_forge/app/assisted_route_machine.py`
- `services/wayline_forge/app/api.py`
- `services/wayline_forge/app/launcher.py`
- `services/wayline_forge/app/verified_question.py`
- `services/wayline_forge/app/loopback_security.py`
- `services/wayline_forge/app/providers/llama_cpp.py`
- `services/wayline_forge/app/providers/narrative.py`
- `services/wayline_forge/app/providers/template_narrative.py`
- `services/wayline_forge/app/settings.py`
- `services/wayline_forge/requirements-live.lock`
- `services/wayline_forge/WaylineForge.spec`
- `services/wayline_forge/scripts/build_mac_sidecar.py`
- `services/wayline_forge/scripts/build_reviewed_cache.py`
- `services/wayline_forge/scripts/publish_reviewed_cache.py`
