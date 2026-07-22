# 0003. Scope the determinism claim to a single process

## Status

Accepted

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

This was also confirmed with same-process comparisons (`test_two_runs_produce_identical_bytes`
in `tests/test_reproducible.py`), which reliably pass: two builds *within one interpreter
process* are byte-identical every time. The nondeterminism is specifically a cross-process
phenomenon, and it is not eliminated by pinning `PYTHONHASHSEED`.

### Root cause: narrower than originally diagnosed

The divergence occurs inside `HTML(...).write_pdf(...)` in `src/rebind/render.py`, upstream of
anything `reproducible.py` controls, in WeasyPrint's font-embedding/subsetting path. The
original diagnosis attributed this to Python-level string-hash randomization (a `dict`/`set`
keyed on strings, whose iteration order `PYTHONHASHSEED` controls). The eight-run experiment
above shows that explanation is incomplete: with `PYTHONHASHSEED` fixed identically across all
eight processes, Python's string/bytes hash values are guaranteed identical in every process,
yet the output still varied every time.

The evidence is consistent with a different, and more resistant, class of cause: an unordered
container (a `dict` or `set`) keyed on something whose default hash is based on **object
identity** (`id()`) rather than value -- CPython's default `object.__hash__` derives from an
object's memory address. Object addresses are affected by OS-level address space layout
randomization (ASLR) at process start, which is a separate mechanism from Python's
`PYTHONHASHSEED` and is not controlled by it. This would explain both observations at once:
stable within a process (addresses don't change once assigned) and unstable across processes
even with `PYTHONHASHSEED` pinned (ASLR still varies the addresses). This code path was not
required reading for this decision (rebind's own source does not construct it), and pinpointing
the exact container and library (WeasyPrint, fontTools, or a native dependency such as Pango,
HarfBuzz, or fontconfig that WeasyPrint calls into) needs further upstream investigation; it is
not asserted here as confirmed, only as the explanation consistent with all evidence collected.

## Decision

1. **Pin `PYTHONHASHSEED` wherever Rebind controls the process**, because it is still a
   necessary (if not sufficient) part of eliminating Python-level hash-randomization effects,
   and because it makes test runs reproducible with respect to that one axis:
   - The test suite pins it via a relaunch mechanism in `tests/conftest.py`: since
     `PYTHONHASHSEED` must be set before the interpreter starts (hash randomization is seeded
     once at process startup; setting the environment variable from already-running Python
     code has no effect on that process), `conftest.py` checks the seed at collection time
     and, if it does not match the pinned value, relaunches `pytest` in a child process with
     the correct value set and exits with the child's status code. (An in-place re-exec via
     `os.execve` was tried first and segfaults under this platform's process model; a spawned
     child process achieves the same effect portably.) Every `pytest` invocation is therefore
     pinned regardless of the ambient shell environment.
   - Rebind ships no console entry point as of this decision (no `src/rebind/app.py`, no
     `[project.scripts]` in `pyproject.toml`). If/when one is added, it must apply the same
     relaunch-before-work pattern at its own entry point, for the same reason: setting the
     environment variable inside `main()` is too late for the already-started interpreter.
     This is stated plainly here rather than left silently unhandled: **pinning the hash seed
     does not, by itself, deliver cross-process byte-identity** (see evidence above), so an
     entry point that pins it is a partial mitigation, not a fix.
2. **Narrow the formal claim to what was actually verified.** Rebind's determinism
   constraint is now: the **document model** (structure, tagging, content, and all metadata
   `reproducible.py` pins) **is deterministic**; **PDF bytes are byte-identical for two builds
   within the same process**; **cross-process PDF byte-identity is not currently guaranteed,
   even with a pinned `PYTHONHASHSEED`**, and remains an open upstream issue. Code comments and
   docstrings in `src/rebind/reproducible.py` were updated to say this explicitly.
3. **Keep the underlying defect visible in both of its observed forms.** Two dedicated tests
   in `tests/test_reproducible.py`, both marked `xfail` and referencing this ADR:
   - `test_output_varies_across_differing_hash_seeds_upstream_bug` -- the originally diagnosed
     reproduction (two different seeds).
   - `test_output_still_varies_across_processes_even_with_hash_seed_pinned` -- the stronger
     finding from this ADR (same seed, still varies).

   If a future WeasyPrint/fontTools/native-dependency release removes this source of
   variance, either test will XPASS, which is the signal to revisit this ADR.

## Consequence for Phase 1's golden-file testing

Golden-file (byte-for-byte) comparisons are only valid **within the same process that
generates the golden file and performs the comparison** (e.g. a single test run that renders
both the fixture and the comparison file back to back). They are **not** currently valid
across separate process invocations -- including the common CI pattern of "generate golden
files once, compare against them in later, separate test runs" -- because cross-process
byte-identity does not hold even with a pinned hash seed. Any golden-file strategy for Phase 1
must either (a) regenerate and compare golden files within a single process per test run, or
(b) compare at a level above raw PDF bytes (e.g. the document's tag tree, extracted text, or a
normalized/re-parsed structural representation) until the upstream cross-process
nondeterminism is resolved.

## Upstream reporting

This should be reported upstream to the WeasyPrint project
(https://github.com/Kozea/WeasyPrint), since the nondeterminism originates in its font
handling, not in Rebind, and the evidence above (identical `PYTHONHASHSEED` still producing
divergent output) suggests the responsible party may need to look beyond Python-level hash
randomization -- e.g. at object-identity-based container ordering, or a native dependency
(fontTools, Pango, HarfBuzz, or fontconfig) whose internal ordering is influenced by process
memory layout (ASLR). As of this decision, no upstream issue has been filed; filing one,
including the eight-run byte evidence above, is tracked as follow-up work and is not part of
this decision.
