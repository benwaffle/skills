---
name: claude-stats
description: Generate an interactive HTML report of your Claude Code usage (sessions, messages, tokens, active days, streaks, peak times, model usage by day, top projects/tools) by parsing ~/.claude/projects/*.jsonl. Use when the user asks for their Claude stats, usage, activity, streaks, token breakdown, or wants to see how they use Claude over time.
user-invocable: true
allowed-tools:
  - Bash(python3 *)
  - Bash(open *)
  - Read
---

# claude-stats

Builds a single self-contained HTML report from the user's local Claude Code conversation logs.

## How to run

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")}/build_stats.py" [OUTPUT_HTML] [CLAUDE_DIR]
```

- `OUTPUT_HTML` — where to write the report. Defaults to `./claude_stats.html`.
- `CLAUDE_DIR` — Claude config directory. Defaults to `$CLAUDE_DIR` or `~/.claude`.

The script writes a sidecar `.json` next to the HTML (same basename) for debugging or re-use.

After running, open the HTML in the browser: `open <output_html>` (macOS) or `xdg-open` (Linux).

## What it reports

- Totals: sessions, user/assistant messages, tokens (input/output/cache-create/cache-read).
- Timeline: daily activity, token usage over time.
- **Model usage by day** (stacked bar, one color per model).
- Peak hours + peak weekdays, hour×weekday heatmap.
- Streaks: longest + current (computed over all time, not the current filter).
- Top projects, top tools, top models.
- Session table sortable by duration / messages / tokens.

## Interactive filtering

The report has a filter bar: **All time · By month · By week · Last 30d · Last 7d**. Selecting *By month* or *By week* reveals clickable chips (one per bucket, showing its message count). All charts, cards, and tables rescope to the selection.

Streaks intentionally stay all-time — streak length inside a single week is meaningless.

## Data source notes

- Scans every `*.jsonl` under `<CLAUDE_DIR>/projects/` recursively (includes subagent logs).
- Timestamps are converted to **local time** for hour-of-day / weekday bucketing.
- Excludes `<synthetic>` assistant messages from model tallies (they have no real usage).
- Distinguishes genuine user prompts from tool-result blocks (both have `type: "user"` but tool results aren't counted as user messages).
- Tool usage is counted per `tool_use` block in assistant messages.

## Typical invocation pattern

When the user asks for "my Claude stats" or "usage report":

1. Run the script to a temp path, e.g. `/tmp/claude_stats.html`.
2. Summarize the top-level numbers in chat (sessions, messages, tokens, active days, streaks, peak hour/weekday).
3. `open` the HTML so the user can explore interactively.

The report is fully client-side — Chart.js is loaded from jsdelivr CDN. No data leaves the machine.
