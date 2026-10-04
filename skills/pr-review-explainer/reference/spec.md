# Spec format

One YAML file per PR. `build.py` resolves every marker against the real diff and fails with the reason if one doesn't match, so a spec can't silently drift from the code.

```yaml
repo: /path/to/worktree          # checkout at the PR head; relative paths resolve from the spec's directory
base: origin/main                # the diff is merge-base(base, HEAD)..HEAD
github: org/repo                 # for permalinks on every hunk header and ref
pr: 42
title: Retry failed webhook deliveries
kicker: "PR #42 · WEB-7"         # "#N" anywhere in rendered text links to that PR in `github`
                                 # (titles of every linked PR/issue and ticket are fetched at build time with `gh` and `twg`
                                 # and shown on hover; a failed fetch only warns; `--no-fetch` skips it)
jira:                            # ticket keys in these projects link to the ticket everywhere they're rendered
  site: https://example.atlassian.net
  projects: [WEB]
voice: calm-female               # optional: a voice from scripts/voices.yaml (build.py --voice overrides it)
lexicon:                         # PR-only pronunciations (reusable ones go in scripts/lexicon.yaml)
  cfg: spell                     # read letter by letter, by every voice
  Acme: { ipa: "ˈækmi", text: "acky" }   # ipa for Kokoro, a text respelling for Breeze; give both
  Zorp: "zˈɔːɹp"                 # a bare string is IPA, so only Kokoro uses it
lab:                             # optional: input for scripts/lab.py only
  hunks:                         # one summary per hunk for the lab's outline; `lab.py --hunks` lists them
    - { file: cmd/main.go, hunk: 1, text: "Loads the config at boot and passes it to the server." }
    - { file: api/server.go, from: "cache *Cache", text: "Mechanical: the constructor takes the cache." }
    - { file: api/cache.go, text: "The new cache. Review note: entries never expire." }   # a file with one hunk needs neither

scenes:
  - kind: stat | code | summary
    chapter: Short name on the scrubber
    kicker: Small caps line above the title (defaults to chapter)
    title: One-line headline
    side: wide                   # code scenes: 506px side column instead of 334px
    hunks: [...]                 # code scenes
    widgets: {name: {...}}       # code scenes
    tests: [...]                 # summary scenes
    items: [...]                 # summary scenes
    cues: [...]
```

## Hunk windows

```yaml
hunks:
  - file: internal/pkg/thing.go
    from: "func (s *Server) Handle("     # first row of the window (also picks the hunk)
    to: "re:^}$"                          # last row, searched from `from`; omit for the end of the hunk
    extra: 1                              # rows past `to` (negative trims)
    before: 0                             # rows before `from`
    hunk: 2                               # explicit hunk index instead of searching
    mode: diff | morph | type             # morph: old code first, then the change animates; type: new file rows type in
    structural: true                      # difftastic rows (default); false shows the raw line diff
```

**Markers** match a row's text:
- a plain substring;
- `+:` / `-:` restrict to added or deleted rows (`+:` also matches `~` and `±` rows);
- `re:` makes it a regex. `re:^}$` is the closing brace of a top-level block, and `"re:^\t}$"` (double-quoted YAML) is the brace of a block nested one level.

**Structural rows.** When difftastic understands the language:
- `~` is a line whose only change is whitespace or alignment. It is shown once, dimmed.
- `±` is an existing line with tokens inserted, such as a new parameter. It is shown once, with the insertion marked.
- `+` and `-` are real edits, with the changed tokens marked when the line is paired.

Deleted rows absorbed into `~` or `±` are hidden, so a focus marker must target the new line. The build tells you when a marker hits a hidden row.

## Cues and actions

```yaml
cues:
  - say: "The handler [[h]]stores the config, then [[p]]passes it on."   # caption; [[x]] are anchors, not spoken
    speak: "The handler stores the config, then passes it on."            # optional: what the voice says
    do:
      - { focus: [["type handler struct {", "re:^}$", 1]], at: h }        # [from, to|null, hunk-index]; several ranges allowed
      - { morph: 0, at: p }                                                # play hunk 0's old→new animation
      - { note: "Text with `code`, **bold**, {a:chips}." }
      - { review: "A finding.", ref: { file: path, marker: "text" } }      # amber card; ref defaults to the last focus
      - { widget: order, step: 1, at: end }
      - { hl: path/in/stat.go }                                            # stat scenes: light up a file row
      - { show: 0, at: p }                                                 # summary scenes: reveal item 0
```

`at` is an anchor name, a number of seconds after the cue starts, or `end`. The default is 0.3 s. Anchor times are estimated from the anchor's character position in the sentence, so put the anchor right before the word.

## Widgets

Children reveal by step: content with `step: k` appears when the cue reaches `{widget: name, step: k}`. Step 0 is when the widget slides in. `hl: k` outlines a cell or node at step k. Text fields accept the same inline markup as notes: `` `code` ``, `**bold**`, `~~strike~~`, `{a:chip}`. Chip colors are `a` blue, `b` violet, `c` amber, `d` green and `x` struck-through grey.

```yaml
widgets:
  rules:
    kind: table
    title: What each rule does
    columns: ["", "Matches when", "Goes to"]
    widths: "26px 1fr auto"          # CSS grid columns
    mono: [0]                        # monospace column indexes
    rows:
      - ["10", "{a:POST}", "queue"]                              # plain row, visible at step 0
      - { cells: ["40", "{a:PUT} ∧ {x:dry-run}", "warn: queue"], step: 2, hl: 3 }   # ok:/bad:/warn:/dim: tone a cell
  order:
    kind: flow                       # vertical chain of boxes
    title: Order inside Handle
    nodes:
      - { label: "`fetch`", sub: "small grey line" }
      - { label: "`save`", tone: hot, step: 1 }                  # tones: hot, err, ok, key
      - { label: "`lookup(key)`", branch: { label: "500", tone: err, step: 2 } }
  errs:
    kind: snippet                    # monospace block
    title: Real output
    wrap: true
    lines: ["plain line", { text: "added", tone: add }, { text: "removed", tone: del }, { text: "highlight", tone: warn }]
  flow:
    kind: sequence                   # message ladder: one lifeline per actor
    title: Who sends what
    legend: "{c:A} old · {d:B} new"  # optional line under the title
    actors: [MME, HSS, S-CSCF]
    rows:
      - { from: MME, to: HSS, label: ULR, tag: "{d:IMSI B}", sub: "small grey line under the arrow" }
      - { from: HSS, to: MME, label: CLA, dashed: true, tone: err, step: 1 }   # tones: err, ok, dim
      - { note: "box on a lifeline", at: HSS, tone: hot, step: 1 }             # at: one actor or a list to span
      - { divider: "this PR", step: 2 }                                         # full-width section label
  custom:
    kind: html                       # escape hatch; use data-step="k" / data-hl="k" on children
    html: "<div data-step='1'>...</div>"
```

## Summary scenes

```yaml
  - kind: summary
    chapter: Verdict
    title: Good structure; settle two things first
    tests: [TestFoo, "TestBar  (4 cases)"]
    items:
      - { file: path/to/file.go, marker: "unique line text", text: "The finding, one sentence.", nit: false }
    cues:
      - say: "…[[a]]first thing, [[b]]second."
        do: [{ show: 0, at: a }, { show: 1, at: b }]
```

At most 4 items fit. Fold nits into one item. Item refs resolve to the first line containing `marker` in the PR-head version of the file.
