#!/usr/bin/env python3
"""Aggregate Claude Code usage stats and emit an interactive HTML report.

Usage: build_stats.py [OUTPUT_HTML] [CLAUDE_DIR]
  OUTPUT_HTML  defaults to ./claude_stats.html
  CLAUDE_DIR   defaults to $CLAUDE_DIR or ~/.claude
"""
import argparse
import json
import os
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta, date as date_cls
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("output", nargs="?", default="claude_stats.html",
                help="Output HTML path (default: ./claude_stats.html)")
ap.add_argument("claude_dir", nargs="?",
                default=os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")),
                help="Path to ~/.claude (default: $CLAUDE_DIR or ~/.claude)")
args = ap.parse_args()

ROOT = Path(args.claude_dir) / "projects"
OUT = Path(args.output).resolve()
if not ROOT.exists():
    sys.exit(f"error: projects directory not found: {ROOT}")

# Totals
total_lines = 0
total_files = 0
user_messages = 0
assistant_messages = 0
tool_results = 0
sidechain_messages = 0
synthetic_messages = 0
first_ts = None
last_ts = None

# Per-day granular stats so client can aggregate to any window
# day["YYYY-MM-DD"] = {
#   messages, user_messages, tokens:{in,out,cc,cr},
#   hourly:[24], sessions:set,
#   models:{model: {messages, tokens}},
#   projects:{project: messages},
#   tools:{tool: count},
# }
def new_day():
    return {
        "messages": 0,
        "user": 0,
        "assistant": 0,
        "tokens": {"in": 0, "out": 0, "cc": 0, "cr": 0},
        "hourly": [0]*24,
        "sessions": set(),
        "models": defaultdict(lambda: {"messages": 0, "tokens": 0}),
        "projects": defaultdict(int),
        "tools": defaultdict(int),
        "skills": defaultdict(int),
    }

daily = defaultdict(new_day)

# Session metadata
sessions = {}  # id -> meta

def parse_ts(s):
    if not s: return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

for path in ROOT.rglob("*.jsonl"):
    total_files += 1
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                total_lines += 1
                line = line.strip()
                if not line: continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue

                ts = parse_ts(rec.get("timestamp"))
                typ = rec.get("type")
                sid = rec.get("sessionId")
                cwd = rec.get("cwd") or "(unknown)"
                is_side = bool(rec.get("isSidechain"))
                msg = rec.get("message") or {}
                model = msg.get("model") or rec.get("model")
                usage = msg.get("usage") or {}

                if is_side:
                    sidechain_messages += 1

                is_user_msg = False
                is_asst_msg = False
                tools_in_msg = []
                skills_in_msg = []

                if typ == "user":
                    content = msg.get("content")
                    is_tool_result = False
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                is_tool_result = True
                                break
                    if is_tool_result:
                        tool_results += 1
                    else:
                        user_messages += 1
                        is_user_msg = True
                elif typ == "assistant":
                    if model == "<synthetic>":
                        synthetic_messages += 1
                    else:
                        assistant_messages += 1
                        is_asst_msg = True
                        content = msg.get("content")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "tool_use":
                                    tname = block.get("name", "unknown")
                                    tools_in_msg.append(tname)
                                    if tname == "Skill":
                                        sk = (block.get("input") or {}).get("skill")
                                        if sk:
                                            skills_in_msg.append(sk)

                if ts and sid:
                    if first_ts is None or ts < first_ts: first_ts = ts
                    if last_ts is None or ts > last_ts: last_ts = ts

                    s = sessions.get(sid)
                    if not s:
                        s = {"first": ts, "last": ts, "project": cwd, "messages": 0, "tokens": 0, "models": set()}
                        sessions[sid] = s
                    if ts < s["first"]: s["first"] = ts
                    if ts > s["last"]: s["last"] = ts
                    if model and model != "<synthetic>":
                        s["models"].add(model)

                    if is_user_msg or is_asst_msg:
                        local_ts = ts.astimezone()
                        date_key = local_ts.date().isoformat()
                        d = daily[date_key]

                        msg_tokens = (
                            (usage.get("input_tokens", 0) or 0)
                            + (usage.get("output_tokens", 0) or 0)
                        )

                        d["messages"] += 1
                        if is_user_msg: d["user"] += 1
                        else: d["assistant"] += 1
                        d["tokens"]["in"] += usage.get("input_tokens", 0) or 0
                        d["tokens"]["out"] += usage.get("output_tokens", 0) or 0
                        d["tokens"]["cc"] += usage.get("cache_creation_input_tokens", 0) or 0
                        d["tokens"]["cr"] += usage.get("cache_read_input_tokens", 0) or 0
                        d["hourly"][local_ts.hour] += 1
                        d["sessions"].add(sid)
                        d["projects"][cwd] += 1
                        for tn in tools_in_msg:
                            d["tools"][tn] += 1
                        for sk in skills_in_msg:
                            d["skills"][sk] += 1

                        if is_asst_msg and model and model != "<synthetic>":
                            total_t = (
                                (usage.get("input_tokens", 0) or 0)
                                + (usage.get("output_tokens", 0) or 0)
                                + (usage.get("cache_creation_input_tokens", 0) or 0)
                                + (usage.get("cache_read_input_tokens", 0) or 0)
                            )
                            dm = d["models"][model]
                            dm["messages"] += 1
                            dm["tokens"] += total_t

                        s["messages"] += 1
                        s["tokens"] += msg_tokens
    except Exception as e:
        print(f"Warning: failed to read {path}: {e}", file=sys.stderr)

