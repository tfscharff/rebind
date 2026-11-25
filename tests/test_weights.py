"""The model files fetched into a source checkout (`rebind.weights`). The failure paths -- offline,
a bad checksum -- are in test_layout_model.py; these are the paths that must not download at all,
and the one that must put a good download in place. Every "download" here is a local file URL."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from rebind import weights


def _local_weight(tmp_path: Path, content: bytes = b"model bytes") -> weights.Weight:
    origin = tmp_path / "origin.onnx"
    origin.write_bytes(content)
    return weights.Weight(origin.as_uri(), hashlib.sha256(content).hexdigest(), "a model")


def test_a_good_download_is_put_in_place_and_leaves_no_partial_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(weights, "model_dir", lambda: tmp_path / "models")
    monkeypatch.setitem(weights.WEIGHTS, "latex_ocr/encoder.onnx", _local_weight(tmp_path))

    said: list[str] = []
    path = weights.fetch("latex_ocr/encoder.onnx", report=said.append)

    assert path == tmp_path / "models" / "latex_ocr" / "encoder.onnx"
    assert path.read_bytes() == b"model bytes"
    assert not list((tmp_path / "models").rglob("*.part"))
    assert said and said[0].startswith("Downloading a model")


def test_a_file_already_present_is_not_downloaded_again(tmp_path: Path, monkeypatch):
    present = tmp_path / "models" / "pp_doc_layoutv3.onnx"
    present.parent.mkdir(parents=True)
    present.write_bytes(b"already here")
    monkeypatch.setattr(weights, "model_dir", lambda: tmp_path / "models")
    # A URL that cannot be fetched: reaching for it would report a failure and return None.
    monkeypatch.setitem(weights.WEIGHTS, "pp_doc_layoutv3.onnx", weights.Weight(
        (tmp_path / "nowhere.onnx").as_uri(), "0" * 64, "a model"))

    said: list[str] = []
    assert weights.fetch("pp_doc_layoutv3.onnx", report=said.append) == present
    assert said == []
    assert present.read_bytes() == b"already here"


def test_the_installed_app_never_downloads(tmp_path: Path, monkeypatch):
    # Invariant 4: no network at runtime. Frozen, a missing model stays missing -- the bundle
    # carries its models, and a missing one means running without it, never fetching it.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(weights, "model_dir", lambda: tmp_path / "models")
    monkeypatch.setitem(weights.WEIGHTS, "latex_ocr/encoder.onnx", _local_weight(tmp_path))

    def no_network(*args, **kwargs):
        raise AssertionError("the frozen app tried to download a model")

    monkeypatch.setattr("urllib.request.urlretrieve", no_network)
    said: list[str] = []
    assert weights.fetch("latex_ocr/encoder.onnx", report=said.append) is None
    assert said == []
    assert not (tmp_path / "models").exists()


def test_the_frozen_bundle_looks_for_its_models_inside_itself(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    assert weights.model_dir() == tmp_path / "bundle" / "models"


def test_ensure_weights_is_true_only_when_every_model_is_present(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(weights, "model_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(weights, "WEIGHTS", {
        "good.onnx": _local_weight(tmp_path),
        "bad.onnx": weights.Weight((tmp_path / "missing.onnx").as_uri(), "0" * 64, "a model"),
    })
    assert weights.ensure_weights(report=None) is False
    assert (tmp_path / "models" / "good.onnx").is_file()

    del weights.WEIGHTS["bad.onnx"]
    assert weights.ensure_weights(report=None) is True
