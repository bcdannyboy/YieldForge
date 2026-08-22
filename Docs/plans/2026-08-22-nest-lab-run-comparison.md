# Nest Lab Read-Only Run Comparison Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an accessible, read-only side-by-side comparison of the open completed Nest Lab run and one other immutable completed run.

**Architecture:** Keep the completed-run API unchanged. Add a presentational `RunComparison` component that renders exact fields from two parsed `CompletedRun` records, and keep only the second run's job ID as new Nest Lab state. The existing selected archive remains Run A and the existing request-generation guards continue to own candidate and geometry browsing.

**Tech Stack:** React 19, TypeScript 5.9, Testing Library/Vitest, CSS, Playwright, FastAPI, and the existing Spyrrow-backed local service.

---

### Task 1: Render exact pairwise evidence

**Files:**
- Create: `yf/web/src/nest/RunComparison.tsx`
- Modify: `yf/web/src/nest/NestLab.tsx`
- Test: `yf/web/src/App.test.tsx`

**Step 1: Write the failing test**

Extend the completed-run history test with two distinct records and require:

```tsx
const comparison = screen.getByRole("region", { name: "Read-only run comparison" });
await userEvent.setup().selectOptions(
  within(comparison).getByRole("combobox", { name: "Compare open run with" }),
  "job-older",
);
const evidence = within(comparison).getByRole("table", { name: "Recorded run evidence" });
expect(within(evidence).getByRole("row", { name: /Seed 29 23 Different/i })).toBeVisible();
expect(within(evidence).getByRole("row", { name: /Archived candidates 1 1 Same/i })).toBeVisible();
expect(within(comparison).queryByText(/better|winner|improvement|optimal|savings/i)).not.toBeInTheDocument();
```

Also assert exact job IDs, source slice hashes, assumption codes, archive hashes, completion times,
budgets, worker count, early termination, and minimum separation appear in the table.

**Step 2: Run the test to verify it fails**

Run from `yf/web/`:

```bash
npm test -- --run src/App.test.tsx -t "browses newest-first completed run history"
```

Expected: FAIL because the `Read-only run comparison` region does not exist.

**Step 3: Write the minimal implementation**

Create `RunComparison.tsx` with this public interface:

```tsx
interface RunComparisonProps {
  runs: CompletedRun[];
  runA: CompletedRun | null;
  runB: CompletedRun | null;
  runBId: string | null;
  disabled: boolean;
  onRunBChange: (jobId: string | null) => void;
}
```

Render a labelled selector whose options exclude Run A. When Run B exists, render a semantic table
with `Field`, `Run A · open`, `Run B · comparison`, and `Relation` columns. Build rows for job ID,
completion time, every recorded setting, archived candidate count, assumptions, dataset, source
slice hash, archive schema, and archive hash. Format null separation and missing source binding
explicitly. Use strict formatted-value equality to produce only `Same` or `Different`.

Caption candidate count as archive inventory, not quality. Render an explicit message if fewer than
two completed runs exist. In `NestLab.tsx`, add `comparisonRunId`, resolve both records from
`completedRuns`, and mount the component before the history list.

**Step 4: Run the focused test**

Run the same command. Expected: PASS.

**Step 5: Commit**

```bash
git add yf/web/src/nest/RunComparison.tsx yf/web/src/nest/NestLab.tsx yf/web/src/App.test.tsx
git commit -m "feat: compare completed Nest Lab runs"
```

### Task 2: Preserve valid pairs and clear invalid state

**Files:**
- Modify: `yf/web/src/nest/NestLab.tsx`
- Test: `yf/web/src/App.test.tsx`

**Step 1: Write failing state-transition tests**

Add focused tests that choose the older run as Run B, open it as Run A, and require the former Run
A to become Run B. Also require comparison clearing on solver submission and task change, active-job
lockout, and the fewer-than-two-runs message.

**Step 2: Verify RED**

```bash
npm test -- --run src/App.test.tsx -t "run comparison"
```

Expected: FAIL on pair swapping and state clearing before the transition logic exists.

**Step 3: Implement minimal transition logic**

Keep a synchronously updated `selectedRunIdRef` so `selectCompletedRun` stays stable:

```tsx
const selectedRunIdRef = useRef<string | null>(null);

const selectCompletedRun = useCallback((run: CompletedRun) => {
  const nextId = run.job.job_id;
  const previousId = selectedRunIdRef.current;
  setComparisonRunId((current) => current === nextId && previousId ? previousId : current);
  selectedRunIdRef.current = nextId;
  resetArchiveView();
  setSelectedRunId(nextId);
  setJob(run.job);
}, [resetArchiveView]);
```

Set both selected/comparison state and the ref to `null` on task change and job submission. Add an
effect that clears Run B if its ID disappears from `completedRuns` or equals Run A. Do not alter
candidate/archive request generations.

**Step 4: Verify GREEN**

```bash
npm test -- --run src/App.test.tsx -t "run comparison"
npm test
```

Expected: all tests PASS with no warnings.

**Step 5: Commit**

