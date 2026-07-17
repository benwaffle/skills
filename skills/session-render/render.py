#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "markdown>=3.5",
#     "pygments>=2.17",
# ]
# ///
"""Render a Claude Code session .jsonl transcript into a self-contained HTML file.

Usage:
    render.py <session-id | path/to/session.jsonl> [output.html] [--claude-dir DIR]

- A bare session id is resolved by globbing <claude-dir>/projects/**/<id>*.jsonl.
- output.html defaults to /tmp/claude-session-<id>.html.
- --claude-dir defaults to $CLAUDE_DIR or ~/.claude.
"""
import json, sys, os, glob, html, re, datetime

def _resolve_src_out(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    claude_dir = os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude")
    for i, a in enumerate(argv):
        if a == "--claude-dir" and i + 1 < len(argv):
            claude_dir = argv[i + 1]
    if not args:
        sys.exit("usage: render.py <session-id|path.jsonl> [output.html] [--claude-dir DIR]")
    target = args[0]
    if os.path.isfile(target):
        src = target
    else:
        sid = target[:-6] if target.endswith(".jsonl") else target
        matches = sorted(glob.glob(os.path.join(claude_dir, "projects", "**", sid + "*.jsonl"),
                                   recursive=True))
        if not matches:
            sys.exit("No session .jsonl found for %r under %s/projects" % (target, claude_dir))
        src = matches[0]
        if len(matches) > 1:
            sys.stderr.write("note: %d matches, using %s\n" % (len(matches), src))
    sid = os.path.splitext(os.path.basename(src))[0]
    out = args[1] if len(args) > 1 else os.path.join("/tmp", "claude-session-%s.html" % sid)
    return src, out

SRC, OUT = _resolve_src_out(sys.argv)

import markdown as md
from pygments import highlight as pyg_highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import HtmlFormatter
from pygments.util import ClassNotFound

PYG_STYLE = "github-dark"
_formatter = HtmlFormatter(style=PYG_STYLE, cssclass="hl", nowrap=False)
PYGMENTS_CSS = _formatter.get_style_defs(".hl")

_EXT_LANG = {".py": "python", ".js": "javascript", ".ts": "typescript", ".java": "java",
             ".sh": "bash", ".json": "json", ".html": "html", ".css": "css", ".go": "go",
             ".rs": "rust", ".yml": "yaml", ".yaml": "yaml", ".md": "markdown", ".sql": "sql",
             ".xml": "xml", ".c": "c", ".cpp": "cpp", ".rb": "ruby", ".kt": "kotlin"}

def _lang_from_path(path):
    import os
    return _EXT_LANG.get(os.path.splitext(path or "")[1].lower(), "text")

def highlight_code(code, lang):
    """Syntax-highlight a code string -> <div class='hl'><pre>...</pre></div>."""
    try:
        lexer = get_lexer_by_name(lang, stripnl=False)
    except ClassNotFound:
        return '<div class="hl"><pre>%s</pre></div>' % html.escape(code)
    return pyg_highlight(code, lexer, _formatter)

def render_md(text):
    if not text:
        return ""
    return md.markdown(text, extensions=["fenced_code", "tables", "sane_lists", "nl2br", "codehilite"],
                       extension_configs={"codehilite": {"css_class": "hl", "guess_lang": False,
                                                          "pygments_style": PYG_STYLE}})

def esc(s):
    return html.escape(s if isinstance(s, str) else json.dumps(s))

def fmt_ts(ts):
    if not ts:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts

def fmt_time(ts):
    if not ts:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except Exception:
        return ""

# ---- load ----
entries = []
with open(SRC) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))

# Map tool_use_id -> result text (+ is_error)
results = {}
for e in entries:
    if e.get("type") == "user":
        content = e.get("message", {}).get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    c = block.get("content")
                    if isinstance(c, list):
                        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
                    results[block.get("tool_use_id")] = {
                        "text": c if isinstance(c, str) else json.dumps(c, indent=2),
                        "is_error": bool(block.get("is_error")),
                    }

