---
name: pr-review-explainer
description: Build a narrated, diff-centric walkthrough of a pull request as one self-contained HTML file — a voiced "tour" that scrolls the real diff, highlights lines and shows small supporting diagrams, plus a "read" mode that renders the same scenes as a scrollable document with a structural (difftastic) diff. Use when the user wants to understand or review a PR faster, asks for a PR explainer, walkthrough, video, or tour, or wants review findings delivered against the code.
user-invocable: true
allowed-tools:
  - Bash(uv run *)
  - Bash(git *)
  - Read
  - Write
  - Edit
---

# pr-review-explainer

One HTML file, two modes over the same scene data:

- **Tour**: a 1280×720 player. Kokoro narration drives a timeline that scrolls the real diff, focuses line ranges, morphs old code into new, and slides review notes and small widgets into a side column. Scrubbing, speed, chapters and `?t=` deep links are exact, because every animation is a paused Web Animation set from the audio clock.
- **Read**: the same scenes as a document. The code is on the left. On the right, one block per narrated step; hovering a block lights up its rows. Rows no step points at are folded, and every fold can be collapsed again. A toggle switches between the structural diff (default) and the raw line diff. "▶ from here" jumps into the tour. A final section lists every hunk the tour skipped, so nothing in the PR is hidden.

The diff is the spine. Everything else hangs off a specific hunk.

## Prerequisites

- `uv`, `ffmpeg`, Google Chrome, and `difftastic` (`brew install difftastic`).
- Kokoro model files `kokoro-v1.0.onnx` and `voices-v1.0.bin` in `~/Downloads/kokoro/`, or set `KOKORO_DIR`.
- `uv run` and headless Chrome must run **outside the sandbox**. uv needs `~/.cache/uv` and PyPI on first run; Chrome needs its own profile. Inference itself is offline.

## Workflow

1. **Check out the PR head.** Use a worktree on the PR branch. Confirm `git rev-parse HEAD` equals the PR's head SHA (`gh pr view <n> --json headRefOid`; gh needs the sandbox off). If `git fetch` fails in the sandbox, use the local branch once the SHAs match. The builder always diffs `merge-base(base, HEAD)..HEAD`; never diff against the tip of `main`, because it picks up unrelated commits.
2. **Review first.** Read the whole diff and do a real review before writing narration. The tour is a review delivered as a walk, not a neutral summary. Write each finding down with its file and a unique line of text to anchor it.
3. **Plan the tour** (rules below), then write `spec.yaml` at `$BB_THREAD_STORAGE/pr-explainer/pr<N>/spec.yaml`. The format is in [reference/spec.md](reference/spec.md); a complete (fictional) example is [examples/example.yaml](examples/example.yaml). Keep real specs out of this public repo.
4. **Silent preview**: `uv run scripts/build.py <spec> --no-audio`. This resolves every marker (a bad marker fails the build with the reason) and estimates timings without TTS.
5. **Verify**: `uv run scripts/verify.py <html>`. Read **every** contact sheet. It screenshots each scene start and each action, plus one frame per read-mode section. Layout overflows are reported as JS errors and drawn as a red banner in the frame. Fix, rebuild, re-verify.
6. **Narrate**: `uv run scripts/build.py <spec>`. Read the phoneme dump it prints for every cue with capitals or digits. Add reusable terms to [scripts/lexicon.yaml](scripts/lexicon.yaml) (`spell:` for letter-by-letter, `ipa:` for the rest) and PR-only terms to the spec's `lexicon`. Rebuild; cached cues are instant.
7. **Verify again**, then show it:

   ```
   ::inline-vis{source="thread-storage" file="reports/pr<N>-walkthrough.html" height="720"}
   ```

   Summarize the review findings in chat as well. The file is about 0.7 MB per minute of narration; inline-vis caps at 5 MiB.

## Tour rules

These come from Ben's feedback on earlier explainers.

- **Stay on the code.** Pages of cards, prose sections, or diagrams as the main content drifted too far from the diff and read as a regression. Graphics belong in the side column, each tied to a narrated step, and only where they explain behavior better than the code does.
- **Order by data flow**, not by file: the new data or types, the core function, then callers, then wiring, then a verdict. One scene per concept; 1–4 hunk windows each, trimmed by markers to roughly 20–40 rows.
- **2–5 cues per scene**, each a sentence or two (under about 15 s). Every cue has at least one action: a focus, morph, note or widget step. Put an `[[anchor]]` right before the word an action should land on.
- **Findings go where their code is on screen**: "Review note. …" in the narration plus a `review` card. Repeat them in the verdict scene with refs.
- **Skip mechanical repeats.** Show one instance and say the others are the same. Signature threading and test churn rarely need their own scene; the read mode's "rest of the diff" covers them.
- **Keep it under about 4 minutes.** Cut whatever a reviewer wouldn't get wrong without help. Don't narrate behavior the PR didn't change.
- **Widgets:** tables of at most about 8 rows, short labels, no ASCII art or monospace trees. A side column holds about 470px; the layout check enforces it.
- **Speech:** spell numbers and identifiers the way they should sound in `speak:` ("PR five twenty five", "build user profile"); the caption keeps the real tokens.
- **Link tickets:** set `jira:` in the spec (site plus project keys; take the site from the PR's ticket link) so ticket keys link everywhere. `#N` links to that PR automatically.
- **No summary of findings up front.** Findings appear where their code is, and again in the verdict.

## UI rules

- Anything that expands on click must collapse again from the same control.
- Links open in a new tab; nothing in the page scrolls you away from where you are.

## Files

- `scripts/build.py`: spec → HTML. Merge-base diff, whole-file pygments highlighting, difftastic structural rows, Kokoro TTS, timeline, GitHub permalinks, uncovered-hunk listing.
- `scripts/tts.py`, `scripts/lexicon.yaml`: Kokoro wrapper, pronunciation lexicon, per-cue WAV cache in `~/.cache/pr-review-explainer/tts`.
- `scripts/verify.py`: contact sheets and JS/layout errors.
- `assets/template.html`: the player (tour + read).
- `reference/spec.md`: the spec format. `reference/experiments.md`: visualization ideas, with which ones are built and what to try next.
