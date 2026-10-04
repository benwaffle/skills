# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "mlx-audio[tts] @ git+https://github.com/Blaizzy/mlx-audio@e1b19b9054bf163f5d812221a54fcc346f1890e9",
#   "soundfile",
# ]
# ///
"""Synthesize narration cues with an mlx-audio model (Apple Silicon only). tts.py runs it once per build with every
uncached cue, so the model loads once.

Reads one JSON job on stdin:

    {"repo": "mlx-community/...", "seed": 7, "kwargs": {...},
     "design": {"instruct": "...", "text": "...", "out": "ref.wav"},   # optional: make the voice's reference clip if missing
     "clone": {"ref_audio": "ref.wav", "ref_text": "..."},             # optional: added to kwargs for every cue
     "cues": [{"text": "...", "out": "cue.wav"}]}
"""

import json
import os
import sys
import time

import mlx.core as mx
import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load


def synthesize(model, text, kwargs, seed):
    mx.random.seed(seed)
    chunks, rate = [], None
    for r in model.generate(text=text, **kwargs):
        chunks.append(np.array(r.audio, dtype=np.float32).reshape(-1))
        rate = r.sample_rate
    return np.concatenate(chunks), rate


def write(path, samples, rate):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.wav"
    sf.write(tmp, samples, rate)
    os.replace(tmp, path)


def main():
    job = json.load(sys.stdin)
    model = load(job["repo"])
    kwargs = job.get("kwargs", {})
    design = job.get("design")
    if design and not os.path.exists(design["out"]):
        print(f"    designing the voice -> {design['out']}", file=sys.stderr, flush=True)
        write(design["out"], *synthesize(model, design["text"], {**kwargs, "instruct": design["instruct"]}, job["seed"]))
    kwargs = {**kwargs, **job.get("clone", {})}
    cues = job["cues"]
    for i, cue in enumerate(cues, 1):
        start = time.perf_counter()
        samples, rate = synthesize(model, cue["text"], kwargs, job["seed"])
        write(cue["out"], samples, rate)
        print(f"    tts {i}/{len(cues)}: {len(samples) / rate:4.1f}s audio in {time.perf_counter() - start:4.1f}s",
              file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
