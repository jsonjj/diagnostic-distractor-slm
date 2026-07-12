# Wayline Internal Mac Test Guide

**Build:** `unity/Wayline/Builds/MacDevelopment/Wayline-Development-arm64.app`  
**Scope:** skippable opening cinematic, living-atlas home, and a three-world route
(Valuehold → Decimara → Fracture) with one champion fight per world, each followed
by the complete post-battle Route Trial + reward loop  
**Characters:** original blocked-out low-poly humanoids (distinct hero + three
champion silhouettes/weapons), not final commissioned art  
**Expected banner:** `DETERMINISTIC LOCAL ACCEPTANCE DATA — NOT LIVE SLM`  
**Live SLM:** off until the owner provides the Colab GGUF export — see
[`WAYLINE_LIVE_SLM_GO_LIVE.md`](WAYLINE_LIVE_SLM_GO_LIVE.md)

## What's new in this build

- A short, skippable in-engine opening (press Space/Enter/click or a controller
  button to skip) states why the Meridian broke and why each duel happens, then
  hands off to the home screen.
- The home screen is a living atlas with three route nodes; the active route
  pulses in meridian gold.
- Three worlds are playable in sequence, one fight each. Each world re-themes the
  opponent (Surveyor-General → Tide Marshal → Chain Warden), arena color, and
  trial. Clearing all three shows `MERIDIAN RESTORED`.
- Combat reads as two animated characters: torso lean and weapon swing on attacks,
  an impact camera shake, and a squash-and-settle on contact.

## Launch

1. Quit every older Wayline window.
2. In Finder choose **Go → Go to Folder…** and paste:

   ```text
   /Users/jonat/Projects/diagnostic-distractor-slm/unity/Wayline/Builds/MacDevelopment
   ```

3. Double-click `Wayline-Development-arm64.app`.
4. If macOS blocks the first launch, Control-click the app, choose **Open**, then choose **Open** again. This internal build is ad-hoc signed, not notarized.

## Five-minute acceptance path

1. Watch or skip the opening (Space/Enter/click). On the atlas home screen, click **ENTER VALUEHOLD** (or press Return on a fresh run).
2. On `VALUEHOLD REACH`, click **FACE THE SURVEYOR**. (On later routes the button reads **FACE THE TIDE MARSHAL** / **FACE THE CHAIN WARDEN**.)
3. Fight until either fighter is defeated:

   | Input | Action |
   | --- | --- |
   | `A` / `D` or Left / Right | Move |
   | `J` | Light attack |
   | `K` | Heavy attack |
   | `L` | Parry |
   | Hold Left Shift | Guard |
   | Space | Dodge backward |

4. If defeated, confirm the app returns to the map and allows an immediate rematch. A defeat does not erase progress.
5. After a victory, answer all three Route Trial items and choose `Certain`, `Leaning`, or `Guessing` for each.
6. To exercise delayed batch feedback, intentionally choose option B on question 1 and option A on questions 2 and 3. The exact result must say `1 of 3 answers are incorrect` without identifying the item.
7. Choose **Review answers**, change question 1 to option A, and finish the one allowed review.
8. Confirm final feedback shows the first choice, review choice, correctness, a possible error route when applicable, and a reliable method.
9. Complete the trial, reach the reward screen, and return to the map. The atlas advances to the next route; repeat for Decimara and Fracture. After the third, the home screen reads `MERIDIAN RESTORED`.

The deterministic acceptance answers are:

1. `3,000`
2. `4,798`
3. `4,535`

## What is intentionally unfinished

- This build uses fixed, deterministic acceptance questions; it does not run the Qwen SLM yet (live is one owner-provided GGUF away — see the go-live runbook).
- Characters are original blocked-out low-poly humanoids, not the final commissioned/rigged art; environments are graybox with per-world recoloring.
- Final audio, bespoke animation/VFX, and the full five-fights-per-world campaign are not in this build.
- It is an internal Apple-Silicon build, not a notarized public release.

## Reset to the title

Wayline resumes its last safe checkpoint. To retest from a completely new profile:

1. Quit Wayline.
2. In Finder choose **Go → Go to Folder…** and paste:

   ```text
   ~/Library/Application Support/DefaultCompany/Wayline
   ```

3. Move `wayline-runtime-session-v1.json` and any matching `.bak` or `.tmp` file to the Trash.
4. Launch the app again.

## If an input still does not respond

Send the exact screen name, the input used, and a screenshot. Also attach:

```text
~/Library/Logs/DefaultCompany/Wayline/Player.log
```

Do not test a previously open copy of the app after a rebuild; quit it and reopen the app from the exact build folder above.
