# 0003. Rebind's PDF output is not byte-reproducible, at any process granularity

## Status

Accepted (superseded finding: an earlier version of this decision scoped the determinism claim
to "byte-identical within a single process." That narrower claim has since been retested and
falsified -- see "Same-process byte-identity does not hold either" below. The claim is now
retracted rather than narrowed: rebind's PDF *bytes* are not guaranteed reproducible at all. The
document model remains deterministic; see the Decision section.)

## Context

Rebind's stated constraint (Phase 1 design spec) was: "Deterministic: same input at the same
version produces the same output." This was implemented and verified in
`src/rebind/reproducible.py` (`pin_document_metadata`), which pins the PDF's title, language,
timestamps, and trailer `/ID`. Task 5's code-review fix pass additionally found and fixed a
real bug where `pdf.save(deterministic_id=False, ...)` let qpdf regenerate the trailer's
second `/ID` element from wall-clock time/PID on every save.

After that fix, a cross-process test was added specifically to catch nondeterminism that a
single-process test cannot see: Python's hash randomization (`PYTHONHASHSEED`) is fixed once
per process, so nondeterminism driven by iterating a hash-randomized container (a `dict` or
`set` whose iteration order depends on string hashing) is invisible to a same-process
comparison but visible across two processes with different seeds.

That test failed. Two builds of byte-for-byte identical input (same HTML, same title, same
lang), run in two separate `python -c` subprocesses with `PYTHONHASHSEED=0` and
`PYTHONHASHSEED=12345` respectively, produced PDFs that were **not** byte-identical, diverging
inside an embedded font's compressed stream:

```
first differing byte offset: 2648
a: << /Filter /FlateDecode /Length1 2416 /Length 1345 >>\nstream\nx\xda\xcdV}lSU...
b: << /Filter /FlateDecode /Length1 2416 /Length 1344 >>\nstream\nx\xda\xcdV}lSU...
```

The initial hypothesis, based on that evidence, was that this was purely Python string-hash
randomization inside WeasyPrint's font subsetting, and that pinning `PYTHONHASHSEED` to the
same value across processes would restore byte-identity. **This document records that the
hypothesis was tested directly and falsified; the actual scope of the defect is broader.**

### Byte evidence: pinning the same seed does not fix it

To test the "same pinned seed restores byte-identity" hypothesis, the same input was rendered
in **eight separate subprocess invocations**, all with `PYTHONHASHSEED=0` (i.e. every process
used the identical, non-randomized hash seed), same target filename reused sequentially, same
HTML/title/lang:

```
run  size(bytes)  sha256(12 hex chars)
1    7335         d477877a341e
2    7335         b6bfc878d3ab
3    7335         15c43c58d7c1
4    7335         01752eeb709b
5    7334         58e24146978e
6    7334         3964642eee4f
7    7334         313f072ec30d
8    7334         c5f0f6d6102f
```

All eight runs produced **distinct** SHA-256 hashes despite an identical, pinned
`PYTHONHASHSEED`. A byte-level diff between any two of these files shows the same signature as
the original differing-seed case: identical `/Length1` (uncompressed glyph program size, 2416
in every run) but a different compressed `/Length` and different compressed bytes from that
point on -- i.e. the same embedded-font-subset divergence, occurring **even when Python's hash
randomization is held constant**.

This was originally believed to be confirmed as a same-process invariant by
`test_two_runs_produce_identical_bytes` in `tests/test_reproducible.py`, which was reported to
pass reliably. **That belief has since been retested directly and falsified — see "Same-process
byte-identity does not hold either" below.**

This table is reproducible from the repo, not just asserted here: `scripts/determinism_probe.py`
runs N independent subprocess builds with a pinned `PYTHONHASHSEED` and prints each output's
size and SHA-256. Run it yourself with `uv run python scripts/determinism_probe.py 8` to
regenerate the table above (hashes will differ from the ones printed here since fontTools/
WeasyPrint versions and the exact divergence are not pinned by this decision, but the shape --
all distinct, sizes clustering near two adjacent values -- should reproduce).

### A larger sample changes the picture: the 8-run size split does not hold up

The eight-run table above happens to split cleanly into four runs at 7335 bytes and four at
7334. Read on its own, a clean binary split is at least as consistent with a coarse two-state
effect (e.g. a subset/no-subset decision, or one of two glyph orderings) as with address-space
randomization, which would be expected to scatter sizes more broadly rather than settle into
exactly two values.

To check this, the same probe was re-run with **N = 20**:

