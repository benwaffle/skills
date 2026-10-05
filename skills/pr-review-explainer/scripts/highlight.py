"""Pygments highlighting as (css class, text) tokens per line, and rendering them to HTML with marked char ranges."""

import html

from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.token import STANDARD_TYPES
from pygments.util import ClassNotFound


def lexer_for(path):
    try:
        return get_lexer_for_filename(path, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return TextLexer(stripnl=False, ensurenl=False)


def css_class(ttype):
    while ttype not in STANDARD_TYPES:
        ttype = ttype.parent
    short = STANDARD_TYPES[ttype]
    return "t-" + short if short else ""


# Highlighting whole files (not line by line) keeps multi-line comments and strings right.
def tokens_by_line(text, path):
    lines = [[]]
    for ttype, value in lexer_for(path).get_tokens(text):
        for i, part in enumerate(value.split("\n")):
            if i:
                lines.append([])
            if part:
                lines[-1].append((css_class(ttype), part))
    return lines


def char_ranges(text, byte_ranges):
    b2c = []
    for i, ch in enumerate(text):
        b2c.extend([i] * len(ch.encode()))
    b2c.append(len(text))
    clamp = lambda b: b2c[min(b, len(b2c) - 1)]
    return [(clamp(s), clamp(e)) for s, e in byte_ranges]


def char_links(text, links):
    """(start byte, end byte, ref) spans in a line as (start char, end char, ref)."""
    return [(*c, ref) for c, (*_, ref) in zip(char_ranges(text, [l[:2] for l in links]), links)]


def join_ranges(text, ranges):
    """Char ranges merged where they touch or only whitespace separates them. Difftastic marks each changed atom,
    so a changed string like "deliver %s: %w" arrives as ten ranges."""
    out = []
    for s, e in sorted(ranges):
        if out and not text[out[-1][1]:s].strip():
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def render(tokens, marks, mark_cls, more=()):
    """Tokens as HTML, with the char ranges in `marks` wrapped in <mark class=mark_cls>, and likewise for each
    (ranges, cls) in `more`, nested inside. A range's optional third item becomes the mark's data-ref. A mark spans
    token boundaries, so it splits only where a range of an outer layer ends inside it."""
    layers = [(marks, mark_cls), *more]
    bounds = {b for ranges, _ in layers for r in ranges for b in r[:2]}
    out, opened, pos = [], [], 0
    for cls, text in tokens:
        cuts = sorted({0, len(text), *[max(0, min(len(text), b - pos)) for b in bounds]})
        for a, b in zip(cuts, cuts[1:]):
            want = [(r, mc) for ranges, mc in layers
                    for r in [next((r for r in ranges if r[0] <= pos + a < r[1]), None)] if r is not None]
            keep = 0
            while keep < min(len(opened), len(want)) and opened[keep] == want[keep]:
                keep += 1
            out.append("</mark>" * (len(opened) - keep))
            for r, mc in want[keep:]:
                ref = f' data-ref="{html.escape(r[2])}"' if len(r) > 2 else ""
                out.append(f'<mark class="{mc}"{ref}>')
            opened = want
            piece = html.escape(text[a:b], quote=False)
            out.append(f'<span class="{cls}">{piece}</span>' if cls else piece)
        pos += len(text)
    out.append("</mark>" * len(opened))
    return "".join(out)
