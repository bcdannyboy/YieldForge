# Postgres-backed 256-task corpus catalog design

**Status:** Approved for implementation on 2026-08-21.

## Goal

Expand Corpus Explorer from the two-task committed research slice to a deterministic catalog of
256 fully exported Lectra tasks while preserving the existing pickle-isolation, evidence-binding,
constraint, solver, and claim boundaries. Serve the catalog through Postgres without treating a
mutable database as the source of evidence.

## Non-goals

- Do not expose or imply qualification of all 100,000 source tasks.
- Do not interpret unresolved constraint families.
- Do not add arbitrary sheet-and-parts input, residual geometry, remnant reuse, inventory
  simulation, oracle comparison, savings, or production claims.
- Do not silently expand the order-book fixtures or reinterpret task indexes as chronology.
- Do not let the normal host process open the gzip-pickle source files.

## Selection and capability rules

The catalog exporter walks deterministic candidates using the existing task eligibility and
composition ranking: training partition, sheet type zero, positive dimensions, 20-50 part rows,
then distance from the target composition and source task ID. It continues until exactly 256 tasks
have passed all display-safety requirements:

- task, part, shape, and constraint source rows are structurally valid and source references
  resolve;
- shape coordinate streams are finite, flat-even, single-ring records whose derived polygons are
  valid, simple, and nonzero-area without repair;
- opaque constraint cells can be represented by the strict normalized contract;
- all browser-visible integers and floats remain safely serializable.

Tasks `13958` and `25801` are required continuity members. A task becomes
`runnable_with_explicit_assumptions` only when the existing strict `s1` projection rule succeeds;
it requires the exact acknowledgement
`interpret_s1_degenerate_entries_as_allowed_rotations`. Every other exported task is `view_only`
with explicit reason codes such as `contains_non_s1_constraints` or
`s1_projection_requirements_not_met`. No task becomes `directly_supported`.

Selection produces a bounded research catalog, not a prevalence sample of the release.

## Evidence and qualification boundary

The existing locked Docker qualifier remains the only process allowed to deserialize the source
pickles. A new `catalog` mode receives the same exact manifest and audit SHA-256 bindings as slice
mode and retains the existing networkless, read-only, non-root, resource-limited container.

Audit and two-task slice output remain capped at 4 MiB. Catalog mode gets a separate 64 MiB cap;
raising it does not weaken the other modes. The trusted runner:

1. captures at most the catalog-specific byte ceiling;
2. parses the entire payload through the strict `NormalizedSlice` contract;
3. binds it to the pinned manifest and audit bytes;
4. canonicalizes finite JSON;
5. publishes exactly one regular file atomically into a new empty directory; and
6. refuses overwrite, extra files, symlinks, truncation, schema drift, identity drift, or cleanup
   uncertainty.

The generated canonical catalog is the evidence authority. Its SHA-256, size, task count, source
manifest hash, audit hash, and ruleset version are pinned in a small committed catalog manifest.
The catalog payload may be committed if it remains below repository artifact limits; otherwise it
stays ignored and must reproduce the pinned logical hash before import.

## Postgres read model

Postgres is an indexed serving layer derived from the validated canonical catalog. A dedicated
local Docker Compose service provides a reproducible database without sharing credentials or state
with unrelated local projects.

The initial schema contains:

- `yieldforge_catalog`: one row containing schema version, source identity, canonical catalog hash,
  manifest/audit hashes, ruleset, counts, source DTO, coordinate-unit DTO, summary DTO, and creation
  metadata;
- `yieldforge_catalog_task`: one row per task containing source-order keys, indexed filter facets,
  canonical task summary JSON, complete task-detail JSON, optional verified solver-problem JSON, and
  a record SHA-256;
- an exact schema-version record and indexes for task ID, source order, support state, part-count
  bounds, and constraint-type membership.

The importer is transactional, validates the passive artifact before connecting, imports exactly
256 unique tasks, and is idempotent only for byte-identical catalog identity. It refuses to replace
or mutate another identity. For each task it computes canonical summary/detail hashes and the
catalog logical hash. Eligible solver projections are produced by the existing projection boundary
and stored only after validation.