# Serialize daily with sets/defaultdicts reduced
daily_list = []
for dkey in sorted(daily.keys()):
    d = daily[dkey]
    daily_list.append({
        "date": dkey,
        "messages": d["messages"],
        "user": d["user"],
        "assistant": d["assistant"],
        "tokens": d["tokens"],
        "hourly": d["hourly"],
        "sessions": len(d["sessions"]),
        "session_ids": list(d["sessions"]),
        "models": {m: v for m, v in d["models"].items()},
        "projects": dict(d["projects"]),
        "tools": dict(d["tools"]),
        "skills": dict(d["skills"]),
    })

# Sessions serialize
sessions_list = []
for sid, s in sessions.items():
    sessions_list.append({
        "id": sid,
        "first": s["first"].isoformat(),
        "last": s["last"].isoformat(),
        "duration_s": (s["last"] - s["first"]).total_seconds(),
        "project": s["project"],
        "messages": s["messages"],
        "tokens": s["tokens"],
        "models": sorted(s["models"]),
    })
sessions_list.sort(key=lambda x: x["first"])

# Collect universe of models for consistent ordering (top-N by total messages)
model_totals = Counter()
for d in daily_list:
    for m, v in d["models"].items():
        model_totals[m] += v["messages"]
top_models = [m for m, _ in model_totals.most_common()]

summary = {
    "total_files": total_files,
    "total_lines": total_lines,
    "total_sessions": len(sessions),
    "user_messages": user_messages,
    "assistant_messages": assistant_messages,
    "tool_results": tool_results,
    "sidechain_messages": sidechain_messages,
    "synthetic_messages": synthetic_messages,
    "first_ts": first_ts.isoformat() if first_ts else None,
    "last_ts": last_ts.isoformat() if last_ts else None,
}

DATA = {
    "summary": summary,
    "daily": daily_list,
    "sessions": sessions_list,
    "all_models": top_models,
}

# Quick console summary
print(f"Files scanned: {total_files}  Lines: {total_lines}  Sessions: {len(sessions)}")
print(f"User msgs: {user_messages}  Assistant msgs: {assistant_messages}")
skill_totals = Counter()
for d in daily_list:
    for sk, c in d["skills"].items():
        skill_totals[sk] += c
print(f"Active days: {len(daily_list)}  Distinct models: {len(top_models)}  Skill invocations: {sum(skill_totals.values())} ({len(skill_totals)} unique)")

