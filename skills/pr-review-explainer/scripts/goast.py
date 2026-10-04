"""Go source facts for the lab experiments, read with tree-sitter: struct and enum models, table-driven tests, and
error-handling blocks. Lines are 1-based."""

import re

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser

PARSER = Parser(Language(tsgo.language()))
ERR_CHECK = re.compile(r"\b\w*[eE]rr\w*\s*!=\s*nil\b")
ERR_NAME = re.compile(r"^\w*[eE]rr\w*$")
ERR_MAKER = re.compile(r"\b(?:fmt\.Errorf|errors\.(?:New|Join|Wrap\w*)|multierr\.\w+)\(")
EXIT_CALL = re.compile(r"^(?:panic|[\w.]*\.(?:Fatal|Panic)\w*)$")
BUILTINS = {"append", "cap", "clear", "close", "complex", "copy", "delete", "imag", "len", "make", "max", "min", "new",
            "print", "println", "real", "recover", "bool", "byte", "rune", "string", "error", "int", "int8", "int16",
            "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64", "uintptr", "float32", "float64"}


class Src:
    def __init__(self, text):
        self.b = (text or "").encode()
        self.root = PARSER.parse(self.b).root_node

    def text(self, n):
        return self.b[n.start_byte:n.end_byte].decode() if n else ""


def first(n):
    return n.start_point[0] + 1


def last(n):
    return n.end_point[0] + 1


def flat(s):
    return " ".join(s.split())


def walk(n):
    yield n
    for c in n.named_children:
        yield from walk(c)


def doc_before(src, n):
    """The comment lines directly above a declaration."""
    lines, prev = [], n.prev_named_sibling
    while prev is not None and prev.type == "comment" and last(prev) == first(n) - len(lines) - 1:
        lines.insert(0, re.sub(r"^//\s?", "", src.text(prev)))
        prev = prev.prev_named_sibling
    return " ".join(lines)


def doc_start(n):
    """The first line of the comment block directly above a declaration, or the declaration's own line."""
    line, prev = first(n), n.prev_named_sibling
    while prev is not None and prev.type == "comment" and last(prev) == line - 1:
        line, prev = first(prev), prev.prev_named_sibling
    return line


def trailing_comment(src, n):
    nxt = n.next_named_sibling
    if nxt is not None and nxt.type == "comment" and first(nxt) == last(n):
        return re.sub(r"^//\s?", "", src.text(nxt))
    return ""


def statements(block):
    """A block's statements without comments; tree-sitter-go wraps them in a statement_list."""
    kids = [c for c in block.named_children if c.type != "comment"] if block else []
    if len(kids) == 1 and kids[0].type == "statement_list":
        kids = [c for c in kids[0].named_children if c.type != "comment"]
    return kids


# ---------------- declarations ----------------

def fields(src, struct):
    out = []
    flist = next((c for c in struct.named_children if c.type == "field_declaration_list"), None)
    for f in flist.named_children if flist else []:
        if f.type != "field_declaration":
            continue
        tnode = f.child_by_field_name("type")
        tag = src.text(f.child_by_field_name("tag")).strip("`")
        info = {"type": src.text(tnode), "tnode": tnode, "tag": tag, "line": first(f),
                "doc": doc_before(src, f) or trailing_comment(src, f)}
        names = f.children_by_field_name("name")
        if names:
            out += [{**info, "name": src.text(nm)} for nm in names]
        else:
            out.append({**info, "name": src.text(tnode).lstrip("*").split(".")[-1], "embedded": True})
    return out


def receiver_type(src, n):
    recv = n.child_by_field_name("receiver")
    t = next((w for w in walk(recv) if w.type == "type_identifier"), None)
    return src.text(t)


def declarations(src):
    """Top-level functions, methods (named Recv.Name) and types."""
    out = {}
    for n in src.root.named_children:
        if n.type in ("function_declaration", "method_declaration"):
            name = src.text(n.child_by_field_name("name"))
            key = f"{receiver_type(src, n)}.{name}" if n.type == "method_declaration" else name
            out[f"func {key}"] = {"kind": "func", "name": key, "node": n, "start": first(n), "end": last(n)}
        elif n.type == "type_declaration":
            for spec in n.named_children:
                if spec.type not in ("type_spec", "type_alias"):
                    continue
                name = src.text(spec.child_by_field_name("name"))
                t = spec.child_by_field_name("type")
                info = {"kind": "type", "name": name, "node": spec, "start": first(spec), "end": last(spec),
                        "underlying": flat(src.text(t))}
                if t.type == "struct_type":
                    info["fields"] = fields(src, t)
                out[f"type {name}"] = info
    return out


