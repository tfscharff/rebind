# Rebind

Rebind makes a PDF accessible by adding a text layer, reading order, headings, lists, tables and
figure descriptions. It exports a PDF/UA-2 document that validates with
[veraPDF](https://verapdf.org) at zero failures. It needs no technical setup, no account and no
internet connection.

It works on scanned and born-digital PDFs. The pages look the same afterwards. It is made for
library staff who prepare documents for patrons: interlibrary-loan scans, journal articles, book
chapters and course catalogs.

![Rebind showing a converted page. Each element on the page is boxed and numbered. A chart is
selected, and a box on the right holds its description.](docs/img/editor-figure.png)

## Install

Rebind runs on Windows 10 and 11 (64-bit).

1. Download `rebind-setup.exe` from the
   [latest release](https://github.com/tfscharff/rebind/releases/latest). It is about 350 MB.
2. Run it. You do not need admin rights. It uses about 550 MB of disk space.
3. Windows will warn you that the installer is not signed. Choose *More info*, then *Run anyway*.
4. Open Rebind from the Start menu. It opens a page in your browser. That page is the app.

To quit, close the browser tab.

## Use

1. Drop a PDF on the page. Rebind converts it and shows you the result.
2. Press Tab to move through the document in reading order. Each stop is one element: a heading,
   a paragraph, a table row or a picture.
3. If Rebind got an element's type wrong, press the key for the right type. The keys are in the
   table below.
4. On a picture, type a description of what it shows.
5. Download the PDF.

You do not need to save as you go. If you open a downloaded file in Rebind later, your corrections
are still there.

Press `?` on any element to see the keys that work on it.

| Key | Type | Key | Type | Key | Type |
|---|---|---|---|---|---|
| `p` | Paragraph | `q` | Block quote | `s` | Section |
| `1` to `6` | Heading 1 to 6 | `c` | Caption | `a` | Article |
| `f` | Figure | `t` | Table | `i` | Index |
| `l` | List | `m` | Formula | `n` | No structure |
| `e` | Code | `o` | Form field | `v` | Footnote |
| `h` | Table header cell | `d` | Table data cell | `x` | Not read |

| Key | Action |
|---|---|
| `Tab`, `Shift`+`Tab` | Next element, previous element |
| `Insert` | Add an element |
| `Delete` | Remove an element |
| `[`, `]` | Previous page, next page |
| `Enter` | On a picture or formula, write its description. Elsewhere, list every type. |

You can also drag an element's box to change which lines it holds, and build a table by hand. See
the [editor guide](docs/editor.md).

## What Rebind does

- **It keeps each page as it is.** It adds a hidden text layer and tags on top. The only visible
  change is to text that is too faint to meet the WCAG contrast rule. Rebind darkens that text
  until it passes.
- **It reads scanned pages.** Text recognition (OCR) runs on your computer. If a scan already has
  a text layer, Rebind keeps it and adds any lines it missed.
- **It works out the structure.** This covers reading order across columns, heading levels,
  lists, tables with header cells, captions, footnotes, and superscripts and subscripts. Running
  heads and page numbers are marked so a screen reader skips them.
- **It finds pictures and asks you to describe them.** If the document has a caption for a
  picture, Rebind offers the caption as a starting point. For equations and printed music it
  writes a draft description for you to check.
- **It handles pages that often go wrong.** These include tables printed sideways, large first
  letters at the start of a paragraph, and journal fonts that label their own symbols wrongly.
- **It checks the result.** The report lists the rules that Adobe Acrobat's accessibility checker
  tests and shows which ones pass. For each rule that does not pass, Rebind either offers a fix
  or takes you to the place in the document to fix it.

[How it works](docs/how-it-works.md) explains each of these in detail.

## Limits

- **Text recognition makes mistakes.** Words read from a scan can be wrong. If Rebind cannot read
  a line with confidence, it leaves the line out, and it does not mark where the line was. Check
  faint or damaged pages against the scan.
- **You write the picture descriptions.** Rebind does not describe pictures. It drafts
  descriptions only for equations and for the layout of printed music. A draft is never saved
  into the file unless you accept it.
- **The structure is a best guess.** Rebind works out headings, tables and reading order from
  where things sit on the page. It is sometimes wrong, which is why you check each page.
- **Mathematics is described in words.** It is not converted to MathML. Printed music is kept as
  a picture.
- **The original bookmarks and internal links are removed.** PDF/UA-2 needs a kind of link that
  older files cannot hold. Rebind builds new bookmarks from the headings.
- **Crooked scans are not straightened.** Rebind reads a tilted or curved page as it is.
- **The document language starts as English.** Rebind does not detect the language. Text
  recognition works best on languages that use the Latin alphabet.
- **The installed app is for Windows only.** Rebind can run from source on other systems, but
  that is not tested.
- **Two runs on the same file do not produce identical bytes.** The content is the same.

## Privacy

The installed app does not connect to the internet. Your documents stay on your computer. Rebind
does not collect usage data.

The app shows its page to your own browser at `127.0.0.1:8756`. No other computer can reach it.

While Rebind works, it keeps copies of your document in the Windows temp folder. Each time it
starts, it deletes copies that are more than a day old.

Rebind writes a log file, `rebind.log`, next to the program. The log holds errors and the names of
the files you open. It does not hold the text of your documents.

## Report a problem

Open an [issue](https://github.com/tfscharff/rebind/issues). Please include:

- whether the document is a scan or born-digital, and how many pages it has
- what went wrong, and on which page
- `rebind.log`, if Rebind showed an error

Do not attach copyrighted scans. Issues are public. Describe the page instead.

If the result is confusing or wrong when you use it with a screen reader, please report that too.

## Run from source

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/tfscharff/rebind
cd rebind
uv sync
uv run rebind serve                      # start the app
uv run rebind convert in.pdf out.pdf     # convert one file without the app
```

On Windows, you can also start the app with `rebind.cmd`.

The first run downloads two model files into `models/`: the page-layout model (130 MB) and the
equation reader (179 MB). They are too large to keep in the repository. Rebind checks each file
against a known SHA-256 hash. This is the only time Rebind uses the internet. The installed app
includes both models and never downloads anything.

Without the models, Rebind still runs. It uses its own layout rules and tries the download again
next time.

| Setting | What it does |
|---|---|
| `--title`, `--lang` | Set the document title and language for `convert` |
| `REBIND_LAYOUT_MODEL=off` | Do not use the page-layout model |
| `REBIND_FORMULA_READER=off` | Do not use the equation reader |
| `REBIND_DEBUG=1` | Print the full error when something fails |

Rebind is not on PyPI. For tests and the installer build, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Status

The current version is **1.11.1**. Scanned and born-digital PDFs both work from start to finish,
and the output validates as PDF/UA-2 with zero failures. [CHANGELOG.md](CHANGELOG.md) lists what
changed in each release.

## More documentation

- [How it works](docs/how-it-works.md): what Rebind does to each page, and why
- [Editor guide](docs/editor.md): every key, plus dragging boxes and building tables by hand
- [Decisions](docs/decisions/): why Rebind targets PDF/UA-2, which OCR engine it uses, and other
  choices
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

## License

Rebind is released under the MIT License. See [LICENSE](LICENSE).

Rebind includes other software, models and fonts, each under its own license. The installer puts
those license texts in the `licenses` folder next to the program. In this repository they are in
[`packaging/licenses/`](packaging/licenses/).
