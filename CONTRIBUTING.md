# Contributing to Rebind

Rebind is built for the library community, and contributions from it are especially welcome —
particularly test documents, accessibility review, and reports of where the tool gets things wrong.

## Ways to help that don't require writing code

- **Report a document Rebind handles badly.** This is the single most useful contribution. Describe the
  document and what went wrong. Please do not attach copyrighted scans to public issues; describe the
  characteristics instead, or contact the maintainer to arrange a private transfer.
- **Accessibility review.** If you use assistive technology and Rebind's output is confusing, wrong, or
  annoying, that is a bug and we want to hear about it.
- **Documentation.** If an explanation didn't make sense to you, it won't make sense to the next person.

## Development setup

*To be written once the implementation exists.*

## Principles to preserve

Rebind is built around a small number of invariants. Changes that violate them will not be merged,
however good the results look:

1. **Never fabricate.** No code path may produce content that does not trace back to recognizer output
   with a confidence score. When the source is unrecoverable, output a placeholder and a flag.
2. **Everything has provenance.** Every node in the document model records the page and bounding box it
   came from.
3. **Deterministic.** The same input at the same version must produce the same output. This is what
   makes testing and reproducibility possible.
4. **No API keys, no GPU requirement, no network dependency at runtime.** A library with no budget must
   be able to run this.
5. **No arbitrary limits.** No caps on structure elements, pages, or document size.

## Reporting security or privacy issues

Documents processed by Rebind may contain sensitive material. If you find an issue where Rebind leaks
document content — to logs, telemetry, crash reports, or anywhere off the local machine — please report
it privately to the maintainer rather than in a public issue.

## Code of conduct

Be decent. Assume good faith. Harassment of any kind is not welcome here.
