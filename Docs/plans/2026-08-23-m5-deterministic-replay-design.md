# M5 Deterministic Replay Design

**Date:** 2026-08-23
**Status:** Approved for implementation
**Milestone:** M5 — Deterministic replay

## Decision

M5 will build a pure, event-sourced replay kernel around a deliberately hand-computable two-order
fixture. The kernel will implement the frozen M0 stage order, exact M4 geometry and recursive
remnant consumption, inventory aging, purchase and handling accrual, storage, scrap proceeds, and
terminal liquidation. Replaying the same content-addressed input under the same engine, geometry,
policy, and seed identities must produce canonically identical event history and cost totals.

The fixture uses generated 10 by 10 stock and two rectangular generated parts under one explicitly
assumed material identity. The first order opens one sheet and leaves a 6 by 10 retained remnant.
One hour later, the bounded as-of-time policy consumes that remnant for a 3 by 10 part and returns a
3 by 10 child. The horizon closes one hour later. This is a simulator-mechanics proof, not a demand,
frequency, policy-quality, or savings experiment.

## Approaches considered

1. **Pure replay kernel plus hand-computable fixture — selected.** This isolates the M5 question,
   makes every geometry and cost transition independently checkable, and creates the stable state
   machine M6–M8 need without pulling future-policy work forward.
2. **Replay the existing generated Lectra order books immediately.** This would exercise more
   events, but it would require candidate generation, batching, standard-sheet actions, material
   rate mapping, and policy choices that belong to M6 and M7. A failure would not identify whether
   the replay kernel or benchmark construction was wrong.
3. **Wrap only the canonical M4 witness in timestamps.** This would prove deterministic
   serialization, but it would omit the full-sheet purchase path, inventory aging, storage,
   repeated lineage, and terminal accounting that distinguish M5 from M4.

## Scope and claim ceiling

M5 includes:

- strict content-addressed replay input and result contracts;
- a versioned deterministic policy with seed `0` and no future-event argument;
- exactly one released part per timestamp in the canonical fixture;
- the frozen M0 event order:
  `accrue_storage_to_t`, `reveal_orders_released_at_t`,
  `form_compatible_released_batch`, `select_action`,
  `execute_and_fulfill_atomically`, `record_purchase_handling_and_scrap`, and
  `return_eligible_remnants_at_t`;
- inventory records with exact remnant geometry, material, lineage, and entry time;
- bounded first-fit remnant search in sorted remnant-ID order;
- deterministic full-sheet fallback when no bounded remnant witness is found;
- exact part placement, subtraction, primary-rule classification, and recursive remnant creation;
- half-open storage intervals, purchase cost, return/retrieval handling, scrap proceeds, and
  scrap-only terminal liquidation;
- immutable publication plus independent replay on load; and
- a canonical two-event M5 input/result pair bound to M0 and canonical M4 evidence.

M5 does not support multi-part batching, generate solver candidate sets, estimate reuse frequency,
compare policies, use future information, construct M6 benchmark populations, calculate oracle
savings, or establish physical or commercial value.

## Architecture

Add `yieldforge.replay` beside `yieldforge.reuse`:

- `replay.contracts` owns engine identity, rate manifest, orders, policy, input, event records,
  inventory snapshots, cost ledgers, summaries, and results;
- `replay.engine` owns the pure chronological state transition and exact geometry calls; and
- `experiments.deterministic_replay` builds the canonical fixture, binds M0/M4 evidence, publishes
  immutable artifacts, and independently recomputes loaded results.

The CLI will expose `experiments prepare-deterministic-replay` and
`experiments evaluate-deterministic-replay`. The browser/API workbench remains unchanged.

## Input and evidence binding

The M5 input will bind:

- M0 contract ID and semantic SHA-256;
- M4 input/result IDs and semantic SHA-256 values;
- exact Shapely version;
- replay engine and policy versions;
- policy seed `0`;
- the M4 fit and bounded-search configurations;
- one generated standard-sheet specification;
- one assumed material identity;
- a generated numeric rate manifest with explicit units and six-decimal rounding; and
- two strictly increasing generated order releases plus an explicit horizon end.

