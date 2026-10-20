"""A first draft of a formula's description in words, read from the picture of it on the page.

A displayed equation's text layer says something like "ωd = √(ω0 2 − γ2)" -- the characters in the
order they were drawn, with the structure gone. Read out, that is not the equation. What a reader
needs is "omega sub d equals the square root of omega sub 0 squared minus gamma squared", and
writing that for every equation is exactly the work this saves: the equation is read from its
picture into LaTeX by LaTeX-OCR (pix2tex, Lukas Blecher, MIT; ONNX export by RapidAI, MIT), and the
LaTeX is put into words by `speak`.

It is a *draft*, offered in the editor's description box for the person to check against the page,
and never written into the document unless they accept it. The reader makes mistakes a sighted
person catches at a glance -- a double prime read as a prime, a zero read as the letter O -- which
is why the box asks for that glance (invariant 1: nothing a machine guessed reaches a reader
unseen).

The inference is adapted from RapidLaTeXOCR (MIT): its package pins numpy below 2, which the rest
of Rebind cannot use, and samples each token at random where this takes the most likely one, so the
same picture always gives the same draft (invariant 3).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

import numpy as np

from . import weights
from .pagecache import PageCache, file_key

log = logging.getLogger(__name__)

FILES = ("latex_ocr/image_resizer.onnx", "latex_ocr/encoder.onnx", "latex_ocr/decoder.onnx",
         "latex_ocr/tokenizer.json")
# A formula is rendered at this resolution and cropped; the reader rescales it itself.
FORMULA_DPI = 200
# The reader was trained on single equations of at most this size, in pixels.
MAX_DIMS, MIN_DIMS = (672, 192), (32, 32)
# Equations of the length a page displays fit easily. A read that runs this long has gone wrong --
# the reader starts repeating a token -- and each token costs more than the last, so it is cut off
# and no draft is offered.
MAX_TOKENS = 200
REPEAT_LIMIT = 12
_BOS, _EOS = 1, 2
_MEAN, _STD = 0.7931 * 255, 0.1738 * 255

_reader = None
_reader_failed = False
_lock = threading.Lock()
_drafts = PageCache()


def enabled() -> bool:
    """REBIND_FORMULA_READER=off turns it off (the test suite does, outside its own tests)."""
    return os.environ.get("REBIND_FORMULA_READER", "").strip().lower() not in {"0", "off", "false"}


class _Reader:
    def __init__(self, folder: Path):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 4

        def session(name: str):
            return ort.InferenceSession(str(folder / name), options,
                                        providers=["CPUExecutionProvider"])

        self.resizer = session("image_resizer.onnx")
        self.encoder = session("encoder.onnx")
        self.decoder = session("decoder.onnx")
        vocab = json.loads((folder / "tokenizer.json").read_text(encoding="utf-8"))["model"]["vocab"]
        self.tokens = {index: token for token, index in vocab.items()}

    @staticmethod
    def _pad(img, divisible: int = 32):
        """Crop to the ink, dark on light, and pad out to a multiple of 32 on white."""
        from PIL import Image

        data = np.asarray(img.convert("L"), dtype=np.float64)
        low, high = data.min(), data.max()
        data = (data - low) / max(high - low, 1e-6) * 255
        if data.mean() > 128:
            ink = data < 128
        else:
            ink = data > 128
            data = 255 - data
        ys, xs = np.nonzero(ink)
        if not len(xs):
            return None
        rect = data[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        height, width = rect.shape
        size = (-(-width // divisible) * divisible, -(-height // divisible) * divisible)
        out = Image.new("L", size, 255)
        out.paste(Image.fromarray(rect.astype(np.uint8)), (0, 0))
        return out

    @staticmethod
    def _equation_only(image: np.ndarray) -> np.ndarray:
        """Drop an equation number set apart from the equation: "(3)" at the far right of the
        line. The reader was trained on equations alone, and the gap and the number together
        threw it into reading the whole picture as a table of nothing."""
        gray = image.mean(axis=2) if image.ndim == 3 else image
        ink_columns = (gray < 128).any(axis=0)
        rows = np.nonzero((gray < 128).any(axis=1))[0]
        if not ink_columns.any() or not len(rows):
            return image
        height = rows.max() - rows.min() + 1
        columns = np.nonzero(ink_columns)[0]
        # Runs of inked columns separated by a gap wider than the equation is tall.
        clusters: list[list[int]] = [[columns[0], columns[0]]]
        for column in columns[1:]:
            if column - clusters[-1][1] > max(height, 8):
                clusters.append([column, column])
            else:
                clusters[-1][1] = column
        if len(clusters) == 1:
            return image
        left, right = max(clusters, key=lambda c: c[1] - c[0])
        return image[:, max(left - 4, 0):right + 5]

    @staticmethod
    def _fit(img):
        from PIL import Image

        ratios = [a / b for a, b in zip(img.size, MAX_DIMS)]
        if any(r > 1 for r in ratios):
            size = np.maximum(np.array(img.size) // max(ratios), 1).astype(int)
            img = img.resize(tuple(int(v) for v in size), Image.BILINEAR)
        size = tuple(max(a, b) for a, b in zip(img.size, MIN_DIMS))
        if size != img.size:
            padded = Image.new("L", size, 255)
            padded.paste(img, img.getbbox())
            img = padded
        return img

    @staticmethod
    def _tensor(img) -> np.ndarray:
        data = (np.asarray(img.convert("L"), dtype=np.float32) - _MEAN) / _STD
        return data[None, None, :, :]

    def __call__(self, image: np.ndarray) -> str:
        """LaTeX for the equation in an RGB picture of it."""
        from PIL import Image

        padded = self._pad(Image.fromarray(np.ascontiguousarray(self._equation_only(image))))
        if padded is None:
            return ""
        base = self._fit(padded)
        # The resizer predicts the width the encoder reads best at; a few rounds settle it.
        ratio, width, height = 1.0, base.size[0], base.size[1]
        tensor = self._tensor(base)
        for _ in range(10):
            height = int(height * ratio)
            resized = base.resize((width, height),
                                  Image.BILINEAR if ratio > 1 else Image.LANCZOS)
            candidate = self._pad(self._fit(resized))
            if candidate is None:
                break
            tensor = self._tensor(candidate)
            scores = self.resizer.run(None, {self.resizer.get_inputs()[0].name: tensor})[0]
            width = (int(np.argmax(scores, axis=-1).ravel()[0]) + 1) * 32
            if width == candidate.size[0]:
                break
            ratio = width / candidate.size[0]
        context = self.encoder.run(None, {self.encoder.get_inputs()[0].name: tensor})[0]
        names = [i.name for i in self.decoder.get_inputs()]
        out = np.array([[_BOS]], dtype=np.int64)
        finished = False
        for _ in range(MAX_TOKENS):
            mask = np.ones_like(out, dtype=bool)
            logits = self.decoder.run(None, dict(zip(names, [out, mask, context])))[0][:, -1, :]
            token = int(np.argmax(logits, axis=-1).ravel()[0])
            if token == _EOS:
                finished = True
                break
            out = np.concatenate([out, np.array([[token]], dtype=np.int64)], axis=1)
            tail = out[0, -REPEAT_LIMIT:]
            if len(tail) == REPEAT_LIMIT and (tail == tail[0]).all():
                break           # stuck repeating one token: the read has failed
        if not finished:
            return ""
        return "".join(self.tokens.get(int(t), "") for t in out[0, 1:]
                       if int(t) > _EOS).replace("Ġ", " ").strip()


def _get_reader() -> _Reader | None:
    global _reader, _reader_failed
    with _lock:
        if _reader is None and not _reader_failed:
            if any(weights.weight_path(name) is None for name in FILES):
                log.warning("equation reader not found; formulas get no drafted description")
                _reader_failed = True
                return None
            try:
                _reader = _Reader(weights.model_dir() / "latex_ocr")
            except Exception:
                log.exception("equation reader could not be loaded")
                _reader_failed = True
        return _reader


def available() -> bool:
    """Whether the equation reader's models are here and load."""
    return _get_reader() is not None