# ---------------- types: structs and enums ----------------

def type_ref(src, t, known):
    """A field type as wrappers around a base: *T a pointer, []T a list, map[K]V a map; `ref` names a known type, and
    `span` is the base's (line, start byte, end byte) within its line."""
    mods = []
    while t is not None and t.type in ("pointer_type", "slice_type", "array_type", "map_type", "parenthesized_type"):
        if t.type == "pointer_type":
            mods.append("pointer")
            t = t.named_children[-1]
        elif t.type in ("slice_type", "array_type"):
            mods.append("list")
            t = t.child_by_field_name("element")
        elif t.type == "map_type":
            mods.append(f"map[{src.text(t.child_by_field_name('key'))}]")
            t = t.child_by_field_name("value")
        else:
            t = t.named_children[0]
    base = src.text(t)
    name = base.split(".")[-1]
    span = [first(t), t.start_point[1], t.end_point[1]] if first(t) == last(t) else None
    return {"base": base, "mods": mods, "ref": name if name in known else None, "span": span}


def xml_name(tag):
    m = re.search(r'xml:"([^"]*)"', tag or "")
    if not m:
        return None
    name, *opts = m.group(1).split(",")
    return {"name": name, "attr": "attr" in opts, "omitempty": "omitempty" in opts}


def enum_values(src):
    """Typed constants per type, with iota expanded: {type: [{name, value, derived, doc, line, block}]}. `derived` is set
    when the source doesn't spell the value out (iota, or a spec repeating the one above); `block` is the const
    declaration's line range."""
    out = {}
    for n in src.root.named_children:
        if n.type != "const_declaration":
            continue
        cur, expr = None, None
        for i, spec in enumerate(s for s in n.named_children if s.type == "const_spec"):
            t, v = spec.child_by_field_name("type"), spec.child_by_field_name("value")
            if t is not None:
                cur = src.text(t)
            if v is not None:
                expr = flat(src.text(v))
            if cur is None or expr is None:
                continue
            value = str(i) if expr == "iota" else (re.sub(r"\biota\b", str(i), expr) if "iota" in expr else expr)
            if re.fullmatch(r"[\d\s+*-]+", value):
                value = str(eval(value))  # constant integer arithmetic from iota offsets, digits and operators only
            for nm in spec.children_by_field_name("name"):
                out.setdefault(cur, []).append({"name": src.text(nm), "value": value, "derived": v is None or "iota" in expr,
                                                "line": first(spec), "block": [first(n), last(n)],
                                                "doc": doc_before(src, spec) or trailing_comment(src, spec)})
    return out


def types(files):
    """Every type declared in a package's sources: structs with their fields, enums with their values, and the methods
    defined on each. `files` maps path to source text."""
    parsed = {p: Src(t) for p, t in files.items()}
    names = set()
    for src in parsed.values():
        names |= {d["name"] for d in declarations(src).values() if d["kind"] == "type"}
    out, methods, values = {}, {}, {}
    for path, src in parsed.items():
        for t, vals in enum_values(src).items():
            values.setdefault(t, []).extend({**v, "file": path} for v in vals)
        for d in declarations(src).values():
            if d["kind"] == "func" and "." in d["name"]:
                recv, name = d["name"].split(".", 1)
                methods.setdefault(recv, []).append({"name": name, "file": path, "start": doc_start(d["node"]), "end": d["end"]})
            if d["kind"] != "type":
                continue
            decl = d["node"].parent if d["node"].parent.type == "type_declaration" else d["node"]
            info = {"name": d["name"], "file": path, "line": d["start"], "end": d["end"], "code": [doc_start(decl), last(decl)],
                    "doc": doc_before(src, decl), "underlying": d["underlying"]}
            if "fields" in d:
                info["fields"] = [{"name": f["name"], "type": f["type"], "line": f["line"], "embedded": f.get("embedded", False), "doc": f["doc"],
                                   "xml": xml_name(f["tag"]), "omitempty": ",omitempty" in (f["tag"] or ""),
                                   **type_ref(src, f["tnode"], names)} for f in d["fields"]]
            out[d["name"]] = info
    for t, vals in values.items():
        if t in out:
            out[t]["values"] = vals
    for t, ms in methods.items():
        if t in out:
            out[t]["methods"] = ms
    return out


# ---------------- table-driven tests ----------------

def cell(src, n):
    inner = n.named_children[0] if n.type == "literal_element" and n.named_children else n
    text = src.text(inner)
    s = None
    if inner.type == "interpreted_string_literal":
        s = text[1:-1].encode().decode("unicode_escape", errors="replace")
    elif inner.type == "raw_string_literal":
        s = text[1:-1]
    return {"code": text, "str": s, "multiline": "\n" in text}


