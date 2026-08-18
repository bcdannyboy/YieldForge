# YieldForge Repository Obsidian Vault Design

**Status:** Approved
**Date:** 2026-08-17
**Decision owner:** Daniel Bloom
**Design scope:** Convert the YieldForge proposal into a repository-native Obsidian vault that becomes the primary product, research, developer, decision, evidence, and work-tracking documentation surface.
**Repository base reviewed:** c9db488
**Source proposal:** YieldForge_Proposal_and_Perfect_Information_MVP.docx

## Decision

Create one Obsidian vault at docs/. Use a typed atomic graph rather than a chapter-for-chapter proposal conversion. Keep the graph navigable through four root orientation notes, stable record IDs, native Obsidian Properties and Bases, type-specific templates, and explicit authority boundaries.

The vault will use core Obsidian features only. Its data remains standard Markdown and YAML so GitHub, code editors, command-line tools, and future automation can read it without community plugins.

The vault governs:

- product and research intent;
- the accepted experiment contract;
- accepted architecture decisions;
- current phase, milestone, and bounded work packets;
- narrative summaries of verification and results; and
- source and decision provenance.

The vault does not, by itself, prove implementation or experimental claims. Committed code and tests govern what exists. Immutable result manifests and verification reports govern measured results. Historical manufacturer replay, shop shadowing, and production measurement remain separate evidence tiers.

## Verdict

**Approve with conditions.**

The proposal is strong enough to authorize a bounded documentation and Phase A planning program. It is not yet an implementation specification. The vault must make unresolved technical assumptions, experiment-contract decisions, evidence ceilings, and commercial gates visible instead of silently converting proposal language into current truth.

## Evidence Reviewed

### Repository

- README.md at commit c9db488.
- Untracked YieldForge_Proposal_and_Perfect_Information_MVP.docx.
- Git status and recent history.
- Absence of an existing docs tree, vault, code structure, or documentation convention.

### Proposal

- Complete 40-page proposal text and all 21 references.
- Rendered 41-page DOCX output, including portrait and landscape pages.
- Executive authorization, product thesis, market analysis, end-state design, hypotheses, information sets, MVP architecture, M0-M10 gates, benchmark program, go/no-go thresholds, risks, schemas, tests, parameter grid, glossary, and references.

### Obsidian and precedent

- Obsidian 1.13.4 vault picker and current local vault registrations.
- Existing Guideflare notebook and docs-vault structures.
- Guideflare notebook/governance design and ADR-0005.
- Native Obsidian Properties, Templates, and Bases behavior.

### Primary-source spot checks

- Sparrow repository capabilities, seeded CLI, output formats, tests, and license.
- jagua-rs collision engine, irregular-container, hole, zone, rotation, and hazard support.
- Official AccuFit, Lantek, Plataine, SigmaNEST, and ProNest product materials.
- Forward-looking usable-leftover research and ESICUP dataset availability.

### Missing or intentionally deferred evidence

- No local Sparrow or jagua-rs integration spike has been run.
- No proof exists that Sparrow can expose the required population of feasible, near-tied progressive incumbents within the intended budget.
- No remnant Boolean extraction or canonicalization implementation has been selected or validated.
- No synthetic temporal generator exists.
- No historical manufacturer stream or real-shop remnant data has been obtained.
- No CAM integration or customer discovery has been performed.
- No current code, tests, benchmark manifests, or runtime evidence exists in this repository.

## Proposal Analysis

### What the proposal gets right

The proposal is unusually disciplined about falsification:

- It tests perfect information before investing in forecasting.
- It compares against a strong as-of-time baseline rather than a discard-all-remnants strawman.
- It shares geometry, candidate sets, stock eligibility, and benchmark streams between policies.
- It separates the information gap, search gap, recoverability gap, long-horizon gap, baseline gap, and terminal-value sensitivity.
- It uses no-signal controls, paired streams, calibration/test separation, exact small cases, and conservative ending-inventory treatment.
- It defines explicit non-goals and automatic stop conditions.
- It treats a negative result as a successful prevention of a speculative product build.
- It acknowledges that a positive synthetic result authorizes real-history acquisition, not a commercial product.