def read_latex(image: np.ndarray) -> str | None:
    """LaTeX for an RGB picture of one equation; None when there is no reader."""
    if not enabled():
        return None
    reader = _get_reader()
    if reader is None:
        return None
    with _lock:
        return reader(image)


def drafts_for_page(source: Path, page_number: int, page_width: float, page_height: float,
                    boxes: dict[str, tuple[float, float, float, float]]) -> dict[str, str]:
    """A description in words for each formula box (PDF points) on one page, by element id.

    Remembered for as long as the file is unchanged: the editor rebuilds the document on every
    saved correction, and reading an equation takes about half a second.
    """
    if not boxes or not enabled() or _get_reader() is None:
        return {}
    source = Path(source)
    stamp = file_key(source)
    stamp = stamp and stamp + (page_number,)
    out: dict[str, str] = {}
    image = None
    scale = FORMULA_DPI / 72.0
    for element_id, (x0, y0, x1, y1) in boxes.items():
        key = stamp and (stamp + tuple(round(v, 1) for v in (x0, y0, x1, y1)))
        if key in _drafts:
            out[element_id] = _drafts.get(key)
            continue
        if image is None:
            from .ocr import render_page_to_image
            image = render_page_to_image(source, page_number, dpi=FORMULA_DPI)
        margin = 3.0
        top = max(int((page_height - y1 - margin) * scale), 0)
        bottom = min(int((page_height - y0 + margin) * scale), image.shape[0])
        left = max(int((x0 - margin) * scale), 0)
        right = min(int((x1 + margin) * scale), image.shape[1])
        if bottom <= top or right <= left:
            continue
        try:
            latex = read_latex(np.ascontiguousarray(image[top:bottom, left:right])) or ""
        except Exception:       # a reader failure must never cost the person their document
            log.exception("equation reader failed on page %d", page_number)
            latex = ""
        words = speak(latex)
        out[element_id] = words
        _drafts.put(key, words)
    return {element_id: words for element_id, words in out.items() if words}