# Write JSON for debugging
# Write sidecar JSON next to the HTML for debugging / re-use
json_path = OUT.with_suffix(".json")
with open(json_path, "w") as f:
    json.dump(DATA, f, default=str)

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Claude Usage Report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  :root {
    --bg: #0d1117;
    --panel: #161b22;
    --panel2: #1c2330;
    --border: #2a3240;
    --text: #e6edf3;
    --muted: #8b949e;
    --accent: #d97757;
    --accent2: #f0a080;
    --green: #3fb950;
    --blue: #58a6ff;
    --purple: #bc8cff;
    --red: #f85149;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.5; }
  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 20px; flex-wrap: wrap; gap: 16px; }
  h1 { margin: 0; font-size: 28px; font-weight: 600; }
  h1 .accent { color: var(--accent); }
  .subtitle { color: var(--muted); font-size: 14px; }
  h2 { font-size: 18px; margin: 0 0 16px; font-weight: 600; }
  .grid { display: grid; gap: 16px; }
  .cards { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom: 20px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; }
  .card .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 8px; }
  .card .value { font-size: 28px; font-weight: 700; line-height: 1.1; }
  .card .sub { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .row { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 16px; }
  .row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 900px) { .row, .row-3 { grid-template-columns: 1fr; } }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
  canvas { max-width: 100%; }
  .chart-wrap { position: relative; height: 320px; }
  .chart-wrap.short { height: 240px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  tbody tr:hover { background: var(--panel2); }
  .scroll { max-height: 360px; overflow: auto; }

  /* Filter bar */
  .filterbar { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    padding: 14px 16px; margin-bottom: 20px; display: flex; flex-direction: column; gap: 12px; }
  .filter-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .seg { display: inline-flex; background: var(--panel2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .seg button { background: none; border: none; color: var(--muted); padding: 6px 12px; cursor: pointer; font: inherit; font-size: 13px; }
  .seg button.active { background: var(--accent); color: #fff; }
  .chip { padding: 4px 10px; background: var(--panel2); border: 1px solid var(--border); border-radius: 14px;
    font-size: 12px; cursor: pointer; color: var(--muted); white-space: nowrap; }
  .chip:hover { border-color: var(--accent); color: var(--text); }
  .chip.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .chip .hint { color: rgba(255,255,255,0.7); font-size: 11px; margin-left: 6px; }
  .chip.active .hint { color: rgba(255,255,255,0.85); }
  .filter-label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-right: 4px; }
  .range-info { color: var(--muted); font-size: 13px; }

  .heatmap { display: grid; grid-template-columns: 40px repeat(24, 1fr); gap: 2px; font-size: 10px; color: var(--muted); }
  .heatmap .cell { aspect-ratio: 1 / 1; border-radius: 3px; background: #1c2330; }
  .heatmap .hdr { text-align: center; padding-top: 4px; }
  .heatmap .rowlabel { text-align: right; padding-right: 6px; line-height: 24px; }
  .legend { display: flex; gap: 8px; align-items: center; font-size: 11px; color: var(--muted); margin-top: 12px; }
  .legend .swatch { width: 14px; height: 14px; border-radius: 3px; }
  .pill { display: inline-block; background: var(--panel2); color: var(--muted); padding: 2px 8px; border-radius: 10px; font-size: 11px; }
  .truncate { max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  footer { color: var(--muted); font-size: 12px; margin-top: 40px; text-align: center; }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
  .tab { padding: 8px 14px; cursor: pointer; color: var(--muted); border: none; background: none; font: inherit; border-bottom: 2px solid transparent; }
  .tab.active { color: var(--text); border-bottom-color: var(--accent); }
</style>
</head>
<body>
<div class="container">
  <header>
    <div>
      <h1>claude<span class="accent">stats</span></h1>
      <div class="subtitle" id="subtitle"></div>
    </div>
    <div class="subtitle" id="range"></div>
  </header>

  <div class="filterbar">
    <div class="filter-row">
      <span class="filter-label">Range</span>
      <div class="seg" id="modeSeg">
        <button data-mode="all" class="active">All time</button>
        <button data-mode="month">By month</button>
        <button data-mode="week">By week</button>
        <button data-mode="last30">Last 30d</button>
        <button data-mode="last7">Last 7d</button>
      </div>
      <span class="range-info" id="rangeInfo"></span>
    </div>
    <div class="filter-row" id="chipRow" style="display:none"></div>
  </div>

  <section>
    <div class="grid cards" id="cards"></div>
  </section>

  <div class="row">
    <div class="panel">
      <h2>Model usage by day</h2>
      <div class="chart-wrap" style="height: 360px"><canvas id="modelDailyChart"></canvas></div>
    </div>
    <div class="panel">
      <h2>Breakdown</h2>
      <div id="streakBox"></div>
    </div>
  </div>

  <div class="row">
    <div class="panel">
      <h2>Hour × weekday heatmap</h2>
      <div id="heatmap"></div>
      <div class="legend">
        <span>Less</span>
        <span class="swatch" style="background:#1c2330"></span>
        <span class="swatch" style="background:#3b2a2a"></span>
        <span class="swatch" style="background:#7a3e2a"></span>
        <span class="swatch" style="background:#c05e2a"></span>
        <span class="swatch" style="background:#f5a167"></span>
        <span>More</span>
      </div>
    </div>
    <div class="panel">
      <h2>Peak hours</h2>
      <div class="chart-wrap short"><canvas id="hourChart"></canvas></div>
      <h2 style="margin-top:20px">Peak weekdays</h2>
      <div class="chart-wrap short"><canvas id="dowChart"></canvas></div>
    </div>
  </div>

  <div class="row">
    <div class="panel">
      <h2>Token usage over time</h2>
      <div class="chart-wrap"><canvas id="tokenChart"></canvas></div>
    </div>
    <div class="panel">
      <h2>Token breakdown</h2>
      <div class="chart-wrap"><canvas id="tokenPie"></canvas></div>
    </div>
  </div>

  <div class="row-3">
    <div class="panel">
      <h2>Models</h2>
      <div class="scroll"><table id="modelsTable">
        <thead><tr><th>Model</th><th class="num">Msgs</th><th class="num">Tokens</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
    <div class="panel">
      <h2>Top projects</h2>
      <div class="scroll"><table id="projectsTable">
        <thead><tr><th>Project</th><th class="num">Msgs</th><th class="num">Sessions</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
    <div class="panel">
      <h2>Top tools</h2>
      <div class="scroll"><table id="toolsTable">
        <thead><tr><th>Tool</th><th class="num">Uses</th></tr></thead>
        <tbody></tbody>
      </table></div>
    </div>
  </div>

  <div class="panel" style="margin-top:16px">
    <h2>Skills used</h2>
    <div class="chart-wrap" id="skillsWrap"><canvas id="skillsChart"></canvas></div>
    <div id="skillsEmpty" style="display:none;text-align:center;color:var(--muted);padding:40px 0;font-size:13px">No /skill invocations in this range.</div>
  </div>

  <div class="panel" style="margin-top:16px">
    <div class="tabs">
      <button class="tab active" data-tab="duration_s">Longest sessions</button>
      <button class="tab" data-tab="messages">Most messages</button>
      <button class="tab" data-tab="tokens">Most tokens</button>
    </div>
    <div class="scroll">
      <table id="sessionsTable">
        <thead><tr>
          <th>Date</th><th>Project</th><th class="num">Duration</th>
          <th class="num">Msgs</th><th class="num">Tokens</th><th>Models</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <footer>Report generated __GEN_AT__ — from <code>~/.claude/projects</code></footer>
</div>

<script>
const DATA = __DATA_JSON__;
const HOME = __HOME_JSON__;
const WEEKDAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#2a3240';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

// Stable color palette for models (top-N fixed, rest gray)
const MODEL_COLORS = ['#d97757','#58a6ff','#bc8cff','#3fb950','#f0a080','#58d4ff','#ffd866','#f85149','#a6e3a1','#94e2d5'];
function modelColor(model) {
  const idx = DATA.all_models.indexOf(model);
  if (idx < 0 || idx >= MODEL_COLORS.length) return '#6e7681';
  return MODEL_COLORS[idx];
}
function prettyModel(m) { return m.replace(/^claude-/,'').replace(/-\d{8}$/,''); }

function fmtN(n) {
  if (n == null) return '—';
  const a = Math.abs(n);
  if (a < 1000) return Math.round(n).toString();
  if (a < 1e6) return (n/1e3).toFixed(1) + 'K';
  if (a < 1e9) return (n/1e6).toFixed(1) + 'M';
  if (a < 1e12) return (n/1e9).toFixed(2) + 'B';
  return (n/1e12).toFixed(2) + 'T';
}
function fmtDuration(s) {
  if (!s) return '—';
  if (s < 60) return Math.round(s) + 's';
  if (s < 3600) return Math.round(s/60) + 'm';
  const h = Math.floor(s/3600), m = Math.round((s%3600)/60);
  return h + 'h ' + m + 'm';
}
function fmtHour(h) {
  const suffix = h < 12 ? 'am' : 'pm';
  const hh = ((h + 11) % 12) + 1;
  return hh + suffix;
}
function shortProj(p) {
  return p.replace(HOME, '~/').replace(/^\/private\/tmp\/tmp\./, '/tmp/…');
}
function parseISODate(s) {
  const [y,m,d] = s.split('-').map(Number);
  return new Date(y, m-1, d);
}
function dateOnly(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); }

// ============== Filter state ==============
let mode = 'all'; // all | month | week | last30 | last7
let chipKey = null; // for month: 'YYYY-MM'; for week: 'YYYY-Www'

function monthKey(dateStr) { return dateStr.slice(0, 7); }
function isoWeekKey(dateStr) {
  const d = parseISODate(dateStr);
  // ISO week: Thursday-based
  const tmp = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = tmp.getUTCDay() || 7;
  tmp.setUTCDate(tmp.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil((((tmp - yearStart) / 86400000) + 1) / 7);
  return tmp.getUTCFullYear() + '-W' + String(weekNo).padStart(2, '0');
}

function daysInRange() {
  const all = DATA.daily;
  if (!all.length) return [];
  if (mode === 'all') return all;
  if (mode === 'last30' || mode === 'last7') {
    const n = mode === 'last30' ? 30 : 7;
    const lastDate = parseISODate(all[all.length-1].date);
    const cutoff = new Date(lastDate); cutoff.setDate(cutoff.getDate() - (n-1));
    return all.filter(d => parseISODate(d.date) >= cutoff);
  }
  if (mode === 'month' && chipKey) return all.filter(d => monthKey(d.date) === chipKey);
  if (mode === 'week' && chipKey) return all.filter(d => isoWeekKey(d.date) === chipKey);
  return all;
}

function rangeBounds(days) {
  if (!days.length) return [null, null];
  return [parseISODate(days[0].date), parseISODate(days[days.length-1].date)];
}

// ============== Aggregation ==============
function aggregate(days) {
  const agg = {
    messages: 0, user: 0, assistant: 0,
    tokens: {in:0, out:0, cc:0, cr:0},
    hourly: Array(24).fill(0),
    dow: Array(7).fill(0),
    heatmap: Array.from({length:7}, () => Array(24).fill(0)),
    models: {}, projects: {}, tools: {}, skills: {},
    sessions: new Set(),
    active_days: days.length,
  };
  for (const d of days) {
    agg.messages += d.messages;
    agg.user += d.user;
    agg.assistant += d.assistant;
    agg.tokens.in += d.tokens.in;
    agg.tokens.out += d.tokens.out;
    agg.tokens.cc += d.tokens.cc;
    agg.tokens.cr += d.tokens.cr;
    const dow = (parseISODate(d.date).getDay() + 6) % 7; // Mon=0
    agg.dow[dow] += d.messages;
    for (let h=0; h<24; h++) {
      agg.hourly[h] += d.hourly[h];
      agg.heatmap[dow][h] += d.hourly[h];
    }
    for (const [m, v] of Object.entries(d.models)) {
      if (!agg.models[m]) agg.models[m] = {messages:0, tokens:0};
      agg.models[m].messages += v.messages;
      agg.models[m].tokens += v.tokens;
    }
    for (const [p, c] of Object.entries(d.projects)) {
      if (!agg.projects[p]) agg.projects[p] = {messages:0, sessions:new Set()};
      agg.projects[p].messages += c;
    }
    for (const [t, c] of Object.entries(d.tools)) {
      agg.tools[t] = (agg.tools[t] || 0) + c;
    }
    for (const [s, c] of Object.entries(d.skills || {})) {
      agg.skills[s] = (agg.skills[s] || 0) + c;
    }
    for (const sid of d.session_ids) agg.sessions.add(sid);
  }
  // project sessions: look at sessions that fall in the range
  const [lo, hi] = rangeBounds(days);
  const sessionsIn = DATA.sessions.filter(s => {
    const t = new Date(s.first);
    return (!lo || t >= lo) && (!hi || t <= new Date(hi.getTime() + 86400000));
  });
  for (const s of sessionsIn) {
    if (agg.projects[s.project]) agg.projects[s.project].sessions.add(s.id);
  }
  agg.session_list = sessionsIn;
  return agg;
}

// ============== Streak calc over ALL data ==============
function computeStreaks(days) {
  const dates = days.map(d => parseISODate(d.date));
  if (!dates.length) return {longest: 0, current: 0, longest_start: null, longest_end: null};
  let longest = 1, cur = 1, longestEnd = dates[0], curEnd = dates[0];
  for (let i=1; i<dates.length; i++) {
    if ((dates[i] - dates[i-1]) === 86400000) {
      cur++; curEnd = dates[i];
      if (cur > longest) { longest = cur; longestEnd = curEnd; }
    } else { cur = 1; curEnd = dates[i]; }
  }
  const longestStart = new Date(longestEnd.getTime() - (longest-1)*86400000);
  // current streak
  const today = dateOnly(new Date());
  const dateSet = new Set(days.map(d => d.date));
  let anchor = today;
  if (!dateSet.has(anchor.toISOString().slice(0,10))) {
    const y = new Date(anchor.getTime() - 86400000);
    if (dateSet.has(y.toISOString().slice(0,10))) anchor = y; else return { longest, current: 0, longest_start: longestStart, longest_end: longestEnd };
  }
  let current = 0, d = anchor;
  while (dateSet.has(d.toISOString().slice(0,10))) { current++; d = new Date(d.getTime() - 86400000); }
  return { longest, current, longest_start: longestStart, longest_end: longestEnd };
}

const STREAK = computeStreaks(DATA.daily);

// ============== Render ==============
let hourChart, dowChart, tokenChart, tokenPie, modelDailyChart, skillsChart;

function render() {
  const days = daysInRange();
  const agg = aggregate(days);
  const [lo, hi] = rangeBounds(days);

  // Subtitle
  document.getElementById('subtitle').textContent =
    `${DATA.summary.total_sessions.toLocaleString()} sessions · ${DATA.summary.total_files.toLocaleString()} files · ${DATA.summary.total_lines.toLocaleString()} events`;
  document.getElementById('range').textContent = lo && hi
    ? `${lo.toLocaleDateString()} → ${hi.toLocaleDateString()} · ${days.length} active days`
    : '—';
  document.getElementById('rangeInfo').textContent = lo && hi
    ? `${lo.toLocaleDateString()} → ${hi.toLocaleDateString()}`
    : '';

  // Peaks
  const peakHour = agg.hourly.indexOf(Math.max(...agg.hourly));
  const peakDow = agg.dow.indexOf(Math.max(...agg.dow));
  const toolUsesTotal = Object.values(agg.tools).reduce((a,b)=>a+b, 0);
  const uniqueTools = Object.keys(agg.tools).length;
  const skillUsesTotal = Object.values(agg.skills).reduce((a,b)=>a+b, 0);
  const uniqueSkills = Object.keys(agg.skills).length;
  const tokensTotal = agg.tokens.in + agg.tokens.out + agg.tokens.cc + agg.tokens.cr;
  const avgSessSec = agg.session_list.length
    ? agg.session_list.reduce((a,s)=>a+s.duration_s,0) / agg.session_list.length : 0;
  const sortedDur = agg.session_list.map(s=>s.duration_s).sort((a,b)=>a-b);
  const medSessSec = sortedDur.length ? sortedDur[Math.floor(sortedDur.length/2)] : 0;

  // Cards
  const cards = [
    { label: 'Sessions', value: agg.session_list.length.toLocaleString() },
    { label: 'User messages', value: agg.user.toLocaleString(), sub: `${agg.assistant.toLocaleString()} assistant replies` },
    { label: 'Total tokens', value: fmtN(tokensTotal), sub: `${fmtN(agg.tokens.in + agg.tokens.out)} non-cache` },
    { label: 'Active days', value: agg.active_days.toLocaleString() },
    { label: 'Longest streak', value: STREAK.longest + ' d', sub: mode==='all' && STREAK.longest_start ? `${STREAK.longest_start.toLocaleDateString()} → ${STREAK.longest_end.toLocaleDateString()}` : 'all-time' },
    { label: 'Current streak', value: STREAK.current + ' d', sub: STREAK.current > 0 ? 'keep it going' : '—' },
    { label: 'Peak hour', value: agg.hourly.some(v=>v>0) ? fmtHour(peakHour) : '—', sub: `${agg.hourly[peakHour]?.toLocaleString()||0} events` },
    { label: 'Peak weekday', value: agg.dow.some(v=>v>0) ? WEEKDAYS[peakDow] : '—', sub: `${agg.dow[peakDow]?.toLocaleString()||0} events` },
    { label: 'Tool uses', value: fmtN(toolUsesTotal), sub: `${uniqueTools} unique tools` },
    { label: 'Skill invocations', value: fmtN(skillUsesTotal), sub: `${uniqueSkills} unique skills` },
    { label: 'Avg session', value: fmtDuration(avgSessSec), sub: `median ${fmtDuration(medSessSec)}` },
  ];
  document.getElementById('cards').innerHTML = cards.map(c =>
    `<div class="card"><div class="label">${c.label}</div><div class="value">${c.value}</div><div class="sub">${c.sub||''}</div></div>`
  ).join('');

  // Streak / breakdown box
  const longSess = agg.session_list.slice().sort((a,b)=>b.duration_s-a.duration_s)[0];
  const cacheRatio = (agg.tokens.cr / Math.max(1, agg.tokens.in + agg.tokens.out)).toFixed(1);
  document.getElementById('streakBox').innerHTML = `
    <div style="display:grid;gap:12px;font-size:14px">
      <div><div class="pill">User cadence</div> ${(agg.user / Math.max(1, agg.active_days)).toFixed(1)} msgs / active day</div>
      <div><div class="pill">Msgs / session</div> ${(agg.messages / Math.max(1, agg.session_list.length)).toFixed(1)}</div>
      <div><div class="pill">Tokens / session</div> ${fmtN(tokensTotal / Math.max(1, agg.session_list.length))}</div>
      <div><div class="pill">Cache leverage</div> ${cacheRatio}× cache-read vs. fresh I/O</div>
      <div><div class="pill">Longest session</div> ${longSess ? fmtDuration(longSess.duration_s) + ' · ' + longSess.messages + ' msgs' : '—'}</div>
    </div>
  `;

  // Daily chart
  const dailyLabels = days.map(d => d.date);

  // Model usage by day — stacked bar
  const modelsInRange = Object.keys(agg.models).sort((a,b) => agg.models[b].messages - agg.models[a].messages);
  modelDailyChart && modelDailyChart.destroy();
  modelDailyChart = new Chart(document.getElementById('modelDailyChart'), {
    type: 'bar',
    data: {
      labels: dailyLabels,
      datasets: modelsInRange.map(m => ({
        label: prettyModel(m),
        data: days.map(d => (d.models[m]?.messages) || 0),
        backgroundColor: modelColor(m),
        borderWidth: 0,
      }))
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 10, font: { size: 11 } } },
        tooltip: { mode: 'index', intersect: false }
      },
      scales: {
        x: { type: 'time', time: { unit: days.length > 90 ? 'month' : days.length > 21 ? 'week' : 'day' }, stacked: true, grid: { display: false } },
        y: { beginAtZero: true, stacked: true, grid: { color: '#1c2330' } }
      }
    }
  });

  // Hour chart
  hourChart && hourChart.destroy();
  hourChart = new Chart(document.getElementById('hourChart'), {
    type: 'bar',
    data: { labels: Array.from({length:24}, (_,i)=>fmtHour(i)),
      datasets: [{ data: agg.hourly, backgroundColor: '#58a6ff', borderRadius: 2 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, grid: { color: '#1c2330' } }, x: { grid: { display: false } } } }
  });

  // DOW chart
  dowChart && dowChart.destroy();
  dowChart = new Chart(document.getElementById('dowChart'), {
    type: 'bar',
    data: { labels: WEEKDAYS, datasets: [{ data: agg.dow, backgroundColor: '#bc8cff', borderRadius: 2 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, grid: { color: '#1c2330' } }, x: { grid: { display: false } } } }
  });

  // Token timeline
  tokenChart && tokenChart.destroy();
  tokenChart = new Chart(document.getElementById('tokenChart'), {
    type: 'line',
    data: { labels: dailyLabels, datasets: [{
      data: days.map(d => d.tokens.in + d.tokens.out + d.tokens.cc + d.tokens.cr),
      borderColor: '#3fb950', backgroundColor: 'rgba(63,185,80,0.15)', fill: true, tension: 0.25, pointRadius: 0,
    }]},
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: {
        x: { type: 'time', time: { unit: days.length > 90 ? 'month' : days.length > 21 ? 'week' : 'day' }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { callback: v => fmtN(v) }, grid: { color: '#1c2330' } }
      }
    }
  });

  // Token pie
  tokenPie && tokenPie.destroy();
  tokenPie = new Chart(document.getElementById('tokenPie'), {
    type: 'doughnut',
    data: { labels: ['Input','Output','Cache create','Cache read'],
      datasets: [{ data: [agg.tokens.in, agg.tokens.out, agg.tokens.cc, agg.tokens.cr],
        backgroundColor: ['#58a6ff','#d97757','#bc8cff','#3fb950'], borderColor: '#0d1117', borderWidth: 2 }] },
    options: { maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' },
        tooltip: { callbacks: { label: ctx => `${ctx.label}: ${fmtN(ctx.parsed)}` } } } }
  });

  // Heatmap
  const flat = agg.heatmap.flat();
  const maxV = Math.max(1, ...flat);
  function hmColor(v) {
    if (v === 0) return '#1c2330';
    const t = Math.min(1, Math.log(1+v) / Math.log(1+maxV));
    const stops = [[0,[28,35,48]],[0.2,[59,42,42]],[0.4,[122,62,42]],[0.65,[192,94,42]],[1,[245,161,103]]];
    let a = stops[0], b = stops[stops.length-1];
    for (let i=0;i<stops.length-1;i++) {
      if (t >= stops[i][0] && t <= stops[i+1][0]) { a = stops[i]; b = stops[i+1]; break; }
    }
    const frac = (t - a[0]) / Math.max(0.0001, (b[0]-a[0]));
    const rgb = a[1].map((c,i)=>Math.round(c + (b[1][i]-c)*frac));
    return `rgb(${rgb.join(',')})`;
  }
  let html = '<div class="heatmap"><div></div>';
  for (let h=0; h<24; h++) html += `<div class="hdr">${h%3===0?h:''}</div>`;
  for (let d=0; d<7; d++) {
    html += `<div class="rowlabel">${WEEKDAYS[d]}</div>`;
    for (let h=0; h<24; h++) {
      const v = agg.heatmap[d][h];
      html += `<div class="cell" style="background:${hmColor(v)}" title="${WEEKDAYS[d]} ${fmtHour(h)}: ${v}"></div>`;
    }
  }
  html += '</div>';
  document.getElementById('heatmap').innerHTML = html;

  // Models table
  const modelsSorted = Object.entries(agg.models).sort((a,b) => b[1].messages - a[1].messages);
  document.querySelector('#modelsTable tbody').innerHTML = modelsSorted.map(([m,v]) =>
    `<tr><td class="truncate" title="${m}"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${modelColor(m)};margin-right:6px;vertical-align:middle"></span>${prettyModel(m)}</td><td class="num">${v.messages.toLocaleString()}</td><td class="num">${fmtN(v.tokens)}</td></tr>`
  ).join('');

  // Projects table
  const projSorted = Object.entries(agg.projects).sort((a,b) => b[1].messages - a[1].messages).slice(0, 40);
  document.querySelector('#projectsTable tbody').innerHTML = projSorted.map(([p,v]) =>
    `<tr><td class="truncate" title="${p}">${shortProj(p)}</td><td class="num">${v.messages.toLocaleString()}</td><td class="num">${v.sessions.size.toLocaleString()}</td></tr>`
  ).join('');

  // Tools table
  const toolSorted = Object.entries(agg.tools).sort((a,b) => b[1]-a[1]).slice(0, 40);
  document.querySelector('#toolsTable tbody').innerHTML = toolSorted.map(([t,c]) =>
    `<tr><td>${t}</td><td class="num">${c.toLocaleString()}</td></tr>`
  ).join('');

  // Skills
  const skillSorted = Object.entries(agg.skills).sort((a,b) => b[1]-a[1]);
  skillsChart && skillsChart.destroy();
  const skillsEmpty = document.getElementById('skillsEmpty');
  const skillsCanvas = document.getElementById('skillsChart');
  if (skillSorted.length === 0) {
    skillsEmpty.style.display = 'block';
    skillsCanvas.style.display = 'none';
  } else {
    skillsEmpty.style.display = 'none';
    skillsCanvas.style.display = 'block';
    const topSkills = skillSorted.slice(0, 15);
    skillsChart = new Chart(skillsCanvas, {
      type: 'bar',
      data: {
        labels: topSkills.map(([s]) => s),
        datasets: [{ data: topSkills.map(([,c]) => c), backgroundColor: '#f0a080', borderRadius: 2 }]
      },
      options: {
        indexAxis: 'y',
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, grid: { color: '#1c2330' } },
          y: { grid: { display: false } }
        }
      }
    });
  }

  // Sessions table
  renderSessions(agg.session_list, currentSessionSort);
}