These features should become durable contract, metric, control, and decision records rather than remaining prose inside one proposal.

### Why the proposal cannot become the living notebook unchanged

The document interleaves at least six authority classes:

1. product and commercial hypotheses;
2. market and technical source claims;
3. the proposed experiment contract;
4. implementation hypotheses and candidate technology;
5. current and future work;
6. evidence and decision thresholds.

A chapter-for-chapter conversion would preserve ambiguity about what is proposed, accepted, implemented, measured, and earned. It would also preserve the proposal's monolithic maintenance cost. The rendered document already contains large continuation tables and several mostly empty pages caused by pagination. Markdown should remove that packaging without flattening the underlying relationships.

### Technical assumptions that remain spikes

#### Sparrow candidate capture

Sparrow provides seeded runs, progressive optimization behavior, final JSON/SVG solutions, and intermediate outputs. The proposal assumes the adapter can persist multiple feasible near-tied incumbents with materially different residual topology. That is plausible but unproven. Intermediate output may include infeasible states, and a multi-seed archive may still collapse to similar residual shapes.

The vault must represent this as a Phase A spike and M2 acceptance condition, not as an established capability.

#### Remnant extraction

jagua-rs supports fast collision queries, irregular containers, holes, inferior-quality zones, rotations, separation, and custom hazards. Those capabilities support later remnant-as-stock fit. They do not by themselves establish exact polygon Boolean subtraction, connected-component extraction, canonicalization, material reconciliation, or conservative operational recoverability.

The remnant geometry component therefore needs its own architecture record, interface, spike, invariants, and acceptance evidence.

#### Candidate and compute parity

The primary comparison requires the baseline and oracle to receive the same feasible action archive. The contract must define:

- stock enumeration;
- candidate-generation time or iteration budget;
- seed set;
- progressive incumbent capture;
- feasibility filtering;
- epsilon envelopes;
- candidate deduplication;
- cache identity;
- remnant-as-stock candidate generation; and
- failure/timeout treatment.

Without this, an apparent information advantage may actually be an action-set or compute-budget difference.

#### Metric and time semantics

Before M0 passes, the experiment must freeze:

- the exact unknown-future component definition;
- purchase, storage, handling, process-loss, scrap, and terminal terms;
- event time and storage accrual;
- the meaning of immediate utilization sacrifice;
- multi-sheet batch decomposition;
- ending-inventory liquidation;
- the horizon boundary;
- known-order visibility; and
- how invalid actions, timeouts, and missing candidates are scored.

The proposal gives the intended direction but not an executable metric dictionary.

#### Synthetic-to-real claim ceiling

Controlled recurrence is useful for testing the mechanism. It can also make the mechanism artificially easy to discover. Generator records must expose recurrence, family variation, bundles, regime shifts, and negative controls. The final virtual claim must remain conditional on the modeled regimes.

Even a green oracle result cannot establish operator adoption, physical recoverability, CAM interoperability, customer demand, implementation ROI, or a standalone business.

### Commercial and scope risks

- The broad market category is already occupied by mature CAM, inventory, planning, and vertical optimization systems.
- The differentiated wedge is narrower than "future-aware nesting" or "remnant optimization."
- An overlay strategy reduces replacement risk but makes candidate export, APIs, data quality, and proof attribution first-class constraints.
- The end-state architecture can cause scope leakage into forecasting, scheduling, purchasing, CAM, multi-machine dispatch, and material-quality fields.
- The provisional YieldForge name has collision risk and must remain internal.

The vault should keep End-State Reference records separate from the active MVP roadmap.

## Alternatives Considered

### Proposal mirror

Convert each numbered proposal section into one Markdown note.

**Upside:** Fast migration and direct traceability to the original document.

**Downside:** Preserves mixed authority, large notes, duplicated status, and weak claim/evidence boundaries.

**Decision:** Rejected.

### Lifecycle-and-authority vault

Use a small set of current narrative notes plus folders for decisions, experiments, milestones, plans, verification, sources, and archive.

