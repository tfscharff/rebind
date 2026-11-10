"""Take the README's screenshots: drive a headless Chrome over the DevTools protocol, upload the
demo document to a running Rebind, and save a picture of the result with one element selected.

Three terminals:

    uv run rebind serve
    uv run python scripts/serve_file.py annual-report.pdf 8799      # see demo_document.py
    uv run --with websockets python scripts/screenshots.py docs/img light \
        '{"editor-heading": "Heading 2, item", "editor-figure": "Figure"}'

The third argument maps each picture's name to the start of the label of the element to select.
A fourth, optional, is the window's size, to see the page in a smaller one: `1500,845`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APP = "http://127.0.0.1:8756/"
DOCUMENT = "http://127.0.0.1:8799/"
DEBUG_PORT = 9333

UPLOAD = """(async () => {
  const blob = await fetch(%s).then(r => r.blob());
  const file = new File([blob], 'annual-report.pdf', {type: 'application/pdf'});
  const transfer = new DataTransfer();
  transfer.items.add(file);
  const input = document.querySelector('input[type=file]');
  input.files = transfer.files;
  input.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
})()""" % json.dumps(DOCUMENT)

SELECT = """[...document.querySelectorAll('.ob')]
  .find(box => (box.getAttribute('aria-label') || '').startsWith(%s)).focus()"""


async def capture(out: Path, scheme: str, shots: dict[str, str]) -> None:
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json") as reply:
                tabs = json.load(reply)
            break
        except OSError:
            time.sleep(0.2)
    address = next(tab["webSocketDebuggerUrl"] for tab in tabs if tab["type"] == "page")
    async with websockets.connect(address, max_size=50_000_000) as socket:
        sent = 0

        async def call(method: str, **params):
            nonlocal sent
            sent += 1
            await socket.send(json.dumps({"id": sent, "method": method, "params": params}))
            while True:
                message = json.loads(await socket.recv())
                if message.get("id") == sent:
                    return message.get("result", {})

        async def run(script: str):
            reply = await call("Runtime.evaluate", expression=script, awaitPromise=True,
                               returnByValue=True)
            return reply.get("result", {}).get("value")

        await call("Emulation.setEmulatedMedia",
                   features=[{"name": "prefers-color-scheme", "value": scheme}])
        await call("Page.reload")
        await asyncio.sleep(1.5)
        await run(UPLOAD)
        for _ in range(240):        # the conversion, then the editor drawing its boxes
            await asyncio.sleep(0.5)
            if await run("document.querySelectorAll('.ob').length"):
                break
        await asyncio.sleep(5)
        for name, label in shots.items():
            await run(SELECT % json.dumps(label))
            await asyncio.sleep(1.0)
            picture = await call("Page.captureScreenshot", format="png")
            (out / f"{name}.png").write_bytes(base64.b64decode(picture["data"]))
            print("wrote", out / f"{name}.png")


def main() -> None:
    out, scheme, shots = Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
    size = sys.argv[4] if len(sys.argv) > 4 else "1500,1130"
    out.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="rebind-shot-")
    chrome = subprocess.Popen([
        CHROME, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={profile}", f"--window-size={size}", "--hide-scrollbars",
        "--force-device-scale-factor=1.5", APP])
    try:
        asyncio.run(capture(out, scheme, shots))
    finally:
        chrome.terminate()


if __name__ == "__main__":
    main()
