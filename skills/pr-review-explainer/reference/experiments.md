# Experiments

These are Ben's ideas for visualizing a PR. He wants all of them tried, one at a time, against a real PR, keeping the ones that stick. Update the status and add his verdict after each try.

## Built (v1)

| Idea | Where | Verdict |
|---|---|---|
| Narrated, timeline-driven tour of the real diff (from an earlier one-off video explainer) | tour mode | the bar: "really really good" |
| Doc and video combined in one artifact | tour + read over one scene list | pending |
| Annotations pinned to the code they explain | tour side column; read steps light up their rows on hover | **liked**: "i like the highlighting code when hovering over explanations" |
| Focus mode: fold what the narration doesn't touch | read mode folds runs of 8+ rows with no focus | pending; every fold must collapse again (fixed) |
| Structural (AST-aware) diff: alignment-only changes dimmed, inserted tokens marked inside a line instead of a -/+ pair | difftastic in build.py; read-mode toggle | pending |
| Never lose your place | permalinks open in a new tab | pending |
| Links on ticket keys and `#N` PR refs, with their title and status in a hover card | `jira:` in the spec; titles fetched at build time | asked for |
| Rule data as a table with logic chips (e.g. routing rules from a config file) | `table` widget | liked in the static explainer |
| Run the code and show real output | `snippet` widget fed from a real run | liked |
| Everything in the PR reachable | read mode's "rest of the diff" | pending |

## Tried and dropped

| Idea | Verdict |
|---|---|
| A list of all review findings at the top of read mode, each jumping to its note | "review comments at the top isn't helpful". Findings live where their code is, plus the verdict. |
| Happy-path toggle (v1: folded Go `if err != nil { … }` blocks, matched by `^(\s*)(} else )?if .*err.* != nil {$` up to the `}` at the same indent) | Parked: Ben will revisit it when he works through the experiments. See "To try". |

## To try

- **Peek, don't jump.** Hover a symbol in the diff to see its definition or its other changed call sites in a popover; click to pin it in a side panel. Needs a symbol index; `ast-grep` or tree-sitter over the PR head.
- **AST change summaries.** Chips like "+ field `retry *RetryPolicy`" or "+ param" on a hunk header, built from difftastic's token data plus a tree-sitter pass.
- **Call-flow strip as the table of contents.** Hover a node to peek that function's hunk; click to expand it under the strip. Or a sticky mini-map of the flow next to the diff, with a "you are here" marker that tracks scrolling.
- **Type models as real components.** Struct declarations collapsed to one line per field, with nested types expandable in place and `?` / `[]` badges. Ben disliked ASCII trees.
- **Switches and enums as tables.** Render a `switch` or `const` block as value → behavior.
- **Table-driven tests as tables.** Render a Go `tests := []struct{…}{…}` table, or a pytest `parametrize` list, as a real table: one row per case, columns from the struct fields, the case name first, and changed or added cases marked. Useful both for "what does this cover" and for spotting the missing case (e.g. 19 validator cases where one rule has none).
- **Happy path.** Hide error handling to see the main flow. The Go v1 is described under "Tried and dropped". Also try Python `try/except`, TS `catch`, Rust `?`/`match Err`, and compare it against pseudocode on the same function.
- **Pseudocode summaries.** Ben is undecided.
- **Tables with hierarchy.** Large rule tables group into collapsible rows with a one-line summary each (e.g. "SPT: 6 rules, all tested except negative Group").
- **Interactive evaluators.** For rule data (e.g. "which rules fire for this request?"), embedded as an `html` widget in read mode.
- **Word-level sync.** Force-align each cue's WAV (whisper with word timestamps; `bb whisper` exists) so anchors land exactly instead of being estimated from character position.
- **Smaller audio.** 32 kbps Opus instead of 48 kbps MP3 if the viewers allow it. Kokoro speech is clean, so it should survive.
- **A better voice.** Ben: "the audio's not perfect". The test was a bake-off page with 13 voices from 8 local MLX models reading the same cues: Kokoro (3 voices), Qwen3-TTS 1.7B, Voxtral 4B, Breeze TTS 2, VoxCPM2, Chatterbox Turbo, Pocket TTS and Soprano. A Parakeet transcript check runs on each clip. The grid had 91 clips, and Ben said that was too many to choose from. Ben's framing: pick the model first, because the model decides reading quality such as acronyms, and treat the voice as preference. The skill isn't for commercial use, so non-commercial licenses are fine.

The chooser now has two rounds:
- **Models**, in blind king-of-the-hill A/B. Each model reads through a male voice, so timbre varies little. The winner of each round stays on. Lines include acronyms both with our fixes and as written, unfixed: the unfixed version shows how much lexicon work each model would need for every new PR.
- **The winner's voices**, as a list sorted by pitch.

Both rounds play at the same words per minute, and switching voices mid-line picks up at the same spot.

**Verdict:** Ben picked **Breeze TTS 2, "calm female engineer"**; the calm male engineer is also good. Both are now in `voices.yaml`, with calm-female as the default and Kokoro as `--voice michael`. Breeze designs a voice from a description and clones it for every cue. Design and cloning are bit-identical for a given seed, so the voice needs no audio file in the repo. Any engine other than Kokoro needs a plain-text respelling of the lexicon (`S C S C F`), since it can't take IPA. Voice-design models (Breeze, VoxCPM2) design a voice once and then clone that clip, or the voice drifts between cues.

## Learned the hard way

- Two Web Animations on one element touching the same property: the one created later wins at all times, including its backward fill. Merge tracks, or split them across elements or properties.
- Pygments' short class names collide with your own. The template prefixes them `t-`.
- Highlight whole files, not lines, or multi-line comments and strings break.
- Headless Chrome screenshots of a scrolled page come out blank. Render one read section per frame with `?only=N`.
- The player's `requestAnimationFrame` loop keeps headless Chrome alive after it writes the screenshot. verify.py kills it once the PNG stops growing.
- Kokoro: acronyms need the lexicon; a bare "a" or "an" right before a lexicon term is read as the letter A.
- Diff against `merge-base`, not `origin/main`: a moved main shows unrelated changes.
- Coined words like "or-ed" and "and-ed" trip up every TTS engine. Write "joined with OR" instead.
- Small autoregressive TTS models can run away: Soprano produced 30 s of wordless noise in place of half a sentence. A transcript check catches it. Look for a long stretch with no recognized words, not just a word error rate.
- WebM written to a pipe has no duration (`audio.duration` is Infinity, so seeking breaks). Have ffmpeg write a file.
