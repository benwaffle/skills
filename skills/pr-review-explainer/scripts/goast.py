"""Go source facts for the lab experiments, read with tree-sitter: how each declaration changed, struct and enum models,
table-driven tests, and error-handling blocks. Lines are 1-based."""

import difflib
import re

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser

PARSER = Parser(Language(tsgo.language()))
ERR_CHECK = re.compile(r"\b\w*[eE]rr\w*\s*!=\s*nil\b")


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


def flat(s, limit=None):
    s = " ".join(s.split())
    return s if limit is None or len(s) <= limit else s[:limit - 1] + "…"


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


def trailing_comment(src, n):
    nxt = n.next_named_sibling
    if nxt is not None and nxt.type == "comment" and first(nxt) == last(n):
        return re.sub(r"^//\s?", "", src.text(nxt))
    return ""


# ---------------- declarations and how they changed ----------------

def params(src, plist):
    out = []
    for p in plist.named_children if plist else []:
        if p.type not in ("parameter_declaration", "variadic_parameter_declaration"):
            continue
        t = src.text(p.child_by_field_name("type"))
        if p.type == "variadic_parameter_declaration":
            t = "..." + t
        names = p.children_by_field_name("name") or [None]
        out += [{"name": src.text(nm) if nm else "", "type": t, "line": first(p)} for nm in names]
    return out


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
    out = {}
    for n in src.root.named_children:
        if n.type in ("function_declaration", "method_declaration"):
            name = src.text(n.child_by_field_name("name"))
            key = f"{receiver_type(src, n)}.{name}" if n.type == "method_declaration" else name
            body = n.child_by_field_name("body")
            out[f"func {key}"] = {
                "kind": "func", "name": key, "node": n, "start": first(n), "end": last(n),
                "params": params(src, n.child_by_field_name("parameters")),
                "result": flat(src.text(n.child_by_field_name("result"))),
                "body": body, "norm": flat(src.text(n)),
            }
        elif n.type == "type_declaration":
            for spec in n.named_children:
                if spec.type not in ("type_spec", "type_alias"):
                    continue
                name = src.text(spec.child_by_field_name("name"))
                t = spec.child_by_field_name("type")
                info = {"kind": "type", "name": name, "node": spec, "start": first(spec), "end": last(spec),
                        "shape": t.type, "underlying": flat(src.text(t)), "norm": flat(src.text(spec))}
                if t.type == "struct_type":
                    info["fields"] = fields(src, t)
                out[f"type {name}"] = info
        elif n.type in ("const_declaration", "var_declaration"):
            kind = n.type.split("_")[0]
            for spec in walk(n):
                if spec.type not in ("const_spec", "var_spec"):
                    continue
                value = flat(src.text(spec.child_by_field_name("value")))
                for nm in spec.children_by_field_name("name"):
                    out[f"{kind} {src.text(nm)}"] = {"kind": kind, "name": src.text(nm), "node": spec, "start": first(spec),
                                                     "end": last(spec), "value": value, "norm": flat(src.text(spec))}
        elif n.type == "import_declaration":
            for spec in walk(n):
                if spec.type == "import_spec":
                    path = src.text(spec.child_by_field_name("path")).strip('"')
                    out[f"import {path}"] = {"kind": "import", "name": path, "node": spec, "start": first(spec),
                                             "end": last(spec), "norm": flat(src.text(spec))}
    return out


def calls(src, body):
    out = []
    for n in walk(body) if body else []:
        if n.type == "call_expression":
            args = n.child_by_field_name("arguments")
            out.append({"callee": flat(src.text(n.child_by_field_name("function"))), "line": first(n),
                        "args": [{"text": flat(src.text(a)), "line": first(a)} for a in args.named_children if a.type != "comment"]})
    return out


def sig(d):
    ps = ", ".join(f"{p['name']} {p['type']}".strip() for p in d["params"])
    return f"{d['name']}({flat(ps, 60)})" + (f" {d['result']}" if d["result"] else "")


def short_import(path):
    return "/".join(path.split("/")[-2:])


def chip(line, side, kind, text):
    return {"line": line, "side": side, "kind": kind, "text": text}


