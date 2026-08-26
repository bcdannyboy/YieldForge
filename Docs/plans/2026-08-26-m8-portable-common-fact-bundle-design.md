# M8 Portable Common-Fact Bundle Design

**Status:** Approved 2026-08-26

## Purpose

Remove duplicated common-transition derivation between the M8 generator and fresh checker without
turning a generator hash into authority. Preserve the immutable M7 baseline, exact action/accounting
semantics, independent checking, calibration-only scope, and the sealed evaluation boundary.

This design amends the broader approved fact-DAG design in
`2026-08-25-m8-factored-certificate-redesign-design.md`. Gate 3 uses a fixed-layer portable bundle
instead of a general graph and permits explicit, measured exact-checker fallback for unsupported
survivor nodes.

## Decision

Build a strict `M8FactBundleV2` with fixed dependency layers:

1. candidate/frontier commitments already bound to frozen verified candidates;
2. one portable common-transition lemma per common event;
3. branch-influence facts bound to one common lemma and exact branch state commitments; and
4. action roots referencing the ordered common/influence facts.

The generator returns an explicitly unchecked bundle. A fresh checker process strict-loads and
validates the bundle before any result can become accepted evidence. Existing v1 generator/checker
APIs remain unchanged and authoritative for differential testing.

## Why not the alternatives

- Passing only `M8CommonTransitionFact` either forces the checker to rederive the expensive fact or
  asks it to trust the generator. It does not solve the runtime/authority problem.
- A general Merkle DAG adds arbitrary edges, cycle detection, unused-node handling, and a much larger
  checker attack surface before those capabilities are needed.
- A proof trie or transition automaton could compress long horizons further, but state merging would
  become a new correctness proof. It is deferred unless fixed-layer traversal dominates profiling.

## Contracts

### Common-transition lemma

One lemma binds:

- schema and hash-domain version;
- replay-input and semantic-runtime identities;
- event position and exact cursor-before commitment;
- previous lemma hash, or the baseline fallback cursor hash for the first event;
- the existing portable `M8CommonTransitionFact` payload;
- event problem and candidate-set identities; and
- one evidence mode:
  - `frontier_no_fit`;
  - `counted_no_fit`; or
  - `exact_replay`.

`counted_no_fit` persists the scalar no-fit bindings and complete registered translation batches
needed to audit generated, duplicate, evaluated, and truncation counts. Jagua collision masks are
never serialized or used as no-fit authority.

### Branch-influence fact

One influence fact binds a common lemma, exact branch state before/after, inventory delta, action
bindings, classification, and the existing rejection/search evidence. Its modes are:

- `scalar_no_fit`;
- `policy_dominated_exact_check`;
- `exact_transition`; and
- `state_rejoin`.

Policy-dominated and exact-transition modes may initially invoke explicit exact-checker fallback.
Fallback count and wall time are mandatory evidence; hidden fallback is invalid.

### Action root

An action root retains the v1 runtime, suffix, initial action, baseline action, start state, final
state, and final cost commitments. It references ordered common/influence fact hashes instead of
copying shared common evidence. The bundle root commits the ordered fact hashes and ordered action
roots, preventing proof/fact mixing without modifying frozen v1 proofs.

### Content addressing

Every node uses a full SHA-256 reference over a domain-separated canonical payload:

`sha256("yieldforge.m8.fact.v2\0" + fact_kind + "\0" + canonical_payload)`

Semantic hashes exclude timings, paths, timestamps, process IDs, and insertion order. Counts are
strict integers. Floating-point values used as fact evidence are encoded canonically by exact f64
bits rather than presentation-dependent decimal text.

## Generator boundary

The new builder captures common facts and their evidence before process-local capabilities are
released. It may use the frozen Jagua binary to propose translation batches, but its result remains
typed as unchecked. For the portable pipeline, the generator does not need to repeat the same
independent source-sequence audit that the fresh checker will perform.

The existing `score_sparse_event()` and `check_action_proofs()` behavior remains unchanged. New
bundle APIs are explicit so no caller can mistake unchecked generator output for accepted proof.

## Independent checker

A fresh checker process:

1. strict-loads canonical bytes and validates all hashes, ordering, reachability, and context
   bindings;
2. independently reconstructs the current-event catalog once;
3. verifies candidate/frontier membership from frozen runtime evidence;
4. independently verifies scalar no-fit implications;
5. audits supplied counted-no-fit translation sequences and counts without using Jagua collision
   classifications;
6. invokes exact M7 replay only for an explicit fallback node;
7. algebraically reconciles policy rank, selected action, event, costs, lineage, and cursors;
8. creates checker-owned process-local common capabilities;
9. traverses every action root and reconciles terminal state/cost; and
10. releases every capability before exit.

For fully fact-certified nodes, the checker must not call the generator, `certify_event_passivity`,
or `_derive_m8_common_transition_fact*`. Independence tests patch these entry points to fail.

## Concurrency budget

The old eight-worker claim counted only top-level processes. Counted-no-fit auditing can otherwise
turn six cell workers into as many as 30 active descendants.

Freeze an internal compute budget with no CLI override:

- total active compute slots: `8`;
- nested-capable cell-phase workers: `4`;
- translation-audit workers per cell: `2`;
- reference-phase workers: `6`; and
- isolated Gate-2/Gate-3 profile: `1` outer worker with `4` audit workers.

The distributed runner must reject a budget whose cell workers multiplied by audit workers exceeds
the total slots. It records the outer and nested counts separately. A process-scoped execution
context supplies the audit width; the audit function has no silent four-worker default.

## Error handling

The bundle fails closed on:

- unknown schema, fact kind, or evidence mode;
- dangling, duplicate, unused, out-of-order, cross-runtime, or cross-stream references;
- noncanonical numeric encoding;
- altered scalar, count, translation, rank, cursor, event, action, state, or cost evidence;
- implicit exact replay;
- evaluation bindings or an opened-evaluation marker;
- worker-budget violations; or
- surviving child processes/capabilities after success or failure.

Recomputing enclosing hashes must not make a semantic mutation valid. Checker failures report a
stable code and first failing fact ID.

## Gate 3 success criteria

Gate 3 is a proof-kernel and bounded performance checkpoint, not the official M8 decision.

- Frozen v1/v3 artifacts strict-load unchanged.
- The `no_signal` and `regime_shift` probes emit one common lemma per common event and 428/459 action
  roots respectively.
- All 887 action roots validate in fresh checker processes.
- V1/v2 normalized final costs, final states, event positions, classifications, action bindings,
  state commitments, influences, and policy keys match exactly.
- The frozen 12-action audit has zero v1/v2/reference mismatches.
- Count audit executes once per unique counted-no-fit lemma.
- The two real Gate-2 probes use zero exact-checker fallback.
- All adversarial mutations fail with the intended checker code, including mutations with recomputed
  enclosing hashes.
- Two fresh spawned generations produce byte-identical semantic bundles and root hashes.
- Peak active computation stays within eight slots and all children/capabilities are reaped.
- Evaluation access remains false.

Cold transport, serialization, checker, fallback, and total wall time are reported. The next speed
decision remains the abbreviated `25x`/five-day gate; the official `20x`/seven-day and witness
coverage requirements remain unchanged.

## Non-goals

Gate 3 does not open evaluation, change M0/M6/M7 semantics, alter candidate parity, accept Jagua
collision classifications as authority, prove M8 advantage or savings, or establish physical,
production, buyer, or commercial validity.
