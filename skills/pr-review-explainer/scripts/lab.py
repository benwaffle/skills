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
import goast
import views
from highlight import render

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY = os.path.join(os.path.dirname(HERE), "assets", "lab.html")
PEEK_ROWS = 40
PEEK_MAX = 300


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
    """Read mode's views, plus a symbol index for peeking and the call graph of the functions the PR changes."""

    def __init__(self, repo):
        super().__init__(repo)
        self.funcs = {}
        for d in self.dirs:
            for path in self.package_files(d):
                for decl in goast.declarations(self.src(path)).values():
                    if decl["kind"] == "func":
                        self.funcs[f"{d}:{decl['name']}"] = {"name": decl["name"], "file": path, "line": decl["start"],
                                                           "start": goast.doc_start(decl["node"]), "end": decl["end"]}
        self.methods = {}
        for fid, f in self.funcs.items():
            if "." in f["name"]:
                self.methods.setdefault(f["name"].split(".", 1)[1], []).append(fid)
        self._symbols = {}
        self.peek_used = set()

    def imports(self, src):
        """A file's imports by local name: the package's directory when it's one of the PR's packages, else None."""
        out = {}
        for n in goast.walk(src.root):
            if n.type == "import_spec":
                path = src.text(n.child_by_field_name("path")).strip('"`')
                d = next((d for d in self.dirs if d and (path == d or path.endswith("/" + d))), None)
                alias = n.child_by_field_name("name")
                out[src.text(alias) if alias else path.rsplit("/", 1)[-1]] = d
        return out

    def type_id(self, src, t, scope):
        """The id of the PR's type a type node names, through pointers."""
        while t is not None and t.type in ("pointer_type", "parenthesized_type"):
            t = t.named_children[-1]
        if t is None:
            return None
        if t.type == "type_identifier":
            tid = f"{scope['dir']}:{src.text(t)}"
        elif t.type == "qualified_type":
            tid = f"{scope['imports'].get(src.text(t.child_by_field_name('package')))}:{src.text(t.child_by_field_name('name'))}"
        else:
            return None
        return tid if tid in self.types else None

    def elem_id(self, src, t, scope):
        """The id of the PR's type a slice, array or map type node holds."""
        while t is not None and t.type in ("pointer_type", "parenthesized_type"):
            t = t.named_children[-1]
        if t is not None and t.type in ("slice_type", "array_type"):
            return self.type_id(src, t.child_by_field_name("element"), scope)
        if t is not None and t.type == "map_type":
            return self.type_id(src, t.child_by_field_name("value"), scope)
        return None

    def field_ref(self, tid, name, many):
        """The type a field of one of the PR's types holds: itself or through a pointer, or, with many, as a slice's or
        map's element."""
        f = next((f for f in self.types.get(tid, {}).get("fields", []) if f["name"] == name), None)
        if f is None or not f["ref"]:
            return None
        collection = any(m == "list" or m.startswith("map[") for m in f["mods"])
        return f["ref"] if collection == many else None

    def expr_type(self, src, e, scope, many=False):
        """The PR's type an expression holds (or, with many, holds a collection of), as far as the function's
        declarations say: a variable, or a field of one."""
        while e.type in ("parenthesized_expression", "unary_expression"):
            e = e.child_by_field_name("operand") or e.named_children[-1]
        if e.type == "identifier":
            return (scope["many"] if many else scope["one"]).get(src.text(e))
        if e.type == "selector_expression":
            t = self.expr_type(src, e.child_by_field_name("operand"), scope)
            return t and self.field_ref(t, src.text(e.child_by_field_name("field")), many)
        return None

    def function_scope(self, src, fn, scope):
        """The variables in a function that hold one of the PR's types, or a collection of one: the receiver and
        parameters, `var x T`, `x := T{…}`, and range variables. Names are not scoped further than the function."""
        one, many = {}, {}
        scope = {**scope, "one": one, "many": many}

        def declare(names, t):
            for nm in names:
                if tid := self.type_id(src, t, scope):
                    one[src.text(nm)] = tid
                if eid := self.elem_id(src, t, scope):
                    many[src.text(nm)] = eid

        for plist in (fn.child_by_field_name("receiver"), fn.child_by_field_name("parameters")):
            for p in plist.named_children if plist else []:
                if p.type in ("parameter_declaration", "variadic_parameter_declaration"):
                    declare(p.children_by_field_name("name"), p.child_by_field_name("type"))
        for n in goast.walk(fn):
            if n.type == "var_spec":
                declare(n.children_by_field_name("name"), n.child_by_field_name("type"))
            elif n.type == "short_var_declaration":
                for l, r in zip(n.child_by_field_name("left").named_children, n.child_by_field_name("right").named_children):
                    lit = r.child_by_field_name("operand") if r.type == "unary_expression" else r
                    if lit is not None and lit.type == "composite_literal":
                        declare([l], lit.child_by_field_name("type"))
            elif n.type == "range_clause" and n.child_by_field_name("left") is not None:
                names = n.child_by_field_name("left").named_children
                if len(names) == 2 and (tid := self.expr_type(src, n.child_by_field_name("right"), scope, many=True)):
                    one[src.text(names[1])] = tid
        return scope

    def resolve_call(self, src, fn, scope):
        """The function a call names and the identifier to mark: one in the same package, `pkg.F` in another of the
        PR's packages, or `x.M` where x's type is known, or where only one of the PR's types has a method M."""
        if fn.type == "identifier":
            fid = f"{scope['dir']}:{src.text(fn)}"
            return (fid, fn) if fid in self.funcs else None
        if fn.type != "selector_expression":
            return None
        operand, field = fn.child_by_field_name("operand"), fn.child_by_field_name("field")
        name = src.text(field)
        if operand.type == "identifier" and src.text(operand) in scope["imports"]:
            fid = f"{scope['imports'][src.text(operand)]}:{name}"
            return (fid, field) if fid in self.funcs else None
        t = self.expr_type(src, operand, scope)
        if t and f"{t}.{name}" in self.funcs:
            return f"{t}.{name}", field
        ms = self.methods.get(name, [])
        return (ms[0], field) if len(ms) == 1 else None

    def symbols(self, path):
        """Calls and type names in a file that resolve into the PR's packages: {line: [(start, end, id, kind)]}."""
        if path not in self._symbols:
            src, out = self.src(path), {}
            base = {"dir": os.path.dirname(path), "imports": self.imports(src), "one": {}, "many": {}}
            for top in src.root.named_children:
                scope = self.function_scope(src, top, base) if top.type in ("function_declaration", "method_declaration") else base
                for n in goast.walk(top):
                    hit = None
                    if n.type == "call_expression":
                        found = self.resolve_call(src, n.child_by_field_name("function"), scope)
                        hit = found and (found[0], found[1], "call")
                    elif n.type == "type_identifier":
                        parent = n.parent
                        declared = parent.type in ("type_spec", "type_alias") and parent.child_by_field_name("name").start_byte == n.start_byte
                        tid = self.type_id(src, parent if parent.type == "qualified_type" else n, scope)
                        hit = (tid, n, "type") if tid and not declared else None
                    if hit:
                        ref, node, kind = hit
                        out.setdefault(goast.first(node), []).append((node.start_point[1], node.end_point[1], ref, kind))
            self._symbols[path] = out
        return self._symbols[path]

    def row_links(self, path, n):
        links = super().row_links(path, n)
        if not path.endswith(".go") or self.text(path) is None:
            return links
        taken = [l[:2] for l in links]
        peeks = [(a, b, ref, "peek") for a, b, ref, _ in self.symbols(path).get(n, []) if not any(a < y and x < b for x, y in taken)]
        self.peek_used.update(p[2] for p in peeks)
        return links + peeks

    def peek_blocks(self):
        """For each symbol a shown row marks: the start of its declaration and, for a function, its call sites in the
        PR's packages, the changed ones in full. Marks inside a peek open further peeks, up to PEEK_MAX symbols."""
        paths = {p for p in self.changed_paths if p.endswith(".go") and self.text(p) is not None}
        for d in self.dirs:
            paths |= {f for f in self.repo.ls(self.repo.head, d) if f.endswith(".go")}
        sites = {}
        for path in sorted(paths):
            for line, marks in sorted(self.symbols(path).items()):
                for *_, ref, kind in marks:
                    if kind == "call":
                        sites.setdefault(ref, []).append((path, line))
        out, todo = {}, sorted(self.peek_used)
        while todo and len(out) < PEEK_MAX:
            sid = todo.pop()
            if sid in out:
                continue
            before = set(self.peek_used)
            if sid in self.funcs:
                f = self.funcs[sid]
                kind, name, file, line, start, end = "func", f["name"], f["file"], f["line"], f["start"], f["end"]
            else:
                t = self.types[sid]
                kind, name, file, line, (start, end) = "type", t["name"], t["file"], t["line"], t["code"]
            calls, other = [], 0
            for p, n in sites.get(sid, []):
                if n not in self.changed(p):
                    other += 1
                    continue
                toks, _ = self.tokens(p)
                line_toks = list(toks[n - 1])
                while line_toks and not line_toks[0][1].strip():
                    line_toks.pop(0)
                if line_toks:
                    line_toks[0] = (line_toks[0][0], line_toks[0][1].lstrip())
                calls.append({"file": p, "line": n, "url": self.repo.url(p, n), "h": render(line_toks, [], "")})
            out[sid] = {"kind": kind, "name": name, "file": file, "line": line, "url": self.repo.url(file, line),
                        "isNew": self.is_new(file), "rows": self.code_rows(file, start, min(end, start + PEEK_ROWS - 1)),
                        "more": max(0, end - start + 1 - PEEK_ROWS), "calls": calls, "otherCalls": other}
            todo += sorted(self.peek_used - before)
        return out

    def flow(self):
        """The functions the PR changes in the order they call each other: depth-first from the ones no other changed
        function calls, the widest first. Each comes with its whole code as a read-mode window."""
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

        roots = sorted((fid for fid in changed if not callers[fid]), key=lambda fid: -len(reach(fid, set())))
        order, depth = [], {}

        def visit(fid, d):
            if fid not in depth:
                depth[fid] = d
                order.append(fid)
                for c in calls[fid]:
                    visit(c, d + 1)

        for fid in roots + changed:
            visit(fid, 0)
        nodes = []
        for fid in order:
            f = self.funcs[fid]
            fd = self.repo.file(f["file"])
            win, struct = head_window(fd, f["start"], f["end"])
            self.annotate(fd, win, struct)
            nodes.append({"id": fid, "name": f["name"], "file": f["file"], "line": f["line"], "depth": depth[fid],
                          "calls": calls[fid], "callers": callers[fid], "window": win})
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
        flow = labviews.flow()
        data, _ = builder.build()
    except build.SpecError as e:
        raise SystemExit(f"spec error: {e}")
    lab = {"peek": labviews.peek_blocks(), "flow": flow}
    data["types"] = labviews.type_blocks()  # again, now that peeks have linked more types
    with open(build.TEMPLATE) as f:
        page = f.read().replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))
    with open(OVERLAY) as f:
        overlay = f.read().replace("/*LAB*/null", json.dumps(lab).replace("</", "<\\/"))
    page = page.replace("</body>", overlay + "\n</body>")
    out = args.out or os.path.join(os.environ.get("BB_THREAD_STORAGE", "."), "reports", f"pr{spec.get('pr', 'x')}-lab.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write(page)
    print(f"{len(lab['peek'])} peekable symbols, {len(flow)} changed functions; {len(page) / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