At startup the Postgres query service checks the schema version, expected catalog identity, exact
row count, every record hash, aggregate counts, and solver-projection/capability consistency. It
uses fixed parameterized SQL and read-only transactions. Postgres failure or identity drift fails
closed; it never falls through to a different catalog under the same configured database URL.

Without `YIELDFORGE_DATABASE_URL`, the workbench retains its existing two-task committed-fixture
fallback. This keeps unit tests and minimal local use deterministic. The documented expanded setup
starts Postgres, imports the pinned catalog, and supplies the database URL.

## API and solver flow

The existing successful API contracts remain stable:

- `GET /api/corpus/summary`
- `GET /api/tasks`
- `GET /api/tasks/{tasks_index}`
- the existing solver-job routes

The Postgres service implements the same corpus-service interface. SQL list queries keep the
existing limit of 50, signed opaque cursor, source order, and filters. Cursors remain bound to the
catalog hash, exact filters, and a real task member. Detail requests read and validate the stored
strict DTO.

Solver submission continues to use server-owned capability state. View-only tasks fail before
problem construction or worker spawn. Assumption-backed tasks accept only their exact listed codes;
the stored projection is checked against the source task binding and catalog hash.

Order-book artifacts remain bound to their existing committed source slice and are not silently
regenerated from the expanded catalog.

## Corpus Explorer behavior

Corpus Explorer loads 50 tasks initially and appends the next signed page on an explicit “Load next
50” action. It shows the number loaded and the bounded catalog total. Applying or clearing filters
discards accumulated pages and restarts at the first page. Task selection and detail requests are
independent from pagination so opening a task does not refetch or discard list pages.

The UI derives support and constraint filters from the server summary, adds maximum-parts filtering,
types the three support states, and renders distinct “Directly supported,” “Assumption-backed,” and
“View only” labels. Deep-linked tasks load detail independently even if they are not on the first
page. Stale list/detail responses cannot overwrite newer state.

Nest Lab continues to show acknowledgement controls only for tasks that actually declare assumption
codes. No directly supported tasks are expected in this catalog, but the UI must not label a future
directly supported task as assumed.

## Error handling

- Catalog generation fails unless exactly 256 tasks satisfy the fixed rules and both continuity
  tasks are present.
- Qualifier publication remains no-clobber and leaves no partial final artifact.
- Import rejects wrong hashes, duplicate IDs/source rows, mismatched summaries/details, projection
  drift, unexpected existing data, and partially populated schemas.
- API startup rejects unavailable or invalid configured Postgres state.
- Invalid or stale cursors remain a bounded client error; the frontend restarts pagination only in
  response to that explicit condition.
- UI list and detail errors remain separate so one failed detail request does not erase usable rows.

## Verification

Implementation follows red-green-refactor testing for:

- 256-task selection, classification, stable ordering, continuity membership, and rejection paths;
- catalog-specific qualifier limits, evidence binding, canonicalization, and atomic publication;
- Postgres schema/import idempotence, tamper rejection, filter/cursor semantics, lazy detail, and
  solver projection;
- FastAPI behavior for later-page tasks and view-only solve rejection;
- frontend pagination, dynamic filters, stable selection, deep links, and accessibility;
- real browser loading of multiple pages, later-task geometry, task `25801` blocking, exact task
  `13958` acknowledgement, real Spyrrow execution, candidate archive rendering, and order-book
  provenance.

Completion also requires the full Python suite, Ruff check and format check, frontend unit tests,
TypeScript/build, real Playwright against the configured Postgres-backed API, `git diff --check`,
and final diff/status review.

## Claim ceiling

The result will be a local research workbench over a deterministically selected 256-task Lectra
catalog. It will establish source-preserving inspection and bounded assumption-backed solver
projection only. It will not establish corpus representativeness, source-unit interpretation,
constraint semantics beyond the existing projection rule, physical feasibility, residual geometry,
remnant reuse, inventory conservation, chronological simulation, an oracle, savings, production
fitness, or buyer value.
