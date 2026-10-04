"""Kokoro TTS with a pronunciation lexicon and a per-cue WAV cache."""

import hashlib
import os
import re

import numpy as np
import soundfile as sf
import yaml

RATE = 24000
VOICE = os.environ.get("PR_EXPLAINER_VOICE", "am_michael")
SPEED = float(os.environ.get("PR_EXPLAINER_SPEED", "1.1"))
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


def spell(term):
    """IPA for a term read letter by letter, primary stress on the last letter: SAR -> ˌɛs ˌeɪ ˈɑːɹ."""
    chars = [c for c in term.upper() if c in LETTERS]
    parts = []
    for i, c in enumerate(chars):
        onset, rest = LETTERS[c]
        parts.append(onset + ("ˈ" if i == len(chars) - 1 else "ˌ") + rest)
    return " ".join(parts)


def load_lexicon(extra=None):
    with open(os.path.join(HERE, "lexicon.yaml")) as f:
        raw = yaml.safe_load(f) or {}
    lex = {term: spell(term) for term in raw.get("spell", [])}
    lex.update(raw.get("ipa", {}))
    for term, value in (extra or {}).items():
        lex[term] = spell(term) if value == "spell" else value
    return lex


class Voice:
    def __init__(self, lexicon):
        self.lexicon = lexicon
        terms = sorted(map(re.escape, lexicon), key=len, reverse=True)
        self.term = re.compile(r"(?<![\w-])(" + "|".join(terms) + r")(?![\w-])") if terms else None
        self._kokoro = None

    def kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(os.path.join(MODEL_DIR, "kokoro-v1.0.onnx"), os.path.join(MODEL_DIR, "voices-v1.0.bin"))
        return self._kokoro

    def phonemes(self, text):
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

    def speak(self, text):
        phonemes = self.phonemes(text)
        key = hashlib.sha1(f"{VOICE}|{SPEED}|{phonemes}".encode()).hexdigest()[:16]
        path = os.path.join(CACHE, f"cue-{key}.wav")
        os.makedirs(CACHE, exist_ok=True)
        if not os.path.exists(path):
            samples, _ = self.kokoro().create(phonemes, voice=VOICE, speed=SPEED, lang="en-us", is_phonemes=True)
            sf.write(path, samples, RATE)
        samples, _ = sf.read(path, dtype="float32")
        return samples, phonemes


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)