```bash
git add yf/web/src/nest/NestLab.tsx yf/web/src/App.test.tsx
git commit -m "fix: stabilize Nest Lab comparison state"
```

### Task 3: Style the comparison for desktop and mobile

**Files:**
- Modify: `yf/web/src/styles.css`
- Test: `yf/web/src/App.test.tsx`

**Step 1: Add a failing structure assertion**

Require the table caption, labelled selector, Run A/Run B headers, and neutral relation cells. The
real Axe scan supplies browser-level accessibility coverage.

**Step 2: Verify RED**

```bash
npm test -- --run src/App.test.tsx -t "browses newest-first completed run history"
```

Expected: FAIL for the newly required caption/header detail.

**Step 3: Add minimal responsive styles**

Add `.run-comparison`, `.run-comparison__controls`, `.run-comparison__table-wrap`, and
`.run-comparison__relation` rules. Keep hashes wrapping, the table horizontally scrollable, and the
selector at least 44px tall on narrow viewports. Do not use green/red ranking colors.

**Step 4: Verify GREEN**

```bash
npm test -- --run src/App.test.tsx -t "browses newest-first completed run history"
npm run typecheck
npm run build
```

Expected: all commands PASS.

**Step 5: Commit**

```bash
git add yf/web/src/styles.css yf/web/src/App.test.tsx
git commit -m "style: present neutral run comparison"
```

### Task 4: Exercise the real API-to-Spyrrow comparison path

**Files:**
- Modify: `yf/web/e2e/workbench.spec.ts`

**Step 1: Write the failing E2E assertions**

After the existing desktop scenario creates its second run, select the first job as Run B and
require exact orientation and seeds:

```ts
const comparison = page.getByRole("region", { name: "Read-only run comparison" });
await comparison.getByRole("combobox", { name: "Compare open run with" }).selectOption(created.job_id);
const evidence = comparison.getByRole("table", { name: "Recorded run evidence" });
await expect(evidence.getByRole("row", { name: /Seed 424 23 Different/i })).toBeVisible();
await firstRun.click();
await expect(comparison.getByRole("combobox", { name: "Compare open run with" }))
  .toHaveValue(secondCreated.job_id);
```

Also assert both exact archive hashes and rerun Axe after the table appears.

**Step 2: Run the real desktop E2E**

```bash
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm run e2e -- --project=desktop
```

Expected on the pre-feature checkout: FAIL because the comparison region is absent. During
implementation, every integration defect gets a reproducing test before the minimum fix.

**Step 3: Run the full real E2E suite**

```bash
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm run e2e
```

Expected: desktop workflows PASS; the deliberate mobile solver-mutation case remains skipped.

**Step 4: Commit**

```bash
git add yf/web/e2e/workbench.spec.ts yf/web/src
git commit -m "test: prove real run comparison"
```

### Task 5: Update bounded workbench documentation

**Files:**
- Modify: `README.md`
- Modify: `Docs/Current Work.md`
- Modify: `Docs/Development/Getting Started.md`
- Modify: `Docs/Development/Research Workbench.md`

**Step 1: Document the exact capability**

Describe the Run A/open plus Run B/chosen interaction, exact recorded fields, real two-run browser
proof, and neutral relation labels. State that candidate count is archive inventory, not quality.

**Step 2: Preserve the claim ceiling and update next work**

Keep the 256-task bounded selection, task `13958` acknowledgement, task `25801` view-only state,
source/derived/generated/assumed labels, and absence of residual geometry, remnant reuse,
simulation, oracle, savings, production, and buyer proof. Replace the completed UI task as “next”
with approving M0's primary outcome, cost accounting, strongest baseline, oracle pairing, and
success threshold before more product surface.

**Step 3: Review and commit**

```bash
git diff --check
git diff -- README.md Docs/Current\ Work.md Docs/Development/Getting\ Started.md Docs/Development/Research\ Workbench.md
git add README.md Docs/Current\ Work.md Docs/Development/Getting\ Started.md Docs/Development/Research\ Workbench.md
git commit -m "docs: document read-only run comparison"
```

Expected: clean whitespace and bounded claims.

### Task 6: Final verification, review, and publication

**Files:**
- Review all changed files

**Step 1: Run Python quality and tests from `yf/`**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Expected: Ruff checks PASS; the full Python suite passes with only documented environment-dependent
skips.

**Step 2: Run frontend verification from `yf/web/`**

```bash
npm test
npm run typecheck
npm run build
YIELDFORGE_E2E_REAL_API=true YIELDFORGE_E2E_EXTERNAL=true npm run e2e
```

Expected: all unit tests, TypeScript/build, and real browser workflows PASS; only the deliberate
mobile mutation scenario skips.

**Step 3: Review repository state**

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -8
```

Inspect every commit and the cumulative diff from `66cf6cf`; confirm no unrelated files, secrets,
runtime archives, browser data, or generated build output are tracked.

**Step 4: Push `main`**

```bash
git push origin main
```

Expected: `origin/main` advances to the verified comparison commit and the worktree is clean.
