# Mathbreakers: Glitch Rally

## Product promise

**Outrace the wrong answers.** Mathbreakers: Glitch Rally is a single-player, browser-based math adventure for sixth graders. Players drive a papercraft rally car through a world whose roads are equations. At each checkpoint, one trusted route and three convincing counterfeits compete for the road. The player identifies the mathematical truth, exposes the trick behind a counterfeit, and repairs it with a strategy rather than memorizing that an answer was wrong.

The game is a tactical puzzle rally, not a driving simulator or a decorated quiz. Short automatic driving beats create momentum between turn-based math decisions. The math determines the route, rival, attack, and comeback.

## Audience and experience goals

- **Primary audience:** sixth-grade learners, approximately ages 11–12, across a range of math confidence.
- **Session length:** 5–8 minutes for one complete run; 60–90 seconds for a normal encounter.
- **Platform:** responsive web, designed first for school laptops and tablets and usable with touch, mouse, or keyboard.
- **Learning goal:** recognize common mathematical misconceptions, choose an appropriate checking strategy, and explain why a tempting answer fails.
- **Emotional arc:** curiosity → suspicion → discovery → repair → mastery.
- **Tone:** energetic, clever, and lightly mischievous. The game challenges an idea, never the learner.

## Player fantasy

The player is a **Mathbreaker**, a rally mechanic who restores roads corrupted by the Glitch Forge. Their toy-like car carries mathematical gadgets instead of conventional weapons. Rival Glitches are cardboard robot vehicles built from faulty reasoning: a denominator becomes a jaw, an operation sign becomes a rotating hood ornament, and an incorrect computation trails behind a rival like exhaust.

The player wins by reading a rival's trick and countering it. A previously confusing wrong answer eventually becomes a recognizable enemy tell. That change—from being fooled by a Glitch to predicting it—is the core power fantasy.

## Design pillars

1. **Math is the action.** A correct decision creates speed, opens a road, or fires a repair; it does not merely unlock unrelated play.
2. **Every mistake creates a comeback.** A wrong route reveals useful evidence and opens Counterbreak gameplay instead of ending the encounter.
3. **Counterfeits have personalities.** The SLM's answer, misconception, and computation become a route, rival family, and attack.
4. **Strategies beat guessing.** Gadgets model reusable reasoning such as estimating, visualizing equal parts, and checking with an inverse operation.
5. **Progress shows mastery.** World repairs, Field Guide discoveries, and cosmetic unlocks celebrate understanding without paywalls or grind.

## The five-minute run

This is the target content-complete product loop. The current technical vertical slice
implements the checkpoint core and run summary as a six-checkpoint reviewed-SLM rally
(plus a separate three-checkpoint fixture run), not the garage, boss, persistent district
repair, or reward layers.

1. **Garage:** choose a car color and one strategy gadget.
2. **Launch:** watch a brief automatic driving beat that establishes the next checkpoint.
3. **Three rally checkpoints in the target loop:** complete the Truth Gate and Counterbreak loop at each one. The released technical slice currently runs all six reviewed encounters; route choices change scenery and which Glitch is encountered, not access to learning content.
4. **Repair stop:** equip a different gadget or improve one use for the rest of the run.
5. **Counterfeit Convoy:** defeat a short boss sequence that recombines Glitch tricks already introduced during the run.
6. **District repair:** restore one visible part of Fraction Foundry, record discoveries in the Field Guide, and earn a cosmetic garage item.

There is no default timer. Driving pauses whenever the player needs to read or reason.

## Exact encounter loop

In the released SLM run, each encounter uses one trusted question and correct answer
plus exactly three reviewed SLM-authored counterfeits. Direct `/prototype/` uses the
same interaction shape with explicitly hand-authored fixtures so it can remain a useful
illustration without misrepresenting provenance.

### 1. Approach

The car automatically reaches a damaged section of the Proof Road. The scenery settles and background movement stops. The question appears beside four neutral route gates: one trusted answer and three counterfeits in a stable ID-based permutation.

### 2. Investigate

The player may inspect the question and use an equipped gadget. Gadgets provide a representation or checking strategy, never the answer. No gate reveals correctness through color, position, animation, wording, or DOM order.

### 3. Lock the route

The player selects a gate and explicitly chooses **Lock route**. Selection remains reversible until this commitment.

