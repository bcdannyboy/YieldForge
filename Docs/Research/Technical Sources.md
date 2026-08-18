# Technical sources

This note records upstream software and public-data leads so implementation evidence is not scattered across chats.

## Installed foundation

- [Spyrrow](https://github.com/PaulDL-RS/spyrrow) — Python bindings and progress reporting for Sparrow; pinned to 0.9.0.
- [Sparrow](https://github.com/JeroenGar/sparrow) — irregular strip-packing solver used through Spyrrow.
- [Jaguar](https://github.com/JeroenGar/jagua-rs) — geometry and collision-detection engine beneath Sparrow.
- [Shapely](https://shapely.readthedocs.io/) — pinned geometry library reserved for residual construction and invariants.

## Candidate next adapters and fixtures

- [PackingSolver](https://github.com/fontanf/packingsolver) — potential independent fit/packing adapter; deferred until its exact role and build requirements are justified.
- [ESICUP datasets](https://github.com/ESICUP/datasets) — established cutting and packing instances.
- [OR-Datasets](https://github.com/Oscar-Oliveira/OR-Datasets) — collected operations-research datasets.
- [CG:SHOP 2024 instances](https://cgshop.ibr.cs.tu-bs.de/competition/cg-shop-2024/) — irregular packing challenge data.
- [BROMRO2](https://github.com/oberlan/bromro2) — benchmark generator and related instances.
- [Mendeley irregular strip-packing dataset](https://data.mendeley.com/datasets/ddc79swng4/1) — additional public-instance lead.

No external dataset has been promoted into the canonical benchmark suite yet. Before committing one, record its upstream version, license, checksum, transformation, unit convention, and reason for inclusion.