# Metadata
title = next((e.get("aiTitle") for e in entries if e.get("type") == "ai-title"), "Claude Code Session")
session_id = next((e.get("sessionId") for e in entries if e.get("sessionId")), "")
ts_all = sorted([e.get("timestamp") for e in entries if e.get("timestamp")])
ts_start = fmt_ts(ts_all[0]) if ts_all else ""
ts_end = fmt_ts(ts_all[-1]) if ts_all else ""
models = sorted({e.get("message", {}).get("model") for e in entries
                 if e.get("type") == "assistant" and e.get("message", {}).get("model")})
cwd = next((e.get("cwd") for e in entries if e.get("cwd")), "")

def render_tool_input(name, inp):
    """Return (header_html, body_html) for a tool_use input."""
    if not isinstance(inp, dict):
        return "", "<pre>%s</pre>" % esc(str(inp))
    if name == "Bash":
        cmd = inp.get("command", "")
        desc = inp.get("description", "")
        head = esc(desc) if desc else ""
        body = '<div class="cmd">%s</div>' % highlight_code(cmd, "bash")
        return head, body
    if name == "Skill":
        skill = inp.get("skill", "")
        args = inp.get("args", "")
        return esc(skill), ('<div class="argline">%s</div>' % esc(args) if args else "")
    if name in ("Read", "Write", "Edit"):
        fp = inp.get("file_path", "")
        rest = {k: v for k, v in inp.items() if k != "file_path"}
        lang = "diff" if name == "Edit" else _lang_from_path(fp)
        body = ""
        for k, v in rest.items():
            if name == "Write" and k == "content":
                body += '<div class="kv"><span class="k">%s</span>%s</div>' % (esc(k), highlight_code(str(v), lang))
            else:
                body += '<div class="kv"><span class="k">%s</span><pre class="out">%s</pre></div>' % (esc(k), esc(str(v)))
        return esc(fp), body
    if name in ("Agent", "Task"):
        sub = inp.get("subagent_type", "")
        desc = inp.get("description", "")
        head = esc(" · ".join(x for x in (sub, desc) if x))
        body = ""
        prompt = inp.get("prompt", "")
        if prompt:
            label = 'prompt <span class="rmeta">%d lines</span>' % (prompt.count("\n") + 1)
            body += ('<details class="result" open><summary>%s</summary>'
                     '<div class="md subprompt">%s</div></details>' % (label, render_md(prompt)))
        rest = {k: v for k, v in inp.items()
                if k not in ("subagent_type", "description", "prompt")}
        if rest:
            body += highlight_code(json.dumps(rest, indent=2), "json")
        return head, body
    # generic
    return "", highlight_code(json.dumps(inp, indent=2), "json")

# Build conversation blocks in file order
blocks = []
for e in entries:
    t = e.get("type")
    if t == "system" and e.get("subtype") == "model_refusal_fallback":
        fb = e.get("fallbackModel") or "another model"
        blocks.append(('notice', e.get("content", ""), fb))
    elif t == "assistant":
        for c in e.get("message", {}).get("content", []):
            ct = c.get("type")
            if ct == "fallback":
                frm = (c.get("from") or {}).get("model", "")
                to = (c.get("to") or {}).get("model", "")
                blocks.append(('fallback', frm, to))
            elif ct == "thinking":
                if c.get("thinking", "").strip():
                    blocks.append(('thinking', c.get("thinking")))
            elif ct == "text":
                if c.get("text", "").strip():
                    blocks.append(('assistant_text', c.get("text"), e.get("timestamp")))
            elif ct == "tool_use":
                blocks.append(('tool_use', c.get("name"), c.get("input"), c.get("id")))
    elif t == "user":
        content = e.get("message", {}).get("content")
        origin_kind = (e.get("origin") or {}).get("kind")
        is_system = e.get("promptSource") == "system" or (origin_kind and origin_kind != "human")
        if isinstance(content, str):
            if e.get("isCompactSummary"):
                blocks.append(('sys_msg', content, e.get("timestamp"), "Session continued from compacted summary"))
            elif is_system:
                blocks.append(('sys_msg', content, e.get("timestamp"), None))
            else:
                blocks.append(('user', content, e.get("timestamp")))
        elif isinstance(content, list):
            for c in content:
                if c.get("type") == "text" and e.get("isMeta"):
                    blocks.append(('meta_inject', c.get("text"), e.get("attributionSkill") or e.get("sourceToolUseID")))
                # tool_result handled via results map (inline w/ tool_use)

