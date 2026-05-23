---
name: slack-copy
description: Put formatted content on the macOS clipboard so it renders correctly when pasted into a Slack message composer (bold/italic/lists via HTML, tables via TSV).
user-invocable: true
allowed-tools:
  - Bash(pbcopy*)
  - Bash(pbpaste*)
  - Bash(osascript*)
  - Bash(uv run*)
  - Bash(textutil*)
  - Read
  - Write
---

# Slack Copy — paste formatted content into Slack messages

Slack's macOS desktop message composer is picky about what it renders from a pasted clipboard. Empirically verified behavior:

| Format | Slack message composer? | How to put it on the clipboard |
|---|---|---|
| Bold, italic, strikethrough | ✅ via HTML | `<b>`, `<i>`, `<s>` on `public.html` |
| Bulleted / numbered list | ✅ via HTML | `<ul><li>`, `<ol><li>` |
| Inline code | ✅ via HTML | `<code>` |
| Code block | ✅ via HTML | `<pre>` (line breaks preserved; `<pre><code>` is equivalent — no syntax highlighting either way) |
| Blockquote | ✅ via HTML | `<blockquote>` (renders with the left bar; `<code>` inside it works) |
| Links | ✅ via HTML | `<a href="…">…</a>` |
| **Table** | ❌ HTML `<table>` is stripped | **Use tab-separated plain text** (TSV). Slack renders TSV as a table. |
| Headings, colors, custom fonts | ❌ | Not supported in the composer; use a canvas. |

Three big gotchas:

1. **`<meta charset="utf-8">` prefix breaks Slack's HTML paste handler** — strip it. Inline-formatting HTML must start directly with the first element (`<b>`, `<ul>`, etc.).
2. **Tables: Slack ignores HTML `<table>` entirely**. The way to get a table is plain-text **TSV** (cells separated by `\t`, rows by `\n`). Put that on `public.utf8-plain-text`. (HTML can still be on the same pasteboard with a separate marker; Slack will pick plain text for tables.)
3. **Slack collapses block-level whitespace** — `<p>` and the implicit block break around `<ul>`/`<ol>` are *not* honored. To get visible section breaks you must insert explicit `<br>` tags: `<br>` after a header before its list, and `<br><br>` for a blank line between sections. Without them, "What changed" runs straight into the first list item.
4. **Inline whitespace from your source HTML *is* honored** — opposite problem from #3. Slack preserves:
   - whitespace immediately after `<br>` (a newline-then-indent in your heredoc shows up as a leading space on the next line)
   - the trailing newline at the end of the HTML (adds an empty line at the bottom)

   Easiest fixes: keep HTML on a single line with no whitespace between `<br>` and the next token, and write the file with `printf '%s'` (no trailing newline) instead of `cat <<'EOF'` (which always appends one). If you must use a heredoc for readability, post-process: `tr -d '\n' < raw.html > clean.html` or `sed -E 's/<br>[[:space:]]+/<br>/g'`.

When the user asks to "put X on the clipboard for Slack", "copy a table for Slack", "format for Slack", or invokes `/slack-copy`, use this skill.

## How to put data on the macOS clipboard

Two channels matter for Slack: `public.html` (rich) and `public.utf8-plain-text` (fallback). Use `scripts/set-clipboard.py` to set both at once with the right UTIs:

```bash
uv run --with pyobjc-framework-Cocoa \
  python ~/dev/skills/skills/slack-copy/scripts/set-clipboard.py \
  <html-file> <plain-text-file>
```

The script clears the pasteboard, writes both representations to one `NSPasteboardItem`, and prints the resulting types so you can verify.

For TSV-only (tables), `pbcopy < table.tsv` is enough — Slack only needs the plain channel for tables.

## Recipes

### Rich text (bold / italic / list / links / code)

Generate an HTML fragment with no `<meta>` or `<html>` wrapper, and a clean plain-text fallback:

