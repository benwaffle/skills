# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pygments", "tree-sitter", "tree-sitter-go"]
# ///
"""Build a lab page of diff-rendering experiments for a PR, each shown on the PR's own code next to today's rendering.

    uv run lab.py spec.yaml [--out PATH]

Uses the spec's repo, base and github; its scenes are ignored. Only Go files are analyzed.
"""

import argparse
import json
import os
import re

import yaml

import build
import goast

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "assets", "lab.html")
MAX_HAPPY = 5


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


def chip_at(line, side, text):
    return goast.chip(line, side, "mod", text)


def regions(spans, gap=4):
    out = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


class Lab:
    def __init__(self, repo):
        self.repo = repo
        self.paths = [s["path"] for s in repo.numstat() if s["path"].endswith(".go")]
        self.paths.sort(key=lambda p: p.endswith("_test.go"))
        self.src, self.struct, self.changed = {}, {}, {}
        for p in self.paths:
            fd = repo.file(p)
            self.src[p] = (goast.Src(repo.show(repo.base, p)), goast.Src(repo.show(repo.head, p)))
            self.struct[p] = [fd.structural([dict(r) for r in rows]) for rows in fd.hunks]
            self.changed[p] = changed_lines(self.struct[p])

    def chips(self):
        out = []
        for p in self.paths:
            fd = self.repo.file(p)
            old, new = self.src[p]
            co, cn = self.changed[p]
            chips = goast.change_chips(old, new, co, cn)
            if fd.is_new:
                chips = [c for c in chips if not c["text"].startswith("+ import")]
            hunks = []
            for hi, rows in enumerate(fd.hunks):
                w = fd.window([dict(r) for r in rows], self.struct[p][hi])
                w["chips"] = []
                hunks.append((w, {r["n"] for r in rows if r["n"]}, {r["o"] for r in rows if r["o"]}))
            for c in chips:
                body = c.pop("body", None)
                if body:
                    # A body edit spread over several hunks gets its own count on each.
                    for w, news, olds in hunks:
                        plus = [n for n in body["new"] if n in news]
                        minus = [o for o in body["old"] if o in olds]
                        if plus or minus:
                            w["chips"].append({**c, "line": plus[0] if plus else minus[0], "side": "new" if plus else "old",
                                               "text": f"{body['name']} body +{len(plus)} −{len(minus)}"})
                    continue
                lines = 1 if c["side"] == "new" else 2
                home = next((h for h in hunks if c["line"] in h[lines]), None)
                if home is None:
                    home = min(hunks, key=lambda h: min((abs(c["line"] - x) for x in h[lines]), default=1 << 30))
                home[0]["chips"].append(c)
            for hi, (w, _, _) in enumerate(hunks):
                rows = self.struct[p][hi]
                plus = [r["n"] for r in rows if r["k"] in "+±"]
                minus = [r["o"] for r in rows if r["k"] == "-"]
                if not w["chips"] and (plus or minus):
                    w["chips"].append(chip_at(plus[0] if plus else minus[0], "new" if plus else "old", f"+{len(plus)} −{len(minus)} lines"))
            out.append({"file": p, "isNew": fd.is_new, "hunks": [h[0] for h in hunks]})
        return out

    def types(self):
        known, featured, today = {}, [], []
        dirs = sorted({os.path.dirname(p) for p in self.paths if not p.endswith("_test.go")})
        for d in dirs:
            files = build.git(self.repo.path, "ls-tree", "--name-only", self.repo.head, d + "/").split()
            pkg = {f: self.repo.show(self.repo.head, f) for f in files if f.endswith(".go") and not f.endswith("_test.go")}
            known.update(goast.types(pkg))
        for p in self.paths:
            if p.endswith("_test.go"):
                continue
            fd = self.repo.file(p)
            _, cn = self.changed[p]
            spans = []
            for t in known.values():
                if t["file"] == p and any(t["line"] <= n <= t["end"] for n in cn):
                    featured.append(t["name"])
                    spans.append((t["line"], t["end"]))
                    for f in t.get("fields", []):
                        f["added"] = not fd.is_new and f["line"] in cn
            for a, b in regions(spans):
                w = range_window(fd, a, b)
                if w:
                    today.append(w)
        for t in known.values():
            for f in t.get("fields", []):
                base = f["base"].split(".")[-1]
                if f["ref"] is None and base in known and base != f["base"]:
                    f["ref"] = base
        referenced = {f["ref"] for n in featured for f in known[n].get("fields", []) if f["ref"]}
        roots = [n for n in featured if n not in referenced]
        return {"types": known, "featured": featured, "roots": roots, "today": today}

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
                loop = range_window(fd, *t["loop"]) if t["loop"] else None
                end = t["loop"][1] if t["loop"] else t["end"]
                out.append({**t, "file": p, "isNew": fd.is_new, "loopWindow": loop, "today": range_window(fd, t["line"], end)})
        return out

    def happy(self):
        out = []
        for p in self.paths:
            if p.endswith("_test.go"):
                continue
            fd = self.repo.file(p)
            _, cn = self.changed[p]
            _, new = self.src[p]
            blocks = goast.error_blocks(new)
            for d in goast.declarations(new).values():
                if d["kind"] != "func" or not any(d["start"] <= n <= d["end"] for n in cn):
                    continue
                w = range_window(fd, d["start"], d["end"])
                if not w:
                    continue
                shown = {r["n"] for r in w["rows"] if r["n"]}
                folds = [b for b in blocks if d["start"] <= b["start"] and b["end"] <= d["end"]
                         and all(n in shown for n in range(b["start"], b["end"] + 1))]
                if folds:
                    out.append({"file": p, "func": d["name"], "window": w, "folds": folds})
        out.sort(key=lambda e: -len(e["folds"]))
        return out[:MAX_HAPPY]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    args = ap.parse_args()
    with open(args.spec) as f:
        spec = yaml.safe_load(f)
    repo_path = os.path.expanduser(spec["repo"])
    if not os.path.isabs(repo_path):
        repo_path = os.path.join(os.path.dirname(os.path.abspath(args.spec)), repo_path)
    repo = build.Repo(repo_path, spec.get("base", "origin/main"), spec["github"])
    lab = Lab(repo)
    data = {
        "meta": {"title": spec["title"], "kicker": spec.get("kicker", f"PR #{spec.get('pr')}"), "pr": spec.get("pr"),
                 "github": spec["github"]},
        "chips": lab.chips(), "types": lab.types(), "tests": lab.tests(), "happy": lab.happy(),
    }
    with open(os.path.join(os.path.dirname(HERE), "assets", "template.html")) as f:
        css = re.search(r"<style>(.*?)</style>", f.read(), re.S).group(1)
    with open(TEMPLATE) as f:
        page = f.read().replace("/*CSS*/", css).replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))
    out = args.out or os.path.join(os.environ.get("BB_THREAD_STORAGE", "."), "reports", f"pr{spec.get('pr', 'x')}-lab.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write(page)
    counts = {k: len(data[k]) if isinstance(data[k], list) else len(data[k]["featured"]) for k in ("chips", "tests", "happy", "types")}
    print(f"{counts} {len(page) / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
