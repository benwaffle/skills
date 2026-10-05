"""Go-aware renderings for read mode, from tree-sitter (goast.py): field types that link to their declarations, with
badges that spell the type out; calls and type names that peek at their declarations; table-driven test cases shown as
a table; and error handling folded to one line. Other languages get none of them."""

import itertools
import os
import re
from collections import Counter

import goast
from highlight import char_links, css_class, lexer_for, render, tokens_by_line

ONE_LINE_STMTS = 2
MIN_CASES = 2
DIFF_MIN = 3
PEEK_ROWS = 40
PEEK_MAX = 300


def strip_indent(tokens):
    out = [t for t in tokens]
    while out and not out[0][1].strip():
        out.pop(0)
    if out:
        out[0] = (out[0][0], out[0][1].lstrip(), out[0][2])
    return out


def split_live(tokens, ranges):
    """A line's tokens as (class, text, live), cut where the char ranges start and end; live is inside a range."""
    out, pos = [], 0
    for cls, text in tokens:
        cuts = sorted({0, len(text), *[max(0, min(len(text), b - pos)) for r in ranges for b in r]})
        out += [(cls, text[a:b], any(s <= pos + a < e for s, e in ranges)) for a, b in zip(cuts, cuts[1:])]
        pos += len(text)
    return out


def render_dimmed(tokens):
    out = []
    for live, run in itertools.groupby(tokens, key=lambda t: t[2]):
        html = render([(c, t) for c, t, _ in run], [], "")
        out.append(html if live else f'<span class="dim">{html}</span>')
    return "".join(out)


def one_line(tokens, lines, block):
    """An if-block's lines joined onto one highlighted line: `if err != nil { return err }`. Lines inside a statement
    join with a space, statements with "; ", and only the first few statements are kept. Comments are left out, since
    joined they read as commented-out code. Everything but the block's bright spans is dimmed."""
    ranges = {}
    for span in block["bright"]:
        for n, a, b in span:
            text = lines[n - 1]
            ranges.setdefault(n, []).extend(r[:2] for r in char_links(text, [(a, len(text.encode()) if b is None else b, "")]))
    rows = [[t for t in split_live(tokens[n - 1], ranges.get(n, [])) if not t[0].startswith("t-c")]
            for n in range(block["start"], block["end"] + 1)]
    rows = [l[:-1] + [(l[-1][0], l[-1][1].rstrip(), l[-1][2])] if l else l for l in rows]
    body = [strip_indent(l) for l in rows[1:-1] if any(t[1].strip() for t in l)]
    out, stmts = list(rows[0]) + [("", " ", False)], 0
    for i, line in enumerate(body):
        text = "".join(t[1] for t in line).rstrip()
        out += line
        continues = text.endswith(("(", "[", "{", ",", "+", "-", "*", "/", "&&", "||"))
        if not continues:
            stmts += 1
            if stmts == ONE_LINE_STMTS and i < len(body) - 1:
                out.append(("", "; … ", False))
                break
        sep = "" if text.endswith(("(", "[", "{")) else " " if continues else "; " if i < len(body) - 1 else " "
        out.append(("", sep, continues and line[-1][2]))
    out += strip_indent(rows[-1])
    return render_dimmed(out)


def flat_tokens(code):
    """A Go expression's tokens with every whitespace run collapsed to one space, for a table cell's one-line form."""
    out = []
    for ttype, value in lexer_for("cell.go").get_tokens(code):
        text = re.sub(r"\s+", " ", value)
        if out and out[-1][1].endswith(" ") and text.startswith(" "):
            text = text[1:]
        if text:
            out.append([css_class(ttype), text])
    return out


def full_html(code):
    """A multi-line Go expression, highlighted, with the indentation its lines share removed and tabs as two spaces."""
    lines = code.split("\n")
    indent = min((len(l) - len(l.lstrip("\t")) for l in lines[1:] if l.strip()), default=0)
    lines = [lines[0]] + [re.sub(r"^\t+", lambda m: "  " * len(m.group()), l[indent:]) for l in lines[1:]]
    return "\n".join(render(toks, [], "") for toks in tokens_by_line("\n".join(lines), "cell.go"))


