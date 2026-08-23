# M4 — Remnant reuse proof

**Status:** Passed — exact source-shape reuse witness published

M4 turns an exact retained remnant back into stock. A later part or batch must fit inside that irregular container and produce another exact residual while preserving provenance.

> **Question:** Can a theoretically valuable leftover actually satisfy future demand?

## Acceptance boundary

A favorable, hand-verifiable sequence creates a remnant and later consumes it to avoid opening a new sheet. This proves possibility, not frequency or real-shop recoverability.

## Frozen implementation

M4 separates collision truth from placement discovery. The authoritative backend is Shapely 2.1.2
exact polygon containment and difference: it respects concavities and holes, enforces allowed
rotations and exact material identity, supports explicit clearance, and reconciles the parent area
against the placed part, process loss, retained children, and scrap. This is the required collision
detection boundary for M4.

The separate deterministic search proposes bounding-box, vertex-alignment, and 17 by 17 grid
translations, sorts transforms by rotation and translation, and evaluates at most 4,096 candidates
per remnant/part pair. A proposed placement cannot become evidence until the exact backend validates
it. Exhaustion means only `no_witness_within_registered_search`; it is not a proof that no placement
exists.

The canonical input is `yfri-26460ffca19eebfc9e479d01`, with semantic SHA-256
`sha256:26460ffca19eebfc9e479d0182b55bce39c4c5ffec79c5c5feb82c10b7b68f7f`. It binds the
canonical M0, M2, and M3 evidence, 406 M3 primary-rule remnants, 6,607 later-part roles, the exact
fit and search configurations, and Shapely 2.1.2.

## Canonical result

The committed result is `yfrr-b8b1578fc5e0225f00c4386e`, with semantic SHA-256
`sha256:b8b1578fc5e0225f00c4386eae71a33fff4bdef642347941958dbff56b9b7901`.

| Measure | Result |
| --- | ---: |
| Registered origin remnants | 406 |
| Registered future parts | 6,607 |
| Eligible ordered pairs | 1,331,906 |
| Pairs attempted before first fit | 123 |
| Bounded no-witness pairs | 122 |
| Exact fit witnesses | 1 |
| Candidate transforms evaluated | 499,713 |
| Avoided full-sheet openings in the declared toy state | 1 |

The first witness uses:

- origin task `147`, selected-candidate position `0`, candidate
  `cand_3e5dda8564ffe5f3f591`;
- origin remnant `yfrm-4ea3541f2241e00dc6f2bdd2`, reconstructed from exact component
  `6e140ad19f12ef8fd90b4e2e530a1d9c7b76138e3e4f5109363dc6b32ad102ae`;
- future task `2531`, part `lectra:2531:part:110001`;
- generated fit placement rotation `0.0` and translation `(0.0, 0.0)`;
- one generation-2 child remnant, `yfrm-4f31d96476ee866210345b4f`; and
- zero material-reconciliation delta.

The same future part also revalidates at its recorded placement on the full source sheet. In the
declared one-order comparison, the origin remnant is already on hand and no full sheet is open, so
using the remnant avoids one full-sheet opening. No price is assigned.

## Verification

- Full Python suite: 706 passed and 3 environment-specific qualifier tests skipped. The skips are
  one Linux-only sealed-memory test and two Docker-fixture tests; none exercises M4.
- Python lint, formatting check, source distribution, and wheel build: passed.
- Frontend unit suite: 56 passed; TypeScript checking and production build also passed.
- Real-browser mutation suite: not applicable because M4 adds a solver-independent experiment CLI
  and evidence artifacts without changing browser or API behavior.

The implementation and evidence sequence is `1b8e8b2`, `a2bfc5c`, `6019d3e`, `583c852`,
`1a8e1b2`, and `1439939`.

## Decision, provenance, and next boundary

M4 passes its frozen technical gate. Exact remnant reuse is mechanically possible for one bounded
source-shape role under the modeled geometry, and consumption preserves exact recursive accounting
and content-addressed provenance.

The source supplies part shapes, sheet dimensions, and allowed orientation states through the
already acknowledged M2 projection. Candidate placements are solver-generated; remnants and child
geometry are derived. Greater task index is a generated deterministic order, not observed
chronology. The single material identity is explicitly assumed because the Lectra corpus supplies
no production material properties.

This result does not estimate reuse frequency, policy advantage, purchased-material savings,
physical recoverability, production readiness, or commercial value. M5 is next: deterministic
chronological replay must establish reproducible inventory histories and costs. A `jagua-rs`
adapter remains a potential acceleration spike for the repeated collision queries that replay may
create; it was not required for M4 correctness because exact collision detection is already present.
