"""Opt-in test that builds the frozen PyInstaller bundle and exercises the running .exe.

The rest of the suite runs unfrozen (via `uv run pytest`, no `-m packaging` needed) and cannot
catch a regression in `packaging/rebind.spec`'s DLL vendoring -- e.g. someone trims the vendored
GTK3 runtime and rendering silently starts falling back to (or failing without) a system-wide
GTK3 install. This test is slow (a full PyInstaller build) and touches the filesystem/network
port, so it is marked `packaging` and deselected by default (see the `-m "not packaging"` default
in `pyproject.toml`).

Run explicitly with:

    uv run pytest -m packaging -v

Requires PyInstaller (installed via the `dev` extra) and a GTK3-Runtime-Win64 install (or
`REBIND_GTK_RUNTIME` pointed at one) to vendor from, per `packaging/rebind.spec`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.packaging

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGING_DIR = REPO_ROOT / "packaging"
HOST = "127.0.0.1"
PORT = 8756


@pytest.fixture(scope="module")
def frozen_exe() -> Path:
    """Build the frozen bundle once for this module's tests and return the .exe path."""
    if sys.platform != "win32":
        pytest.skip("Frozen build is Windows-only (PyInstaller spec vendors GTK3-Runtime Win64).")

    dist_dir = PACKAGING_DIR / "dist" / "rebind"
    build_dir = PACKAGING_DIR / "build"
    shutil.rmtree(dist_dir, ignore_errors=True)
    shutil.rmtree(build_dir, ignore_errors=True)

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "rebind.spec", "--noconfirm"],
        cwd=PACKAGING_DIR,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"PyInstaller build failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    exe = dist_dir / "rebind.exe"
    assert exe.is_file(), f"Expected frozen exe at {exe}, build did not produce it."
    return exe


def test_license_inventory_matches_the_built_bundle(frozen_exe: Path):
    """Every DLL the build actually vendors must be accounted for in the license inventory.

    The inventory is the installer's legal claim about what it redistributes, and it is written
    against a DLL set that is expected to shrink (most vendored DLLs are never loaded). A trim
    that is not reflected here would leave the installer over-claiming; adding a DLL without
    mapping it would leave it under-claiming, which is the one that matters. `--check` fails on
    drift in either direction, so this pins the two together at the point the bundle is built.
    """
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "license_inventory.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"License inventory drifted from the built bundle:\n{result.stdout}\n{result.stderr}"
    )


def test_frozen_exe_renders_a_real_pdf(frozen_exe: Path):
    """Launch rebind.exe and confirm /render-smoke actually renders -- not just imports.

    DETACHED_PROCESS with no stdout/stderr redirection is load-bearing, not incidental. An
    earlier version of this test used stdout=subprocess.PIPE, which hands the child a valid
    stdout handle and therefore left `sys.stdout` non-None inside the console=False build. That
    masked a crash-on-startup that every real launch hit -- see the use_colors comment in
    `rebind.app.main`. The exe must be started the way a Start menu shortcut starts it: no
    console, no inherited handles. The cost is that there is no captured output to report on
    failure, which is what `rebind.log` next to the exe is for.
    """
    log_file = frozen_exe.parent / "rebind.log"
    log_file.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [str(frozen_exe)],
        cwd=frozen_exe.parent,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
    )

    def _diagnostics() -> str:
        if not log_file.exists():
            return "no rebind.log was written -- the process died before logging started"
        return log_file.read_text(encoding="utf-8", errors="replace")

    try:
        base_url = f"http://{HOST}:{PORT}"
        deadline = time.monotonic() + 30
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(
                    f"rebind.exe exited early (code {proc.returncode}):\n{_diagnostics()}"
                )
            try:
                httpx.get(f"{base_url}/health", timeout=1).raise_for_status()
                break
            except (httpx.HTTPError, ConnectionError) as exc:
                last_error = exc
                time.sleep(0.5)
        else:
            pytest.fail(
                f"rebind.exe never became ready to serve /health: {last_error}\n"
                f"{_diagnostics()}"
            )

        response = httpx.post(f"{base_url}/render-smoke", timeout=30)
        response.raise_for_status()
        body = response.json()

        assert body["success"] is True, body.get("error")
        assert isinstance(body["size_bytes"], int)
        assert body["size_bytes"] > 0

        # The OCR engine is the heaviest bundled native dependency and is never exercised by
        # startup or rendering (its import is lazy). Prove the shipping bundle can actually OCR --
        # models, onnxruntime and opencv all resolving from inside the frozen exe.
        ocr = httpx.post(f"{base_url}/ocr-smoke", timeout=60)
        ocr.raise_for_status()
        ocr_body = ocr.json()
        assert ocr_body["success"] is True, ocr_body.get("error")
        assert "REBIND" in (ocr_body["recovered"] or "").upper()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