# ---- LaTeX into words ---------------------------------------------------------------------------
# Plain English in the style of a person reading an equation aloud, not a full speech grammar: the
# draft is for someone to check and correct, and a reading they can follow beats a complete one.

_GREEK = {name: name for name in (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho "
    "sigma tau upsilon phi chi psi omega").split()}
_GREEK.update({"varepsilon": "epsilon", "vartheta": "theta", "varphi": "phi", "varrho": "rho",
               "varsigma": "sigma", "varpi": "pi"})
_GREEK.update({name.capitalize(): "capital " + name for name in (
    "gamma delta theta lambda xi pi sigma upsilon phi psi omega").split()})

_SYMBOLS = {
    "+": "plus", "-": "minus", "=": "equals", "<": "is less than", ">": "is greater than",
    "*": "times", "/": "divided by", "!": "factorial", "|": "vertical bar", ",": ",",
    ";": ";", ":": ":", ".": "point", "'": "prime",
    "−": "minus", "×": "times", "·": "times", "±": "plus or minus", "≤": "is less than or equal to",
    "≥": "is greater than or equal to", "≠": "is not equal to", "≈": "is approximately equal to",
    "∞": "infinity", "→": "goes to",
}
_COMMANDS = {
    "times": "times", "cdot": "times", "div": "divided by", "pm": "plus or minus",
    "mp": "minus or plus", "le": "is less than or equal to", "leq": "is less than or equal to",
    "ge": "is greater than or equal to", "geq": "is greater than or equal to",
    "neq": "is not equal to", "ne": "is not equal to", "approx": "is approximately equal to",
    "sim": "is similar to", "simeq": "is approximately equal to", "equiv": "is equivalent to",
    "propto": "is proportional to", "ll": "is much less than", "gg": "is much greater than",
    "infty": "infinity", "partial": "partial", "nabla": "del", "to": "goes to",
    "rightarrow": "goes to", "leftarrow": "comes from", "Rightarrow": "implies",
    "Leftrightarrow": "if and only if", "mapsto": "maps to", "in": "is in",
    "notin": "is not in", "subset": "is a subset of", "subseteq": "is a subset of or equal to",
    "cup": "union", "cap": "intersection", "forall": "for all", "exists": "there exists",
    "neg": "not", "land": "and", "lor": "or", "prime": "prime", "circ": "degrees",
    "ldots": "dot dot dot", "cdots": "dot dot dot", "dots": "dot dot dot", "hbar": "h bar",
    "ell": "ell", "angle": "angle", "perp": "is perpendicular to", "parallel": "is parallel to",
    "sum": "the sum", "prod": "the product", "int": "the integral", "oint": "the contour integral",
    "lim": "the limit", "max": "the maximum", "min": "the minimum",
    "sin": "sine", "cos": "cosine", "tan": "tangent", "cot": "cotangent", "sec": "secant",
    "csc": "cosecant", "arcsin": "arc sine", "arccos": "arc cosine", "arctan": "arc tangent",
    "sinh": "hyperbolic sine", "cosh": "hyperbolic cosine", "tanh": "hyperbolic tangent",
    "log": "log", "ln": "natural log", "exp": "the exponential of", "det": "the determinant of",
}
# Spelled out in the reader's output as separate letters ("c o s"), which a person reads as a word.
_FUNCTION_WORDS = {name: _COMMANDS[name] for name in (
    "sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh", "log", "ln", "exp", "lim",
    "max", "min", "det")}