**Upside:** Low orientation cost and moderate maintenance.

**Downside:** Important hypotheses, metrics, risks, entities, and sources remain embedded in larger notes.

**Decision:** Not selected.

### Typed atomic graph

Give decision-relevant concepts stable identities and relationships while using root orientation notes and native Bases to make the graph usable.

**Upside:** Precise provenance, queryable status, durable links, focused diffs, explicit evidence coverage, and good support for a research-heavy program.

**Downside:** Highest clerical and validation burden; easy to over-fragment without templates and indexes.

**Decision:** Selected by the principal.

## Vault Architecture

    docs/
      Home.md
      Current Truth.md
      Current Work.md
      Work Log.md

      Product/
        Product Index.md
        Thesis.md
        Target Customer.md
        Differentiated Wedge.md
        Claim Ceiling.md
        End-State Reference.md

      Research/
        Research Index.md
        Experiment Charter.md
        Hypotheses/
        Metrics/
        Information Sets/
        Cost Model/
        Controls/
        Demand Regimes/

      Architecture/
        Architecture Index.md
        Boundaries/
        Components/
        Domain Entities/
        Invariants/
        Interfaces/

      Delivery/
        Delivery Index.md
        Phases/
        Milestones/
        Work Packets/

      Evidence/
        Evidence Index.md
        Claims/
        Verification/
        Results/

      Decisions/
        Decision Index.md
        ADRs/
        Investment Decisions/

      Risks/
        Risk Index.md

      Sources/
        Source Index.md

      Archive/
        Proposal v1/
        Superseded Notes/

      _bases/
      _templates/
      _attachments/
      plans/
      .obsidian/

The repository README remains outside the vault and acts as the GitHub/repository gateway into docs/Home.md.
The lowercase plans/ directory holds long-form approved designs and implementation plans; atomic Delivery records link to them rather than duplicating them.

## Root Orientation Surface

### Home

The human and agent entrypoint. It states the project in one paragraph and links to:

- Current Truth;
- Current Work;
- Product Index;
- Research Index;
- Architecture Index;
- Delivery Index;
- Evidence Index;
- Decision Index;
- Risk Index; and
- Source Index.

It embeds or links native Bases for the current milestone, open work, blocking risks, pending decisions, and latest verification.

### Current Truth

A short, manually curated summary of what the repository actually supports. It records a verified commit and separates:

- what exists;
- what has been specified but not implemented;
- what has been virtually verified;
- what is not known;
- current claim ceilings; and
- the next evidence gate.

Current Truth is derived orientation, not primary evidence.

### Current Work

Identifies exactly one active phase and milestone. It shows active bounded work packets, blockers, pending decisions, and the smallest next step. It may contain multiple active work packets within the one milestone, but no second active milestone.

### Work Log

An append-only, concise handoff stream. Each entry records date, outcome, verification, decision changes, relevant commit or manifest, and next step. It is not a substitute for Git history or evidence reports.

## Shared Property Schema

Every atomic note uses a stable subset of:

    id
    type
    state
    authority
    phase
    milestone
    owner
    created
    updated
    depends_on
    related
    tags

Rules:

- IDs are immutable and unique.
- Property types remain consistent across the vault.
- Links stored in properties use quoted Obsidian link syntax.
- No nested property objects are required.
- Empty type-specific properties are omitted rather than filled with placeholders.
- State and authority vocabularies are closed and validated.

### State vocabulary

- draft
- proposed
- accepted
- active
- blocked
- complete
- rejected
- superseded
- archived

Not every type may use every state. Type-specific templates and validation rules define valid transitions.

### Authority vocabulary

- narrative
- working-status
- experiment-contract
- accepted-decision
- architecture-decision
- implementation-reference
- verification
- result
- source
- archive

## Atomic Record Types

### Product records

- product thesis;
- target customer;
- differentiated wedge;
- product claim ceiling;
- end-state reference;
- commercial gate; and
- non-goal.

### Research records

- experiment charter;
- hypothesis;
- metric;
- information set;
- baseline;
- oracle;
- control;
- cost term;
- demand regime;
- benchmark parameter; and
- ablation.

