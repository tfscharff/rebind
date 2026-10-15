"""The model files Rebind runs but cannot keep in git, and fetching them into a source checkout.

The page-layout model (130 MB) and the equation reader (179 MB) are both over GitHub's file limit.
The installer carries them, and the installed app never downloads anything (invariant 4). A source
checkout fetches whatever is missing once, on first use -- the `rebind` command, the PyInstaller
spec and the tests that need a model all call `ensure_weights` -- the same step as `uv sync`
fetching packages, each file checked against a pinned SHA-256.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Weight:
    url: str
    sha256: str
    what: str           # named in the one line printed while it downloads


_LATEX_OCR = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0/"

# Paths are relative to the models folder, and are where the bundle carries them too.
WEIGHTS: dict[str, Weight] = {
    "pp_doc_layoutv3.onnx": Weight(
        "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/"
        "pp_doc_layout/pp_doc_layoutv3.onnx",
        "250dbad1dfb9e4983fab75e1bf5085cd56ec3f41d5c7d0f8623ec74856e7aa67",
        "the page-layout model (130 MB)"),
    "latex_ocr/image_resizer.onnx": Weight(
        _LATEX_OCR + "image_resizer.onnx",
        "e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d",
        "the equation reader, 1 of 4 (39 MB)"),
    "latex_ocr/encoder.onnx": Weight(
        _LATEX_OCR + "encoder.onnx",
        "01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d",
        "the equation reader, 2 of 4 (89 MB)"),
    "latex_ocr/decoder.onnx": Weight(
        _LATEX_OCR + "decoder.onnx",
        "bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072",
        "the equation reader, 3 of 4 (51 MB)"),
    "latex_ocr/tokenizer.json": Weight(
        _LATEX_OCR + "tokenizer.json",
        "1dc27b18d6a518d0d5ff3f4bb7bd98521fe80ad39e5b2a246d4109f1bb9d5019",
        "the equation reader, 4 of 4"),
}


def model_dir() -> Path:
    """Inside the frozen bundle, or the clone's `models/` folder."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "models"
    return Path(__file__).resolve().parents[2] / "models"


def weight_path(name: str) -> Path | None:
    path = model_dir() / name
    return path if path.is_file() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(name: str, report=print) -> Path | None:
    """Download one model file if it is missing. Never raises, and leaves nothing half-written:
    offline, Rebind runs without that model and the next run tries again."""
    path = weight_path(name)
    if path is not None or getattr(sys, "frozen", False):
        return path
    import urllib.request

    weight = WEIGHTS[name]
    target = model_dir() / name
    partial = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if report:
            report(f"Downloading {weight.what}, once, to {target.parent} ...")
        urllib.request.urlretrieve(weight.url, partial)
        actual = _sha256(partial)
        if actual != weight.sha256:
            raise ValueError(f"checksum mismatch: got {actual}, expected {weight.sha256}")
        partial.replace(target)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        if report:
            report(f"Could not fetch {weight.what} ({exc}); continuing without it.")
        return None
    return target


def ensure_weights(report=print) -> bool:
    """Fetch every missing model file. True when all of them are present."""
    fetched = [fetch(name, report) for name in WEIGHTS]
    return all(path is not None for path in fetched)