```
run  size(bytes)  sha256 (first 12 hex chars)
1    7334         1d2146599d5c
2    7334         fc5b91d270fa
3    7334         eeb457809cd8
4    7335         8251417ea2ff
5    7335         5366f2b8d6c3
6    7334         da7788fa8201
7    7334         c6017d6f3554
8    7334         1550d215d07e
9    7334         fd31242bc6e8
10   7334         0a78a5b40225
11   7334         b1c3370866ea
12   7334         46535d18469e
13   7334         69d9cbb079a7
14   7333         6bd2c5234214
15   7334         0e99a5fe614b
16   7334         2446f510ac5b
17   7333         5fbc55c51fb7
18   7334         a38493c44668
19   7334         2ba4985ebd1c
20   7332         a1a1597b278b

20 distinct SHA-256 hashes out of 20 runs.
Byte-length distribution: 7332 (1 run), 7333 (2 runs), 7334 (15 runs), 7335 (2 runs).
```

At N = 20, the sizes do **not** stay confined to the two values seen at N = 8: four distinct
sizes appear (7332-7335), with 7334 as a clear mode and the other three sizes each occurring
once or twice. This means the "clean binary split" observed at N = 8 was very likely a small-
sample artifact, not evidence of a genuine two-state mechanism -- with only eight draws from a
distribution that puts most of its mass on one value and small amounts on a few neighbors,
landing on exactly two observed values is unsurprising. The larger sample does not, however,
cleanly settle the question in the other direction either: the distribution is not the wide,
unclustered scatter one might naively expect from ASLR-driven addresses either. It is a narrow
spread of byte-length outcomes (a 3-byte range) with an uneven mode, which is compatible with
several mechanisms and does not, by itself, single one out.

### Same-process byte-identity does not hold either

A later investigation (prompted by a previously-flagged, then-unexplained flake in
`test_two_runs_produce_identical_bytes` during full-suite runs) retested the one claim this
ADR still made: that two builds *within a single process* are always byte-identical. That claim
is **false**. It does not survive direct, repeated measurement:

- **Full suite, run 12 times back to back:** 1/12 runs (8.3%) failed
  `test_two_runs_produce_identical_bytes`.
- **The same test run in isolation (`pytest tests/test_reproducible.py::test_two_runs_produce_identical_bytes`),
  12 times, concurrently with an unrelated full-suite loop running in another process:** 2/12
  runs (16.7%) failed.
- **The same isolated test, run 15 times sequentially with no other process running at all** (to
  rule out cross-process resource contention, e.g. a shared fontconfig/font cache, as a
  confound): **2/15 runs (13.3%) still failed.** This rules out "leftover WeasyPrint state from
  earlier tests in the suite" and "contention with another concurrent process" as the
  explanation — the failure rate is statistically indistinguishable whether the test runs alone,
  back-to-back with itself, or alongside a full suite in another process.
- **A minimal repro** (`_build()` called twice back-to-back inside one Python process, no
  pytest, no fixtures, no `tmp_path` reuse across attempts) reproduced the same failure signature
  on the 5th of 5 attempts in one run.

In every failing case, the two builds have the same `Length1` (uncompressed glyph program size,
e.g. `2416`) but differ starting partway through the *compressed* `FlateDecode` font stream that
follows it — the identical signature already documented above for the cross-process case. One
concrete example, `_build()` called twice in the same process:

```
first differing byte offset: 2677 (of ~7334 total bytes)
dict header at that offset (both builds): << /Filter /FlateDecode /Length1 2416 /Length 1344 >>
first : ...\x14?\xf7\xf5\xbd\xae\x9b|\xac\xd8n\x04\x91\xb5+\xeb\x98\xc0\x84u\xddTd\x8e\xb9\x95\x81\xd31\xf6\xe1\xe4cP\xba\xafB\xbbvk'<\x05\xb7B\x04q\x89\x11
second: ...\x14?\xf7\xf5\xbd\xae\x9b|\xac\xd0n\x04\x91\xb5+\xeb\x98\xc0\x84u\xddTd\x8e\xb9\x95\x81\xd31\xf6\xe1\xe4cP\xba\xafB\xbbvk'<\x05G!\x82\xb8\xc4\x08
```

Everything before the divergence point — including the entire uncompressed glyph program length
(`Length1`) — is identical; only the emitted compressed bytes inside the embedded font's
`FontFile2` stream vary. This is the same embedded-font-subsetting code path implicated above
(`src/rebind/render.py` → WeasyPrint's `pdf/fonts.py`, which subsets via HarfBuzz's native
subsetter when available — see `Font._harfbuzz_subset`), not a test-harness artifact: each of
the two builds in every reproduction uses its own target file, its own `tmp_path`, and no shared
global state that rebind's own code introduces. The two builds run consecutively in the very
same interpreter, with `PYTHONHASHSEED` (and everything else about the environment) held
constant by construction, and still occasionally diverge. This means the earlier working theory
— that the phenomenon needs a process boundary at all — was itself incomplete: whatever varies
(most plausibly heap-allocation-pattern-dependent iteration inside HarfBuzz's native subsetter,
since the two calls in the same process do not necessarily reuse identical memory addresses or
allocator state between the first and second build) can vary between two calls in one process,
not only between two processes.