### Architecture records

- boundary;
- component;
- domain entity;
- invariant;
- interface;
- data contract;
- technology decision; and
- spike.

### Delivery records

- phase;
- milestone;
- work packet;
- implementation plan; and
- handoff.

### Evidence records

- claim;
- verification report;
- result;
- benchmark run;
- manifest;
- negative result; and
- incident or invalidation.

### Governance records

- ADR;
- investment decision;
- risk;
- source; and
- archive map.

## Type-Specific Properties

### Claim

    claim_level
    evidence_tier
    claim_ceiling
    supported_by
    challenged_by
    verification_status

### Hypothesis

    hypothesis_id
    falsifiable_statement
    decision_role
    tested_by
    result

### Metric

    definition_status
    unit
    direction
    aggregation
    decision_role
    computed_from

The complete formula and edge cases belong in the body, not a property string.

### Milestone

    phase
    acceptance_gate
    evidence
    started
    completed

### Work packet

    milestone
    outcome
    scope_owner
    verification
    blocked_by

### Risk

    severity
    likelihood
    horizon
    mitigation
    trigger
    owner

### Source

    source_kind
    publisher
    url
    accessed
    freshness
    supports
    source_hash

### Verification or result

    verifies
    subject_commit
    manifest_hash
    command
    outcome
    evidence_tier

Commands, logs, and findings belong in the body or immutable linked artifacts, not long properties.

## Native Bases

Create core Bases for:

- active work;
- phase and milestone status;
- blocked work;
- open and triggered risks;
- claims by level and evidence tier;
- claims missing support;
- hypotheses and results;
- metrics missing frozen definitions;
- unresolved decisions;
- source freshness;
- recent verification; and
- orphaned or unclassified notes.

Bases are views over Markdown properties, not a second data store. Base files contain no unique project facts.

## Templates

Provide core Templates for:

- claim;
- hypothesis;
- metric;
- information set;
- control;
- component;
- domain entity;
- invariant;
- milestone;
- work packet;
- ADR;
- investment decision;
- risk;
- source;
- verification report; and
- result.

Every template includes:

- the minimum properties for its type;
- a one-sentence statement;
- context;
- explicit nonclaims or scope;
- acceptance or verification;
- relationships;
- history; and
- source links.

Templates must not create empty administrative sections that users are expected to maintain without decision value.

## Authority and Evidence Model

### Authority flow

    Source or proposal
      -> claim or hypothesis
      -> accepted experiment contract
      -> milestone and bounded work packet
      -> code, tests, and manifests
      -> verification or result
      -> Current Truth
      -> continue, redesign, or stop decision

Material changes flow back through an ADR or experiment-contract revision. Passed gates and prior results are superseded, not silently rewritten.

### Claim ladder

1. proposed
2. specified
3. implemented
4. virtually-verified
5. historical-replay
6. shop-shadow
7. production-measured

A claim cannot exceed its linked evidence tier. A positive Phase D synthetic result remains virtually verified under modeled conditions.

### Evidence ceilings

The perfect-information MVP can support:

- exact geometry and inventory accounting claims within the implemented model;
- deterministic replay claims;
- candidate and search-quality findings;
- conditional net material savings within declared benchmark regimes; and
- a decision to stop, seek real history, or begin stochastic valuation research.

It cannot support:

- real physical recoverability;
- operator adoption;
- CAM integration reliability;
- thermal or process validity;
- realized customer ROI;
- market demand;
- production readiness; or
- a broad superiority claim over commercial systems.

## Work-Tracking Workflow

1. Select one active phase and milestone.
2. Open one or more bounded work packets within that milestone.
3. Give each packet an observable outcome, owned files, dependencies, tests, non-goals, acceptance evidence, and escalation condition.
4. Implement and verify outside the notebook.
5. Create a verification or result note with paths, commands, manifest hashes, findings, and limitations.
6. Complete or reject the packet.
7. Update Current Truth only when supported facts change.
8. Update Current Work and append Work Log at every stable handoff.
9. Use an ADR or experiment-contract revision for material changes.
10. Pass a milestone only when its linked acceptance evidence is complete.

