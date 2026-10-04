"""Go-aware renderings for read mode, from tree-sitter (goast.py): field types that link to their declarations, with
badges that spell the type out; table-driven test cases shown as a table; and error handling folded to one line. Other
languages get none of them."""

import itertools
import os
import re

import goast
from highlight import char_links, css_class, lexer_for, render, tokens_by_line

ONE_LINE_STMTS = 2
MIN_CASES = 2


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
    join with a space, statements with "; ", and only the first few statements are kept. Everything but the block's
    work calls is dimmed."""
    ranges = {}
    for call in block["calls"]:
        for n, a, b in call:
            text = lines[n - 1]
            ranges.setdefault(n, []).extend(r[:2] for r in char_links(text, [(a, len(text.encode()) if b is None else b, "")]))
    rows = [split_live(tokens[n - 1], ranges.get(n, [])) for n in range(block["start"], block["end"] + 1)]
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


def badges(field):
    """A field's type spelled out at the end of its line: optional (an omitempty pointer), pointer, list of, map K →."""
    out = []
    for m in field["mods"]:
        if m == "pointer":
            out.append(["opt", "optional"] if field["omitempty"] else ["ptr", "pointer"])
        elif m == "list":
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
        dirs = sorted({os.path.dirname(p) for p in self.changed_paths
                       if p.endswith(".go") and not p.endswith("_test.go") and self.text(p) is not None})
        out = {}
        for d in dirs:
            files = {f: self.text(f) for f in self.repo.ls(self.repo.head, d) if f.endswith(".go") and not f.endswith("_test.go")}
            out.update({f"{d}:{name}": t for name, t in goast.types(files).items()})
        by_pkg = {os.path.basename(d): d for d in dirs}
        for tid, t in out.items():
            d = tid.rsplit(":", 1)[0]
            for f in t.get("fields", []):
                pkg, _, name = f["base"].rpartition(".")
                target = f"{by_pkg.get(pkg) if pkg else d}:{name}"
                f["ref"] = target if target in out else None
        return out

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
            xrefs = char_links(lines[n - 1], self.links.get((path, n), []))
            row = {"k": "+" if is_new or n in changed else " ", "o": None, "n": n, "h": render(toks[n - 1], [], "", [(xrefs, "xref")])}
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
            links = self.links.get((fd.path, r["n"]))
            if links:
                win["rows"][i] = fd.html_row(r, links)
                self.used.update(ref for *_, ref in links)
            if (fd.path, r["n"]) in self.ann:
                win["rows"][i]["ann"] = self.ann[(fd.path, r["n"])]
        win["folds"] = self.window_folds(fd.path, struct, index)
        win["tables"] = self.window_tables(fd.path, struct, index, fd.is_new)
