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

## Learned the hard way

- Two Web Animations on one element touching the same property: the one created later wins at all times, including its backward fill. Merge tracks, or split them across elements or properties.
- Pygments' short class names collide with your own. The template prefixes them `t-`.
- Highlight whole files, not lines, or multi-line comments and strings break.
- Headless Chrome screenshots of a scrolled page come out blank. Render one read section per frame with `?only=N`.
- The player's `requestAnimationFrame` loop keeps headless Chrome alive after it writes the screenshot. verify.py kills it once the PNG stops growing.
- Kokoro: acronyms need the lexicon; a bare "a" or "an" right before a lexicon term is read as the letter A.
- Diff against `merge-base`, not `origin/main`: a moved main shows unrelated changes.