Task checkboxes may appear inside a work packet. Individual two-minute tasks do not each become atomic graph records.

## Phase and Milestone Mapping

### Phase A: Foundation

- M0 Experiment specification and invariants.
- M1 Canonical schemas and geometry round-trip.
- M2 Sparrow candidate harness and archive.

### Phase B: Physical core

- M3 Remnant extraction and accounting.
- M4 Irregular remnant reuse and fit oracle.
- M5 Deterministic chronological simulator.

### Phase C: Oracle

- M6 Strong baseline policies.
- M7 Perfect-information rollout oracle.
- M8 Beam-search oracle and exact checks.

### Phase D: Evidence

- M9 Benchmark generator and sensitivity plan.
- M10 Experiment execution and verdict.

The vault bootstrap is a documentation prerequisite. It does not pass M0 or authorize M1.

## Proposal Migration Map

| Proposal material | Destination |
| --- | --- |
| Sections 0-1 | Home, Product records, initial authorization decision, claim ceiling |
| Section 2 | Product principles, geometry/economic concepts, metric records |
| Section 3 | Source records, market claims, differentiated wedge, novelty risks |
| Section 4 | End-state reference, target-customer assumptions, commercial gates |
| Section 5 | Experiment Charter, H1-H6, information sets, frozen scope |
| Section 6 | Architecture components, entities, interfaces, invariants, non-goals |
| Section 7 | Phase and M0-M10 records |
| Section 8 | Sources, regimes, parameters, controls, metrics, ablations |
| Section 9 | Go/no-go decision template and thresholds |
| Section 10 | Atomic risk records |
| Section 11 | Initial authorization decision |
| Appendix A | Data-contract hypotheses and entity records |
| Appendix B | Acceptance criteria linked to milestones and invariants |
| Appendix C | Benchmark parameter and ablation records |
| Appendix D | Term definitions and aliases |
| References | SRC records and Source Index |

## Source Preservation

- Preserve the original DOCX unchanged under Archive/Proposal v1/.
- Record its SHA-256, original path, date, version, and Git status at migration.
- Create a proposal source record and a complete migration map.
- Do not treat the archived DOCX as current authority after atomization.
- Create one source record for each of the 21 references.
- Record access date and whether the claim was reverified during migration.
- Preserve vendor claims as vendor claims, not independent performance proof.
- Prefer source notes to copied excerpts; quote only when exact wording is decision-relevant.

## Diagram and Attachment Policy

- Use Mermaid for maintained architecture, state, and flow diagrams.
- Preserve the polished proposal figures as archival attachments if needed for fidelity.
- Do not duplicate the same diagram as independently maintained Mermaid and image without naming one canonical.
- Store vault attachments under _attachments/ with stable descriptive names.
- Link large generated experiment outputs by manifest or repository path rather than embedding them into notes.

## Obsidian Configuration

Commit:

- app settings required for link updates and default note placement;
- core plugin enablement;
- Templates folder configuration;
- portable property type configuration if stable;
- Bases;
- templates; and
- a vault-specific ignore policy.

Do not commit:

- workspace.json;
- personal pane layout;
- hotkeys unless explicitly standardized;
- cache;
- Sync state;
- local history;
- device-specific state; or
- community plugin state.

Enable the core file explorer, search, quick switcher, backlinks, outgoing links, properties, templates, Bases, outline, bookmarks, command palette, and file recovery. Keep community plugins unnecessary.

## Documentation Validation

Add a zero-dependency validator that fails on:

- missing required root or index notes;
- duplicate IDs;
- unknown type, state, or authority values;
- missing required properties for a type;
- broken internal links;
- broken embedded attachments;
- more than one active phase;
- more than one active milestone;
- an active work packet outside the active milestone;
- a completed milestone without evidence links;
- a claim level above its evidence tier;
- a source record without an access date;
- a current-truth record without a verified commit;
- an archived proposal section without a migration destination; and
- committed personal Obsidian state.

Warn on:

