---
name: session-render
description: Render a single Claude Code session transcript (.jsonl) as a self-contained, minimal HTML file — left-aligned threaded log with syntax-highlighted code/bash and collapsible tool output, no external assets. Use when the user wants to view, export, share, or "render" a session by id or .jsonl path as HTML.
user-invocable: true
allowed-tools:
  - Bash(uv run *)
  - Bash(open *)
  - Bash(find *)
  - Read
---

# session-render

Turns one Claude Code session log into a standalone HTML page you can open, archive, or email. The output is **fully self-contained** — syntax highlighting is done at build time with Pygments, so there is zero CDN/JS dependency and it works offline.

## How to run

Dependencies (`markdown`, `pygments`) are declared inline via [PEP 723](https://peps.python.org/pep-0723/) script metadata, so `uv run` installs them automatically — no `--with` flags needed:

```bash
uv run "${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")}/render.py" \
  <session-id | path/to/session.jsonl> [output.html]
```

- First arg: a bare **session id** (resolved by globbing `~/.claude/projects/**/<id>*.jsonl`) or a direct **path** to a `.jsonl`.
- Second arg (optional): output path. Defaults to `/tmp/claude-session-<id>.html`.
- `--claude-dir DIR` overrides the search root (defaults to `$CLAUDE_DIR` or `~/.claude`).

Then open it: `open <output.html>` (macOS) / `xdg-open` (Linux).

## Layout

Left-aligned threaded log, intentionally minimal (no avatars/bubbles/shadows):

- **You** and **Claude** messages are top-level, rendered from markdown.
- **Tool calls** nest one level deeper under a hairline guide rail: a monospace caption (`› Bash — description`), the syntax-highlighted command, and the output. Tool output is **expanded by default** (collapsible); errors are flagged red.
- **Thinking** and **injected skill/context** blocks are collapsed `<details>`.
- Model-fallback and refusal notices render as quiet left-aligned system notes.
- Sticky-free header shows title, session id, start/end time, and model(s).

## Notes

- Empty/stripped `thinking` blocks are skipped.
- `tool_use` ↔ `tool_result` are linked by id, so each result renders inline under its call.
- Bash commands highlight with the bash lexer; fenced code blocks highlight by their language; `Write` content highlights by file extension. Plain stdout is shown as-is in a dark block.
- Code style is `github-dark` (set `PYG_STYLE` near the top of `render.py` to change it).
- The script prints the output path, byte size, and block count when done.

## Finding a session id

```bash
# by recent activity
ls -t ~/.claude/projects/*/*.jsonl | head
# by title/content
grep -l "some text" ~/.claude/projects/*/*.jsonl
```
