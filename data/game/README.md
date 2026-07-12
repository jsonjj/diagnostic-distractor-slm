# Glitch Rally content workspace

`questions_v1.jsonl` contains 60 original sixth-grade Number questions. The
generation loader checks every record against the exact frozen 140-row holdout before
the model is called.

All non-release artifacts belong under `data/game/work/`, which is gitignored:

- raw Colab candidates;
- automatic validation reports;
- owner review queues and decisions;
- reviewed records and rejection notes.

Only the final sanitized pack may cross into `game/content/packs/`. It contains the
approved question, SLM distractor answers, computations, misconception labels, repair
copy, and non-personal reproducibility metadata. It excludes raw responses, reviewer
identity, and review notes.

The SHA-256 chain detects stale or accidental mutation in a trusted local workspace.
It is not a digital signature and does not authenticate an adversarial person who can
rewrite both content and checksums.