Note the `<br>` after `Affected:` — without it, "Affected:" runs straight into the first bullet.

```bash
cat > /tmp/msg.html <<'EOF'
<b>Heads up:</b> the rollout is delayed.<br><br>
<b>Affected</b><br>
<ul>
<li>service <code>provisioner</code> in <i>prod2</i></li>
<li>see <a href="https://example/ticket">ticket 1234</a></li>
</ul>
EOF
cat > /tmp/msg.txt <<'EOF'
Heads up: the rollout is delayed. Affected:
- service `provisioner` in prod2
- see ticket 1234 (https://example/ticket)
EOF
uv run --with pyobjc-framework-Cocoa \
  python ~/dev/skills/skills/slack-copy/scripts/set-clipboard.py /tmp/msg.html /tmp/msg.txt
```

### Table (TSV)

Just tabs and newlines. Header row optional. `pbcopy` is enough:

```bash
{
  printf 'Time (UTC)\tService\tStatus\n'
  printf '23:22:16\tprovisioner\tdegraded\n'
  printf '23:36:10\trgw\trecovered\n'
} | pbcopy
```

Or pass through `column -s$'\t' -t` first only if you want it human-readable in a terminal — don't do that for the clipboard, Slack needs literal tabs.

### Mixed message (text + table)

Slack doesn't render an HTML table even when other HTML works, so a single paste can't include both styled text *and* a table. Options:

- Paste the text first, then the table on a second paste.
- Or put the leading text in plain Slack markup (`*bold*`, `_italic_`, `\`code\``) typed inline, and only paste the TSV.
- For a single artifact, paste into a Slack **canvas** instead — canvases render markdown tables natively, so a pipe-style `| a | b |` table works.

## Debugging "did Slack honor my HTML?"

When experimenting with what Slack accepts, **always use different content for HTML vs plain-text fallback** so you can tell which one ended up in the message. If you put the same text in both and a "table" renders, you can't tell whether Slack honored your HTML or just rendered tab-separated plain text. Use a sentinel:

```bash
printf '<b>HTML-BRANCH</b>' > /tmp/h
printf 'PLAIN-BRANCH'      > /tmp/p
uv run --with pyobjc-framework-Cocoa \
  python ~/dev/skills/skills/slack-copy/scripts/set-clipboard.py /tmp/h /tmp/p
```

After pasting, "HTML-BRANCH" bolded = HTML honored. "PLAIN-BRANCH" plain = HTML rejected, fallback used.

To inspect what's actually on the pasteboard:

```bash
osascript -e 'clipboard info'   # lists UTIs and sizes
pbpaste                          # plain text
osascript -e 'try
  return (the clipboard as «class HTML») as string
on error e
  return "no HTML: " & e
end try'
```

## Things that DON'T work (so don't try them)

- Heredocs with newlines between elements — newlines after `<br>` show as leading spaces; trailing newline shows as an empty final line. Use `printf '%s' '…all-on-one-line…'` or post-process.
- HTML `<table>` in a message composer — always stripped.
- RTF (`textutil -convert rtf`) on `public.rtf` — Slack ignores it for tables.
- HTML with `<meta charset>` prefix — breaks even simple `<b>`/`<i>` rendering.
- HTML with `<thead>`/`<tbody>` wrappers — doesn't help (and `<table>` is stripped anyway).
- Word-flavored HTML with `<!--StartFragment-->` markers — Slack still strips tables.
- `<p>` for paragraph breaks — Slack collapses them. Use `<br>` / `<br><br>` instead.
- Relying on `<ul>`/`<ol>` to introduce vertical space — Slack jams the preceding text right against the first list item. Insert `<br>` between them.

## Slack canvases (different surface)

If the user is pasting into a Slack **canvas** rather than a message, the rules differ — canvases render markdown natively, including pipe-style tables. For canvas paste, generate markdown:

```markdown
| col1 | col2 |
|---|---|
| a | b |
```

and `pbcopy` it.
