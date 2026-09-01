# Technical sources

This note records upstream software and public-data leads so implementation evidence is not scattered across chats.

## Installed foundation

- [Spyrrow](https://github.com/PaulDL-RS/spyrrow) — Python bindings and progress reporting for Sparrow; pinned to 0.9.0.
- [Sparrow](https://github.com/JeroenGar/sparrow) — irregular strip-packing solver used through Spyrrow.
- [Jaguar](https://github.com/JeroenGar/jagua-rs) — geometry and collision-detection engine beneath Sparrow.
- [Shapely](https://shapely.readthedocs.io/) — pinned geometry library reserved for residual construction and invariants.

## Research datasets used

- [Lectra / University of Bordeaux nesting dataset](https://doi.org/10.5281/zenodo.7030786) —
  source-observed geometry and task composition used in the canonical Lectra lineage and the M11
  semi-synthetic evaluation; version 1.1 is licensed CC BY 4.0.
- [LOCo 2D irregular cutting-stock instances](https://www.loco.ic.unicamp.br/files/instances/2dics_cutting_stock.zip)
  — source-observed geometry and demand used in M11. No express redistribution license was found
  for the reviewed source/archive, so the existing LOCo-derived tree and history remain outside the
  public-release boundary pending permission or verified sanitization.

See [Third-party notices](../../THIRD_PARTY_NOTICES.md) for exact attribution, versions, checksums,
transformations, and redistribution limits. External datasets did enter the research benchmark and
M11 evidence lineage; generated chronology and assumed economics must not be mistaken for source
measurements.

## Deferred adapters and fixture leads

- [PackingSolver](https://github.com/fontanf/packingsolver) — potential independent fit/packing adapter; deferred until its exact role and build requirements are justified.
- [ESICUP datasets](https://github.com/ESICUP/datasets) — established cutting and packing instances.
- [OR-Datasets](https://github.com/Oscar-Oliveira/OR-Datasets) — collected operations-research datasets.
- [CG:SHOP 2024 instances](https://cgshop.ibr.cs.tu-bs.de/competition/cg-shop-2024/) — irregular packing challenge data.
- [BROMRO2](https://github.com/oberlan/bromro2) — benchmark generator and related instances.
- [Mendeley irregular strip-packing dataset](https://data.mendeley.com/datasets/ddc79swng4/1) — additional public-instance lead.

Before adding another dataset, record its upstream version, license, checksum, transformation, unit
convention, intended evidence role, and reason for inclusion.
