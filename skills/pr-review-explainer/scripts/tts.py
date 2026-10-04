"""Narration TTS: the voices in voices.yaml, a pronunciation lexicon, and a per-cue WAV cache.

Kokoro reads phonemes, so the lexicon gives it exact IPA. mlx voices (Breeze) read plain text, so the lexicon gives them
respellings ("S C S C F"), and tts_mlx.py synthesizes every uncached cue of a build in one run.
"""

import hashlib
import json
import os
import re
import subprocess

import numpy as np
import soundfile as sf
import yaml

RATE = 24000
MODEL_DIR = os.path.expanduser(os.environ.get("KOKORO_DIR", "~/Downloads/kokoro"))
CACHE = os.path.expanduser(os.environ.get("PR_EXPLAINER_CACHE", "~/.cache/pr-review-explainer/tts"))
HERE = os.path.dirname(os.path.abspath(__file__))

# Letter and digit sounds for spelled-out terms, split into (onset, vowel-led rest) so a stress mark can sit before the vowel.
LETTERS = {
    "A": ("", "eɪ"), "B": ("b", "iː"), "C": ("s", "iː"), "D": ("d", "iː"), "E": ("", "iː"), "F": ("", "ɛf"),
    "G": ("dʒ", "iː"), "H": ("", "eɪtʃ"), "I": ("", "aɪ"), "J": ("dʒ", "eɪ"), "K": ("k", "eɪ"), "L": ("", "ɛl"),
    "M": ("", "ɛm"), "N": ("", "ɛn"), "O": ("", "oʊ"), "P": ("p", "iː"), "Q": ("kj", "uː"), "R": ("", "ɑːɹ"),
    "S": ("", "ɛs"), "T": ("t", "iː"), "U": ("j", "uː"), "V": ("v", "iː"), "W": ("d", "ʌbəljuː"), "X": ("", "ɛks"),
    "Y": ("w", "aɪ"), "Z": ("z", "iː"),
    "0": ("z", "iəɹoʊ"), "1": ("w", "ʌn"), "2": ("t", "uː"), "3": ("θɹ", "iː"), "4": ("f", "ɔːɹ"),
    "5": ("f", "aɪv"), "6": ("s", "ɪks"), "7": ("s", "ɛvən"), "8": ("", "eɪt"), "9": ("n", "aɪn"),
}
# Letters whose names start with a vowel sound take "an": an S A R, a T A S.
VOWEL_LETTERS = set("AEFHILMNORSX8")


def spell(term):
    """IPA for a term read letter by letter, primary stress on the last letter: SAR -> ˌɛs ˌeɪ ˈɑːɹ."""
    chars = [c for c in term.upper() if c in LETTERS]
    parts = []
    for i, c in enumerate(chars):
        onset, rest = LETTERS[c]
        parts.append(onset + ("ˈ" if i == len(chars) - 1 else "ˌ") + rest)
    return " ".join(parts)


def spell_text(term):
    """A term read letter by letter, as text: S-CSCF -> "S C S C F", AVPs -> "A V Ps"."""
    plural = len(term) > 1 and term.endswith("s") and not term[:-1].islower()
    chars = [c.upper() for c in (term[:-1] if plural else term) if c.isalnum()]
    return " ".join(chars) + ("s" if plural else "")


def pattern(terms):
    if not terms:
        return None
    alternatives = "|".join(sorted(map(re.escape, terms), key=len, reverse=True))
    return re.compile(r"(?<![\w-])(" + alternatives + r")(?![\w-])")


class Lexicon:
    """lexicon.yaml plus a spec's own terms. A spec value is "spell", an IPA string, or {ipa: ..., text: ...}."""

    def __init__(self, extra=None):
        with open(os.path.join(HERE, "lexicon.yaml")) as f:
            raw = yaml.safe_load(f) or {}
        self.ipa = {term: spell(term) for term in raw.get("spell", [])} | raw.get("ipa", {})
        self.text = {term: spell_text(term) for term in raw.get("spell", [])} | raw.get("text", {})
        for term, value in (extra or {}).items():
            if value == "spell":
                self.ipa[term], self.text[term] = spell(term), spell_text(term)
            elif isinstance(value, str):
                self.ipa[term] = value
            else:
                if "ipa" in value:
                    self.ipa[term] = value["ipa"]
                if "text" in value:
                    self.text[term] = value["text"]


def cached(key):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, f"cue-{hashlib.sha1(key.encode()).hexdigest()[:16]}.wav")


def read(path):
    samples, rate = sf.read(path, dtype="float32")
    if rate != RATE:
        raise RuntimeError(f"{path}: {rate} Hz audio; the build mixes cues at {RATE} Hz")
    return samples


