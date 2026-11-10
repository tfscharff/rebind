# Contributing to Rebind

Rebind is built for the library community, and contributions from it are especially welcome —
particularly test documents, accessibility review, and reports of where the tool gets things wrong.

## Ways to help that don't require writing code

- **Report a document Rebind handles badly.** This is the single most useful contribution. Describe the
  document and what went wrong. Please do not attach copyrighted scans to public issues; describe the
  characteristics instead (scan or born-digital, how many columns, what is on the page).
- **Accessibility review.** If you use assistive technology and Rebind's output is confusing, wrong, or
  annoying, that is a bug and we want to hear about it.
- **Documentation.** If an explanation didn't make sense to you, it won't make sense to the next person.

## Development setup

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/tfscharff/rebind
cd rebind
uv sync --extra dev        # the dev extra brings pytest, ruff, PyInstaller and WeasyPrint
uv run pytest              # about five minutes
uv run ruff check .
```

- **Models.** The first run downloads the page-layout model and the equation reader into `models/`
  (about 310 MB together) and checks their SHA-256s. The tests run with both switched off, apart
  from those marked `layout_model` and `formula_reader`.
- **veraPDF.** The conformance tests validate real output with [veraPDF](https://verapdf.org),
  which needs Java. Put `verapdf` on your `PATH` or set `REBIND_VERAPDF` to it. Without it those
  tests are skipped, so a green run does not mean the output conforms.
- **WeasyPrint** renders the synthetic PDFs the tests are built on. It is a test dependency only
  and needs its GTK libraries on Windows; Rebind itself never renders HTML.
- **DejaVu Serif** must be installed. The synthetic PDFs are set in it; without it another font
  is substituted silently and a few tests about particular lines fail.
  `PYTHONPATH=. uv run python scripts/fixture_fonts.py` shows which font you are getting.
- **CI.** Every push and pull request runs `ruff` and the tests on a Windows machine at GitHub,
  with veraPDF installed (`.github/workflows/ci.yml`). Two tests in `tests/test_app.py` that
  stand in for the app quitting are left out there, and should be left out of a full local run
  the same way: their watchdog threads outlive them and end the test run.
- **Samples.** Real documents used in development are copyrighted and are not in the repository.
  A test must build its own input (`tests/fixtures.py`).

Building the installer (Windows, [Inno Setup 6](https://jrsoftware.org/isinfo.php)):

```
uv run pyinstaller packaging/rebind.spec --noconfirm
ISCC.exe packaging/rebind.iss
uv run pytest -m packaging     # builds the bundle and runs a conversion through the .exe
```

Every behaviour change comes with a test, and the comment beside a rule says what real document
it was written for. Please keep both habits.

## Principles to preserve

Rebind is built around a small number of invariants. Changes that violate them will not be merged,
however good the results look:

1. **Never fabricate.** No code path may produce text that does not trace back to the file's own text
   layer or to recognizer output with a confidence score. What cannot be read is left out, never
   guessed; a drafted description is offered to the person and written only if they accept it.
2. **Everything has provenance.** Every line knows the page and the box it came from.
3. **Deterministic in what it decides.** The same input at the same version must produce the same
   structure and text. The PDF's bytes are not reproducible (ADR 0003), so no test compares them.
4. **No API keys, no GPU requirement, no network dependency at runtime.** A library with no budget must
   be able to run this.
5. **No arbitrary limits.** No caps on structure elements, pages, or document size.
6. **The page is kept.** Rebind adds accessibility to the page it was given; it does not redraw
   the page except where conformance leaves no other way.

## Reporting security or privacy issues

Documents processed by Rebind may contain sensitive material. If you find an issue where Rebind leaks
document content — to logs, telemetry, crash reports, or anywhere off the local machine — please report
it privately, through *Report a vulnerability* on the repository's
[Security tab](https://github.com/tfscharff/rebind/security), rather than in a public issue.

## Code of conduct

Be decent. Assume good faith. Harassment of any kind is not welcome here.
