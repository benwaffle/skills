# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pygments", "numpy", "tree-sitter", "tree-sitter-go"]
# ///
"""Build a lab page of read-mode experiments for a PR: the walkthrough's own read mode, silent, with each experiment
next to today's rendering of the same code.

    uv run lab.py spec.yaml [--out PATH]

A kept experiment moves into build.py's read mode and leaves the lab. Data an experiment needs beyond the walkthrough's
goes in the overlay's LAB object.
"""

import argparse
import json
import os

import yaml

import build

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY = os.path.join(os.path.dirname(HERE), "assets", "lab.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    args = ap.parse_args()
    with open(args.spec) as f:
        spec = yaml.safe_load(f)
    try:
        builder = build.Builder(spec, os.path.dirname(os.path.abspath(args.spec)), silent=True, fetch=False)
        data, _ = builder.build()
    except build.SpecError as e:
        raise SystemExit(f"spec error: {e}")
    lab = {}
    with open(build.TEMPLATE) as f:
        page = f.read().replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))
    with open(OVERLAY) as f:
        overlay = f.read().replace("/*LAB*/null", json.dumps(lab).replace("</", "<\\/"))
    page = page.replace("</body>", overlay + "\n</body>")
    out = args.out or os.path.join(os.environ.get("BB_THREAD_STORAGE", "."), "reports", f"pr{spec.get('pr', 'x')}-lab.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write(page)
    print(f"{len(page) / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
