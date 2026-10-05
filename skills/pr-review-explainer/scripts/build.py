# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pygments", "numpy", "soundfile", "kokoro-onnx", "tree-sitter", "tree-sitter-go"]
# ///
"""Build a PR walkthrough (narrated tour + read-mode document) from a scene spec.

    uv run build.py spec.yaml [--out PATH] [--no-audio] [--voice NAME]

The spec format is documented in ../reference/spec.md.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import yaml

import views
from highlight import char_ranges, join_ranges, render, tokens_by_line

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "assets", "template.html")
CUE_GAP, SCENE_GAP, LEAD_IN = 0.45, 1.1, 0.8
WORDS_PER_SEC = 2.7
CONTEXT = 4
ANCHOR = re.compile(r"\[\[([\w-]+)\]\]")
GH_REF = re.compile(r"(?:^|[^&\w])#(\d+)\b")


class SpecError(Exception):
    pass


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


class Repo:
    def __init__(self, path, base, github):
        self.path, self.github = path, github
        self.head = git(path, "rev-parse", "HEAD").strip()
        self.base = git(path, "merge-base", base, "HEAD").strip()
        self.branch = git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.base_name = base
        self._files = {}

    def show(self, rev, path):
        r = subprocess.run(["git", "-C", self.path, "show", f"{rev}:{path}"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    def numstat(self):
        rows = []
        for line in git(self.path, "diff", "--numstat", self.base, self.head).strip().splitlines():
            a, d, p = line.split("\t")
            if a != "-":
                rows.append({"a": int(a), "d": int(d), "path": p})
        return rows

    def file(self, path):
        if path not in self._files:
            self._files[path] = FileDiff(self, path)
        return self._files[path]

    def ls(self, rev, directory):
        """The files directly inside a directory at rev, as paths from the repo root."""
        return git(self.path, "ls-tree", "--name-only", rev, directory + "/" if directory else ".").split()

    def url(self, path, line, old=False):
        rev = self.base if old else self.head
        return f"https://github.com/{self.github}/blob/{rev}/{path}" + (f"#L{line}" if line else "")


# ---------------- diff model ----------------

class FileDiff:
    def __init__(self, repo, path):
        self.repo, self.path = repo, path
        old, new = repo.show(repo.base, path), repo.show(repo.head, path)
        self.is_new, self.is_deleted = old is None, new is None
        self.old_tok = tokens_by_line(old or "", path)
        self.new_tok = tokens_by_line(new or "", path)
        self.new_lines = (new or "").split("\n")
        self.hunks = self._hunks()
        self.struct = None if (self.is_new or self.is_deleted) else self._difftastic(old, new)
        self.covered = set()

    def _hunks(self):
        out = git(self.repo.path, "diff", f"-U{CONTEXT}", self.repo.base, self.repo.head, "--", self.path)
        hunks, old, new = [], 0, 0
        for line in out.splitlines():
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if m:
                old, new = int(m[1]), int(m[2])
                hunks.append([])
            elif hunks and line[:1] in "+- " and not line.startswith(("+++", "---")):
                kind, text = line[:1], line[1:]
                hunks[-1].append({"k": kind, "o": old if kind != "+" else None, "n": new if kind != "-" else None, "text": text})
                old += kind != "+"
                new += kind != "-"
        return hunks

    def _difftastic(self, old, new):
        ext = os.path.splitext(self.path)[1]
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "old" + ext), os.path.join(d, "new" + ext)
            for p, text in ((a, old), (b, new)):
                with open(p, "w") as f:
                    f.write(text)
            r = subprocess.run(["difft", "--display", "json", a, b], capture_output=True, text=True,
                               env={**os.environ, "DFT_UNSTABLE": "yes"})
        if r.returncode not in (0, 1) or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        if data.get("language") in (None, "Text"):
            return None
        lhs, rhs = {}, {}
        for chunk in data.get("chunks", []):
            for e in chunk:
                left, right = e.get("lhs"), e.get("rhs")
                if left:
                    lhs[left["line_number"] + 1] = {"changes": [(c["start"], c["end"]) for c in left["changes"]],
                                                    "pair": right["line_number"] + 1 if right else None}
                if right:
                    rhs[right["line_number"] + 1] = {"changes": [(c["start"], c["end"]) for c in right["changes"]],
                                                     "pair": left["line_number"] + 1 if left else None}
        return lhs, rhs

    def structural(self, rows):
        """Rows as difftastic sees them: whitespace-only changes become one '~' row, pure insertions inside an
        existing line become one '±' row with the inserted tokens marked, and real edits keep their marks."""
        if self.struct is None:
            return [dict(r) for r in rows]
        lhs, rhs = self.struct
        out = []
        for r in rows:
            if r["k"] == "-":
                info = lhs.get(r["o"])
                if not info or not info["changes"]:
                    continue
                out.append({**r, "marks": info["changes"] if info["pair"] else []})
            elif r["k"] == "+":
                info = rhs.get(r["n"])
                if not info:
                    out.append({**r, "k": "~"})
                elif info["pair"] and not lhs.get(info["pair"], {}).get("changes"):
                    out.append({**r, "k": "±", "o": info["pair"], "marks": info["changes"]})
                else:
                    out.append({**r, "marks": info["changes"] if info["pair"] else []})
            else:
                out.append(dict(r))
        return out

    def html_row(self, r, links=()):
        """A row as HTML. `links` are (start byte, end byte, ref[, mark class]) spans; the class defaults to xref, a
        type link."""
        old = r["k"] == "-"
        toks, line = (self.old_tok, r["o"]) if old else (self.new_tok, r["n"])
        tokens = toks[line - 1] if line and line - 1 < len(toks) else []
        if "".join(t for _, t in tokens) != r["text"]:
            tokens = [("", r["text"])]
        marks = join_ranges(r["text"], char_ranges(r["text"], r.get("marks") or []))
        return {"k": r["k"], "o": r["o"], "n": r["n"], "h": render(tokens, marks, "x-del" if old else "x-ins", views.link_layers(r["text"], links))}

    def window(self, rows, struct_rows):
        nums = [r["n"] or r["o"] for r in struct_rows if r["n"] or r["o"]] or [0]
        first_new = next((r["n"] for r in struct_rows if r["n"]), None)
        win = {"file": self.path, "isNew": self.is_new, "range": f"L{min(nums)}–{max(nums)}",
               "url": self.repo.url(self.path, first_new, old=self.is_deleted), "rows": [self.html_row(r) for r in struct_rows]}
        raw = [self.html_row(r) for r in rows]
        if raw != win["rows"]:  # new files and files difftastic can't parse look the same either way, so send them once
            win["raw"] = raw
        return win


# ---------------- link titles ----------------

def spec_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from spec_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from spec_strings(v)


def github_ref(repo, n):
    r = subprocess.run(["gh", "api", f"repos/{repo}/issues/{n}"], capture_output=True, text=True, timeout=30, check=True)
    d = json.loads(r.stdout)
    if d.get("pull_request"):
        state = "merged" if d["pull_request"].get("merged_at") else "draft" if d.get("draft") else d["state"]
        return {"title": d["title"], "state": f"PR · {state}"}
    return {"title": d["title"], "state": f"issue · {d['state']}"}


def jira_ref(key):
    r = subprocess.run(["twg", "jira", "workitem", "get", key, "--output", "json", "--output-summary", "none"],
                       capture_output=True, text=True, timeout=60, check=True)
    d = json.loads(r.stdout)["data"][0]
    status = d.get("status")
    status = status.get("name") if isinstance(status, dict) else status
    return {"title": d.get("summary") or d["fields"]["summary"], "state": status or ""}


def fetch_refs(spec):
    """Titles for every Jira key and #N reference in the spec's text, shown when hovering their links."""
    text = "\n".join([*spec_strings(spec.get("scenes", [])), spec.get("title", ""), spec.get("kicker", "")])
    jobs = {}
    projects = (spec.get("jira") or {}).get("projects") or []
    if projects:
        for key in set(re.findall(r"\b(?:%s)-\d+\b" % "|".join(map(re.escape, projects)), text)):
            jobs[key] = lambda key=key: jira_ref(key)
    for n in set(GH_REF.findall(text)):
        jobs["#" + n] = lambda n=n: github_ref(spec["github"], n)
    refs = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {k: pool.submit(f) for k, f in jobs.items()}
    for k, fut in sorted(futures.items()):
        try:
            refs[k] = fut.result()
        except Exception as e:
            print(f"  warning: no hover title for {k}: {e}", file=sys.stderr)
    return refs