def change_chips(old, new, changed_old, changed_new):
    """One chip per notable change, anchored to the line it is about: added and removed declarations, params, fields,
    call arguments and imports, plus a +/- line count for function bodies."""
    before, after = declarations(old), declarations(new)
    enums = enum_values(new)
    enum_counts = {t: len(v) for t, v in enums.items()}
    members = {v["name"] for vals in enums.values() for v in vals}
    out = []
    for key, d in after.items():
        if key in before:
            continue
        if d["kind"] == "func":
            out.append(chip(d["start"], "new", "add", f"+ func {sig(d)}"))
        elif d["kind"] == "type":
            extra = f" · {len(d['fields'])} fields" if "fields" in d else ""
            if enum_counts.get(d["name"]):
                extra = f" · {enum_counts[d['name']]} values"
            shape = "struct" if d["shape"] == "struct_type" else flat(d["underlying"], 30)
            out.append(chip(d["start"], "new", "add", f"+ type {d['name']} {shape}{extra}"))
        elif d["kind"] == "import":
            out.append(chip(d["start"], "new", "add", f"+ import {short_import(d['name'])}"))
        elif d["kind"] in ("const", "var") and d["name"] not in members:
            out.append(chip(d["start"], "new", "add", f"+ {d['kind']} {d['name']}" + (f" = {flat(d['value'], 30)}" if d["value"] else "")))
    for key, d in before.items():
        if key not in after:
            label = f"− func {d['name']}()" if d["kind"] == "func" else f"− {d['kind']} {short_import(d['name']) if d['kind'] == 'import' else d['name']}"
            out.append(chip(d["start"], "old", "del", label))
    for key, a in after.items():
        b = before.get(key)
        if not b or a["norm"] == b["norm"]:
            continue
        if a["kind"] == "func":
            out += param_chips(b, a)
            if a["result"] != b["result"]:
                out.append(chip(a["start"], "new", "mod", f"~ {a['name']} returns {b['result'] or '()'} → {a['result'] or '()'}"))
            out += call_chips(old, new, b, a, changed_new)
            body = a["body"]
            if body is not None:
                plus = sorted(n for n in changed_new if first(body) <= n <= last(body))
                minus = [n for n in changed_old if b["body"] is not None and first(b["body"]) <= n <= last(b["body"])]
                if plus or minus:
                    out.append({**chip(plus[0] if plus else a["start"], "new", "mod", f"{a['name']} body +{len(plus)} −{len(minus)}"),
                                "body": {"name": a["name"], "new": plus, "old": minus}})
        elif a["kind"] == "type" and "fields" in a and "fields" in b:
            out += field_chips(b, a)
        elif a["kind"] == "type":
            out.append(chip(a["start"], "new", "mod", f"~ type {a['name']}: {flat(b['underlying'], 24)} → {flat(a['underlying'], 24)}"))
        elif a["kind"] in ("const", "var"):
            out.append(chip(a["start"], "new", "mod", f"~ {a['kind']} {a['name']} = {flat(a['value'], 30)}"))
    return sorted(out, key=lambda c: (c["side"] == "old", c["line"]))


def param_chips(b, a):
    out = []
    old = [(p["name"], p["type"]) for p in b["params"]]
    new = [(p["name"], p["type"]) for p in a["params"]]
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if op in ("insert", "replace"):
            for p in a["params"][j1:j2]:
                if op == "replace" and any(q["name"] == p["name"] for q in b["params"][i1:i2]):
                    q = next(q for q in b["params"][i1:i2] if q["name"] == p["name"])
                    out.append(chip(p["line"], "new", "mod", f"~ {a['name']} param {p['name']}: {q['type']} → {p['type']}"))
                else:
                    out.append(chip(p["line"], "new", "add", f"+ {a['name']} param {p['name']} {p['type']}".rstrip()))
        if op in ("delete", "replace"):
            for p in b["params"][i1:i2]:
                if not any(q["name"] == p["name"] for q in a["params"][j1:j2]):
                    out.append(chip(a["start"], "new", "del", f"− {a['name']} param {p['name']} {p['type']}".rstrip()))
    return out


def field_chips(b, a):
    out = []
    old = {f["name"]: f for f in b["fields"]}
    new = {f["name"]: f for f in a["fields"]}
    for name, f in new.items():
        if name not in old:
            out.append(chip(f["line"], "new", "add", f"+ {a['name']}.{name} {f['type']}"))
        elif flat(old[name]["type"]) != flat(f["type"]):
            out.append(chip(f["line"], "new", "mod", f"~ {a['name']}.{name}: {old[name]['type']} → {f['type']}"))
        elif old[name]["tag"] != f["tag"]:
            out.append(chip(f["line"], "new", "mod", f"~ {a['name']}.{name} tag"))
    for name in old:
        if name not in new:
            out.append(chip(a["start"], "new", "del", f"− {a['name']}.{name}"))
    return out


