# Experiments

These are Ben's ideas for visualizing a PR. He wants all of them tried, one at a time, against a real PR, keeping the ones that stick. Update the status and add his verdict after each try.

## Built (v1)

| Idea | Where | Verdict |
|---|---|---|
| Narrated, timeline-driven tour of the real diff (from an earlier one-off video explainer) | tour mode | the bar: "really really good" |
| Doc and video combined in one artifact | tour + read over one scene list | pending; Ben is undecided whether read mode should stay equivalent to the video |
| Annotations pinned to the code they explain | tour side column; read steps light up their rows on hover | **liked**: "i like the highlighting code when hovering over explanations" |
| Focus mode: fold what the narration doesn't touch | read mode folds runs of 8+ rows with no focus | pending; every fold must collapse again (fixed) |
| Structural (AST-aware) diff: alignment-only changes dimmed, inserted tokens marked inside a line instead of a -/+ pair | difftastic in build.py; read-mode toggle | pending |
| Never lose your place | permalinks open in a new tab | pending |
| Links on ticket keys and `#N` PR refs, with their title and status in a hover card | `jira:` in the spec; titles fetched at build time | asked for |
| Rule data as a table with logic chips (e.g. routing rules from a config file) | `table` widget | liked in the static explainer |
| Run the code and show real output | `snippet` widget fed from a real run | liked |
| Everything in the PR reachable | read mode's "rest of the diff" | pending |
| Message ladder: who sends what to whom, old vs new identity in different chip colors, revealed step by step | `sequence` widget | asked for ("visualize who's sending what kind of messages to whom"); "works great in the video" |
| Go types as code you can open (from the lab): a field's type opens its declaration under the line, with its methods; badges spell out pointer, optional, list of, map, and `iota` values | read mode, `views.py` | keep; always on, since each link is its own toggle |
| Test cases as a table in place of their literals (from the lab), including part of a table when a hunk shows only some cases | read mode, `views.py` | keep; the bar above the table switches to the code |
| Happy path (from the lab): error handling folded to one line of its real code, with work calls bright. Never folds a block with a change in it, so a fold can't hide one. A block a step points at wholly starts open, and a hovered step lights the fold that stands for its rows | read mode, `views.py` | keep; each fold is its own toggle |

## In the lab (diff rendering)

`scripts/lab.py` builds `pr<N>-lab.html`: each experiment on the PR's own code, with a Today / Experiment toggle and keep / maybe / drop buttons that collect into a box to paste back. The code analysis is Go only, through tree-sitter (`scripts/goast.py`). First tried on a PR that adds an XML config package (19 new types, a 19-case table test).

**Round 1 verdicts:**
- **Change summaries as chips** (declarations, params, fields and call arguments from tree-sitter): *maybe*, "meh … too deterministic, let the LLM do it instead".
- **Types as cards**: *keep*, "interesting, can we make it show the code directly but have rich expandable text?"
- **Test tables beside the code**: *maybe*, "Can we put the table in line or overlaid on the code?"
- **Happy path with "on error: …"**: *keep*, "cool, instead of writing `on error` want to write the actual code? `if err != nil { ....`".

**Round 2 verdicts:**
- **The PR as an outline** (every hunk folded under a one-line summary the agent wrote while reviewing): *drop*, "it's easier to read the code than the english descriptions. but we should keep exploring ideas around explaining code".
- **Types as code you can open**: *keep*, "very good! let's just reduce the empty gutter width left of the line numbers".
- **Test cases as a table, inside the test**: *keep*, "can we not upper case the column names, and syntax highlight the cells that are code".
- **Happy path** (only `err != nil` checks folded): *keep*, "when i expand an error line to show the full version, hide the collapsed version and let me click on the full version to collapse". He also asked whether checks like `if key == ""` and `if _, exists := seen[key]; exists` should count, and whether the LLM should decide. The answer was a broader rule in code: the check is mechanical, the result is predictable, it needs no spec work, and a wrong fold still shows the real code on one line.

**Round 3 verdicts:**
- **Types as code you can open** (empty old-line-number column dropped): no verdict yet.
- **Test cases as a table, inside the test** (field-name headers, highlighted code cells): *keep*, but a cell whose shared start was trimmed to `…` and whose rest was short had no way to show the full value.
- **Happy path** (broader error rule, fold replaced by the block when open): *keep*, "good, but some calls are grayed out that i want to see, like `xml.Unmarshal()` or `criterion.validate()`".