# ---- emit HTML ----
parts = []

def entry(who, who_cls, ts, body_html, anchor=None):
    t = fmt_time(ts)
    if t and anchor:
        time_html = '<a class="time" href="#%s">%s</a>' % (anchor, t)
    elif t:
        time_html = '<span class="time">%s</span>' % t
    else:
        time_html = ''
    aid = ' id="%s"' % anchor if anchor else ''
    return ('<div class="entry %s"%s><div class="role %s">%s%s</div>'
            '<div class="ebody md">%s</div></div>' % (who_cls, aid, who_cls, who, time_html, body_html))

ucount = 0
for b in blocks:
    kind = b[0]
    if kind == 'user':
        ucount += 1
        parts.append(entry("You", "u", b[2], render_md(b[1]), anchor="u%d" % ucount))
    elif kind == 'assistant_text':
        parts.append(entry("Claude", "c", b[2], render_md(b[1])))
    elif kind == 'thinking':
        parts.append('<div class="nested"><details class="thinking"><summary>Thought for a moment</summary><div class="md">%s</div></details></div>'
                     % render_md(b[1]))
    elif kind == 'tool_use':
        name, inp, tid = b[1], b[2], b[3]
        head, body = render_tool_input(name, inp)
        res = results.get(tid)
        res_html = ""
        if res:
            cls = "result error" if res["is_error"] else "result"
            txt = res["text"] or "(empty)"
            nlines = txt.count("\n") + 1
            verb = "error" if res["is_error"] else "output"
            label = '%s <span class="rmeta">%d lines</span>' % (verb, nlines)
            if name in ("Agent", "Task") and not res["is_error"]:
                label = 'response <span class="rmeta">%d lines</span>' % nlines
                res_html = ('<details class="result" open><summary>%s</summary>'
                            '<div class="md subresp">%s</div></details>' % (label, render_md(txt)))
            else:
                res_html = ('<details class="%s" open><summary>%s</summary><pre class="out">%s</pre></details>'
                            % (cls, label, esc(txt)))
        sub = ' <span class="thead">%s</span>' % head if head else ''
        parts.append('<div class="nested"><details class="tool">'
                     '<summary class="tcap"><span class="tname">%s</span>%s</summary>%s%s</details></div>'
                     % (esc(name), sub, body, res_html))
    elif kind == 'fallback':
        parts.append('<div class="banner">Model switched: <code>%s</code> &rarr; <code>%s</code></div>'
                     % (esc(b[1]), esc(b[2])))
    elif kind == 'notice':
        parts.append('<div class="banner muted">%s</div>' % esc(b[1]))
    elif kind == 'sys_msg':
        txt = b[1]
        m = re.search(r'<summary>(.*?)</summary>', txt, re.S)
        label = esc(b[3]) if b[3] else (esc(m.group(1).strip()) if m else "System message")
        m = re.search(r'<result>(.*?)</result>', txt, re.S)
        if m:
            body = '<div class="md subresp">%s</div>' % render_md(m.group(1).strip())
        else:
            body = '<pre class="out">%s</pre>' % esc(txt)
        parts.append('<div class="nested"><details class="meta"><summary>%s</summary>%s</details></div>'
                     % (label, body))
    elif kind == 'meta_inject':
        label = esc(str(b[2] or "context"))
        parts.append('<div class="nested"><details class="meta"><summary>Injected context (%s)</summary><pre class="out">%s</pre></details></div>'
                     % (label, esc(b[1])))

conversation = "\n".join(parts)