- **Trusted route:** the car earns Proof Boost. The Glitch Forge launches the encounter's
  reviewed `featuredCounterfeitId` rival so the player still investigates SLM-authored
  reasoning without relying on source-array position.
- **Counterfeit route:** that exact answer becomes the rival, and the cracked road absorbs the initial boost. The player remains in the encounter with no score or progress penalty.

### 4. Reveal the Glitch

The chosen counterfeit unfolds into its canonical rival vehicle. Its SLM-generated computation appears as an exhaust ribbon, and its misconception label is translated into concise player-facing language. The game shows the reasoning before asking the player to repair it.

### 5. Counterbreak

The player selects the repair strategy that defeats the revealed misconception. For example, against direct numerator-and-denominator addition, the useful repair is **Make equal-sized pieces first**. An incorrect repair remains selected as an attempt, offers a focused hint, and lets the player try again without losing the run.

### 6. Fire and repair

The correct strategy loads the Patch Cannon. Its beam replaces the faulty step with trusted repair steps, closes the road seam, and resolves the encounter. The player sees both the repaired computation and a one-sentence explanation of the Glitch's trick.

### 7. Return to the rally

The car drives onto the repaired Proof Road. The Field Guide records the encountered Glitch family, and the next short driving beat begins.

### Vertical-slice encounter

The first implementation uses a Fraction Foundry beacon problem:

> A beacon used `3/4` of its charge, then another `1/8`. How much charge was used?

- Trusted answer: `7/8`
- Counterfeit `4/12`: adds numerators and denominators directly
- Counterfeit `3/32`: multiplies instead of adding
- Counterfeit `5/8`: subtracts instead of adding
- Trusted repair: `3/4 = 6/8`, then `6/8 + 1/8 = 7/8`

This record is a hand-authored interaction fixture. It is explicitly **not** an SLM
generation and is not approved gameplay content. It remains available only at direct
`/prototype/`. The root launch uses the separate `glitch-rally-v1` release, whose six
encounters carry real v7.1 provenance, deterministic validation, holdout exclusion, and
the owner's explicit review.

## The SLM as the Counterfeit Engine

The final `Qwen3-4B` model with the v7.1 LoRA is central to encounter creation but does not run in a child's browser. A trusted gameplay author supplies the question, correct answer, and topic. The SLM generates three candidate distractors, each containing an answer, named misconception, and question-specific computation. Deterministic checks and owner review approve content before it enters the static game pack.

| SLM output | Game expression |
|---|---|
| Distractor answer | Counterfeit route gate and rival's claimed result |
| Misconception label | Canonical Glitch family, silhouette, personality, and repair strategy |
| Computation | Rival exhaust ribbon, attack explanation, and evidence for Counterbreak |

The SLM never supplies authoritative questions, correct answers, scoring rules, or feedback. Unchecked generations never reach players. Gameplay content never uses the frozen 140-item model-evaluation holdout.

The model's role is visible without making children wait for generation:

- The approach announces that the **Glitch Forge loaded three counterfeits**.
- Every rival displays its forged answer and computation.
- Field Guide entries distinguish the generated trick from the trusted repair.
- A **Behind the Glitches** panel explains that a small local language model invents plausible wrong reasoning, which is checked before release.

## Glitch vehicle families

Generated labels map to a small authored taxonomy so every enemy has reliable visuals, language, and repair rules.

| Family | Mathematical tell | Vehicle personality | Counter-strategy |
|---|---|---|---|
| **Fraction Forger** | Counterfeits fraction pieces or denominators | A paper jaw clips and restamps fraction strips | Rebuild equal-sized pieces |
| **Operation Swapper** | Replaces the requested operation | A rotating hood sign swaps symbols | Restate the story action |
| **Reciprocal Rogue** | Flips the wrong quantity in division | Hinged fraction panels spin around | Name which divisor is inverted |
| **Decimal Drifter** | Slides a decimal into another lane | Wheels and digits drift out of alignment | Align place values |
| **Place-Value Phantom** | Loses or invents a place-value unit | Translucent digit tabs fade in and out | Name each digit's unit |
| **Sign Flipper** | Reverses a positive/negative rule | A polarity plate flips red-to-blue | Model direction and sign |
| **Order Hacker** | Scrambles parentheses, powers, or operation order | Stacked operation cards reorder themselves | Mark the operation sequence |
| **Factor Faker** | Confuses factors, multiples, GCF, or LCM | Gear teeth pretend to fit incompatible cogs | List or factor systematically |
| **Rounding Rascal** | Checks or changes the wrong digit | A paper dial points one place off | Mark the target and deciding digit |

