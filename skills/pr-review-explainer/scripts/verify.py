# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Screenshot a built walkthrough at every scene start and action time, plus each read-mode section,
and stitch them into contact sheets. Almost every visual bug shows up in these frames.

    uv run verify.py path/to/pr123-walkthrough.html [--out DIR] [--only 3]   # --only: one scene index

Headless Chrome must run outside the sandbox. It uses chrome-headless-shell when installed
(`npx @puppeteer/browsers install chrome-headless-shell@stable`), else Google Chrome, or $CHROME.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageFont

# Google Chrome puts an icon in the macOS Dock for every headless instance, one per screenshot; the shell has none.
SHELLS = ["~/.cache/puppeteer/chrome-headless-shell/*/chrome-headless-shell-*/chrome-headless-shell",
          "~/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell",
          "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"]


def find_chrome():
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    if found := shutil.which("chrome-headless-shell"):
        return found
    shells = [p for pattern in SHELLS for p in glob.glob(os.path.expanduser(pattern))]
    return max(shells, key=os.path.getmtime) if shells else "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


CHROME = find_chrome()
HEADLESS = [] if os.path.basename(CHROME) == "chrome-headless-shell" else ["--headless=new"]
# A fresh profile otherwise spends most of a minute on first-run, updater and sync work before it exits.
QUIET = ["--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-component-update",
         "--disable-sync", "--disable-extensions", "--disable-default-apps", "--metrics-recording-only", "--mute-audio",
         "--disable-features=MediaRouter,OptimizationHints,Translate"]


def shoot(url, png, size):
    if os.path.exists(png):
        os.remove(png)
    with tempfile.TemporaryDirectory() as profile:
        p = subprocess.Popen([CHROME, *HEADLESS, "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}", *QUIET,
                              f"--window-size={size[0]},{size[1]}", "--virtual-time-budget=4000", "--enable-logging=stderr", "--v=0",
                              f"--screenshot={png}", url], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        # The player's requestAnimationFrame loop keeps headless Chrome alive after it writes the screenshot.
        last, deadline = -1, time.time() + 60
        while time.time() < deadline and p.poll() is None:
            size_now = os.path.getsize(png) if os.path.exists(png) else -1
            if size_now > 0 and size_now == last:
                break
            last = size_now
            time.sleep(.5)
        p.kill()
        _, stderr = p.communicate()
    errors = []
    for line in stderr.splitlines():
        if "Uncaught" in line or ("CONSOLE" in line and "rror" in line):
            m = re.search(r'CONSOLE[^"]*"(.*)", source', line)
            errors.append(m[1] if m else line)
    return png, errors


def sheet(frames, labels, cols, thumb, dest):
    font = ImageFont.load_default(size=18)
    w, h = thumb
    rows = (len(frames) + cols - 1) // cols
    img = Image.new("RGB", (cols * w, rows * (h + 28)), "#111")
    draw = ImageDraw.Draw(img)
    for k, (png, label) in enumerate(zip(frames, labels)):
        x, y = (k % cols) * w, (k // cols) * (h + 28)
        if os.path.exists(png):
            img.paste(Image.open(png).convert("RGB").resize(thumb), (x, y))
        draw.text((x + 6, y + h + 4), label, fill="#ddd", font=font)
    img.save(dest)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--out", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "pr-explainer-verify"))
    ap.add_argument("--only", type=int, help="only frames from this scene index")
    ap.add_argument("--per-sheet", type=int, default=12)
    args = ap.parse_args()
    page = os.path.abspath(args.page)
    os.makedirs(args.out, exist_ok=True)
    with open(page + ".times.json") as f:
        times = json.load(f)
    if args.only is not None:
        times = [x for x in times if x["label"].split(":")[0].split(".")[0] == str(args.only)]
    scenes = sorted({int(x["label"].split(":")[0].split(".")[0]) for x in times})

    jobs = [(f"file://{page}?t={x['t']}", os.path.join(args.out, f"tour-{k:03d}.png"), (1280, 720), f"t={x['t']}  {x['label']}")
            for k, x in enumerate(times)]
    # One section per frame: command-line screenshots of a scrolled page come out blank.
    jobs += [(f"file://{page}?mode=read&only={s}", os.path.join(args.out, f"read-{s:02d}.png"), (1320, 1500), f"read · scene {s}")
             for s in scenes]
    if args.only is None:
        with open(page) as f:
            has_flow = '"flow": []' not in f.read()
        if has_flow:
            jobs.append((f"file://{page}?mode=read&only=flow", os.path.join(args.out, "read-flow.png"), (1320, 1500), "read · call order"))
        jobs.append((f"file://{page}?mode=read&only=rest", os.path.join(args.out, "read-rest.png"), (1320, 1500), "read · rest of the diff"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda j: shoot(*j[:3]), jobs))

    errors = sorted({e for _, errs in results for e in errs})
    tour = [(j[1], j[3]) for j in jobs if "/tour-" in j[1]]
    read = [(j[1], j[3]) for j in jobs if "/read-" in j[1]]
    out = []
    for i in range(0, len(tour), args.per_sheet):
        chunk = tour[i:i + args.per_sheet]
        out.append(sheet([c[0] for c in chunk], [c[1] for c in chunk], 3, (640, 360), os.path.join(args.out, f"sheet-tour-{i // args.per_sheet}.png")))
    for i in range(0, len(read), 4):
        chunk = read[i:i + 4]
        out.append(sheet([c[0] for c in chunk], [c[1] for c in chunk], 2, (660, 750), os.path.join(args.out, f"sheet-read-{i // 4}.png")))
    print("\n".join(out))
    print("JS errors:\n  " + "\n  ".join(errors) if errors else "JS errors: none")


if __name__ == "__main__":
    main()
