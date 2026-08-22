# Nest Lab read-only run comparison design

**Status:** Approved for implementation on 2026-08-22.

## Goal

Let a researcher compare two completed task-bound solver runs in Nest Lab without changing either
immutable archive or implying that one run is scientifically, operationally, or economically
better.

## Scope and interaction

The archive already open in Nest Lab is Run A. A labelled selector inside Completed run history
chooses one different completed archive as Run B. The selector is available only when at least two
completed runs exist. The comparison remains browsing-only and does not rerun, delete, copy
settings from, or mutate either job.

Opening another history card changes Run A. If the opened run was Run B, the former Run A becomes
Run B so the pair remains visible; otherwise the selected Run B remains unchanged. A task change or
new solver submission clears Run B. Comparison controls are disabled while a non-terminal solver
job is active, matching the existing history lock.

## Evidence shown

The comparison uses the already validated completed-run response and performs no additional
archive fetch. A semantic side-by-side table shows exact recorded values for:

- job ID and completion time;
- seed, computation budget, hard runtime limit, worker count, early-termination flag, and minimum
  item separation;
- archived candidate count;
- acknowledged assumption codes;
- dataset ID and source-slice SHA-256;
- candidate-archive schema and verified batch SHA-256.

A third column reports only the neutral relation `same` or `different`. It does not calculate a
winner, score, percentage improvement, candidate quality, solver optimality, material savings, or
economic preference. Candidate count is inventory metadata, not a quality metric.

## State and error handling

Comparison state stores only Run B's job ID; Run A remains the existing selected archive. The
component resolves both IDs against the current completed-run page, so a refreshed page that no
longer contains Run B clears the comparison rather than retaining stale evidence. Existing strict
contract parsing, task-bound history errors, candidate archive loading, geometry loading, and
stale-response guards remain authoritative.

With fewer than two completed runs, Nest Lab explains that another completed archive is required.
The comparison adds no backend endpoint and no filesystem, raw-problem, or unverified manifest
exposure.

## Verification

Implementation follows red-green-refactor testing for:

- Run A plus Run B selection and exact side-by-side values;
- neutral `same`/`different` relations and absence of evaluative language;
- pair preservation when Run B is opened as Run A;
- comparison clearing on task change and solver submission;
- active-job lockout and fewer-than-two-runs behavior;
- real desktop Playwright execution that creates two task `13958` jobs through FastAPI and the
  Spyrrow boundary, then compares their immutable recorded evidence.

Completion also requires the full Python suite, Ruff check and format check, frontend unit tests,
TypeScript/build, real Playwright against the running Postgres-backed API, `git diff --check`, and
final diff/status review.

## Claim ceiling

The comparison proves only that two immutable local run records can be inspected side by side. It
does not establish solver quality, optimality, physical feasibility, corpus representativeness,
residual geometry, remnant reuse, chronological simulation, an oracle comparison, savings,
production fitness, or buyer value.