**Practical consequence:** byte-identity is not a reliable, deterministic property of rebind's
PDF output at any granularity — same process or across processes. It is better described as
*usually* byte-identical (roughly 85-90% of paired same-process builds in the measurements
above) with a real, intermittent, non-negligible chance of divergence confined to the
compressed bytes of embedded font subsets.

### Correction (post-Phase 0): HarfBuzz's subsetter is not involved at all

The passage above, and the hypothesis about "heap-allocation-pattern-dependent iteration inside
HarfBuzz's native subsetter," are **wrong about the mechanism**, though the observations they
rest on stand. Rebind never runs HarfBuzz's subsetter. Two independent reasons, either alone
sufficient:

- `libharfbuzz-subset-0.dll` is not present in the GTK3 runtime at all, and WeasyPrint dlopens
  it with `allow_fail=True`, so `harfbuzz_subset` is `None`.
- `Font.subset` additionally requires `harfbuzz.hb_version_atleast(4, 1, 0)`. The bundled
  HarfBuzz is older than that, so even supplying the DLL would not change which path runs.

Every subset is therefore produced by `Font._fonttools_subset` — pure Python, via fontTools.
This **eliminates the native-allocator hypothesis**, which was the leading candidate, and
narrows the remaining search to fontTools' subsetting output or the zlib compression applied to
it. That is a strictly easier problem than a native heap-order theory: it is inspectable from
Python, and fontTools has no address-dependent iteration to appeal to.

This does not change the decision below. Byte-identity remains a property Rebind does not claim,
and the determinism invariant stays scoped to the document model. It does mean that anyone
picking up the root-cause investigation should start at fontTools, not HarfBuzz.

Confirmed by `tests/test_font_subsetting.py`, which also verifies that subsetting genuinely
happens — an earlier note claimed the missing DLL had disabled it, and that was wrong too: the
fallback path was subsetting correctly the whole time.

### Root cause: unresolved, with competing hypotheses

The divergence occurs inside `HTML(...).write_pdf(...)` in `src/rebind/render.py`, upstream of
anything `reproducible.py` controls, in WeasyPrint's font-embedding/subsetting path. The
original diagnosis attributed this to Python-level string-hash randomization (a `dict`/`set`
keyed on strings, whose iteration order `PYTHONHASHSEED` controls). The eight-run experiment
above shows that explanation is incomplete: with `PYTHONHASHSEED` fixed identically across all
eight processes, Python's string/bytes hash values are guaranteed identical in every process,
yet the output still varied every time.

Two hypotheses remain on the table, and **this decision does not resolve between them**:

