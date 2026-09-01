# Third-party notices

This file records attribution and redistribution boundaries for third-party research data used by
YieldForge. It does not select a license for YieldForge's own code, documentation, proposal, or
diagrams. See [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md) before changing repository visibility.

## Lectra / University of Bordeaux nesting dataset

YieldForge used the following dataset:

- **Authors:** Corentin Lallier, Laurent Vézard, Bruno Pinaud, and Guillaume Blin
- **Title:** *Nesting Tasks Dataset for 2D-Nesting Efficiency Estimation*
- **Version:** 1.1
- **Published:** 2022-05-25
- **DOI:** [10.5281/zenodo.7030786](https://doi.org/10.5281/zenodo.7030786)
- **Source record:** [Zenodo record 7030786](https://zenodo.org/records/7030786)
- **License:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)

### YieldForge transformations

YieldForge verified the pinned version and source checksums, parsed the published parts,
constraints, shapes, and tasks, and produced normalized catalogs and bounded representative
slices. The research then selected task/candidate subsets, assigned stable content identities,
derived geometry-family and remnant-reuse records, and combined source-observed geometry and task
composition with explicitly generated chronology and assumed economics for the M11 experiment.
Aggregate result manifests and reports are additional YieldForge transformations; they are not
original dataset measurements of chronology, cost, savings, or factory performance.

These transformations changed format, selected subsets, and added derived/generated fields. They
are not endorsed by the dataset authors or their institutions. The CC BY 4.0 license and its
attribution and change-notice requirements continue to apply to redistributed Lectra-derived
material.

## LOCo 2D irregular cutting-stock instances

YieldForge also used the official LOCo 2D irregular cutting-stock (`2D-ICS`) instances:

- **Citation:** A. M. Del Valle, T. A. Queiroz, E. C. Xavier, and F. K. Miyazawa,
  *Two-dimensional Irregular Cutting Stock Problem — Instances* (2011)
- **Official landing page:** [LOCo 2D-ICS instances](https://www.loco.ic.unicamp.br/files/instances/2dics/)
- **Archive:** [LOCo 2D-ICS download](https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip)
- **Pinned archive SHA-256:** `86980c3d4a33fb329bd9a4cdc9464a6de9e8450baf70b1b4365944ab471a5133`

As reviewed on 2026-09-01, neither the official landing page nor the pinned archive supplied an
express license granting redistribution of the dataset or derived source material. Public
availability of a download is not itself a redistribution license. YieldForge therefore does not
treat the LOCo archive, normalized LOCo catalog, source demand, source geometry, or reconstructive
derivatives as cleared for public redistribution.

The existing private Git history contains LOCo-derived material. That history is **not cleared for
public visibility** unless the owner first obtains sufficient written permission or explicitly
authorizes and verifies a sanitized history that removes the uncleared source and derivatives.
The three selected M11 manifests contain no source geometry or source demand, but they collectively
embed complete non-reconstructive checkpoint, receipt, and segment-summary payloads. Those payloads
disclose per-stream/per-policy records, legacy byte offsets and failure details, and standalone
sidecar names and hashes. The corresponding standalone checkpoint, summary, and sidecar files are
present in the authenticated private packet, not the tracked tree; their embedded record payloads
are disclosed in the tracked manifests.
“Non-reconstructive” is a technical observation about geometry and demand, not a license or rights
clearance. Including these manifests does not clear any other LOCo-derived artifact or the existing
history for public visibility.
