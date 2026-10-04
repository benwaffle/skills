# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pygments", "tree-sitter", "tree-sitter-go"]
# ///
"""Build a lab page of diff-rendering experiments for a PR, each shown on the PR's own code next to today's rendering.

    uv run lab.py spec.yaml [--out PATH]
    uv run lab.py spec.yaml --hunks      # list every hunk, with hints, for writing the spec's `lab.hunks` summaries

Uses the spec's repo, base and github, plus its optional `lab:` section; its scenes are ignored. The code analysis
covers Go files only; the hunk outline covers every file.
"""

import argparse
import json
import os
import re
import sys

import yaml

import build
import goast

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "assets", "lab.html")
MAX_HAPPY = 5
ONE_LINE_STMTS = 2


def changed_lines(struct_hunks):
    """Lines with a real change, as difftastic sees it: alignment-only rows ('~') don't count."""
    new = {r["n"] for rows in struct_hunks for r in rows if r["k"] in "+±" and r["n"]}
    old = {r["o"] for rows in struct_hunks for r in rows if r["k"] == "-" and r["o"]}
    return old, new


def range_window(fd, start, end):
    """The diff rows of a file between new lines start and end, with deletions inside or just before the range."""
    raw = []
    for rows in fd.hunks:
        prev = None
        for r in rows:
            if r["n"] is not None:
                prev = r["n"]
                if start <= r["n"] <= end:
                    raw.append(dict(r))
            elif prev is not None and start - 1 <= prev < end:
                raw.append(dict(r))
    return fd.window(raw, fd.structural(raw)) if raw else None


def strip_indent(tokens):
    out = [t for t in tokens]
    while out and not out[0][1].strip():
        out.pop(0)
    if out:
        out[0] = (out[0][0], out[0][1].lstrip())
    return out


def one_line(tokens, start, end):
    """An if-block's lines joined onto one highlighted line: `if err != nil { return err }`. Lines inside a statement
    join with a space, statements with "; ", and only the first few statements are kept."""
    lines = [tokens[n - 1] for n in range(start, end + 1)]
    body = [strip_indent(l) for l in lines[1:-1] if any(t.strip() for _, t in l)]
    out, stmts = list(lines[0]) + [("", " ")], 0
    for i, line in enumerate(body):
        text = "".join(t for _, t in line).rstrip()
        out += line
        continues = text.endswith(("(", "[", "{", ",", "+", "-", "*", "/", "&&", "||"))
        if not continues:
            stmts += 1
            if stmts == ONE_LINE_STMTS and i < len(body) - 1:
                out.append(("", "; … "))
                break
        out.append(("", "" if text.endswith(("(", "[", "{")) else " " if continues else "; " if i < len(body) - 1 else " "))
    out += strip_indent(lines[-1])
    return build.render(out, [], "")