Every approved distractor maps to one of these authored families. There is no runtime
wildcard that can bypass review or introduce arbitrary components.

## Strategy gadgets and rally tools

- **Scan Pulse:** highlights a reasonable magnitude or interval so the player can reject impossible routes.
- **Model Drone:** projects a visual model such as fraction strips, a number line, or grouped quantities.
- **Reverse Gear:** checks a candidate using an inverse operation or substitution.
- **Patch Cannon:** the always-available resolution tool; it fires only after the player chooses a repair strategy.
- **Proof Boost:** immediate rally momentum earned for selecting the trusted route on the first commitment. It is feedback, not a permanent power advantage.

Using a strategy gadget never reduces rewards. Gadgets are limited by encounter suitability, not an anxiety-producing energy economy.

## Progression and motivation

The game rewards competence, curiosity, and collection rather than compulsory streaks.

- **World repair:** each completed run restores machinery, bridges, lighting, and traffic in Fraction Foundry.
- **Field Guide:** records each Glitch's visual tell, forged computation, plain-language misconception, and counter-strategy.
- **Garage expression:** unlocks car colors, decals, horns, paper trails, and dashboard trophies; cosmetics do not alter mathematical difficulty.
- **Route choice:** lets players choose scenery, known Glitch families, or an unfamiliar challenge before a run.
- **Boss mastery:** asks players to recognize and compare previously encountered tricks rather than introducing an unprepared final gimmick.
- **Comeback satisfaction:** repairing a route after a wrong choice grants full completion and makes the original misconception memorable.

The game never labels a learner as having a misconception based on one answer. A selection is evidence about that moment, not a diagnosis or identity.

## Visual and interface direction

The world is a tactile **papercraft rally workshop** drawn entirely with SVG and CSS: layered paper, inked edges, fold tabs, brass-pin pivots, stamped labels, and toy vehicles with readable silhouettes. It should feel handmade and energetic rather than like a school dashboard or a neon “AI” interface.

The signature element is the **Proof Road**: a road, equation beam, and progress path in one. Answer gates physically join this road; faulty computations crack or distort it; a trusted repair closes its seam. This is the one dominant visual idea, so surrounding panels remain quiet and legible.

### Palette

- Cloud `#EAF1F4` — world background
- Paper `#FBFCF8` — reading surfaces
- Graphite `#18252E` — text and ink outlines
- Blueprint `#2857C5` — player controls and car identity
- Repair Gold `#F2AA32` — strategy tools and active repair
- Fault Coral `#D94B61` — revealed faults only
- Confirmed Teal `#238E72` — repaired states only

Color supports states but never carries meaning alone. Patterns, icons, labels, and shape changes accompany every state.

### Typography and layout

- Broad system-rounded display lettering gives district and Glitch names toy-box character.
- Highly legible system UI text carries questions, instructions, and controls without remote font loading.
- The current dependency-free runtime renders bounded, escaped math text. A later KaTeX
  enhancement is optional and must retain readable text semantics and a safe trust
  configuration.
- Desktop and tablet layouts place the rally stage beside the question panel. Below `760px`, the stage stacks above the question and controls.
- One primary action appears per phase. Answer and repair targets are at least `48px` high.

## Motion direction

Motion explains mathematical cause and effect. The player car entering the Proof Road is always the primary action; the rival reacts only after contact, and background motion pauses during reading and decisions.

| Beat | Duration | Purpose |
|---|---:|---|
| Select press and settle | `90ms` | Confirms input without implying correctness |
| Commit anticipation | `120ms` | Car compresses backward to show intent |
| Lane travel | `320ms` | Car follows a readable curved path into the chosen gate |
| Contact | `80ms` | Anchored squash gives the gate impact and weight |
| Glitch reveal | `300ms` | Rival unfolds after the mathematical result is known |
| Repair beam | `240ms` | Patch travels from the chosen strategy to the broken step |
| Paper-tab settle | `280ms` | Delayed parts follow through after the road is repaired |

Anticipation communicates commitment; staging protects the current mathematical idea; squash and stretch show force at contact; arcs keep vehicle travel lively; and restrained follow-through gives the paper construction character. Motion is deterministic from encounter phase and outcome. It does not use unseeded randomness, unrelated looping effects, or generic bounce on every control.

