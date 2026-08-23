# M3 — Residual geometry truth

**Status:** Passed — exact residual accounting and pair comparison completed

M3 determines what material actually remains after placed parts, clearance, kerf, and forbidden regions are removed. It extracts connected residual polygons, separates retained remnants from scrap, and accounts for every unit of modeled material.

> **Question:** Do candidate nests create genuinely different, correctly accounted-for remnants?

## Acceptance boundary

Adversarial fixtures satisfy the material-reconciliation invariant, invalid geometry fails loudly, and M2 candidates show exact residual differences rather than merely visual placement differences.

## Frozen implementation

M3 used a solver-independent Shapely 2.1.2 overlay boundary. The source-faithful empirical arm
modeled the complete fixed sheet, the archived placed polygons, zero part buffer, and no forbidden
regions because the Lectra source and M2 confirmation do not provide physical kerf or clearance.
Explicit nonnegative buffers and forbidden polygons remain implemented and covered by adversarial
tests, but they are not silently promoted to source evidence.

The input pack selected the first two canonically distinct ordinary candidates after sorting by
`(used width, candidate ID)` inside M2's frozen 0.5% envelope. Selection occurred before residual
geometry was calculated. The pack contains 203 task pairs and 406 candidates:

- input ID: `yfgi-2fe5b848ea643d282c284f90`;
- input SHA-256: `sha256:2fe5b848ea643d282c284f90fc645ecc8d00a8467e6a7f53fed506cb9fa0eaa0`;
- bound M2 result: `yfgfr-47d42952e0003154baceee02`; and
- bound M0 contract: `yfm0-29b7efe8ac2a0a9995c4f907`.

## Canonical result

The committed result is `yfgr-0ac2c37f0938d9d399e7a076`, with semantic SHA-256
`sha256:0ac2c37f0938d9d399e7a076238bd574ed6d24ec7db3d8ea4e3af7b37165412d`.

| Measure | Result |
| --- | ---: |
| Registered/evaluated/valid pairs | 203 / 203 / 203 |
| Failures | 0 |
| Maximum reconciliation delta | 0.0 |
| Pairs with different exact residual geometry | 202 / 203 (99.5074%) |
| Mean symmetric difference / sheet area | 0.8377% |
| Median symmetric difference / sheet area | 0.1343% |
| P95 symmetric difference / sheet area | 3.5361% |
| Maximum symmetric difference / sheet area | 22.1654% |
| Permissive classification differences | 0 / 203 |
| Primary classification differences | 0 / 203 |
| Conservative classification differences | 0 / 203 |

Task `62660` was the single exact-equal pair: candidates `cand_f4842a4f835ba2b973ce` and
`cand_cad973d3e4418d4ffc6a` had zero symmetric difference.

## Decision and interpretation

M3 passes its frozen technical gate: adversarial behavior is covered, all registered pairs were
evaluated from verified evidence, every observation reconciled within tolerance, and at least one
pair differed exactly.

The finding is narrower than a reuse result. Exact residual shape diversity is widespread, but the
current component-level eligibility rules did not distinguish any selected pair. M4 must therefore
test exact future-shape fit directly rather than treating eligibility labels as a proxy for reuse.
M3 does not establish future usefulness, avoided sheet purchases, savings, solver optimality,
physical recoverability, or commercial value.
