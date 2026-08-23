# M5 — Deterministic replay

**Status:** Passed — canonical chronological state and cost replay published

M5 gives the experiment memory and time. Orders arrive, information is revealed, inventory changes,
remnants age, purchases and handling costs accrue, and the terminal rule closes the horizon.

> **Question:** Can we reproduce the consequences of a sequence of material decisions accurately and deterministically?

## Acceptance boundary

Identical manifests, component versions, policies, and seeds produce canonically identical event
histories and cost totals. M5 proves this replay mechanism on a hand-computable generated fixture. It
does not estimate benchmark performance, policy advantage, purchased-material savings, physical
recoverability, or commercial value.

## Frozen implementation

The pure `yieldforge.replay` state machine follows the seven M0 event stages in their registered
order. The policy sees only the released order and current inventory, evaluates compatible remnants
in sorted content-ID order, and opens a generated standard sheet when the bounded search finds no
remnant witness. A bounded miss remains inconclusive; it is not promoted to a no-fit claim.

Every selected placement is validated by the M4 Shapely 2.1.2 exact-polygon boundary. Consuming
stock subtracts the placed geometry, classifies retained versus scrap components with the M0 primary
rule, reconciles area, and preserves immutable recursive lineage. This subtraction is also the
collision boundary: already consumed material is absent from future stock geometry, so a future
placement cannot overlap it.

Costs accrue half-up to six decimal places. Storage uses the M0 half-open interval from the previous
timestamp to the current timestamp. Geometry, chronology, material, and economic provenance are
stored separately: this fixture's sheet, parts, chronology, and rates are generated; its material
identity is explicitly assumed.

The canonical input is `yfrpi-a4c97d5d026be4d28a533464`, with semantic SHA-256
`sha256:a4c97d5d026be4d28a53346401be9e1cf241198350da971722b881a542644863`. It binds
M0 `yfm0-29b7efe8ac2a0a9995c4f907`, M4 input `yfri-26460ffca19eebfc9e479d01`, M4
result `yfrr-b8b1578fc5e0225f00c4386e`, the engine and policy versions, Shapely 2.1.2,
seed `0`, exact fit/search settings, generated rates, two UTC order releases, and the explicit
terminal horizon.

## Canonical result

The committed result is `yfrpr-3e53070d65447bef0e7bcc24`, with semantic SHA-256
`sha256:3e53070d65447bef0e7bcc24d9278eada1e71fc8572e6cf82984732571bbbd56`.

| Boundary | Action | Inventory consequence | Delta net cost | Cumulative net cost |
| --- | --- | --- | ---: | ---: |
| `2026-01-01T00:00:00Z` | Open one 10 by 10 sheet for a 4 by 10 part | Return one generation-1 remnant of area 60 | 102.0 | 102.0 |
| `2026-01-01T01:00:00Z` | Retrieve that remnant for a 3 by 10 part | Return one generation-2 remnant of area 30 | 5.6 | 107.6 |
| `2026-01-01T02:00:00Z` | Close and liquidate terminal inventory | Credit the remaining area 30 | -2.7 | 104.9 |

The first delta is purchase `100` plus return handling `2`. The second is storage `0.6`, retrieval
handling `3`, and return handling `2`. Terminal closure adds storage `0.3` and subtracts scrap-only
credit `3`. All two orders are fulfilled, one sheet is opened, one remnant is retrieved, and the
technical decision is `pass`.

The canonical JSON file hashes are
`9e2b1de64cb91551d38e76dbd6aaf29c89ba55be255131432a35e7f477a82e38` for the input and
`a7321db21536f521c6e4a44d3811ec331c8036638a3c2aee48b6b05a52871068` for the result.
Independent loading reconstructs M4 exact evidence, replays every M5 transition, and requires model
equality. One hundred repeated M5 executions were identical and averaged approximately `4.08 ms`
per two-order replay on the development machine.

## Verification

- Full Python suite: 727 passed and 3 environment-specific qualifier tests skipped in 16 minutes
  10 seconds. The skips are the existing Linux sealed-memory test and two Docker-fixture tests.
- Post-review replay and canonical-artifact suite: 20 passed after adding adversarial order-count and
  cumulative-ledger reidentification checks.
- Python lint and formatting: all checks passed across 83 files.
- Source distribution and wheel builds: passed.
- Frontend regression suite: 56 passed; TypeScript checking and the production build passed.
- Real-browser mutation suite: not applicable because M5 adds a solver-independent experiment CLI,
  replay kernel, and evidence artifacts without changing browser or API behavior.

The implementation and evidence sequence is `1a63f28`, `1d50fdf`, `d441298`, `b8d6685`,
`73ae6e3`, `d576c01`, and `abec6b1`.

## Decision and next boundary

M5 passes its frozen technical gate. Deterministic inventory, lineage, chronology, and net-cost
mechanics now have immutable replay evidence. The result remains a mechanism proof on generated
chronology and economics, not a savings result.

M6 is next. Its preparation inventory is in
`Docs/plans/2026-08-23-m6-temporal-benchmark-preparation.md`. The existing order-book subsystem is a
useful schema prototype, but its three tiny fixtures are not yet the registered M6 benchmark. A
second collision runtime remains deferred until a representative M6 pilot shows that exact geometry
queries are a material runtime bottleneck.