Under `prefers-reduced-motion: reduce`, travel, shake, scenery loops, and unfolding are removed. Clear labels, outlines, and immediate state changes preserve the complete interaction and meaning.

## Content, safety, and accessibility rules

- Every gameplay question and repair is trusted and reviewed for sixth-grade correctness.
- Every counterfeit must be numerically distinct, must not be equivalent to the correct answer in another form, and must have a non-empty misconception and grounded computation that ends in its claimed answer.
- Production pack construction reuses the offline hardened computation-consistency checks, verifies canonical Glitch and repair references, and rejects exact or near-duplicate frozen-holdout questions. The browser does not perform or weaken those approval checks.
- Player-facing language describes the action: “This route combined unlike pieces.” It never says “You are bad at fractions” or assigns a learner type.
- A wrong route does not remove progress, rewards, lives, or access to help.
- There is no default timer, public score, forced streak, advertising, purchase, loot box, or energy meter.
- Weapons are bright repair tools used against cardboard robot vehicles. No people or animals are targets; defeated rivals separate into paper parts and repair tape rather than showing injury.
- Native buttons, visible focus, full keyboard operation, touch targets, and color-independent state cues are required.
- Decorative SVG paths are hidden from assistive technology; the rally stage has a concise accessible summary.
- Correctness is not leaked before commitment through visual treatment, ordering, accessible labels, or animation.
- The current game stores no account, identity, learner profile, analytics, or persistent
  progress. Run state exists only in memory; a later local-save feature would remain on
  the device and include a reset.

## Scope

### Current released vertical slice

- A six-checkpoint reviewed-SLM run containing `GR-NUM-010`, `GR-NUM-018`,
  `GR-NUM-024`, `GR-NUM-036`, `GR-NUM-037`, and `GR-NUM-055`
- Questions across Decimal Docks, Factor Forest, Fraction Foundry, and Integer Iceway;
  these districts provide cross-topic breadth, not a finished campaign or map
- A direct three-checkpoint `/prototype/` route using clearly labeled hand-authored
  fixtures for interaction comparison
- Full choose → commit → Glitch reveal → Counterbreak → repair → next checkpoint → finish → replay flow
- Correct-route Proof Boost and exact wrong-route rival behavior
- Run progress, banked Proof Boosts, Patch Cannon attempt totals, and deterministic non-positional answer/repair ordering
- A four-lane Proof Road tied to gate order, text labels for every committed route,
  dynamic stage summaries, and deterministic accents for all nine canonical families
- Implemented responsive papercraft rules, keyboard and touch interaction, persistent
  live status, and reduced-motion behavior; real-browser verification remains pending
- Strict browser loader for sanitized owner-approved packs, while prototype fixtures remain unable to masquerade as approved content
- Explicit startup behavior: the root URL selects `glitch-rally-v1`, direct
  `/prototype/` stays illustrative, and a selected same-origin pack must verify
  completely or stop safely without a fixture fallback
- A 60-question original generation bank and completed free-Colab → validation → review
  → export path for the first sanitized release
- Hash-bound pack provenance, including the exact frozen 140-row holdout exclusion
- No runtime model, server, account, analytics, or paid dependency

### First content-complete MVP

- One Fraction Foundry rally with a 5–8 minute run
- 15–20 owner-reviewed, non-holdout encounters
- At least three authored Glitch vehicle families drawn from the approved taxonomy
- Scan Pulse, Model Drone, and Reverse Gear, with Patch Cannon as the core repair tool
- Truth Gate, Fault Trace, and Predict the Glitch encounter variants
- One Counterfeit Convoy boss assembled from previously taught tricks
- Field Guide, district restoration, cosmetic garage rewards, and local save
- Static deployment suitable for free hosting

### Explicit exclusions

- Live SLM inference, runtime GPU use, external AI APIs, backend services, databases, or secrets
- Manual or physics-based steering, 3D, Phaser, open-world movement, or racing against a clock
- Multiplayer, accounts, cloud saves, leaderboards, chat, teacher dashboards, or analytics
- A full multi-district campaign in the first content MVP, adaptive learner diagnosis,
  or unreviewed procedural question generation
- Paid assets, remote-font dependency, licensed music, ads, purchases, loot boxes, or premium currency
- Human or animal combat, realistic weapons, failure lives, punitive streaks, or homework-style grading
- Model retraining, changes to the final SLM, or use of the frozen evaluation holdout as game content