- **Object-identity hashing influenced by ASLR.** An unordered container (a `dict` or `set`)
  keyed on something whose default hash is based on object identity (`id()`) rather than
  value -- CPython's default `object.__hash__` derives from an object's memory address, which
  OS-level address space layout randomization (ASLR) varies per process start, independently of
  `PYTHONHASHSEED`. This would explain both observations at once: stable within a process
  (addresses don't change once assigned) and unstable across processes even with
  `PYTHONHASHSEED` pinned.
- **A coarse, small-state decision elsewhere in the subsetting path** (e.g. a subset/no-subset
  branch, or a small number of glyph-ordering outcomes) that happens to vary per process for a
  reason unrelated to address-space layout. The N = 20 run above rules out the *specific* claim
  that this is a clean two-state split, but it does not rule out a small-state mechanism in
  general -- a narrow, unevenly-weighted spread of outcomes is also consistent with, for
  example, a handful of possible orderings with unequal probability.

Neither hypothesis has been confirmed by inspecting the actual WeasyPrint/fontTools/native-
dependency code path responsible; this code path was not required reading for this decision
(rebind's own source does not construct it). Pinpointing the exact container and library
(WeasyPrint, fontTools, or a native dependency such as Pango, HarfBuzz, or fontconfig that
WeasyPrint calls into) needs further upstream investigation. Stating this as ASLR without that
investigation would be overstating what the evidence shows; the honest summary is that the
mechanism is unresolved and both hypotheses remain live.

## Decision

1. **Pin `PYTHONHASHSEED` only where a specific test needs a controlled seed, in that test's
   own subprocess environment**, not process-wide. An earlier version of this decision had
   `tests/conftest.py` relaunch the whole `pytest` process in a child with the seed pinned
   whenever the ambient environment didn't match. That was removed: since pinning the seed
   does not, by itself, deliver cross-process byte-identity (see evidence above), the
   process-wide relaunch was buying only cosmetic reproducibility at one fixed seed, at the
   real cost of an untraced child process that silently breaks IDE debugger breakpoints and
   coverage collection for the whole suite. The two tests in `tests/test_reproducible.py` that
   actually need a controlled `PYTHONHASHSEED` (see below) set it directly in the environment
   of the subprocess they spawn to build a PDF, which is the correct scope for that control and
   does not affect the process running pytest itself.
   - **Correction (final whole-branch review): this statement is stale.** Rebind now ships both
     a console entry point (`src/rebind/app.py`, `main()`) and a `[project.scripts]` entry
     (`rebind = "rebind.app:main"` in `pyproject.toml`), added in a later Phase 0 task after
     this ADR was originally written. Neither pins `PYTHONHASHSEED` for the whole process, so
     the substance of this decision (pin only where a specific test needs it, in that test's
     own subprocess) is unaffected -- only the "ships no console entry point" factual claim
     was wrong and has been corrected here. If either entry point is ever changed to pin a
     seed for some other reason, note that pinning the hash seed does not, by itself, deliver
     cross-process byte-identity (see evidence above) -- it would be a partial mitigation for
     Python-level hash effects, not a fix for this ADR's finding.
2. **Narrow the formal claim to what was actually verified -- and correct it again now that
   the remaining claim has also been falsified.** Rebind's determinism constraint is now: the
   **document model** (structure, tagging, content, and all metadata `reproducible.py` pins)
   **is deterministic**. **PDF bytes are NOT guaranteed byte-identical, even for two builds
   within the same process** -- repeated measurement (see "Same-process byte-identity does not
   hold either" above) found same-process divergence at a rate of roughly 1 in 7 to 1 in 12
   paired builds, with the same embedded-font-subset signature as the cross-process case.
   Rebind previously claimed same-process byte-identity as its one remaining determinism
   guarantee; **that guarantee does not hold and the claim is retracted.** Code comments and
   docstrings in `src/rebind/reproducible.py` were updated to say this explicitly.
3. **Keep the underlying defect visible in all of its observed forms.** Three dedicated tests
   in `tests/test_reproducible.py`, all marked `xfail(strict=False)` and referencing this ADR:
   - `test_output_varies_across_differing_hash_seeds_upstream_bug` -- the originally diagnosed
     reproduction (two different seeds).
   - `test_output_still_varies_across_processes_even_with_hash_seed_pinned` -- the stronger
     finding from this ADR (same seed, still varies).
   - `test_two_runs_produce_identical_bytes` -- originally written (and, for a time, believed)
     to prove same-process byte-identity; converted to a non-strict `xfail` once repeated
     measurement showed it fails intermittently even in isolation, with no other process
     running, for the same reason as the two tests above.

   If a future WeasyPrint/fontTools/native-dependency release removes this source of
   variance, any of the three will XPASS, which is the signal to revisit this ADR.

## Consequence for Phase 1's golden-file testing

Golden-file (byte-for-byte) comparisons are **not currently valid at any granularity** --
neither across separate process invocations nor within a single process that generates and
compares a fixture back to back -- because byte-identity does not reliably hold even for two
builds in the same process (see evidence above). Phase 1's golden-file strategy must therefore
compare **above the level of raw PDF bytes**: the document's tag tree, extracted text, page
structure, or a normalized/re-parsed structural representation, never PDF bytes directly, until
the upstream embedded-font-subsetting nondeterminism is resolved.

## Upstream reporting

This should be reported upstream to the WeasyPrint project
(https://github.com/Kozea/WeasyPrint), since the nondeterminism originates in its font
handling, not in Rebind, and the evidence above (identical `PYTHONHASHSEED` still producing
divergent output, and a byte-length distribution at N = 20 that is neither a clean two-state
split nor a wide ASLR-like scatter) shows the responsible party needs to look beyond
Python-level hash randomization, without yet pinning down which of the two hypotheses named
above (object-identity/ASLR-influenced container ordering, or some other small-state
mechanism) is correct. `scripts/determinism_probe.py` is included in this repo specifically so
that evidence can be regenerated and attached to an upstream report at whatever N is useful. As
of this decision, no upstream issue has been filed; filing one, including the byte evidence
above, is tracked as follow-up work and is not part of this decision.
