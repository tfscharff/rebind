# Third-party license texts bundled in the installer

This directory is installed into the app's `licenses\` folder (see `rebind.iss`'s `[Files]`
section) and its `LICENSE-THIRD-PARTY.txt` is wired up as the installer's `LicenseFile`.

## What is here

| File | What it is |
|---|---|
| `LICENSE-THIRD-PARTY.txt` | The notice shown in the setup wizard and installed with the app. **Generated** -- do not edit. |
| `DLL-INVENTORY.md` | Per-DLL mapping: project, license expression, license text, and how each was determined. **Generated** -- do not edit. |
| `LICENSE-*.txt` (28 files) | Canonical upstream license texts, fetched verbatim from the FSF, SPDX, or the project's own repository. Not paraphrases. |

Both generated files come from `scripts/license_inventory.py`, which holds the DLL-to-license
mapping and is the source of truth for it:

```bash
uv run python scripts/license_inventory.py           # regenerate both
uv run python scripts/license_inventory.py --check   # verify only
```

The script reads the DLLs actually present in a built bundle and **fails** if the set on disk
and the set it maps disagree in either direction, so the vendored set cannot change without the
inventory being brought along with it. Rebind's own `LICENSE` is installed separately as
`LICENSE-Rebind.txt` by `rebind.iss`.

## Status

The inventory is complete for the bundle as currently built: all 80 DLLs in
`packaging/dist/rebind/_internal/gtk3-runtime/bin/` are mapped, every referenced license text is
present, and the LGPL dynamic-linking conclusion is documented in `DLL-INVENTORY.md` rather than
left implicit. This satisfies the pre-release license obligation.

It will need regenerating -- not redoing -- when the vendored DLL set is trimmed. Most of these
libraries are never loaded: WeasyPrint 69 dlopens six (gobject, pango, harfbuzz, harfbuzz-subset,
fontconfig, pangoft2), and `packaging/rebind.spec` vendors the GTK runtime's entire `bin\`
directory regardless. Trimming to the actual dependency closure would drop roughly two thirds of
these entries, including every LGPL-3 component. Run the script afterwards and it will report
exactly which entries became stale.

## Corrections to earlier assumptions

An earlier draft of this file guessed at the mapping from a partial list. Two entries were wrong,
and both are now determined from the binaries themselves:

- **`libiconv-2.dll` is GNU libiconv (LGPL-2.1-or-later), not win-iconv (MIT).** The upstream
  packager's `license.txt` credits win-iconv, but the DLL exports `_libiconv_version` /
  `libiconv_open` and its version resource reads "libiconv ... 1.16 ... Free Software Foundation".
  This is a materially heavier obligation than the one previously assumed.
- **`libpcre-1.dll` is PCRE1, not PCRE2.** Both are BSD-3-Clause, so the conclusion is unchanged,
  but the text supplied is PCRE1's own `LICENCE`.

More generally, the runtime's own `gtk3-runtime/license.txt` cannot be relied on: it names 13
projects while the runtime ships 80 DLLs, and omits GnuTLS, Nettle, GMP, libidn2, libunistring,
SQLite, libtiff, JasPer, librsvg, gtksourceview and libsoup entirely. Four of those omissions are
LGPL-3, the heaviest obligations in the bundle.