def link_layers(text, links):
    """(start byte, end byte, ref[, mark class]) spans in a line as render() layers, one per mark class."""
    layers = {}
    for l in links:
        layers.setdefault(l[3] if len(l) > 3 else "xref", []).append(l[:3])
    return [(char_links(text, ls), cls) for cls, ls in layers.items()]


def usual_diffs(cells):
    """Gives each struct-literal cell in a column a short form, `diff`, with only the fields where it departs from the
    column's usual: a value other than the most common one for that field, a field most cells don't set, or one most
    cells set and this one doesn't. Cases that differ deep inside a long literal then look different. A column needs
    a few struct literals to have a usual."""
    comp = [c for c in cells if c and c.get("fields") is not None]
    if len(comp) < DIFF_MIN:
        return
    counts = {}
    for c in comp:
        for p, v in c["fields"].items():
            counts.setdefault(p, Counter())[v] += 1
    half = len(comp) / 2
    for c in comp:
        fields = c["fields"]
        diffs = [f"{p}: {v}" for p, v in fields.items() if sum(counts[p].values()) <= half or v != counts[p].most_common(1)[0][0]]
        for p, cnt in counts.items():
            if p in fields or sum(cnt.values()) <= half:
                continue
            ancestors = [p[:m.start()] for m in re.finditer(r"\.|\[", p)] + [p]
            if any(a in fields for a in ancestors):  # set here as a whole, by a call or a variable
                continue
            # A missing subtree shows once, at its root: `TransportKeys: unset`, not each of its fields.
            root = next(a for a in ancestors if not any(k == a or k.startswith((a + ".", a + "[")) for k in fields))
            if f"{root}: unset" not in diffs:
                diffs.append(f"{root}: unset")
        c["diff"] = flat_tokens(f"{c['ctype']}{{{', '.join(diffs) if diffs else '…'}}}")


def badges(field):
    """A field's type spelled out at the end of its line: optional (an omitempty pointer), pointer, list of, map K →."""
    out = []
    for m in field["mods"]:
        if m == "pointer":
            out.append(["opt", "optional"] if field["omitempty"] else ["ptr", "pointer"])
        elif m == "list":
            if field["base"] != "byte":  # []byte is a blob, not a list
                out.append(["list", "list of"])
        else:
            out.append(["map", re.sub(r"^map\[(.*)\]$", r"map \1 →", m)])
    return out


