# Third-party notices and source provenance

This file records attribution, upstream identities, and YieldForge transformations for research
data used by the project. YieldForge did not create the upstream datasets described here.

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
are not endorsed by the dataset authors or their institutions. The CC BY 4.0 attribution and
change-notice terms continue to accompany the Lectra-derived material.

## LOCo 2D irregular cutting-stock instances

YieldForge also used the official LOCo 2D irregular cutting-stock (`2D-ICS`) instances:

- **Citation:** A. M. Del Valle, T. A. Queiroz, E. C. Xavier, and F. K. Miyazawa,
  *Two-dimensional Irregular Cutting Stock Problem — Instances* (2011)
- **Official landing page:** [LOCo 2D-ICS instances](https://www.loco.ic.unicamp.br/files/instances/2dics/)
- **Archive:** [LOCo 2D-ICS download](https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip)
- **Pinned archive SHA-256:** `86980c3d4a33fb329bd9a4cdc9464a6de9e8450baf70b1b4365944ab471a5133`

### YieldForge transformations

YieldForge verified the pinned archive, parsed and normalized its source geometry and demand, and
used selected source-derived records in the research benchmark and M11 LOCo segment. M11 combined
those upstream observations with YieldForge-derived geometry families and cost ledgers, generated
chronology, generated fallback/candidate structures, and assumed material economics. The resulting
benchmarks, manifests, and reports are transformations used to test YieldForge's modeled selection
policy; they are not measurements published by the LOCo authors and are not endorsed by them or
their institutions.

The tracked M11 manifests omit source geometry and source demand while disclosing compact
checkpoint, receipt, and segment-summary records. Their standalone raw sidecars and replay packet
remain outside the tracked repository, as described in
[the artifact policy](Docs/Development/Artifact%20Policy.md).