| Round 4 | What the lab shows | Verdict |
|---|---|---|
| Types as code you can open | The real declarations, starting from the types that contain the rest. A type name in a field opens that type's declaration nested under the line. Methods open the same way. Line-end badges: `pointer`, `optional` (`omitempty` pointers), `list of`, `map K →`; `iota` consts show `= N`. Code with no old side drops the empty old-line-number column. | keep ("lgtm") |
| Test cases as a table, inside the test | The test function as code, with the case literals replaced in place by the table; a bar switches between table and code. Columns are the field names as written. Code cells are syntax-highlighted. Boilerplate shared by 3+ cases shows as `…`, and every trimmed or cut cell has **more** to show it in full. | keep ("lgtm") |
| Happy path | Each error-handling `if` (no else) joined onto one dimmed, highlighted line of its real code, like an editor fold. It counts when the condition checks `err != nil`, or when the body only returns an error, assigns to err-named variables (`errs = multierr.Append(…)`), panics or calls a `Fatal` log, optionally ending in `continue` or `break`. Calls that do real work stay bright (`if err := f(); …`, `errs = multierr.Append(errs, x.validate())`); error constructors, builtins and conversions dim with the rest. Opening a fold replaces the line with the block; clicking the block folds it again. | keep ("lgtm") |

All three moved into read mode (see Built), with no global toggles: Ben said "we don't need toggles, mostly, esp for things that have no impact when collapsed or things with built-in toggles". The tour still shows plain code.

## Tried and dropped

| Idea | Verdict |
|---|---|
| A list of all review findings at the top of read mode, each jumping to its note | "review comments at the top isn't helpful". Findings live where their code is, plus the verdict. |
| Happy-path toggle (v1: folded Go `if err != nil { … }` blocks, matched by `^(\s*)(} else )?if .*err.* != nil {$` up to the `}` at the same indent) | Parked until Ben worked through the experiments; now back in the lab, using tree-sitter instead of the regex. |
| Change summaries as chips (lab round 1): declarations, params, fields and call arguments from tree-sitter | "too deterministic, let the LLM do it instead", which became the outline. |
| The PR as an outline (lab round 2): every hunk folded under a one-line summary the agent wrote | "it's easier to read the code than the english descriptions". Prose standing in for code loses to the code itself. |

## Open from the second test PR

A subagent ran the whole skill on a 40-file PR that mostly modifies existing code. These were found and are not fixed yet:
- **Stat scene:** `hl` on a file row below the pane's fold doesn't scroll to it, so the highlight is off screen.
- **Tour code pane:** long lines are cut at the right edge, worst with `side: wide`.
- **Spec format:**
  - `side:` is set per scene, so cues without notes still give up code width;
  - no action can point at a summary scene's `tests`;
  - a summary holds at most 4 items.
- **Test tables:** a changed case shows only its new value; the old one is in the raw diff only.
- **Rest of the diff:** modified files still carry both structural and raw rows, which makes up most of the page's size.
- **Narration:** two cue starts may be clipped by the voice ("Unwrap first…" heard as "App first…"); listen before trusting it.

## To try

- **Explaining code without replacing it.** Ben: "keep exploring ideas around explaining code". Two summaries lost to the code they described (chips and the outline), so explanations should sit beside the code or inside it, not in place of it.
- **Peek, don't jump.** Hover a symbol in the diff to see its definition or its other changed call sites in a popover; click to pin it in a side panel. Needs a symbol index; `ast-grep` or tree-sitter over the PR head. Ben: "i like it".
- **Call-flow strip as the table of contents.** Hover a node to peek that function's hunk; click to expand it under the strip. Or a sticky mini-map of the flow next to the diff, with a "you are here" marker that tracks scrolling. Ben likes "how it guides me thru the PR in a logical order" and is weighing horizontal against vertical timelines and tables of contents, so try both.
- **Graphics beside the diff in read mode.** The tour's widgets (tables, flows, and the message ladder) "work great in the video"; Ben wonders how to bring them in alongside the code diff.
- **Switches as tables.** Render a `switch` as value → behavior (enum values already show with the types).
- **Lab experiments in other languages.** Table tests from pytest `parametrize`; happy path for Python `try/except`, TS `catch`, Rust `?`/`match Err`; compare happy path against pseudocode on the same function.
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
- The inline viewer shows pages in an iframe sandboxed without `allow-same-origin`, where even reading `localStorage` throws and stops the script. Guard it.
- A rendering tuned on one PR breaks on the next. The lab's first PR was all new files; on a PR that modifies code:
  - windows built from line ranges had silent gaps;
  - folds hid the only change in a hunk;
  - a prefix trim hid the one field that told two cases apart.

  Try every rendering on a modification-heavy PR before it moves into read mode.
- WebM written to a pipe has no duration (`audio.duration` is Infinity, so seeking breaks). Have ffmpeg write a file.