let currentSessionSort = 'duration_s';
function renderSessions(list, key) {
  const sorted = list.slice().sort((a,b)=>(b[key]||0)-(a[key]||0)).slice(0, 50);
  document.querySelector('#sessionsTable tbody').innerHTML = sorted.map(x => {
    const dt = new Date(x.first);
    return `<tr>
      <td>${dt.toLocaleDateString()} ${dt.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</td>
      <td class="truncate" title="${x.project}">${shortProj(x.project)}</td>
      <td class="num">${fmtDuration(x.duration_s)}</td>
      <td class="num">${x.messages.toLocaleString()}</td>
      <td class="num">${fmtN(x.tokens)}</td>
      <td class="truncate">${(x.models||[]).map(prettyModel).join(', ')}</td>
    </tr>`;
  }).join('');
}

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentSessionSort = btn.dataset.tab;
    renderSessions(aggregate(daysInRange()).session_list, currentSessionSort);
  });
});

// Chip row for month/week
function buildChips() {
  const row = document.getElementById('chipRow');
  if (mode !== 'month' && mode !== 'week') { row.style.display = 'none'; row.innerHTML = ''; return; }
  row.style.display = 'flex';
  const buckets = {};
  for (const d of DATA.daily) {
    const k = mode === 'month' ? monthKey(d.date) : isoWeekKey(d.date);
    if (!buckets[k]) buckets[k] = { key: k, messages: 0, days: 0 };
    buckets[k].messages += d.messages;
    buckets[k].days++;
  }
  const keys = Object.keys(buckets).sort().reverse();
  if (!chipKey || !buckets[chipKey]) chipKey = keys[0];
  const label = k => {
    if (mode !== 'month') return k;
    const [y, m] = k.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
  };
  row.innerHTML = `<span class="filter-label">${mode === 'month' ? 'Month' : 'Week'}</span>` +
    keys.map(k => `<span class="chip ${k===chipKey?'active':''}" data-key="${k}">${label(k)}<span class="hint">${buckets[k].messages}</span></span>`).join('');
  row.querySelectorAll('.chip').forEach(el => {
    el.addEventListener('click', () => { chipKey = el.dataset.key; buildChips(); render(); });
  });
}

document.querySelectorAll('#modeSeg button').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('#modeSeg button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    mode = b.dataset.mode;
    chipKey = null;
    buildChips();
    render();
  });
});

buildChips();
render();
</script>
</body>
</html>
"""

html = (HTML_TEMPLATE
        .replace("__DATA_JSON__", json.dumps(DATA, default=str))
        .replace("__HOME_JSON__", json.dumps(str(Path.home()) + "/"))
        .replace("__GEN_AT__", datetime.now().strftime("%Y-%m-%d %H:%M")))

with open(OUT, "w") as f:
    f.write(html)
print(f"Wrote {OUT}  ({os.path.getsize(OUT)//1024} KB)")