class Kokoro:
    def __init__(self, cfg, lexicon):
        self.voice, self.speed = cfg["voice"], cfg.get("speed", 1.0)
        self.lexicon = lexicon.ipa
        self.term = pattern(self.lexicon)
        self._kokoro = None

    def kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro as Model

            self._kokoro = Model(os.path.join(MODEL_DIR, "kokoro-v1.0.onnx"), os.path.join(MODEL_DIR, "voices-v1.0.bin"))
        return self._kokoro

    def render(self, text):
        out = []
        parts = self.term.split(text) if self.term else [text]
        for i, part in enumerate(parts):
            if i % 2:
                out.append(self.lexicon[part])
            elif part.strip():
                # A segment cut off before a lexicon term can end in a bare article, which phonemizes as the letter A.
                body, article = re.match(r"(.*?)(?:\b(an?)\s*)?$", part, re.S | re.I).groups()
                if body.strip():
                    out.append(self.kokoro().tokenizer.phonemize(body, "en-us"))
                if article:
                    out.append({"a": "ɐ", "an": "ɐn"}[article.lower()])
        return " ".join(p.strip() for p in out).replace(" ,", ",").replace(" .", ".")

    def prepare(self, texts):
        pass

    def speak(self, text):
        phonemes = self.render(text)
        path = cached(f"{self.voice}|{self.speed}|{phonemes}")
        if not os.path.exists(path):
            samples, _ = self.kokoro().create(phonemes, voice=self.voice, speed=self.speed, lang="en-us", is_phonemes=True)
            sf.write(path, samples, RATE)
        return read(path), phonemes


class Mlx:
    def __init__(self, name, cfg, lexicon, seed, reference):
        self.cfg, self.seed, self.reference = cfg, seed, reference
        self.lexicon = lexicon.text
        self.term = pattern(self.lexicon)
        self.ref = os.path.join(os.path.dirname(CACHE), "voices", f"{name}.wav")

    def render(self, text):
        if not self.term:
            return text
        parts = self.term.split(text)
        for i in range(1, len(parts), 2):
            parts[i] = self.lexicon[parts[i]]
            # A term respelled as letters takes the article of its first letter's name: "a SAR" becomes "an S A R".
            first = re.match(r"([A-Z0-9])(?:\s|s?$)", parts[i])
            if first:
                word = "an" if first.group(1) in VOWEL_LETTERS else "a"
                parts[i - 1] = re.sub(r"\b([Aa])n?(\s+)$", lambda m: (word.capitalize() if m.group(1).isupper() else word) + m.group(2),
                                      parts[i - 1])
        return "".join(parts)

    def path(self, rendered):
        # The reference clip is part of the key, so a voice re-designed with different results never mixes with old cues.
        with open(self.ref, "rb") as f:
            ref = hashlib.sha1(f.read()).hexdigest()
        settings = json.dumps({k: self.cfg.get(k) for k in ("repo", "kwargs")}, sort_keys=True)
        return cached(f"mlx|{settings}|{ref}|{self.seed}|{rendered}")

    def run(self, cues):
        values = {"audio": self.ref, "text": self.reference}
        job = {
            "repo": self.cfg["repo"], "seed": self.seed, "kwargs": self.cfg.get("kwargs", {}),
            "design": {"instruct": self.cfg["design"], "text": self.reference, "out": self.ref},
            "clone": {key: values[key.split("_")[1]] for key in self.cfg["clone"]},
            "cues": cues,
        }
        subprocess.run(["uv", "run", "--quiet", os.path.join(HERE, "tts_mlx.py")], input=json.dumps(job), text=True, check=True)

    def prepare(self, texts):
        if not os.path.exists(self.ref):
            self.run([])
        todo = {}
        for text in texts:
            rendered = self.render(text)
            path = self.path(rendered)
            if not os.path.exists(path):
                todo[path] = rendered
        if todo:
            print(f"  synthesizing {len(todo)} cues")
            self.run([{"text": rendered, "out": path} for path, rendered in todo.items()])

    def speak(self, text):
        rendered = self.render(text)
        if not os.path.exists(self.ref) or not os.path.exists(self.path(rendered)):
            self.prepare([text])
        return read(self.path(rendered)), rendered


def load_voice(name=None, lexicon=None):
    with open(os.path.join(HERE, "voices.yaml")) as f:
        cfg = yaml.safe_load(f)
    name = name or cfg["default"]
    if name not in cfg["voices"]:
        raise ValueError(f"unknown voice {name!r}; voices.yaml has {', '.join(cfg['voices'])}")
    voice = cfg["voices"][name]
    if voice["engine"] == "kokoro":
        return Kokoro(voice, Lexicon(lexicon))
    return Mlx(name, voice, Lexicon(lexicon), cfg["seed"], cfg["reference"])


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)