class Views:
    def __init__(self, repo):
        self.repo = repo
        self.changed_paths = {s["path"] for s in repo.numstat()}
        self._text, self._tokens, self._src, self._changed, self._folds, self._tables = {}, {}, {}, {}, {}, {}
        self.types = self._types()
        self.links, self.ann = self._line_index()
        self.used = set()
        self.funcs = self._funcs()
        self.methods = {}
        for fid, f in self.funcs.items():
            if "." in f["name"]:
                self.methods.setdefault(f["name"].split(".", 1)[1], []).append(fid)
        self._symbols, self.peek_used = {}, set()

    def text(self, path):
        if path not in self._text:
            self._text[path] = self.repo.show(self.repo.head, path)
        return self._text[path]

    def tokens(self, path):
        if path not in self._tokens:
            text = self.text(path) or ""
            self._tokens[path] = (tokens_by_line(text, path), text.split("\n"))
        return self._tokens[path]

    def src(self, path):
        if path not in self._src:
            self._src[path] = goast.Src(self.text(path))
        return self._src[path]

    def changed(self, path):
        """New lines with a real change, as difftastic sees it: alignment-only rows ('~') don't count."""
        if path not in self._changed:
            fd = self.repo.file(path) if path in self.changed_paths else None
            self._changed[path] = {r["n"] for rows in (fd.hunks if fd else []) for r in fd.structural([dict(x) for x in rows])
                                   if r["k"] in "+±" and r["n"]}
        return self._changed[path]

    def is_new(self, path):
        return path in self.changed_paths and self.repo.file(path).is_new

    # ---------------- types ----------------

    def _types(self):
        """Every type in the packages the PR touches, keyed `dir:Name`. A field's type resolves within its package, or
        across those packages by package name."""
        self.dirs = sorted({os.path.dirname(p) for p in self.changed_paths
                            if p.endswith(".go") and not p.endswith("_test.go") and self.text(p) is not None})
        self.by_pkg = {os.path.basename(d): d for d in self.dirs}
        out = {}
        for d in self.dirs:
            out.update({f"{d}:{name}": t for name, t in goast.types(self.package_files(d)).items()})
        for tid, t in out.items():
            d = tid.rsplit(":", 1)[0]
            for f in t.get("fields", []):
                pkg, _, name = f["base"].rpartition(".")
                target = f"{self.by_pkg.get(pkg) if pkg else d}:{name}"
                f["ref"] = target if target in out else None
        return out

    def package_files(self, d):
        """A package's non-test sources at the PR head."""
        return {f: self.text(f) for f in self.repo.ls(self.repo.head, d) if f.endswith(".go") and not f.endswith("_test.go")}

    def row_links(self, path, n):
        """The links on a line, as (start byte, end byte, ref[, mark class]): type links, whose class defaults to xref,
        and peeks."""
        links = self.links.get((path, n), [])
        if not path.endswith(".go") or self.text(path) is None:
            return links
        peeks = [(a, b, ref, "peek") for a, b, ref, _ in self.symbols(path).get(n, []) if not any(a < y and x < b for x, y, *_ in links)]
        self.peek_used.update(p[2] for p in peeks)
        return links + peeks

    def _line_index(self):
        """Per (file, line): its type links, as (start byte, end byte, type id), and its badges."""
        links, ann = {}, {}
        for t in self.types.values():
            seen = set()
            for f in t.get("fields", []):
                if f["line"] in seen:  # `a, b *T` declares two fields on one line
                    continue
                seen.add(f["line"])
                if f["ref"] and f["span"]:
                    line, a, b = f["span"]
                    links.setdefault((t["file"], line), []).append((a, b, f["ref"]))
                if bs := badges(f):
                    ann.setdefault((t["file"], f["line"]), []).extend(bs)
            for v in t.get("values", []):
                if v["derived"]:
                    ann.setdefault((v["file"], v["line"]), []).append(["val", f"= {v['value']}"])
        return links, ann

    def code_rows(self, path, start, end):
        """Head-side rows of a file, highlighted, with type links and badges."""
        toks, lines = self.tokens(path)
        changed, is_new = self.changed(path), self.is_new(path)
        rows = []
        for n in range(start, min(end, len(toks)) + 1):
            links = self.row_links(path, n)
            self.used.update(l[2] for l in links if len(l) == 3)
            row = {"k": "+" if is_new or n in changed else " ", "o": None, "n": n,
                   "h": render(toks[n - 1], [], "", link_layers(lines[n - 1], links))}
            if (path, n) in self.ann:
                row["ann"] = self.ann[(path, n)]
            rows.append(row)
        return rows

    def type_blocks(self):
        """The declaration of every type a shown row links to, and of the types those link to, with its typed constants
        and methods."""
        reach, todo = set(), list(self.used)
        while todo:
            tid = todo.pop()
            if tid not in reach:
                reach.add(tid)
                todo += [f["ref"] for f in self.types[tid].get("fields", []) if f["ref"]]
        out = {}
        for tid in sorted(reach):
            t = self.types[tid]
            a0, b0 = t["code"]
            rows = self.code_rows(t["file"], a0, b0)
            vals = sorted(t.get("values", []), key=lambda v: (v["file"], v["block"]))
            for f, group in itertools.groupby(vals, key=lambda v: v["file"]):
                group = list(group)
                a, b = min(v["block"][0] for v in group), max(v["block"][1] for v in group)
                if not (f == t["file"] and a == b0 + 1):
                    rows.append({"gap": True, "label": "" if f == t["file"] else os.path.basename(f)})
                rows += self.code_rows(f, a, b)
            methods = [{"name": m["name"], "rows": self.code_rows(m["file"], m["start"], m["end"])} for m in t.get("methods", [])]
            out[tid] = {"name": t["name"], "file": t["file"], "line": t["line"], "url": self.repo.url(t["file"], t["line"]),
                        "isNew": self.is_new(t["file"]), "rows": rows, "methods": methods}
        return out

    # ---------------- peeks ----------------

    def _funcs(self):
        """Every function and method in the packages the PR touches, keyed `dir:Name` or `dir:Type.Method`."""
        out = {}
        for d in self.dirs:
            for path in self.package_files(d):
                for decl in goast.declarations(self.src(path)).values():
                    if decl["kind"] == "func":
                        out[f"{d}:{decl['name']}"] = {"name": decl["name"], "file": path, "line": decl["start"],
                                                      "start": goast.doc_start(decl["node"]), "end": decl["end"]}
        return out

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

    def outside_type(self, src, t, scope):
        """Whether a type node names a type from a package outside the PR, through pointers."""
        while t is not None and t.type in ("pointer_type", "parenthesized_type"):
            t = t.named_children[-1]
        return t is not None and t.type == "qualified_type" and scope["imports"].get(src.text(t.child_by_field_name("package")), "") is None

    def outside_call(self, src, e, scope):
        """Whether an expression calls a function of a package outside the PR, like `sqlx.Open(…)`."""
        fn = e.child_by_field_name("function") if e.type == "call_expression" else None
        if fn is None or fn.type != "selector_expression" or fn.child_by_field_name("operand").type != "identifier":
            return False
        return scope["imports"].get(src.text(fn.child_by_field_name("operand")), "") is None

    def from_outside(self, src, e, scope):
        """Whether an expression holds a value of a type outside the PR, as far as the function's declarations say: a
        variable of such a type or from an outside call, or a field of one of the PR's types that isn't one of them."""
        while e.type in ("parenthesized_expression", "unary_expression"):
            e = e.child_by_field_name("operand") or e.named_children[-1]
        if e.type == "identifier":
            return src.text(e) in scope["outside"]
        if e.type == "call_expression":
            return self.outside_call(src, e, scope)
        if e.type == "selector_expression":
            operand = e.child_by_field_name("operand")
            if self.from_outside(src, operand, scope):
                return True
            t = self.expr_type(src, operand, scope)
            name = src.text(e.child_by_field_name("field"))
            f = next((f for f in self.types.get(t, {}).get("fields", []) if f["name"] == name), None) if t else None
            return f is not None and not f["ref"]
        return False

    def function_scope(self, src, fn, scope):
        """The variables in a function that hold one of the PR's types, or a collection of one: the receiver and
        parameters, `var x T`, `x := T{…}`, and range variables. Also the ones that hold a type from outside the PR,
        declared with one or assigned from an outside call. Names are not scoped further than the function."""
        one, many, outside = {}, {}, set()
        scope = {**scope, "one": one, "many": many, "outside": outside}

        def declare(names, t):
            for nm in names:
                if tid := self.type_id(src, t, scope):
                    one[src.text(nm)] = tid
                if eid := self.elem_id(src, t, scope):
                    many[src.text(nm)] = eid
                if self.outside_type(src, t, scope):
                    outside.add(src.text(nm))

        for plist in (fn.child_by_field_name("receiver"), fn.child_by_field_name("parameters")):
            for p in plist.named_children if plist else []:
                if p.type in ("parameter_declaration", "variadic_parameter_declaration"):
                    declare(p.children_by_field_name("name"), p.child_by_field_name("type"))
        for n in goast.walk(fn):
            if n.type == "var_spec":
                declare(n.children_by_field_name("name"), n.child_by_field_name("type"))
            elif n.type == "short_var_declaration":
                left, right = n.child_by_field_name("left").named_children, n.child_by_field_name("right").named_children
                if len(right) == 1 and self.outside_call(src, right[0], scope):
                    outside.update(src.text(l) for l in left)
                for l, r in zip(left, right):
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
        PR's packages, or `x.M` where x's type is known, or where only one of the PR's types has a method M and x isn't
        known to come from outside the PR."""
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
        if self.from_outside(src, operand, scope):
            return None
        ms = self.methods.get(name, [])
        return (ms[0], field) if len(ms) == 1 else None

    def symbols(self, path):
        """Calls and type names in a file that resolve into the PR's packages: {line: [(start, end, id, kind)]}."""
        if path not in self._symbols:
            src, out = self.src(path), {}
            base = {"dir": os.path.dirname(path), "imports": self.imports(src), "one": {}, "many": {}, "outside": set()}
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

    def peek_blocks(self):
        """The start of the declaration of each symbol a shown row peeks at. Peeks inside a peek open further peeks, up
        to PEEK_MAX symbols."""
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
            out[sid] = {"kind": kind, "name": name, "file": file, "line": line, "url": self.repo.url(file, line),
                        "isNew": self.is_new(file), "rows": self.code_rows(file, start, min(end, start + PEEK_ROWS - 1)),
                        "more": max(0, end - start + 1 - PEEK_ROWS)}
            todo += sorted(self.peek_used - before)
        return out

    # ---------------- error handling ----------------

    def error_folds(self, path):
        if path not in self._folds:
            toks, lines = self.tokens(path)
            self._folds[path] = [{**b, "html": one_line(toks, lines, b)} for b in goast.error_blocks(self.src(path))]
        return self._folds[path]

    def window_folds(self, path, struct, index):
        """Error-handling blocks wholly inside the window whose rows are all unchanged or all added, so a fold never
        hides a change."""
        out = []
        for b in self.error_folds(path):
            if not all(n in index for n in range(b["start"], b["end"] + 1)):
                continue
            a, z = index[b["start"]], index[b["end"]]
            kinds = {struct[i]["k"] for i in range(a, z + 1)}
            if kinds <= {" ", "~"} or kinds == {"+"}:
                out.append({"a": a, "b": z, "k": "+" if kinds == {"+"} else " ", "html": b["html"]})
        return out

    # ---------------- table-driven tests ----------------

    def table_tests(self, path):
        if path not in self._tables:
            tables = goast.table_tests(self.src(path))
            for t in tables:
                for r in t["rows"]:
                    for c in r["cells"].values():
                        if c["str"] is None or c["multiline"]:
                            c["toks"] = flat_tokens(c["code"])
                            if c["multiline"]:
                                c["html"] = full_html(c["code"])
                for col in t["columns"]:
                    usual_diffs([r["cells"].get(col) for r in t["rows"]])
                for r in t["rows"]:
                    for c in r["cells"].values():
                        c.pop("fields", None)
                        c.pop("ctype", None)
            self._tables[path] = tables
        return self._tables[path]

    def window_tables(self, path, struct, index, is_new):
        """Test-case literals wholly inside the window, as a table to show in their place. A window that deletes a
        case keeps its code, since the table only has the new side."""
        out = []
        for t in self.table_tests(path):
            cases = [c for c in t["rows"] if all(n in index for n in range(c["line"], c["end"] + 1))]
            if len(cases) < MIN_CASES:
                continue
            a, z = index[cases[0]["line"]], index[cases[-1]["end"]]
            if any(struct[i]["k"] == "-" for i in range(a, z + 1)):
                continue
            rows = []
            for c in cases:
                keys = [index[n] for n in range(c["line"], c["end"] + 1)]
                kinds = {struct[i]["k"] for i in keys}
                mark = None if is_new else "added" if kinds == {"+"} else "changed" if kinds & {"+", "±"} else None
                rows.append({"cells": c["cells"], "keys": keys, "mark": mark})
            out.append({"a": a, "b": z, "columns": t["columns"], "rows": rows, "total": len(t["rows"])})
        return out

    # ---------------- windows ----------------

    def annotate(self, fd, win, struct):
        """Adds the renderings to a read-mode window whose rows render `struct`: type links and badges on its rows, and
        error-handling folds and test-case tables as row index ranges."""
        if not fd.path.endswith(".go") or fd.is_deleted:
            return
        index = {}
        for i, r in enumerate(struct):
            if not r["n"] or r["k"] == "-":
                continue
            index[r["n"]] = i
            links = self.row_links(fd.path, r["n"])
            if links:
                win["rows"][i] = fd.html_row(r, links)
                self.used.update(l[2] for l in links if len(l) == 3)
            if (fd.path, r["n"]) in self.ann:
                win["rows"][i]["ann"] = self.ann[(fd.path, r["n"])]
        win["folds"] = self.window_folds(fd.path, struct, index)
        win["tables"] = self.window_tables(fd.path, struct, index, fd.is_new)
