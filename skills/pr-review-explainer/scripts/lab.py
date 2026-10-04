# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pygments", "tree-sitter", "tree-sitter-go"]
# ///
"""Build a lab page of diff-rendering experiments for a PR, each shown on the PR's own code next to today's rendering.

    uv run lab.py spec.yaml [--out PATH]

Uses the spec's repo, base and github; its scenes are ignored. A kept experiment moves into build.py's read mode and
leaves the lab.
"""

import argparse
import json
import os
import re

import yaml

import build

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(os.path.dirname(HERE), "assets", "lab.html")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out")
    args = ap.parse_args()
    with open(args.spec) as f:
        spec = yaml.safe_load(f)
    data = {"meta": {"title": spec["title"], "kicker": spec.get("kicker", f"PR #{spec.get('pr')}"), "pr": spec.get("pr"),
                     "github": spec["github"]}}
    with open(build.TEMPLATE) as f:
        css = re.search(r"<style>(.*?)</style>", f.read(), re.S).group(1)
    with open(TEMPLATE) as f:
        page = f.read().replace("/*CSS*/", css).replace("/*DATA*/null", json.dumps(data).replace("</", "<\\/"))
    out = args.out or os.path.join(os.environ.get("BB_THREAD_STORAGE", "."), "reports", f"pr{spec.get('pr', 'x')}-lab.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write(page)
    print(f"{len(page) / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