- stale Current Truth relative to HEAD;
- orphaned notes;
- accepted notes with no incoming links;
- stale sources;
- risks with no trigger or mitigation;
- metrics without frozen definitions;
- hypotheses without linked tests;
- completed packets without a Work Log entry; and
- result summaries without a manifest or explicit reason one is not applicable.

The first validator may parse a deliberately small YAML subset. Full schema tooling can be added when the repository's Python environment is established.

## Acceptance Tests

The vault bootstrap is accepted when:

- docs/ opens as an Obsidian vault;
- Home, Current Truth, Current Work, and Work Log render correctly;
- all required indexes and templates exist;
- native Bases show the expected records;
- every proposal section and reference is mapped;
- all internal links resolve;
- all IDs are unique;
- exactly one phase and milestone are active;
- M0 remains unpassed;
- claim levels do not exceed evidence;
- Current Truth states that no implementation or benchmark evidence exists;
- the archived proposal hash matches the original;
- the validation command passes;
- negative fixtures prove the validator catches key failure classes;
- GitHub renders the root README and representative notes acceptably; and
- a live Obsidian pass verifies navigation, Bases, backlinks, templates, and automatic link updates.

## Automation Candidates

- Documentation validator in local checks and CI.
- Unique-ID and required-property checks.
- Link and attachment validation.
- Claim/evidence ceiling validation.
- Active phase/milestone invariant.
- Proposal migration coverage.
- Source freshness reporting.
- Generation of README status links from stable root notes only if manual drift becomes recurrent.

Do not automatically rewrite narrative conclusions, Current Truth, ADRs, or result interpretations.

## Initial Atomic Records

The migration should create, at minimum:

- H1 through H6;
- M0 through M10;
- Phase A through Phase D;
- the five information sets;
- Oracle Advantage and Unknown-Future Component metrics;
- all supporting green/yellow/red gate metrics;
- material accounting, determinism, information isolation, and candidate-parity invariants;
- baseline, rollout, beam, no-signal, exact-small-case, terminal, and recoverability records;
- the proposal's core demand regimes;
- the proposal's risk register;
- SRC-001 through SRC-021;
- the initial Phase A authorization decision; and
- the proposal v1 source and migration map.

## ADR Delta

### Context

YieldForge has a comprehensive proposal but no living repository documentation, implementation, or evidence. The documentation system must support a falsification-first research program without turning proposal prose into unearned truth.

### Decision

Use docs/ as a single core-only Obsidian vault with a typed atomic graph, four root orientation notes, native Bases, templates, split authority, an explicit evidence ladder, and automated structural validation.

### Alternatives

- proposal mirror;
- lifecycle-and-authority vault; and
- repository-root vault.

### Consequences

- High-quality provenance and focused diffs.
- Strong protection against claim/evidence drift.
- More files and more maintenance than a narrative vault.
- Dependence on stable IDs, templates, indexes, and validation.
- The proposal becomes an archived source rather than the current plan.
- End-state product concepts remain visible without contaminating active MVP scope.

### Accepted risks

- The atomic graph may become over-fragmented.
- Native Bases may evolve.
- Property editing can drift without validation.
- Source records require upkeep.
- Current Truth remains a manual synthesis step.

### Revisit triggers

Revisit this design if:

- routine work requires updating more than five notes for one bounded change;
- orphan or stale-note warnings remain common for two consecutive milestones;
- Bases cannot express the required views;
- contributors cannot orient from Home and Current Work within five minutes;
- source maintenance materially exceeds research value;
- the project reaches a production phase requiring generated public documentation; or
- a repeated authority error escapes the validator.

## Nonclaims

This design does not:

- pass M0;
- implement the vault;
- validate Sparrow candidate capture;
- select a geometry Boolean library;
- implement any schema;
- run a benchmark;
- authorize forecasting;
- authorize CAM or customer integration;
- establish a commercial opportunity;
- clear the YieldForge name; or
- convert proposal thresholds into achieved evidence.

## Next Step

Write and review the detailed implementation plan for the vault bootstrap. Execute that plan separately. After the vault passes its structural and live-Obsidian acceptance gates, begin M0 as a distinct experiment-contract task.
