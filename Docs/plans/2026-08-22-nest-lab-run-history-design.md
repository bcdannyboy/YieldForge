# Nest Lab completed-run history design

**Status:** Approved for implementation on 2026-08-22.

## Goal

Add explicit completed-run history and selection to Nest Lab so a researcher can identify,
reopen, and compare immutable task-bound solver archives without confusing mutable job state with
completed execution evidence.

## Scope

The history contains only completed jobs with verified immutable candidate archives. It does not
become a general job monitor and does not expose failed, cancelled, timed-out, queued, or running
attempts. It does not add arbitrary problem input, residual geometry, remnant reuse, simulation,
oracle comparison, or savings claims.

## Public completed-run contract

Add a dedicated completed-run response rather than stretching the existing general `JobView`.
Each completed run contains:

- the existing public job view, including job ID, timestamps, candidate count, source-task binding,
  and archive availability;
- the immutable solver settings: seed, computation seconds, one-worker count, early-termination
  flag, minimum item separation, and hard runtime seconds;
- the candidate archive schema identity and verified batch SHA-256.

The server obtains settings from the persisted strict `SolveRequest` already owned by the job
service. It computes the public archive identity only from a successfully reopened and validated
`CandidateBatch`; it never returns an internal archive path. A completed snapshot without a
readable, source-bound archive fails closed instead of appearing in history.

Expose a bounded newest-first page for a task at
`GET /api/tasks/{tasks_index}/completed-runs?limit=20`. The task and catalog identity checks remain
server-owned. The existing solver-job endpoints remain stable.

## Nest Lab interaction

Nest Lab loads task detail and completed-run history independently. The history panel:

- labels itself **Completed run history** and explains that every item is immutable archive
  evidence;
- shows newest runs first;
- displays completion time, seed, computation/runtime limits, candidate count, worker count,
  early-termination/separation settings, acknowledged assumptions, job ID, and the full archive
  batch SHA-256;
- visibly and accessibly marks the selected run;
- defaults to the newest completed run;
- shows an explicit empty state when no completed archive exists.

Selecting a history item resets candidate and geometry selection, loads that exact job's complete
candidate archive through the existing paginated API, selects its newest candidate by default, and
renders geometry from the verified archive. Older candidate or geometry responses cannot overwrite
a newer run selection.

Starting a new solve clears the history selection while preserving the older cards. History
selection is disabled while that job is active so the live stream and cancellation controls cannot
be abandoned accidentally. When the job completes and the immutable archive is available, Nest Lab
refreshes history, selects the newly completed run, and reconciles its complete candidate batch.

History selection is browsing only. It does not copy settings into the form, rerun a job, delete an
archive, rank runs, or claim that one run is economically preferable.

## State and error handling

Task, history, live job, candidate archive, and geometry requests retain separate state. A history
load failure does not erase task eligibility or the live job. An archive or geometry failure keeps
the history visible and identifies the selected run that could not be opened.

The frontend strictly parses the completed-run schema, settings, source binding, and lowercase
64-character SHA-256. Backend responses remain bounded to 50 runs and never expose filesystem
paths, worker PIDs, raw problem payloads, or unverified manifest bytes.

## Verification

Implementation follows red-green-refactor testing for:

- API newest-first ordering, limits, exact source-task binding, run settings, verified batch hash,
  absence of internal paths, and fail-closed archive corruption;
- frontend strict contract parsing;
- newest-run default selection, older-run switching, full settings/hash display, empty/error states,
  active-run selection lockout, stale archive/geometry response suppression, and post-completion
  history refresh;
- a real browser flow that creates two task `13958` runs with different settings, observes the
  newest run become selected, switches back to the older immutable archive, and renders its
  candidates and geometry through the real FastAPI and Spyrrow boundary.

Completion also requires the full Python suite, Ruff check and format check, frontend unit tests,
TypeScript/build, real Playwright against the running Postgres-backed API, `git diff --check`, and
final diff/status review.

## Claim ceiling

Run history proves bounded rediscovery and browsing of immutable local solver archives and their
recorded settings. It does not compare runs scientifically, prove solver optimality, establish
physical feasibility, calculate residual geometry, simulate reuse, produce an oracle or savings
result, or establish production or buyer value.