_BIG_OPERATORS = {"sum": "the sum", "prod": "the product", "int": "the integral",
                  "oint": "the contour integral", "lim": "the limit"}
_SILENT = {",", ";", ":", "!", " ", "\\", "quad", "qquad", "hfill", "left", "right", "big", "Big",
           "bigg", "Bigg", "displaystyle", "textstyle", "nonumber", "notag", "limits", "nolimits"}
_WRAPPERS = {"mathrm", "mathbf", "mathit", "mathcal", "mathsf", "mathtt", "mathbb", "boldsymbol",
             "bm", "text", "textrm", "textbf", "textit", "operatorname", "rm", "bf", "it", "cal",
             "mathscr", "mathfrak", "hat", "bar", "vec", "tilde", "dot", "ddot", "overline"}
_ACCENTS = {"hat": "hat", "bar": "bar", "vec": "vector", "tilde": "tilde", "dot": "dot",
            "ddot": "double dot", "overline": "bar"}
_TOKEN = re.compile(r"\\[A-Za-z]+|\\.|[{}^_]|\s+|.", re.S)


def _tokens(latex: str) -> list[str]:
    return [t for t in _TOKEN.findall(latex) if not t.isspace()]


class _Speaker:
    def __init__(self, latex: str):
        self.tokens = _tokens(latex)
        self.at = 0

    def _next(self) -> str | None:
        if self.at >= len(self.tokens):
            return None
        token = self.tokens[self.at]
        self.at += 1
        return token

    def _group(self) -> list[str]:
        """One argument: a braced group, or the single next item."""
        token = self._next()
        if token is None:
            return []
        if token == "{":
            words: list[str] = []
            while self.at < len(self.tokens) and self.tokens[self.at] != "}":
                words += self._item()
            self._next()        # the closing brace
            return words
        self.at -= 1
        return self._item()

    def _optional(self) -> list[str] | None:
        """An optional [argument], as in \\sqrt[3]{x}."""
        if self.at < len(self.tokens) and self.tokens[self.at] == "[":
            self.at += 1
            words: list[str] = []
            while self.at < len(self.tokens) and self.tokens[self.at] != "]":
                words += self._item()
            self._next()
            return words
        return None

    @staticmethod
    def _is_simple(words: list[str]) -> bool:
        """One thing said: a single word, or a number however many digits it has."""
        return len(words) <= 1 or all(w.isdigit() for w in words)

    def _power(self, words: list[str]) -> list[str]:
        if words in (["prime"], ["'"]):
            return ["prime"]
        if words in (["prime", "prime"], ["'", "'"]):
            return ["double prime"]
        if words == ["2"]:
            return ["squared"]
        if words == ["3"]:
            return ["cubed"]
        if words == ["minus", "1"]:
            return ["inverse"]
        if self._is_simple(words):
            return ["to the power", *words]
        return ["to the power of", *words, ", end power"]

    def _limits(self) -> tuple[list[str], list[str]]:
        """The sub- and superscript after a sum or integral, in either order."""
        lower: list[str] = []
        upper: list[str] = []
        while self.at < len(self.tokens) and self.tokens[self.at] in ("_", "^"):
            which = self._next()
            if which == "_":
                lower = self._group()
            else:
                upper = self._group()
        return lower, upper

    def _big_operator(self, name: str) -> list[str]:
        """"the sum from i equals 1 to n of", rather than "sub i equals 1 to the power n"."""
        lower, upper = self._limits()
        words = [_BIG_OPERATORS[name]]
        if lower:
            words += ["from" if upper or name != "lim" else "as", *lower]
        if upper:
            words += ["to", *upper]
        return [*words, "of"] if lower or upper else words

    def _item(self) -> list[str]:
        token = self._next()
        if token is None:
            return []
        if token == "{":
            self.at -= 1
            return self._group()
        if token == "}":
            return []
        if token == "^":
            return self._power(self._group())
        if token == "_":
            words = self._group()
            return ["sub", *words] if self._is_simple(words) else ["sub", *words, ", end sub"]
        if token in ("~", "&"):            # spacing and alignment, not content
            return []
        if token.startswith("\\"):
            name = token[1:]
            if name in ("begin", "end", "hspace", "vspace"):
                self._group()               # egin{array}{cc}: the environment's name is layout
                if name == "begin":
                    while self.at < len(self.tokens) and self.tokens[self.at] == "{":
                        self._group()
                return []
            if name in _SILENT or not name:
                return []
            if name == "frac" or name in ("dfrac", "tfrac"):
                top, bottom = self._group(), self._group()
                if self._is_simple(top) and self._is_simple(bottom):
                    return [*top, "over", *bottom]
                return ["the fraction", *top, "over", *bottom, ", end fraction"]
            if name == "sqrt":
                index = self._optional()
                body = self._group()
                if index:
                    lead = ["the cube root of"] if index == ["3"] else ["the", *index, "th root of"]
                else:
                    lead = ["the square root of"]
                return [*lead, *body] if self._is_simple(body) else [*lead, *body,
                                                                     ", end root"]
            if name in _ACCENTS:
                return [*self._group(), _ACCENTS[name]]
            if name in _WRAPPERS:
                return self._group()
            if name in _GREEK:
                return [_GREEK[name]]
            if name in _BIG_OPERATORS:
                return self._big_operator(name)
            if name in _COMMANDS:
                return [_COMMANDS[name]]
            if len(name) == 1:          # \{ \} \% and the like
                return [_SYMBOLS.get(name, name)]
            return [name]
        if token in "([":
            return ["open paren"] if token == "(" else ["open bracket"]
        if token in ")]":
            return ["close paren"] if token == ")" else ["close bracket"]
        if token in _SYMBOLS:
            return [_SYMBOLS[token]]
        return [token]

    def words(self) -> list[str]:
        out: list[str] = []
        while self.at < len(self.tokens):
            out += self._item()
        return out


