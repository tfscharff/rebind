# Rebind

Rebind makes a PDF accessible. Give it a PDF, scanned or born-digital, tagged or not, and it gives
back the same document, looking the same, with what a screen reader needs added: a text layer,
reading order, headings, lists, tables and figure descriptions. The result is a PDF/UA-2 document
that validates with [veraPDF](https://verapdf.org) at zero failures.

It is built for library staff who remediate documents for patrons: interlibrary-loan scans, journal
articles, book chapters, course catalogs. It needs no technical setup, no account and no internet
connection. It runs on an ordinary Windows computer, and a document never leaves it.

What a machine cannot decide, Rebind asks you, in one place: it shows each page with every element
boxed, you walk through with Tab, correct a type with one key, and type a description for each
picture.

## Install

**Windows 10 or 11 (64-bit).** Download `rebind-setup.exe` from the
[latest release](https://github.com/tfscharff/rebind/releases/latest) and run it. No admin rights
are needed. It takes about 550 MB of disk space; most of the 350 MB download is the two models it
uses to read page layout and equations.

The installer is not code-signed, so Windows SmartScreen warns the first time: choose *More info*,
then *Run anyway*.

Open Rebind from the Start menu. It opens a page in your browser; that page is the app. Closing the
tab quits it.

## Use

1. Drop a PDF on the page. Rebind converts it and shows the result: Adobe's accessibility checks on
   the left, the document in the middle, and the element you are on to the right.
2. Press Tab to walk the document in reading order. Each stop is one element: a heading, a
   paragraph, a table row, a picture.
3. Where Rebind got a type wrong, press the key for the right one (the table below). On a picture,
   type what it shows. Press `?` on anything to see every key that works there.
4. Download the PDF. Nothing needs saving along the way, and a downloaded file can be opened in
   Rebind again later with every correction still in place.

| key | | key | | key | |
|---|---|---|---|---|---|
| `p` | Paragraph | `q` | Block quote | `s` | Section |
| `1`–`6` | Heading 1–6 | `c` | Caption | `a` | Article |
| `f` | Figure | `t` | Table | `i` | Index |
| `l` | List | `m` | Formula | `n` | No structure |
| `e` | Code | `o` | Form field | `v` | Footnote |
| `h` | Table header cell | `d` | Table data cell | `x` | Not read |
| `Insert` | New element | `Delete` | Remove element | `[` `]` | Previous / next page |

Frames can be dragged and tables built by hand: see
[the editor reference](docs/editor.md).

## What it does

- **Keeps the page.** A born-digital page stays as it is, crisp and vector. A scan stays the scan.
  Rebind adds an invisible, tagged text layer over it. The one visible change is text that fails
  WCAG colour contrast, which is darkened just enough to pass.
- **Reads scans.** Pages with no text are recognised on the computer itself (RapidOCR). A scan that
  arrives with another tool's text layer keeps it, and Rebind adds what that layer missed.
- **Finds the structure.** Reading order across columns, headings and their levels, lists, tables
  with header cells, captions, footnotes, superscripts, running heads and page numbers (marked as
  page furniture, not read).
- **Handles awkward pages.** Tables printed sideways, drop caps, journal fonts that mislabel their
  own symbols, pages whose boxes do not start at the corner.
- **Finds pictures** and asks you to describe each one, starting from the document's own caption
  where there is one. Equations and printed music get a drafted description for you to check.
- **Checks its work.** The report lists Adobe Acrobat's accessibility rules and where each stands.
  Anything open comes with a fix Rebind can make or the place in the document to make it.

[How it works](docs/how-it-works.md) explains each of these, with the reasons.

## Limitations

- **OCR makes mistakes.** Text recognised from a scan can contain misread words. A line the
  recogniser is not confident of is left out, not guessed at, and nothing marks where it was: check
  a faint or damaged page against the scan.
- **Descriptions are yours to write.** Rebind does not describe pictures. It drafts a description
  only for equations and for the layout of printed music, and never writes a draft into the file
  unless you accept it.
- **Structure is inferred.** Headings, tables and reading order are judged from the page's
  geometry and a layout model. The walk exists because those judgements are sometimes wrong.
- **Mathematics is described in words**, not converted to MathML. Printed music is kept as a
  figure, not read as notes.
- **A document's own bookmarks and internal links are dropped** and bookmarks are rebuilt from
  Rebind's headings (PDF/UA-2 requires a kind of link that older files cannot contain).
- **Pages are not straightened.** A skewed or spine-curved scan is read as it is.
- **English by default.** The document language is set to English; `--lang` changes it from the
  command line. OCR is tuned for Latin-script text.
- **Windows only** as an installed app. It runs from source elsewhere, untested.
- Output is not byte-for-byte reproducible between runs.

## Privacy

The installed app makes no network connections. It serves its page to your own browser at
`127.0.0.1:8756` and nowhere else, and there is no telemetry.

While it works, Rebind keeps copies of the document in the Windows temp folder. Copies more than a
day old are deleted the next time Rebind starts. A log, `rebind.log`, is written beside the
program. It records each request and any error, including the names of the files you open, but
not their text.

## Reporting a problem

Open an [issue](https://github.com/tfscharff/rebind/issues). The most useful report is a document
Rebind handled badly: say what kind it is (scan or born-digital, how many pages), what went wrong
and on which page, and attach `rebind.log` if there was an error. Please do not attach copyrighted
scans to a public issue; describe the page instead.

If Rebind's output is confusing or wrong with the assistive technology you use, that is a bug, and
it is the kind this project most wants to hear about.

## From source

Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/tfscharff/rebind
cd rebind
uv sync
uv run rebind serve                      # the app
uv run rebind convert in.pdf out.pdf     # no app; --title and --lang set the metadata
```

On Windows, `rebind.cmd` in the clone starts the app.

The first run from a clone downloads the page-layout model (130 MB) and the equation reader
(179 MB) into `models/` and checks their SHA-256s: they are too large for the repository. That is
the only time Rebind uses the network, and the installed app never does. Offline, Rebind runs on
its geometric rules alone and tries again next time. `REBIND_LAYOUT_MODEL=off` and
`REBIND_FORMULA_READER=off` switch each model off on purpose; `REBIND_DEBUG=1` prints a full
traceback on failure.

Rebind is not published on PyPI. [CONTRIBUTING.md](CONTRIBUTING.md) covers the tests and the
installer build.

## Status

**1.11.0.** Born-digital and scanned inputs both work end to end, through convert, walk, describe
and download, to a document that validates PDF/UA-2 at zero failures. See
[CHANGELOG.md](CHANGELOG.md) for what changed in each release.

## Documentation

- [How it works](docs/how-it-works.md): what Rebind does to a page, and why
- [The editor](docs/editor.md): every key, frames, tables by hand
- [Decisions](docs/decisions/): the records behind the larger choices (PDF/UA-2, OCR engine,
  determinism, code signing)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT; see [LICENSE](LICENSE). Bundled components carry their own licenses. The installer puts the
full notices in the `licenses` folder beside the program, and they are in
[`packaging/licenses/`](packaging/licenses/) here.