meta_rows = []
if session_id: meta_rows.append(("Session ID", session_id))
if ts_start: meta_rows.append(("Started", ts_start))
if ts_end: meta_rows.append(("Ended", ts_end))
if models: meta_rows.append(("Model(s)", ", ".join(models)))
if cwd: meta_rows.append(("Working dir", cwd))
meta_html = "".join('<div class="metarow"><b>%s</b><code>%s</code></div>' % (esc(k), esc(v))
                    for k, v in meta_rows)

HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>
:root{
 --bg:#fff; --text:#1c1c1c; --muted:#6f6f6f; --faint:#a6a6a6;
 --line:#ececec; --code:#0d1117;
 --user:#3a5ccc; --claude:#b06440; --err:#b3392f;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%%;}
body{margin:0;background:var(--bg);color:var(--text);
 font:15.5px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
 -webkit-font-smoothing:antialiased;}
.wrap{max-width:1080px;margin:0 auto;padding:0 24px 110px;}

header{padding:40px 0 20px;margin-bottom:8px;border-bottom:1px solid var(--line);}
header h1{margin:0 0 8px;font-size:18px;line-height:1.35;font-weight:600;letter-spacing:-.01em;}
.meta{font-size:12px;color:var(--faint);line-height:1.8;}
.meta .metarow{margin-right:6px;}
.meta .metarow:not(:last-child)::after{content:"·";margin-left:6px;color:var(--line);}
.meta b{font-weight:500;} .meta code{font:12px ui-monospace,Menlo,monospace;color:var(--muted);}

/* messages: top level, left aligned */
.entry{margin:26px 0;}
.role{font-size:11px;font-weight:600;letter-spacing:.09em;text-transform:uppercase;
 color:var(--faint);margin-bottom:6px;}
.role.u{color:var(--user);} .role.c{color:var(--claude);}
.entry.u{background:#eef2fc;border:1px solid #dbe3f8;border-radius:8px;padding:12px 16px;}
.role .time{float:right;font-weight:400;letter-spacing:0;color:var(--faint);text-transform:none;}
.role a.time:hover{color:var(--user);text-decoration:underline;}
.entry{scroll-margin-top:16px;}
.entry:target{outline:2px solid #b9c7f0;outline-offset:4px;border-radius:8px;}
.ebody{margin:0;}

/* nested level: tool calls, thinking, injected context */
.nested{padding:0 0 0 18px;border-left:1px solid var(--line);}
.nested + .nested{margin-top:22px;}
.nested>details[open]{padding-bottom:7px;}
/* summaries carry the block padding so the whole strip is clickable */
.nested details>summary{display:block;margin-left:-18px;padding:7px 0 7px 18px;}
.nested details>summary:hover{background:#f5f6f8;}
/* sections inside a tool call (prompt/response/output) indent under the header */
details.tool>details{margin-left:18px;}
details.tool>details>summary{margin-left:-36px;padding-left:36px;}

.md p:first-child{margin-top:0;} .md p:last-child{margin-bottom:0;}
.md h1,.md h2,.md h3{font-size:1.03em;margin:1em 0 .4em;font-weight:600;}
.md ul,.md ol{padding-left:20px;margin:.5em 0;} .md li{margin:.15em 0;}
.md table{border-collapse:collapse;margin:9px 0;font-size:.95em;}
.md th,.md td{border:1px solid var(--line);padding:5px 10px;text-align:left;}
.md code{background:#f3f3f3;padding:1px 5px;border-radius:4px;
 font:13px ui-monospace,SFMono-Regular,Menlo,monospace;}
.hl code,.md pre code{background:none;color:inherit;padding:0;border-radius:0;font-size:inherit;}
.md blockquote{border-left:2px solid var(--line);margin:.6em 0;padding:0 0 0 14px;color:var(--muted);}

/* code blocks (pygments wraps in .hl) */
.hl{border-radius:6px;overflow:hidden;margin:.7em 0;}
.hl pre{margin:0;padding:12px 14px;overflow:auto;white-space:pre-wrap;word-break:break-word;
 font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.md .hl:first-child{margin-top:0;} .md .hl:last-child{margin-bottom:0;}

/* tool calls — minimal */
.tcap{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);
 margin-bottom:5px;white-space:pre-wrap;word-break:break-word;}
.tname{color:var(--text);font-weight:600;}
.tname::before{content:"▸ ";color:var(--faint);}
details[open].tool>.tcap .tname::before{content:"▾ ";}
.thead{color:#4a4a4a;}
.tool .hl{margin:0;}
.argline{font:13px ui-monospace,Menlo,monospace;color:var(--text);margin-bottom:4px;}
.kv .k{display:block;font-size:10.5px;font-weight:600;letter-spacing:.05em;
 text-transform:uppercase;color:var(--faint);margin:6px 0 3px;}
pre.out{margin:0;padding:11px 14px;background:var(--code);color:#c9d1d9;border-radius:6px;
 overflow:auto;max-height:380px;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 white-space:pre-wrap;word-break:break-word;}
details.result,details.error{margin-top:6px;}
details summary{cursor:pointer;list-style:none;user-select:none;}
details>summary::-webkit-details-marker{display:none;}
details.result>summary,details.error>summary{font:11px ui-monospace,Menlo,monospace;color:var(--faint);}
details.result>summary::before,details.error>summary::before{content:"▸ ";}
details[open].result>summary::before,details[open].error>summary::before{content:"▾ ";}
details.result>summary:hover,details.error>summary:hover{color:var(--muted);}
details[open].result>summary,details[open].error>summary{margin-bottom:5px;}
.rmeta{opacity:.7;}
details.error>summary{color:var(--err);}

/* subagent (Agent/Task) prompt + response, rendered as markdown */
.subprompt,.subresp{font-size:14px;line-height:1.6;}
.subprompt{border-left:2px solid var(--line);padding-left:12px;margin-bottom:4px;}
.subresp{padding:2px 0;}

/* thinking + injected context — minimal */
details.thinking>summary,details.meta>summary{color:var(--faint);font-size:12.5px;
 font-style:italic;}
details.thinking>summary::before,details.meta>summary::before{content:"▸ ";font-style:normal;}
details[open].thinking>summary::before,details[open].meta>summary::before{content:"▾ ";}
details.thinking>summary:hover,details.meta>summary:hover{color:var(--muted);}
details.thinking>.md{padding:7px 0 2px;color:var(--muted);font-style:italic;}
details.meta>pre{margin:7px 0 0;padding:11px 14px;background:var(--code);color:#c9d1d9;border-radius:6px;
 overflow:auto;font:11.5px/1.5 ui-monospace,Menlo,monospace;max-height:300px;
 white-space:pre-wrap;word-break:break-word;}

/* system note — left aligned, quiet */
.banner{font-size:12.5px;color:var(--muted);margin:20px 0;padding:0 0 0 14px;
 border-left:2px solid var(--line);line-height:1.6;}
.banner code{font:12px ui-monospace,Menlo,monospace;color:var(--text);}
a{color:var(--user);text-decoration:none;} a:hover{text-decoration:underline;}
footer{color:var(--faint);font-size:12px;margin-top:52px;padding-top:18px;border-top:1px solid var(--line);}
@media(max-width:640px){.nested{padding-left:13px;}}
/* ---- pygments (%(pygstyle)s) ---- */
%(pygcss)s
</style></head><body><div class="wrap">
<header>
 <h1>%(title)s</h1>
 <div class="meta">%(meta)s</div>
</header>
%(conversation)s
<footer>Rendered from session transcript &middot; %(n)d entries</footer>
</div></body></html>
""" % {"title": esc(title), "meta": meta_html, "conversation": conversation, "n": len(entries),
       "pygcss": PYGMENTS_CSS, "pygstyle": PYG_STYLE}

with open(OUT, "w") as f:
    f.write(HTML)
print("Wrote", OUT, "(%d bytes, %d blocks)" % (len(HTML), len(blocks)))
