# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pygments", "numpy", "tree-sitter", "tree-sitter-go"]
# ///
"""Build a lab page of read-mode experiments for a PR: the walkthrough's own read mode, silent, with each experiment
next to today's rendering of the same code.

    uv run lab.py spec.yaml [--out PATH]

A kept experiment moves into build.py's read mode and leaves the lab.
"""

import argparse
import bisect
import json
import os

import yaml

import build
import views

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY = os.path.join(os.path.dirname(HERE), "assets", "lab.html")


def head_window(fd, start, end):
    """Head lines start to end as diff rows with no gaps: the diff's own rows where it has them, deletions included,
    and the head file's lines as context everywhere else."""
    by_new, dels, pairs = {}, {}, []
    for rows in fd.hunks:
        pending, last = [], None
        for r in rows:
            if r["n"] is None:
                pending.append(dict(r))
                continue
            by_new[r["n"]], last = dict(r), r["n"]
            if r["o"] is not None:
                pairs.append((r["n"], r["o"]))
            if pending:
                dels[r["n"]], pending = pending, []
        if pending and last is not None:
            dels[last + 1] = pending
    pairs.sort()
    raw = []
    for n in range(start, end + 1):
        raw += dels.get(n, [])
        if n in by_new:
            raw.append(by_new[n])
            continue
        i = bisect.bisect_left(pairs, (n, -1)) - 1
        o = None if fd.is_new else n - (pairs[i][0] - pairs[i][1] if i >= 0 else 0)
        raw.append({"k": " ", "o": o, "n": n, "text": fd.new_lines[n - 1]})
    struct = fd.structural(raw)
    return fd.window(raw, struct), struct


class LabViews(views.Views):
    """Read mode's views, plus the call graph of the functions the PR changes."""

    def narrated(self, scenes):
        """The changed functions the tour shows a line of, each with the first scene that does."""
        out = {}
        for si, s in enumerate(scenes):
            for w in s.get("hunks", []):
                lines = {r["n"] for r in w["rows"] if r.get("n")}
                for fid, f in self.funcs.items():
                    if f["file"] == w["file"] and any(f["start"] <= n <= f["end"] for n in lines):
                        out.setdefault(fid, si)
        return out

    def flow(self, narrated):
        """The functions the PR changes in the order they call each other: depth-first from the ones no other changed
        function calls, in the order the tour shows the narrated function nearest each. Each comes with its whole code
        as a read-mode window, and its parent in the walk."""
        changed = [fid for fid, f in self.funcs.items() if any(f["start"] <= n <= f["end"] for n in self.changed(f["file"]))]
        changed.sort(key=lambda fid: (self.funcs[fid]["file"], self.funcs[fid]["line"]))
        changed_set, calls = set(changed), {}
        for fid in changed:
            f, seen = self.funcs[fid], []
            for line, marks in sorted(self.symbols(f["file"]).items()):
                if f["line"] <= line <= f["end"]:
                    seen += [ref for *_, ref, kind in marks if kind == "call" and ref in changed_set and ref != fid]
            calls[fid] = list(dict.fromkeys(seen))
        callers = {fid: [g for g in changed if fid in calls[g]] for fid in changed}

        def reach(fid, seen):
            if fid not in seen:
                seen.add(fid)
                for c in calls[fid]:
                    reach(c, seen)
            return seen

        def nearest_shown(fid):
            """The first scene to show one of the narrated functions fewest calls away. A deep helper that many
            entry points share, like a validity check, says little about where an entry point belongs."""
            level, seen = [fid], {fid}
            while level:
                if scenes := [narrated[g] for g in level if g in narrated]:
                    return min(scenes)
                level = list(dict.fromkeys(c for g in level for c in calls[g] if c not in seen))
                seen.update(level)
            return float("inf")

        roots = sorted((fid for fid in changed if not callers[fid]), key=lambda fid: (nearest_shown(fid), -len(reach(fid, set()))))
        order, depth, parent = [], {}, {}

        def visit(fid, d, up):
            if fid not in depth:
                depth[fid], parent[fid] = d, up
                order.append(fid)
                for c in calls[fid]:
                    visit(c, d + 1, fid)

        for fid in roots + changed:
            visit(fid, 0, None)
        nodes = []
        for fid in order:
            f = self.funcs[fid]
            fd = self.repo.file(f["file"])
            win, struct = head_window(fd, f["start"], f["end"])
            self.annotate(fd, win, struct)
            nodes.append({"id": fid, "name": f["name"], "file": f["file"], "line": f["line"], "depth": depth[fid],
                          "parent": parent[fid], "scene": narrated.get(fid), "calls": calls[fid], "callers": callers[fid],
                          "window": win})
        return nodes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    args = ap.parse_args()
    with open(args.spec) as f:
        spec = yaml.safe_load(f)
    try:
        builder = build.Builder(spec, os.path.dirname(os.path.abspath(args.spec)), silent=True, fetch=False)
        builder.views = labviews = LabViews(builder.repo)
        data, _ = builder.build()
    except build.SpecError as e:
        raise SystemExit(f"spec error: {e}")
    flow = labviews.flow(labviews.narrated(data["scenes"]))
    # Again, for the symbols and types the flow's windows link.
    data["peek"] = labviews.peek_blocks()
    data["types"] = labviews.type_blocks()
    with open(build.TEMPLATE) as f:
        page = f.read().replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))
    with open(OVERLAY) as f:
        overlay = f.read().replace("/*LAB*/null", json.dumps({"flow": flow}).replace("</", "<\\/"))
    page = page.replace("</body>", overlay + "\n</body>")
    out = args.out or os.path.join(os.environ.get("BB_THREAD_STORAGE", "."), "reports", f"pr{spec.get('pr', 'x')}-lab.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write(page)
    print(f"{len(data['peek'])} peekable symbols, {len(flow)} changed functions; {len(page) / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
