# Third-party license texts bundled in the installer

This directory is installed into the app's `licenses\` folder (see `rebind.iss`'s `[Files]`
section) and its `LICENSE-THIRD-PARTY.txt` is wired up as the installer's `LicenseFile`.

## What is here

| File | What it is |
|---|---|
| `LICENSE-THIRD-PARTY.txt` | The notice shown in the setup wizard and installed with the app. **Generated** -- do not edit. |
| `PYTHON-INVENTORY.md` | Table of every bundled Python distribution: name, version, license, text. **Generated** -- do not edit. |
| `python/*.txt` | Each bundled distribution's own license text, copied verbatim from its wheel. **Generated** -- do not edit. |
| `LICENSE-*.txt` | Canonical upstream license texts (Apache-2.0 for the OCR models/RapidOCR fallback; FreeType for the credit obligation; plus texts retained for native libraries bundled inside the Python wheels). Verbatim, not paraphrases. |

The generated files come from `scripts/license_inventory.py`:

```bash
uv run python scripts/license_inventory.py           # regenerate the notice + python/ texts
uv run python scripts/license_inventory.py --check   # verify only (used by pytest -m packaging)
```

The script resolves the runtime dependency closure the bundle freezes (from the top-level runtime
deps in `pyproject.toml`), writes each distribution's own license text, and **fails** if any
bundled distribution has no discoverable license text. Rebind's own `LICENSE` is installed
separately as `LICENSE-Rebind.txt` by `rebind.iss`.

## Scope

Rebind remediates PDFs in place and no longer renders HTML, so **WeasyPrint and its vendored GTK3
native stack are no longer bundled** -- that whole per-DLL licensing burden (GnuTLS, Nettle, GMP
and the rest of the LGPL-3 stack) is gone. What remains to credit is the runtime Python closure:
the OCR engine (rapidocr_onnxruntime + onnxruntime + opencv), pikepdf, pypdfium2, Pillow, the web
server, and their dependencies -- each redistributed unmodified under its own license, plus the
bundled PP-OCR models (Apache-2.0). Native libraries that ship *inside* those wheels (e.g. FreeType,
libpng and zlib inside Pillow) are covered by the wheel's own license text; the FreeType credit the
FTL requires is reproduced in `LICENSE-THIRD-PARTY.txt`.