class Lab:
    def __init__(self, repo, spec_lab):
        self.repo, self.spec_lab = repo, spec_lab or {}
        self.all_paths = [s["path"] for s in repo.numstat()]
        self.paths = sorted((p for p in self.all_paths if p.endswith(".go")), key=lambda p: p.endswith("_test.go"))
        self.src, self.struct, self.changed, self._tokens = {}, {}, {}, {}
        for p in self.all_paths:
            fd = repo.file(p)
            self.struct[p] = [fd.structural([dict(r) for r in rows]) for rows in fd.hunks]
            self.changed[p] = changed_lines(self.struct[p])
        for p in self.paths:
            self.src[p] = (goast.Src(repo.show(repo.base, p)), goast.Src(repo.show(repo.head, p)))

    def tokens(self, path):
        if path not in self._tokens:
            text = self.repo.show(self.repo.head, path) or ""
            self._tokens[path] = (build.tokens_by_line(text, path), text.split("\n"))
        return self._tokens[path]

    def code_rows(self, path, start, end, marks=None):
        """Head-side rows of any file, highlighted, with byte ranges in `marks` ({line: [(a, b)]}) wrapped as type links."""
        toks, lines = self.tokens(path)
        changed = self.changed.get(path, (set(), set()))[1]
        is_new = path in self.all_paths and self.repo.file(path).is_new
        rows = []
        for n in range(start, min(end, len(toks)) + 1):
            cr = build.char_ranges(lines[n - 1], (marks or {}).get(n, []))
            rows.append({"k": "+" if is_new or n in changed else " ", "o": None, "n": n, "h": build.render(toks[n - 1], cr, "xref")})
        return rows

    # ---------------- 1. hunk outline ----------------

    def hunk_index(self, entry):
        fd = self.repo.file(entry["file"])
        if not fd.hunks:
            sys.exit(f"lab.hunks: {entry['file']} has no changes")
        if "hunk" in entry:
            return entry["hunk"]
        if "from" in entry:
            hi = next((i for i, rows in enumerate(fd.hunks) if any(build.matches(r, entry["from"]) for r in rows)), None)
            if hi is None:
                sys.exit(f"lab.hunks: {entry['file']}: no hunk has {entry['from']!r}")
            return hi
        if len(fd.hunks) == 1:
            return 0
        sys.exit(f"lab.hunks: {entry['file']} has {len(fd.hunks)} hunks: give `from` or `hunk`")

    def summaries(self):
        out = {}
        for e in self.spec_lab.get("hunks", []):
            out[(e["file"], self.hunk_index(e))] = e["text"]
        return out

    def outline(self):
        summaries = self.summaries()
        out = []
        for p in self.all_paths:
            fd = self.repo.file(p)
            hunks = [{**fd.window([dict(r) for r in rows], self.struct[p][hi]), "summary": summaries.get((p, hi))}
                     for hi, rows in enumerate(fd.hunks)]
            out.append({"file": p, "isNew": fd.is_new, "hunks": hunks})
        return out

    def list_hunks(self):
        """Every hunk with a few changed lines and the declaration changes in it, to write summaries from."""
        summaries = self.summaries()
        for p in self.all_paths:
            fd = self.repo.file(p)
            chips = []
            if p in self.src:
                co, cn = self.changed[p]
                chips = goast.change_chips(*self.src[p], co, cn)
            print(p + ("  (new file)" if fd.is_new else ""))
            for hi, rows in enumerate(self.struct[p]):
                news = {r["n"] for r in fd.hunks[hi] if r["n"]}
                nums = [r["n"] or r["o"] for r in rows if r["n"] or r["o"]]
                plus, minus = sum(r["k"] in "+±" for r in rows), sum(r["k"] == "-" for r in rows)
                have = "summarized" if (p, hi) in summaries else "NO SUMMARY"
                print(f"  [hunk {hi}] L{min(nums)}-{max(nums)}  +{plus} −{minus}  {have}")
                for c in chips:
                    if c["side"] == "new" and c["line"] in news:
                        print(f"      · {c['text']}")
                for r in [r for r in rows if r["k"] in "+±-"][:4]:
                    print(f"      {r['k']} {r['text'].strip()[:100]}")

    # ---------------- 2. types ----------------

    def types(self):
        known, featured, today = {}, [], []
        dirs = sorted({os.path.dirname(p) for p in self.paths if not p.endswith("_test.go")})
        for d in dirs:
            files = build.git(self.repo.path, "ls-tree", "--name-only", self.repo.head, d + "/").split()
            pkg = {f: self.repo.show(self.repo.head, f) for f in files if f.endswith(".go") and not f.endswith("_test.go")}
            known.update(goast.types(pkg))
        # Each package was parsed alone, so a qualified field type like ifc.Config only resolves now.
        for t in known.values():
            for f in t.get("fields", []):
                name = f["base"].split(".")[-1]
                if f["ref"] is None and name in known:
                    f["ref"] = name
        for p in self.paths:
            if p.endswith("_test.go"):
                continue
            fd = self.repo.file(p)
            _, cn = self.changed[p]
            spans = []
            for t in known.values():
                if t["file"] == p and any(t["code"][0] <= n <= t["code"][1] for n in cn):
                    featured.append(t["name"])
                    spans.append(t["code"])
            for a, b in sorted(spans):
                w = range_window(fd, a, b)
                if w:
                    today.append(w)
        # Only types a reader can reach from the PR's own are sent, each with its code.
        reach, todo = set(), list(featured)
        while todo:
            n = todo.pop()
            if n in reach:
                continue
            reach.add(n)
            todo += [f["ref"] for f in known[n].get("fields", []) if f["ref"] in known]
        out = {}
        for n in reach:
            t = known[n]
            marks = {}
            for f in t.get("fields", []):
                if f["ref"] in reach and f["span"]:
                    marks.setdefault(f["span"][0], []).append((f["span"][1], f["span"][2]))
            rows = self.code_rows(t["file"], *t["code"], marks)
            if t.get("values"):
                a, b = min(v["block"][0] for v in t["values"]), max(v["block"][1] for v in t["values"])
                if a > t["code"][1] + 1:
                    rows.append({"gap": True})
                rows += self.code_rows(t["file"], max(a, t["code"][1] + 1), b)
            methods = [{"name": m["name"], "rows": self.code_rows(m["file"], m["start"], m["end"])} for m in t.get("methods", [])]
            out[n] = {**t, "rows": rows, "methods": methods, "isNew": self.repo.file(t["file"]).is_new if t["file"] in self.all_paths else False}
        referenced = {f["ref"] for n in featured for f in known[n].get("fields", []) if f["ref"]}
        roots = [n for n in featured if n not in referenced]
        return {"types": out, "featured": featured, "roots": roots, "today": today}

    # ---------------- 3. table-driven tests ----------------

    def tests(self):
        out = []
        for p in self.paths:
            if not p.endswith("_test.go"):
                continue
            fd = self.repo.file(p)
            _, cn = self.changed[p]
            for t in goast.table_tests(self.src[p][1]):
                if not any(t["line"] <= n <= t["end"] for n in cn):
                    continue
                for r in t["rows"]:
                    span = set(range(r["line"], r["end"] + 1))
                    r["mark"] = None if fd.is_new else ("added" if span <= cn else "changed" if span & cn else None)
                w = range_window(fd, *t["fn"])
                if w:
                    out.append({**t, "file": p, "isNew": fd.is_new, "window": w})
        return out

    # ---------------- 4. happy path ----------------

    def happy(self):
        out = []
        for p in self.paths:
            if p.endswith("_test.go"):
                continue
            fd = self.repo.file(p)
            _, cn = self.changed[p]
            _, new = self.src[p]
            toks, _ = self.tokens(p)
            blocks = goast.error_blocks(new)
            for d in goast.declarations(new).values():
                if d["kind"] != "func" or not any(d["start"] <= n <= d["end"] for n in cn):
                    continue
                w = range_window(fd, d["start"], d["end"])
                if not w:
                    continue
                shown = {r["n"] for r in w["rows"] if r["n"]}
                folds = [{**b, "html": one_line(toks, b["start"], b["end"])} for b in blocks
                         if d["start"] <= b["start"] and b["end"] <= d["end"] and all(n in shown for n in range(b["start"], b["end"] + 1))]
                if folds:
                    out.append({"file": p, "func": d["name"], "window": w, "folds": folds})
        out.sort(key=lambda e: -len(e["folds"]))
        return out[:MAX_HAPPY]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    ap.add_argument("--hunks", action="store_true", help="list every hunk for writing summaries, then exit")
    args = ap.parse_args()
    with open(args.spec) as f:
        spec = yaml.safe_load(f)
    repo_path = os.path.expanduser(spec["repo"])
    if not os.path.isabs(repo_path):
        repo_path = os.path.join(os.path.dirname(os.path.abspath(args.spec)), repo_path)
    repo = build.Repo(repo_path, spec.get("base", "origin/main"), spec["github"])
    lab = Lab(repo, spec.get("lab"))
    if args.hunks:
        lab.list_hunks()
        return
    data = {
        "meta": {"title": spec["title"], "kicker": spec.get("kicker", f"PR #{spec.get('pr')}"), "pr": spec.get("pr"),
                 "github": spec["github"]},
        "outline": lab.outline(), "types": lab.types(), "tests": lab.tests(), "happy": lab.happy(),
    }
    with open(os.path.join(os.path.dirname(HERE), "assets", "template.html")) as f:
        css = re.search(r"<style>(.*?)</style>", f.read(), re.S).group(1)
    with open(TEMPLATE) as f:
        page = f.read().replace("/*CSS*/", css).replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))
    out = args.out or os.path.join(os.environ.get("BB_THREAD_STORAGE", "."), "reports", f"pr{spec.get('pr', 'x')}-lab.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write(page)
    hunks = sum(len(f["hunks"]) for f in data["outline"])
    summarized = sum(1 for f in data["outline"] for h in f["hunks"] if h["summary"])
    print(f"outline {summarized}/{hunks} hunks summarized, {len(data['types']['featured'])} types, {len(data['tests'])} test tables, "
          f"{len(data['happy'])} happy-path functions; {len(page) / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
