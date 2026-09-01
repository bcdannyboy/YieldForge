# Artifact policy

**Status:** active boundary for the private research archive and any candidate public tree.

YieldForge separates evidence by what it proves, what it contains, what rights attach to it, and
whether a clean clone can actually use it. File size or a SHA-256 alone does not decide whether an
artifact belongs in Git: a digest can authenticate bytes, but it cannot reconstruct missing bytes
or grant permission to publish them.

## Artifact classes

| Class | Repository treatment | What it can support |
|---|---|---|
| Canonical tracked evidence | Track bounded contracts, reports, source manifests, normalized test fixtures, and selected result records only when their provenance, rights, content, and size have been reviewed. | The claim stated by that artifact and its validator; never a broader physical or commercial claim. |
| Selected M11 manifests | Track exactly the three authenticated manifests listed below. A policy test pins both the exact tracked file set and raw bytes, rejects source-rich geometry/demand fields, and permits ignored local replay files to coexist. | Parent bindings, execution completeness, embedded checkpoint/receipt/summary payloads, aggregate economics, and the terminal disposition. They are not the full raw replay packet. |
| Local or source-rich evidence | Keep raw sources, resumable state, standalone observation/validity/cell sidecars, standalone checkpoint/summary files, raw Gate 2/Gate 3 results, browser/build output, and other reconstructive evidence outside Git or under conservative ignore rules. | Full local validation or resume only when the separately preserved packet and its permissioned inputs are present. |
| Permissioned source inputs | Acquire separately under the source's actual terms. Record version, origin, checksums, transformations, and failed/blocked acquisition explicitly. | Source-bound validation within the permission granted; public readability never substitutes for redistribution permission. |
| Historical source-rich material | Treat existing private commits as preservation records, not automatically publishable artifacts. | Private audit only until the public-release rights and history gates are resolved. |

## Selected M11 manifest set

The tracked M11 economic manifest directory contains exactly these files:

| Manifest | Raw file SHA-256 |
|---|---|
| `m11-economic-calibration-manifest-3409ada18b831fee1394410dfec88a02806ff0c3709372cfbe9340e05b920533.json` | `7b7f2b9e954730e31711fea95a476a8f38f08750a9feddc326aab2d8ce4e02f9` |
| `m11-economic-validity-stage-8d736641be30b3d04dad50eb21698738ac4924bb42f664961911910ddb9ddfe4.json` | `512a371bdee295f21699ddd23174b88319b92061ceacc7fdfe6a87d7b619d7e0` |
| `m11-economic-central-manifest-71171ff1cb601f546f55b78eda8dc2b81d60d7e02949042a55d53feb29e5dcf2.json` | `3f3eb6aaa59ea4a1809e8684b3603096b180ecad4d53e42af6e950d08f7f4633` |

The long digests in the filenames are the experiment's semantic content identities. The raw hashes
above additionally authenticate the exact serialized files copied from the preserved M11
worktree. The manifests intentionally exclude WKB/WKT, polygon coordinates, parts, placements,
source demand, and other reconstructive source fields. They nevertheless disclose extensive
experiment metadata and collectively embed complete checkpoint, receipt, and segment-summary
payloads: per-stream/per-policy records and costs, legacy offsets and failure details, plus
standalone sidecar names and hashes.

The exact tracked-set rule is deliberate. Standalone segment-summary and checkpoint files and all
raw supporting sidecars are omitted and ignored, but the corresponding non-reconstructive record
payloads embedded in the three manifests are disclosed. “Non-reconstructive” describes the absence
of recoverable source geometry and demand; it is not a finding that the files are licensed or
otherwise cleared for publication.

## Local preservation boundary

New raw or resumable experiment output belongs under ignored local storage such as `yf/var/` or an
ignored result family. In particular, do not add the following M11 families to a public commit:

- raw Gate 2 or early Gate 3 results;
- Gate 3 calibration-observation or validity-receipt compressed sidecars;
- Gate 3 central-cell compressed sidecars;
- standalone calibration, validity, or central-cell checkpoints and segment summaries; or
- source geometry, source demand, source archives, or reconstructive normalized derivatives whose
  public rights have not been established.

Preserve local evidence by exact path, byte count, and SHA-256 in a private archive manifest. An
ignored file is not disposable, and an untracked file is not canonical merely because a report
mentions it.

## Rights boundary

The Lectra v1.1 dataset is attributed and transformed under CC BY 4.0 as recorded in
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md). The LOCo source and pinned archive supplied
no express redistribution grant found during this review. Existing LOCo-derived current-tree and
history content therefore remains private unless permission is obtained or an owner-authorized
sanitized history is verified. See [PUBLIC_RELEASE.md](../../PUBLIC_RELEASE.md).
The selected manifests' lack of reconstructive geometry/demand does not resolve this rights gate.

## Clean-clone and source-distribution limits

A clean Git clone can inspect the reports, authenticate the three selected manifests, and run tests
that do not require separately acquired sources or local services. It cannot recreate omitted raw
source/evidence bytes from their hashes.

The M11 independent notebook can be inspected in a clean clone, but its complete raw reconciliation
cannot run without the separately preserved, permissioned replay packet: source-bound parents and
raw calibration-observation, validity-receipt, and central-cell sidecars, plus the standalone file
layout expected by the replay. Supplying that packet permits authentication against the selected
manifests. Their embedded checkpoint/receipt/summary records are already disclosed, but they do not
reproduce the omitted raw sidecar bytes.

The Python source distribution is narrower still. Its explicit allowlist contains package source
and build metadata, and excludes datasets, documentation, tests, local evidence, web dependencies,
browser output, caches, build output, and native targets. Passing an sdist build proves that package
boundary only; it is not a clean-room reproduction of the research result.