def _join_spelled_functions(words: list[str]) -> list[str]:
    """"c o s" -> "cosine": the reader often spells a function name out letter by letter."""
    out: list[str] = []
    i = 0
    while i < len(words):
        for length in (5, 4, 3, 2):
            run = words[i:i + length]
            if (len(run) == length and all(len(w) == 1 and w.isalpha() for w in run)
                    and "".join(run) in _FUNCTION_WORDS):
                out.append(_FUNCTION_WORDS["".join(run)])
                i += length
                break
        else:
            out.append(words[i])
            i += 1
    return out


_FUNCTION_NAMES = set(_FUNCTION_WORDS.values())


def _function_arguments(words: list[str]) -> list[str]:
    """"cosine open paren x close paren" -> "cosine of x": brackets after a function name are its
    argument. Anywhere else they stay spoken, since "n open paren n plus 1" is a product."""
    out: list[str] = []
    silent: list[bool] = []         # per open bracket: was it read as "of"?
    for word in words:
        if word == "open paren":
            argument = bool(out) and out[-1] in _FUNCTION_NAMES
            silent.append(argument)
            out.append("of" if argument else word)
        elif word == "close paren" and silent:
            if not silent.pop():
                out.append(word)
        else:
            out.append(word)
    return out


def speak(latex: str) -> str:
    """LaTeX put into words a person would read aloud, e.g. "x squared plus 1"."""
    if not latex.strip():
        return ""
    words = _function_arguments(_join_spelled_functions(_Speaker(latex).words()))
    text = " ".join(w for w in words if w)
    text = re.sub(r"\s+([,;:])", r"\1", text)
    text = re.sub(r"(?<=\d) (?=\d)", "", text)          # "1 0" -> "10"
    text = re.sub(r"(?<=\d) point (?=\d)", ".", text)     # "3 point 1 4" -> "3.14"
    text = re.sub(r"\b(\d+|[A-Za-z]) th\b", r"\1th", text)             # "the 4 th root" -> "the 4th root"
    text = re.sub(r"\s+", " ", text).strip(" ,")
    return text
