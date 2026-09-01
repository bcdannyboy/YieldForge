# Public release gate

**Status:** not authorized for public visibility.

This repository is being consolidated as a private, paused research archive. Consolidating the
research lineage onto private `main` and making a repository public are separate operations. A
clean merge, passing tests, or a complete README does not grant data rights, choose a project
license, approve embedded identity metadata, or authorize a visibility change.

## Current rights boundary

Lectra-derived material is covered by CC BY 4.0 when the attribution and change notice in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) travels with it. No express redistribution license
was found for the LOCo 2D-ICS source or pinned archive. The current private tree and Git history
contain LOCo-derived source geometry/demand material and are therefore not cleared for public
visibility as they stand.

The three selected M11 manifests do not contain source geometry or source demand, but they disclose
embedded checkpoint, receipt, and segment-summary payloads, including per-stream/per-policy records,
legacy offsets and failure details, and standalone sidecar names and hashes. Their
non-reconstructive character is a technical content observation, not third-party rights clearance
or publication authorization.

Before publication, the owner must either:

1. obtain written permission sufficient for the intended LOCo redistribution; or
2. explicitly authorize a sanitized-history procedure, then verify from a fresh clone that the
   current tree and all history intended for publication contain no uncleared LOCo source or
   reconstructive derivative.

Removing a file only from the current checkout is insufficient when its bytes remain in Git
history. Hashes, citations, and retained derived evidence may remain only after the sanitized result
is reviewed against the artifact policy; describing a record as non-reconstructive does not grant
permission to publish it.

## Unresolved owner decisions

| Decision | Why it cannot be inferred during cleanup | Required owner resolution |
|---|---|---|
| LOCo permission or sanitized history | The official source supplies no express redistribution grant, while derived material exists in history. | Provide sufficient written permission, or authorize a specific history-sanitization boundary and its verification. |
| Project license | No cleanup task can decide how others may use YieldForge's original code and documentation. | Select and approve a `LICENSE`, including any intentional differences between code and content. |
| Proposal and diagram ownership | The repository includes a proposal conversion and visual material whose ownership and third-party elements require confirmation. | Confirm the owner has the right to publish each item, replace/remove uncleared items, and record any required notices. |
| Git author identity | Commit metadata embeds author names and email addresses. Rewriting or retaining that metadata is an identity/privacy decision. | Confirm the existing metadata is acceptable or authorize a defined rewrite before publication. |
| Provisional project name | “YieldForge” has not been cleared here as a durable public project or product name. | Approve the provisional name for publication or rename the repository and public-facing references. |
| Repository visibility | Publication is an external state change with legal, privacy, and reputational consequences. | Explicitly approve the final reviewed commit/history and the change from private to public. |

## Technical release checks after those decisions

- Verify the intended public history from a fresh clone, including a secret/credential scan,
  machine-path scan, link check, and third-party-rights inventory.
- Authenticate the exact three selected M11 manifests and review their disclosed embedded
  checkpoint, receipt, and segment-summary payloads.
- Confirm that no standalone source geometry/demand, raw execution packet, sidecar, checkpoint, or
  segment-summary file is tracked unintentionally.
- Build and inspect the source distribution. It is a software source package, not a research-data
  or full-replay bundle.
- Run the repository's Python, web, formatting, and clean-clone checks at the exact release commit.
- Keep the research workbench local. Its development services have not been reviewed or hardened
  for exposure to untrusted networks.
- Record the owner decisions and release commit before changing remote visibility.

Until every applicable owner decision and technical check is complete, the private consolidated
repository is the preservation artifact; it is not a public release candidate.
