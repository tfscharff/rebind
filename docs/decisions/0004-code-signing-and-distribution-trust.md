# 0004. Code signing: pursue free OSS signing, not an EV certificate

## Status

Accepted, partially unverified. The landscape findings and the decision are firm. The central
empirical question -- what a managed institutional Windows machine actually does with Rebind's
installer -- **remains untested**, because testing it requires a managed machine and this
decision was made on an unmanaged one. Nothing below should be read as evidence that Rebind
installs cleanly in a library IT environment.

## Context

Rebind's entire distribution premise is a double-click installer used by librarians who, per the
project's hard constraints, "cannot obtain API keys and do not know what one is." Those users are
overwhelmingly on institution-managed Windows machines. If Windows or the institution's endpoint
protection blocks the installer, the product does not exist for its intended audience -- and no
amount of pipeline quality compensates.

The binary is unsigned. This was flagged during Phase 0 as untested and plausibly a larger
adoption risk than the unbuilt installer, and was missing from the design spec's risk table.

### What was measured locally

On the development machine (Windows 11 Home, Defender in `Normal` running mode, real-time
protection on, no third-party AV, no managed policy):

```
Get-AuthenticodeSignature rebind.exe  ->  Status: NotSigned
Start-MpScan -ScanPath packaging\dist\rebind -ScanType CustomScan
  -> completed, no threat detections referencing rebind
```

**This is weak evidence and must not be cited as reassurance.** It is the friendliest possible
configuration: consumer Defender with no Attack Surface Reduction rules, no application
allowlisting (AppLocker/WDAC), no third-party endpoint agent, and -- critically -- no Mark of the
Web on the files, since they were built locally rather than downloaded. SmartScreen's
reputation check is triggered by the MOTW alternate data stream that a browser attaches to a
downloaded file; a locally-built binary never encounters it. The measurement above therefore says
almost nothing about the case that matters.

What it does establish is a narrow negative: Rebind's bundle does not trip a *signature-based*
Defender detection outright, so the problem to solve is reputation and policy, not a specific
malware signature.

### One concrete defect found and fixed

`rebind.exe` had **no Win32 version resource at all** -- `ProductName`, `CompanyName`,
`FileDescription`, `FileVersion` and `LegalCopyright` were all empty, because PyInstaller emits
no version resource unless one is supplied. Combined with the binary's other properties, that is
close to a textbook antivirus heuristic profile: unsigned, ~170 MB, packed by PyInstaller,
shipping ~80 native DLLs, opening a local network listener at startup, and carrying no
identifying metadata whatsoever.

Metadata is not a substitute for a signature, but it is free and it removes one strike. Fixed in
`packaging/version_info.txt`, wired into `packaging/rebind.spec` via `EXE(version=...)`, and
verified on a rebuilt bundle. It is also a prerequisite for the signing route chosen below.

### The signing landscape changed in ways that invalidate the obvious plan

The intuitive move -- buy an EV certificate, because EV used to bypass SmartScreen outright --
**no longer works.** Per Microsoft's current code signing guidance:

| Option | Cost | SmartScreen behavior |
|---|---|---|
| Azure Artifact Signing (formerly Trusted Signing) | ~$9.99/month | Reputation builds over time; initial warnings expected |
| OV certificate | $150-300/year | Same -- reputation builds over time |
| EV certificate | $400+/year | **Same as OV since 2024** -- the instant bypass was removed |
| Self-signed | Free | Blocks installation for public users |
| Unsigned | Free | Strong SmartScreen block; enterprises may block entirely |
| SignPath Foundation (OSS) | Free | OV-level signing for qualifying open-source projects |

Two consequences. First, **paying the EV premium to avoid SmartScreen warnings is no longer
justified** -- it buys the same reputation-building curve as far cheaper options. Second, since
every paid option lands in the same place, cost becomes the deciding factor, and for an
MIT-licensed public repository the cheapest option is free.

Azure Artifact Signing is the general recommendation for non-Store distribution, is available to
individuals in the USA and Canada, and as of its April 2026 GA no longer requires the three years
of verifiable history that the preview demanded. It is the fallback if the free route fails.

### SignPath Foundation eligibility

SignPath Foundation provides free OV-level code signing to qualifying open-source projects.
Rebind's posture against the requirements:

| Requirement | Rebind |
|---|---|
| OSI-approved license, no commercial dual-licensing | ✅ MIT |
| No proprietary code from maintainers | ✅ |
| Not malware/PUP/hacking tools | ✅ |
| No undisclosed data collection or system modification | ✅ -- no network at runtime is already a hard constraint |
| Actively maintained | ✅ |
| **Already released in the form to be signed** | ❌ **Not yet** -- the installer has never been built |
| **Binaries built from source in a verifiable way** | ❌ **Not yet** -- no CI build pipeline exists |
| Product name and version attributes set consistently | ✅ -- fixed above |
| MFA, defined author/reviewer/approver roles, published signing policy | ❌ Not yet |

The two hard blockers are both downstream of work already known to be outstanding: there must be
a real release, and it must come out of a verifiable (CI) build rather than a developer's laptop.

## Decision

1. **Do not buy an EV certificate.** Its distinguishing benefit for this problem was removed in
   2024. Do not buy an OV certificate either while a free route remains open.
2. **Target SignPath Foundation** for signing, applying once Rebind has a first tagged release
   built by CI. This is free, OV-level, and appropriate to an MIT-licensed public project.
3. **Fall back to Azure Artifact Signing (~$9.99/month)** if SignPath declines or its process
   proves too slow. Do not treat this as a failure case -- it is Microsoft's own recommendation
   for non-Store distribution and is cheap.
4. **Ship version metadata regardless of signing status** -- done, and it stands on its own
   merits for AV heuristics.
5. **Expect SmartScreen warnings on early releases no matter what.** No available option grants
   instant trust. Plan for this as a documentation and communication problem: the install
   instructions must tell a librarian exactly what warning they will see and what to click, in
   plain language, rather than leaving them to conclude the software is broken or malicious.
6. **Treat institutional endpoint protection as a separate, still-open risk from SmartScreen.**
   Signing addresses reputation; it does not address application allowlisting (AppLocker/WDAC),
   ASR rules, or a third-party agent's heuristics about large packed binaries. These need real
   testing on a real managed machine.

## What remains untested

The load-bearing empirical questions are all still open:

- What a managed Windows machine at a library actually does with the installer -- SmartScreen,
  Defender under enterprise policy with ASR rules, and any third-party endpoint agent.
- Whether application allowlisting blocks execution outright, which signing would not fix and
  which would require the institution to allowlist Rebind explicitly.
- Whether the local listener on `127.0.0.1:8756` (see `src/rebind/app.py`) trips endpoint
  policy independently of the binary itself. This is an unusual shape for a desktop app and has
  not been considered from a security-policy perspective at all.
- Whether MOTW propagates from the downloaded installer to the extracted payload in a way that
  causes repeat prompting.

The natural place to answer these is Wheaton College, via Allie -- the primary user is inside
exactly the kind of environment that needs testing. That is the cheapest available route to real
evidence and should be pursued before the installer's design is considered settled.

## Consequence for the risk table

The design spec's risk table (section 12) did not mention this at all. Two rows added: one for
unsigned-binary/SmartScreen friction, one for institutional endpoint protection blocking
installation outright. The latter is rated High -- it is unmitigable by Rebind alone if it
occurs, since the remedy lies with the institution's IT department.
