# Wayline Route Trial UI Specification

**Audience:** ages 10–13  
**Single job:** answer every item with confidence, receive one truthful batch-level review opportunity, then understand the final methods.  
**Authority:** supplements `WAYLINE_MASTER_GDD.md`, `WAYLINE_ART_ANIMATION_ASSET_BIBLE.md`, and `WAYLINE_LEARNING_AND_RUNTIME_SPEC.md`.

## Visual direction

The trial is the Routekeeper's atlas-bracer unfolding over the paused arena, not a separate worksheet screen. One narrow meridian-gold line enters from the lower-left bracer position, traces the current reading path, and reconnects at completion. That line is the sole decorative flourish; panels, typography, and motion remain quiet enough for the mathematics to dominate.

### Tokens

| Role | Token |
| --- | --- |
| Deep field | Night ink `#151B26` at 92% opacity |
| Structural field | Lapis `#253B66` |
| Secondary focus | Storm teal `#2D7F83` |
| Combat warning only | Oxide `#A5432F` |
| Selection/focus/route | Meridian gold `#E6AF3B` |
| Reading surface | Limestone `#D7D1C2` |

- Display: Big Shoulders Display, restricted to world/trial labels.
- Reading: Atkinson Hyperlegible for instructions and feedback.
- Mathematics/data: IBM Plex Mono for prompts, options, counts, and confidence notches.
- Until the licensed font files are imported, use Unity's bundled fallback without pretending the final typography gate has passed.

## 1920 x 1080 layout

```text
+--------------------------------------------------------------------------------+
| VALUEHOLD REACH / ROUTE TRIAL                         ITEM 1 OF 3   [Read aloud] |
|                                                                                |
|    .------------------------ meridian route ------------------------------.     |
|    |                                                                           |
|    |   [ Question prompt, maximum readable line width: 32 mono characters ]    |
|    |                                                                           |
|    |   A  answer field                         B  answer field                  |
|    |                                                                           |
|    |   C  answer field                         D  answer field                  |
|    |                                                                           |
|    '---- Confidence:  [Certain]  [Leaning]  [Guessing]  --------> [Continue]   |
|                                                                                |
|  No timer                                        Keyboard/controller hints     |
+--------------------------------------------------------------------------------+
```

- The prompt occupies the visual center and never competes with a character portrait.
- Options form two columns at 1080p and one column when 150% text would otherwise clip.
- Answer fields use asymmetric topographic corners, a 2 px structural border, and a 4 px gold focus edge outside the component bounds.
- Nothing is preselected. `Continue` remains disabled until both answer and confidence are explicitly chosen.
- Progress is written as `Item 1 of 3`; it is not communicated by color alone.

## State behavior

### Answering

- Selected answers use a gold outline plus a filled diamond icon and the label `Selected` for screen speech.
- Confidence uses three written choices and one, two, or three engraved notches. No confidence value changes score or reward.
- Returning to an item preserves both choices locally, while the server remains the scoring authority.

### Exact-count moment

After initial submission, the question surface recedes and the immutable server result becomes the only dominant element:

```text
                         2 of 5
                     answers are incorrect

        You have one review pass. We won't mark which ones yet.
                           [Review answers]
```

All item tiles remain visually neutral. There is no red flash, shake, failure sound, item marker, or reordered choice. A zero count reads `0 of 5 answers are incorrect` and proceeds directly to final feedback.

### Reviewing

- Every item remains editable exactly once; unchanged choices are valid.
- A small neutral `First choice` line records the original option without implying correctness.
- The primary action is consistently named `Finish review` in control, loading, and completion copy.
- Duplicate input while the request is in flight cannot create a second revision.

### Final reveal

Each result presents, in order:

1. `First choice`
2. `Review choice` or `Not changed`
3. `Result: Correct` or `Result: Incorrect`, with text, icon, and border pattern
4. `This answer can come from...` only when a verified distractor was selected
5. `Reliable method`

The learner-facing copy never claims to know what the player thought. `Next method` advances between results; `Complete route trial` is reserved for the final item.

## Motion

The transition is one semantic sequence:

| Phase | Standard | Reduced motion |
| --- | ---: | ---: |
| Arena quiets and focus settles | 180 ms | none |
| Bracer line lifts and traces the route | 420 ms | 180 ms crossfade |
| Atlas surface resolves behind the line | 240 ms overlap | same crossfade |
| Content becomes interactive | after focus target is stable | after 180 ms |

- The meridian line is the primary action. Panels do not bounce independently.
- The wrong count uses one controlled 120 ms scale settle from 96% to 100%; reduced motion uses opacity only.
- Final-result transitions use a short route-line advance, never celebratory motion on an incorrect item.
- Animation state is derived from explicit controller state and normalized phase time, so identical input produces identical presentation.

## Accessibility and safety

- Minimum math size is 32 px and body size 28 px at 1080p; 125% and 150% modes reflow without truncation.
- Keyboard and controller traversal follows prompt, options, confidence, read-aloud, then primary action. Back navigation never submits.
- Focus always has shape and luminance contrast, not just hue.
- macOS speech reads the prompt, option labels and text, confidence state, and final feedback, but never a sealed key before reveal.
- No default mathematics timer, confidence penalty, item-level feedback before reveal, or generated text outside the verified public contract.
- Network/runtime failure keeps the combat result safe, explains that the trial is unavailable, and offers retry or return-to-map; it never substitutes unverified content.

## Implementation acceptance

- The Unity controller contains no answer key, correctness calculation, procedure mapping, or misconception evidence.
- Serialized Unity state and normal logs contain no sealed fields.
- Fake-client tests cover zero wrong, nonzero exact count, one revision, duplicate clicks, reload, keyboard-only completion, and failure recovery.
- PlayMode captures at 100%, 125%, and 150% text show no overlap at 1920 x 1080.
- Both standard and reduced-motion flows preserve identical controller states and submissions.