Preparation must load and independently validate the canonical M4 result before publishing M5
input. The M5 fixture does not reinterpret the canonical M4 witness as observed chronology; M4 is a
validated mechanism dependency, while M5 chronology, rates, material, stock, and parts are generated
or assumed and labeled accordingly.

## Policy and information boundary

The registered policy is `first_fit_remnant_then_standard_sheet.v1`. At each event it receives only
the currently released order, current inventory, fit configuration, and bounded search
configuration. It iterates compatible inventory in remnant-ID order and selects the first exact
witness. If bounded search returns no witness for every compatible item, it opens the declared
standard sheet and uses the same exact geometry boundary.

The policy function cannot receive the replay manifest or future orders. A bounded miss is an
operational policy choice, not proof that no geometric fit exists. M7 will later supply strong
baseline policies; M8 will supply future-aware policies.

## Full-sheet and remnant execution

A full sheet is a transient exact stock polygon. It is purchased when opened, validated against the
part placement, and subtracted under the same M0 primary remnant rule. Every retained residual is
re-rooted as a generation-1 `RemnantStock` whose identity binds the sheet-opening action and exact
component geometry. Scrap residual area accrues scrap proceeds; process loss affects geometry but
has no separate money term.

A remnant action removes exactly one inventory ID, accrues retrieval handling once, consumes it
through `reuse.geometry.consume_remnant`, and returns its primary-rule children with generation and
ancestry extended exactly once. Duplicate inventory IDs, missing selected stock, incompatible
material, invalid geometry, or reconciliation failure abort the replay without a partial result.

## Cost accounting

The generated rate manifest uses `generated_cost_unit`, hours, and area units:

- purchase cost = opened sheet area times purchase cost per area;
- storage cost = retained inventory area times elapsed hours times storage rate;
- return handling = returned eligible-remnant count times return rate;
- retrieval handling = consumed remnant count times retrieval rate;
- scrap proceeds = actually scrapped residual area times scrap-credit rate; and
- terminal scrap credit = remaining retained area times scrap-credit rate at horizon close.

Every monetary component is rounded half-up to six decimal places at accrual. Net cost is purchases
plus storage plus return and retrieval handling, minus realized scrap proceeds and terminal scrap
credit. The event record carries both its delta and the cumulative ledger; the terminal record
closes the horizon after the final action.

For the canonical fixture, the hand calculation is:

- event 0: purchase `100` plus one return handling charge `2` = `102`;
- event 1: storage `0.6`, retrieval `3`, and one return charge `2` = `5.6`;
- horizon close: storage `0.3` and terminal scrap credit `3`; and
- final net cost: `104.9` generated cost units.

## Result and decision

The result persists every event, selected action, placement, geometry accounting, returned inventory
IDs, storage interval, cost delta, cumulative ledger, terminal liquidation, and summary counts. Its
loader must rerun the engine from the bound input and require exact result equality.

M5 passes only when:

1. adversarial event-order, information-boundary, inventory, lineage, geometry, cost, and tamper
   tests pass;
2. the hand-computable totals and inventory chain match exactly;
3. two independent evaluations of the same input are canonically identical;
4. the committed result independently replays on load; and
5. full repository verification passes.

A pass proves deterministic replay mechanics for the bounded fixture. It does not validate an M6
benchmark, a strong baseline, a future-aware oracle, savings, or commercial value.

## Collision-backend decision

Shapely remains the exact collision and subtraction authority. M5 will measure the canonical replay
and a repeated-query diagnostic, but it will not add `jagua-rs` unless collision discovery is a
measured bottleneck under an M6-sized workload. Because M6 stream cardinality is not frozen yet,
the M5 closeout may record “not currently warranted” without claiming future scalability.

## M6 preparation boundary

After M5 passes, create an M6 preparation note that inventories the three existing generated
order-book fixtures, their observed/generated/assumed provenance, required rate and stock manifests,
candidate-archive dependencies, stream split and seed decisions, second-corpus requirement, and
the explicit gates that must be frozen before temporal benchmark generation. Existing order books
remain schema prototypes, not confirmatory benchmark evidence.