def call_chips(old, new, b, a, changed_new):
    """Arguments added to or removed from a call on a changed line; replaced arguments are left to the body count,
    since an outer call around a changed inner call would otherwise report the whole inner call as replaced."""
    out = []
    before = {}
    for c in calls(old, b["body"]):
        before.setdefault(c["callee"], []).append(c)
    seen = {}
    for c in calls(new, a["body"]):
        i = seen.get(c["callee"], 0)
        seen[c["callee"]] = i + 1
        prev = before.get(c["callee"], [])
        if i >= len(prev) or c["line"] not in changed_new:
            continue
        olds, news = [x["text"] for x in prev[i]["args"]], [x["text"] for x in c["args"]]
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=olds, b=news, autojunk=False).get_opcodes():
            if op == "insert":
                out += [chip(x["line"], "new", "add", f"{c['callee']}(): + arg {flat(x['text'], 30)}") for x in c["args"][j1:j2]]
            elif op == "delete":
                out += [chip(c["line"], "new", "del", f"{c['callee']}(): − arg {flat(x, 30)}") for x in olds[i1:i2]]
    return out


# ---------------- types: structs and enums ----------------

def type_ref(src, t, known):
    """A field type as wrappers around a base: *T a pointer, []T a list, map[K]V a map; `ref` names a known type."""
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
    return {"base": base, "mods": mods, "ref": base if base in known else None}


def xml_name(tag):
    m = re.search(r'xml:"([^"]*)"', tag or "")
    if not m:
        return None
    name, *opts = m.group(1).split(",")
    return {"name": name, "attr": "attr" in opts, "omitempty": "omitempty" in opts}


def enum_values(src):
    """Typed constants per type, with iota expanded: {type: [{name, value, doc, line}]}."""
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
                out.setdefault(cur, []).append({"name": src.text(nm), "value": value, "line": first(spec),
                                                "doc": doc_before(src, spec) or trailing_comment(src, spec)})
    return out


def types(files):
    """Every type declared in a package's sources: structs with their fields, enums with their values, and the methods
    defined on each. `files` maps path to source text."""
    parsed = {p: Src(t) for p, t in files.items()}
    names = set()
    for src in parsed.values():
        names |= {d["name"] for d in declarations(src).values() if d["kind"] == "type"}
    out, methods = {}, {}
    for path, src in parsed.items():
        enums = enum_values(src)
        for key, d in declarations(src).items():
            if d["kind"] == "func" and "." in d["name"]:
                recv, name = d["name"].split(".", 1)
                methods.setdefault(recv, []).append(name)
            if d["kind"] != "type":
                continue
            info = {"name": d["name"], "file": path, "line": d["start"], "end": d["end"],
                    "doc": doc_before(src, d["node"].parent) or doc_before(src, d["node"]), "underlying": d["underlying"]}
            if "fields" in d:
                info["fields"] = [{"name": f["name"], "type": f["type"], "line": f["line"], "embedded": f.get("embedded", False), "doc": f["doc"],
                                   "xml": xml_name(f["tag"]), **type_ref(src, f["tnode"], names)} for f in d["fields"]]
            if d["name"] in enums:
                info["values"] = enums[d["name"]]
            out[d["name"]] = info
        for t, vals in enums.items():
            if t in out and "values" not in out[t]:
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
    """`name := []struct{...}{...}` and `map[string]struct{...}{...}` literals inside test functions, with the loop
    that runs them."""
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
            var = src.text(n.child_by_field_name("left"))
            loop = next((f for f in walk(fn) if f.type == "for_statement" and re.search(rf"\brange\s+{re.escape(var)}\b", src.text(f).split("{", 1)[0])), None)
            out.append({"func": fname, "var": var, "line": first(n), "end": last(n), "columns": cols, "rows": rows,
                        "loop": [first(loop), last(loop)] if loop else None})
    return out


# ---------------- error handling ----------------

def error_blocks(src):
    """`if ... err != nil { ... }` blocks, outermost only, each with the call it guards and what it does on error."""
    out = []

    def visit(n):
        if n.type == "if_statement" and ERR_CHECK.search(src.text(n.child_by_field_name("condition"))):
            cons = n.child_by_field_name("consequence")
            stmts = [s for s in cons.named_children if s.type != "comment"] if cons else []
            action = flat(src.text(stmts[0]), 70) if stmts else ""
            if len(stmts) > 1:
                action += f" (+{len(stmts) - 1} more)"
            init = n.child_by_field_name("initializer")
            end = cons.end_point[0] + 1 if cons else last(n)
            alt = n.child_by_field_name("alternative")
            if alt is None:
                out.append({"start": first(n), "end": end, "init": flat(src.text(init)) if init else None,
                            "cond": flat(src.text(n.child_by_field_name("condition"))), "action": action})
                return
        for c in n.named_children:
            visit(c)

    visit(src.root)
    return out