def table_tests(src):
    """`name := []struct{...}{...}` and `map[string]struct{...}{...}` literals inside test functions."""
    out = []
    for fn in src.root.named_children:
        if fn.type != "function_declaration":
            continue
        fname = src.text(fn.child_by_field_name("name"))
        for n in walk(fn):
            if n.type != "short_var_declaration":
                continue
            lit = n.child_by_field_name("right").named_children[0] if n.child_by_field_name("right").named_children else None
            if lit is None or lit.type != "composite_literal":
                continue
            ltype, body = lit.child_by_field_name("type"), lit.child_by_field_name("body")
            keyed = ltype.type == "map_type"
            st = (ltype.child_by_field_name("value") if keyed else ltype.child_by_field_name("element")) if ltype.type in ("map_type", "slice_type") else None
            if st is None or st.type != "struct_type":
                continue
            cols = (["key"] if keyed else []) + [f["name"] for f in fields(src, st)]
            rows = []
            for el in body.named_children:
                if el.type == "comment":
                    continue
                key, value = (el.child_by_field_name("key"), el.child_by_field_name("value")) if el.type == "keyed_element" else (None, el)
                lv = value.named_children[0] if value.type == "literal_element" and value.named_children else value
                if lv.type != "literal_value":
                    continue
                cells = {"key": cell(src, key)} if keyed and key is not None else {}
                pos = [c for c in lv.named_children if c.type != "comment"]
                for i, c in enumerate(pos):
                    if c.type == "keyed_element":
                        cells[src.text(c.child_by_field_name("key"))] = cell(src, c.child_by_field_name("value"))
                    elif i + keyed < len(cols):
                        cells[cols[i + keyed]] = cell(src, c)
                rows.append({"line": first(el), "end": last(el), "cells": cells})
            if not rows:
                continue
            var = src.text(n.child_by_field_name("left"))
            out.append({"func": fname, "var": var, "line": first(n), "end": last(n), "columns": cols, "rows": rows,
                        "fn": [doc_start(fn), last(fn)], "cases": [rows[0]["line"], rows[-1]["end"]]})
    return out


# ---------------- error handling ----------------

def error_statement(src, s):
    """A statement whose only job is an error: returning one, recording one in an err variable, a panic, or a Fatal
    log. os.Exit is left out because signal handlers use it to shut down cleanly."""
    if s.type == "return_statement":
        values = s.named_children[0].named_children if s.named_children else []
        return any(ERR_MAKER.search(src.text(v)) or ERR_NAME.match(src.text(v)) for v in values)
    if s.type in ("assignment_statement", "short_var_declaration"):
        return all(ERR_NAME.match(src.text(x)) for x in s.child_by_field_name("left").named_children)
    if s.type == "expression_statement" and s.named_children and s.named_children[0].type == "call_expression":
        return bool(EXIT_CALL.match(src.text(s.named_children[0].child_by_field_name("function"))))
    return False


def handles_error(src, n):
    """An if with no else that checks `err != nil`, or whose body does nothing but error statements, optionally
    followed by a continue or break."""
    if n.child_by_field_name("alternative") is not None:
        return False
    if ERR_CHECK.search(src.text(n.child_by_field_name("condition"))):
        return True
    body = statements(n.child_by_field_name("consequence"))
    if body and body[-1].type in ("continue_statement", "break_statement"):
        body = body[:-1]
    return bool(body) and all(error_statement(src, s) for s in body)


def work_calls(src, n):
    """The outermost calls under n that do real work: anything but error constructors, builtins, conversions to
    builtin types, panics and Fatal logs. Each is a list of (line, start byte, end byte or None for the line's end)."""
    out = []

    def visit(m):
        if m.type == "call_expression":
            fn = src.text(m.child_by_field_name("function"))
            if not (ERR_MAKER.fullmatch(fn + "(") or fn in BUILTINS or EXIT_CALL.match(fn)):
                (r1, c1), (r2, c2) = m.start_point, m.end_point
                out.append([[r + 1, c1 if r == r1 else 0, c2 if r == r2 else None] for r in range(r1, r2 + 1)])
                return
        for c in m.named_children:
            visit(c)

    visit(n)
    return out


def error_blocks(src):
    """Error-handling if blocks, outermost only: their line range and the calls in them that do real work."""
    out = []

    def visit(n):
        if n.type == "if_statement" and handles_error(src, n):
            out.append({"start": first(n), "end": last(n), "calls": work_calls(src, n)})
            return
        for c in n.named_children:
            visit(c)

    visit(src.root)
    return out