# ---------------- markers ----------------

def matches(row, marker):
    kinds = None
    if marker[:2] == "+:":
        kinds, marker = "+±~", marker[2:]
    elif marker[:2] == "-:":
        kinds, marker = "-", marker[2:]
    if kinds and row["k"] not in kinds:
        return False
    text = row.get("text", "")
    if marker.startswith("re:"):
        return re.search(marker[3:], text) is not None
    return marker in text


def find(rows, marker, after=0, what="marker"):
    for i in range(after, len(rows)):
        if matches(rows[i], marker):
            return i
    raise SpecError(f"{what} not found: {marker!r}")


# ---------------- spec -> data ----------------

class Builder:
    def __init__(self, spec, spec_dir, silent, fetch=True, voice=None):
        self.spec, self.silent, self.fetch = spec, silent, fetch
        repo_path = os.path.expanduser(spec["repo"])
        if not os.path.isabs(repo_path):
            repo_path = os.path.join(spec_dir, repo_path)
        self.repo = Repo(repo_path, spec.get("base", "origin/main"), spec["github"])
        self.views = views.Views(self.repo)
        self.voice = None
        if not silent:
            from tts import load_voice

            try:
                self.voice = load_voice(voice or spec.get("voice"), spec.get("lexicon"))
            except ValueError as e:
                raise SpecError(e)

    def speech(self, cue):
        if isinstance(cue, str):
            cue = {"say": cue}
        return cue.get("speak") or self.parse_say(cue["say"])[0]

    def hunk_window(self, h):
        fd = self.repo.file(h["file"])
        if not fd.hunks:
            raise SpecError(f"{h['file']}: no changes in this diff")
        if "hunk" in h:
            hi = h["hunk"]
        elif "from" in h:
            hi = next((i for i, rows in enumerate(fd.hunks) if any(matches(r, h["from"]) for r in rows)), None)
            if hi is None:
                raise SpecError(f"{h['file']}: hunk start not found: {h['from']!r}")
        elif len(fd.hunks) == 1:
            hi = 0
        else:
            raise SpecError(f"{h['file']} has {len(fd.hunks)} hunks: give `from` or `hunk`")
        rows = fd.hunks[hi]
        a = find(rows, h["from"], what=f"{h['file']} from") if h.get("from") else 0
        b = find(rows, h["to"], a, what=f"{h['file']} to") + h.get("extra", 0) if h.get("to") else len(rows) - 1
        a, b = max(0, a - h.get("before", 0)), min(len(rows) - 1, b)
        fd.covered.update((hi, i) for i in range(a, b + 1))
        raw = [dict(r) for r in rows[a:b + 1]]
        struct = fd.structural(raw) if h.get("structural", True) else [dict(r) for r in raw]
        win = fd.window(raw, struct)
        self.views.annotate(fd, win, struct)
        win["mode"] = h.get("mode", "diff")
        win["_struct"] = struct
        return win

    def resolve_focus(self, scene, ranges):
        pool_all = [(h, i, r) for h, w in enumerate(scene["hunks"]) for i, r in enumerate(w["_struct"])]
        picked = []
        for rng in ranges:
            if isinstance(rng, str):
                rng = [rng, None]
            if isinstance(rng, dict):
                rng = [rng.get("from"), rng.get("to"), rng.get("hunk")]
            start, end, hunk = (list(rng) + [None, None])[:3]
            pool = [x for x in pool_all if hunk is None or x[0] == hunk]
            rows = [x[2] for x in pool]
            try:
                a = find(rows, start, what="focus start")
                b = a if end is None else find(rows, end, a, what="focus end")
            except SpecError as e:
                hidden = any(matches(r, start) for w in scene["hunks"] for r in w["raw"])
                hint = " (it matches a row the structural diff hides as whitespace-only; target the new line)" if hidden else ""
                raise SpecError(f"{scene['title']}: {e}{hint}")
            picked += [[h, i] for h, i, _ in pool[a:b + 1]]
        return picked

    def widget(self, scene, w):
        """A widget with each part's `code`, focus ranges for the rows that part draws, resolved as a focus's are."""
        out = dict(w)
        for key in ("rows", "nodes", "lines"):
            if key in w:
                out[key] = [{**r, "code": self.resolve_focus(scene, [r["code"]] if isinstance(r["code"], str) else r["code"])}
                            if isinstance(r, dict) and "code" in r else r for r in w[key]]
        return out

    def ref_for(self, scene, rows):
        if not rows:
            return None
        h, i = rows[0]
        w, r = scene["hunks"][h], scene["hunks"][h]["_struct"][i]
        line, old = (r["o"], True) if r["k"] == "-" else (r["n"], False)
        return {"label": f"{self.short_path(w['file'])}:{line}", "url": self.repo.url(w["file"], line, old)}

    def short_path(self, path):
        """The shortest tail of a path that no other file in the PR shares: main.go, or migrate/main.go when the PR has
        several."""
        others = [p for p in self.views.changed_paths if p != path]
        parts = path.split("/")
        for k in range(1, len(parts) + 1):
            tail = "/".join(parts[-k:])
            if not any(p == tail or p.endswith("/" + tail) for p in others):
                return tail
        return path

    def ref_marker(self, file, marker):
        fd = self.repo.file(file)
        n = next((i + 1 for i, line in enumerate(fd.new_lines) if marker in line), None)
        if n is None:
            raise SpecError(f"{file}: ref marker not found: {marker!r}")
        return {"label": f"{self.short_path(file)}:{n}", "url": self.repo.url(file, n)}

    def build(self):
        import numpy as np

        spec = self.spec
        parts, t, scenes, times = [], LEAD_IN, [], []
        if self.voice:
            from tts import RATE, silence

            self.voice.prepare([self.speech(c) for sc in spec["scenes"] for c in sc["cues"]])
            parts.append(silence(LEAD_IN))
        for si, sc in enumerate(spec["scenes"]):
            scene = {k: v for k, v in sc.items() if k not in ("cues", "hunks")}
            scene.setdefault("widgets", {})
            scene["hunks"] = [self.hunk_window(h) for h in sc.get("hunks", [])]
            scene["widgets"] = {name: self.widget(scene, w) for name, w in scene["widgets"].items()}
            if scene["kind"] == "summary":
                scene["items"] = [{"text": it["text"], "nit": it.get("nit", False),
                                   "ref": self.ref_marker(it["file"], it["marker"]) if it.get("file") else None}
                                  for it in sc.get("items", [])]
            start, cues, actions, last_focus = t, [], [], []
            times.append({"t": round(start + .9, 2), "label": f"{si}: {sc['title']}"})
            for ci, cue in enumerate(sc["cues"]):
                if isinstance(cue, str):
                    cue = {"say": cue}
                text, anchors = self.parse_say(cue["say"])
                speech = cue.get("speak") or text
                if self.voice:
                    samples, spoken = self.voice.speak(speech)
                    dur = len(samples) / RATE
                    parts += [samples, silence(CUE_GAP)]
                    if spoken != speech or re.search(r"[A-Z]{2,}|\d|[a-z][A-Z]", speech):
                        print(f"    spoken as: {speech}\n               {spoken}")
                    # Autoregressive voices can lose their place and babble; far more audio than words is the sign.
                    words = len(speech.split())
                    if dur > 2 * words / WORDS_PER_SEC + 1:
                        print(f"    warning: {dur:.1f}s of audio for {words} words; listen to this cue: {speech[:70]}")
                else:
                    dur = max(1.2, len(speech.split()) / WORDS_PER_SEC)
                cue_start = t
                t += dur + CUE_GAP
                cues.append({"i": ci, "start": round(cue_start, 3), "end": round(cue_start + dur, 3), "text": text})
                for act in sorted((self.action(a) for a in cue.get("do", [])), key=lambda a: self.at(a, cue_start, dur, anchors, text)):
                    at = self.at(act, cue_start, dur, anchors, text)
                    out = {"t": round(at, 3), "type": act["type"], "cue": ci}
                    if act["type"] == "focus":
                        last_focus = out["rows"] = self.resolve_focus(scene, act["arg"])
                    elif act["type"] in ("note", "review"):
                        out.update(type="note", tag="REVIEW" if act["type"] == "review" else "NOTE", text=act["arg"], rows=last_focus,
                                   ref=self.ref_marker(act["ref"]["file"], act["ref"]["marker"]) if act.get("ref") else self.ref_for(scene, last_focus))
                    elif act["type"] == "widget":
                        if act["arg"] not in scene["widgets"]:
                            raise SpecError(f"{sc['title']}: unknown widget {act['arg']!r}")
                        out.update(name=act["arg"], step=act.get("step", 0))
                    elif act["type"] == "morph":
                        out["hunk"] = act["arg"]
                    else:
                        out["arg"] = act["arg"]
                    actions.append(out)
                    times.append({"t": round(at + 1.0, 2), "label": f"{si}.{ci} {act['type']}"})
            if self.voice:
                parts.append(silence(SCENE_GAP))
            t += SCENE_GAP
            for w in scene["hunks"]:
                w.pop("_struct")
            scenes.append({**scene, "start": round(start, 3), "cues": cues, "actions": actions})
            print(f"  {sc['chapter']:<26} {t - start:5.1f}s")
        for i, s in enumerate(scenes):
            s["end"] = scenes[i + 1]["start"] if i + 1 < len(scenes) else round(t, 3)

        audio = None
        if self.voice:
            audio = self.encode(np.concatenate(parts))
        pr = spec.get("pr")
        meta = {"title": spec["title"], "kicker": spec.get("kicker", f"PR #{pr}"), "github": spec["github"], "jira": spec.get("jira"),
                "refs": fetch_refs(spec) if self.fetch else {},
                "statCommand": f"git diff --stat {self.repo.base_name}...{self.repo.branch}", "head": self.repo.head}
        rest = self.rest()
        peek = self.views.peek_blocks()  # before type_blocks: a peek's rows link more types
        return {"audio": audio, "duration": round(t, 3), "meta": meta, "scenes": scenes,
                "stat": self.repo.numstat(), "rest": rest, "peek": peek, "types": self.views.type_blocks()}, times

    def rest(self):
        """Every hunk with a changed line no scene showed, so the read mode covers the whole diff."""
        out = []
        for s in self.repo.numstat():
            fd = self.repo.file(s["path"])
            hunks, uncovered = [], 0
            for hi, rows in enumerate(fd.hunks):
                missed = sum(r["k"] != " " and (hi, i) not in fd.covered for i, r in enumerate(rows))
                if missed:
                    uncovered += missed
                    raw = [dict(r) for r in rows]
                    struct = fd.structural(raw)
                    win = fd.window(raw, struct)
                    self.views.annotate(fd, win, struct)
                    hunks.append(win)
            if hunks:
                out.append({"file": s["path"], "uncovered": uncovered, "hunks": hunks})
        return out

    @staticmethod
    def parse_say(say):
        anchors, out, pos = {}, [], 0
        for m in ANCHOR.finditer(say):
            out.append(say[pos:m.start()])
            anchors[m[1]] = len("".join(out))
            pos = m.end()
        out.append(say[pos:])
        return "".join(out), anchors

    @staticmethod
    def action(a):
        for kind in ("focus", "note", "review", "widget", "morph", "hl", "show"):
            if kind in a:
                return {**a, "type": kind, "arg": a[kind]}
        raise SpecError(f"unknown action {a!r}")

    @staticmethod
    def at(act, start, dur, anchors, text):
        at = act.get("at")
        if at is None:
            return start + .3
        if isinstance(at, (int, float)):
            return start + at
        if at == "end":
            return start + max(dur - .3, 0)
        if at not in anchors:
            raise SpecError(f"anchor [[{at}]] not in cue: {text!r}")
        return max(start, start + dur * anchors[at] / max(len(text), 1) - .1)

    def encode(self, samples):
        import soundfile as sf
        from tts import RATE

        with tempfile.TemporaryDirectory() as d:
            wav, mp3 = os.path.join(d, "all.wav"), os.path.join(d, "all.mp3")
            sf.write(wav, samples, RATE)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ac", "1", "-codec:a", "libmp3lame", "-b:a", "48k", mp3], check=True)
            with open(mp3, "rb") as f:
                return "data:audio/mpeg;base64," + base64.b64encode(f.read()).decode()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    ap.add_argument("--no-audio", action="store_true", help="silent preview with estimated timings (no TTS)")
    ap.add_argument("--no-fetch", action="store_true", help="skip fetching Jira/GitHub titles for link hover cards")
    ap.add_argument("--voice", help="a voice from voices.yaml; overrides the spec's `voice:`")
    args = ap.parse_args()
    with open(args.spec) as f:
        spec = yaml.safe_load(f)
    try:
        data, times = Builder(spec, os.path.dirname(os.path.abspath(args.spec)), args.no_audio, not args.no_fetch, args.voice).build()
    except SpecError as e:
        sys.exit(f"spec error: {e}")
    out = args.out or os.path.join(os.environ.get("BB_THREAD_STORAGE", "."), "reports", f"pr{spec.get('pr', 'x')}-walkthrough.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(TEMPLATE) as f:
        page = f.read().replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))
    with open(out, "w") as f:
        f.write(page)
    with open(out + ".times.json", "w") as f:
        json.dump(times, f)
    print(f"total {data['duration']:.0f}s, {len(page) / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
