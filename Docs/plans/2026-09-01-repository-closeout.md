# YieldForge Repository Closeout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate the completed YieldForge research lineage onto `main`, publish an evidence-faithful semantic closeout, preserve unique local work, and leave an explicit boundary between the private research archive and a future public release.

**Architecture:** Treat the repository as a paused research archive: README and vault entry points summarize the decision, canonical manifests preserve non-reconstructive audit evidence, and source-rich/raw execution state stays in a verified local archive. Limit executable changes to a tested Python source-distribution boundary; do not alter the nesting, replay, oracle, or economic algorithms.

**Tech Stack:** Markdown/Obsidian, Git worktrees, Python 3.12, `uv`, Hatchling, pytest, Ruff, FastAPI research workbench, React/TypeScript/Vitest/Vite.

---

### Task 1: Publish the approved closeout design and plan

**Files:**

- Create: `Docs/plans/2026-09-01-repository-closeout-design.md`
- Create: `Docs/plans/2026-09-01-repository-closeout.md`

**Step 1: Verify the approved design records the claim boundary**

Run:

```bash
rg -n "0\.536368330506|known-only|sample-size|public release|Sparrow" \
  Docs/plans/2026-09-01-repository-closeout-design.md
```

Expected: the design distinguishes M10 from M11, full-future from known-only value, current-tree integration from public release, and rebuilding Sparrow from a valid reopen trigger.

**Step 2: Check Markdown whitespace**

Run: `git diff --check`

Expected: exit 0.

**Step 3: Commit**

```bash
git add Docs/plans/2026-09-01-repository-closeout-design.md \
  Docs/plans/2026-09-01-repository-closeout.md
git commit -m "docs: plan repository closeout"
```

### Task 2: Add a fail-first source-distribution boundary

**Files:**

- Create: `yf/tests/test_packaging_policy.py`
- Modify: `yf/pyproject.toml`
- Modify: `.gitignore`

**Step 1: Write the failing policy test**

Create a pytest test that parses `yf/pyproject.toml` with `tomllib` and requires an explicit Hatch sdist `include` allowlist plus excludes for, at minimum, `var`, `dist`, `web/node_modules`, browser downloads, web build/test output, Python caches, and native `target` directories. Also assert the repository `.gitignore` protects `yf/dist/` and the two oversized raw M11 artifact name families.

The test should express the public packaging contract, not reimplement Hatchling.

**Step 2: Run the test to verify RED**

Run:

```bash
cd yf
uv run --all-groups pytest tests/test_packaging_policy.py -q
```

Expected: FAIL because the sdist boundary and tracked root ignore rules do not exist.

**Step 3: Add the minimal configuration**

Add `[tool.hatch.build.targets.sdist]` with a root-relative include allowlist for intended package/build metadata and explicit excludes for local evidence, dependencies, caches, test results, browser binaries, build output, and native targets. Add matching `.gitignore` rules at repository scope.

Do not change the wheel package mapping or runtime dependencies.

**Step 4: Run the policy test to verify GREEN**

Run: `uv run --all-groups pytest tests/test_packaging_policy.py -q`

Expected: PASS.

**Step 5: Build and inspect the real sdist**

Run:

```bash
uv build --sdist --out-dir /tmp/yieldforge-closeout-sdist
tar -tzf /tmp/yieldforge-closeout-sdist/yieldforge-0.1.0.tar.gz
```

Expected: a small archive containing intended source/build metadata and no `var/`, `dist/`, `node_modules/`, Playwright browser, web `dist/`, test-results, cache, or native `target/` member.

**Step 6: Commit**

```bash
git add .gitignore yf/pyproject.toml yf/tests/test_packaging_policy.py
git commit -m "build: bound public source distribution"
```

### Task 3: Add public evidence, attribution, and artifact boundaries

**Files:**

- Create: `THIRD_PARTY_NOTICES.md`
- Create: `PUBLIC_RELEASE.md`
- Create: `Docs/Development/Artifact Policy.md`
- Create: `yf/experiments/results/m11-economic-resolution/m11-economic-calibration-manifest-3409ada18b831fee1394410dfec88a02806ff0c3709372cfbe9340e05b920533.json`
- Create: `yf/experiments/results/m11-economic-resolution/m11-economic-validity-stage-8d736641be30b3d04dad50eb21698738ac4924bb42f664961911910ddb9ddfe4.json`
- Create: `yf/experiments/results/m11-economic-resolution/m11-economic-central-manifest-71171ff1cb601f546f55b78eda8dc2b81d60d7e02949042a55d53feb29e5dcf2.json`
- Modify: `Docs/Evidence/M11 - Economic resolution.md`
- Modify: `.gitignore`
- Test: `yf/tests/realistic_falsification/test_public_economic_evidence.py`

**Step 1: Write the failing public-evidence test**

Require the three exact canonical manifest paths and raw SHA-256 identities recorded in the M11 report. Recursively reject keys or string content that disclose source geometry/demand fields such as WKB, polygon coordinates, parts, placements, or source demand. Require the private/source-rich filename families to remain ignored.

**Step 2: Run the test to verify RED**

Run:

```bash
cd yf
uv run --all-groups pytest \
  tests/realistic_falsification/test_public_economic_evidence.py -q
```

Expected: FAIL because the manifests are not tracked in a clean worktree.

**Step 3: Copy only the audited manifest subset**

Copy the three manifests from the preserved M11 worktree. Do not copy:

- `m11-gate3-calibration-observation-*.json.gz`;
- `m11-gate3-validity-receipt-*.json.gz`;
- the 235 MB Gate 2 raw result; or
- the 2.27 GB early Gate 3 raw result.

**Step 4: Document public rights and evidence limits**

`THIRD_PARTY_NOTICES.md` must attribute the Lectra dataset authors, title, version, DOI, CC BY 4.0 license URL, and YieldForge transformations. It must state that the official LOCo source and pinned archive provide no express redistribution license and that existing LOCo-derived history is not cleared for public release.

`PUBLIC_RELEASE.md` must separate repository consolidation from publication and list unresolved owner decisions: LOCo permission or sanitized history, project license, proposal/diagram ownership, Git author identity, provisional name, and repository visibility.

`Artifact Policy.md` must distinguish canonical tracked evidence, compact public manifests, ignored resumable/raw evidence, permissioned source inputs, and reproducibility limits for a clean clone.

Add a publication note to the M11 report: the full private packet remains preserved, the three non-reconstructive manifests are tracked, and the notebook cannot perform its complete raw replay from a clean clone without the separately permissioned packet.

**Step 5: Run the public-evidence test to verify GREEN**

Run: `uv run --all-groups pytest tests/realistic_falsification/test_public_economic_evidence.py -q`

Expected: PASS with all three hashes authenticated and source-rich keys absent.

**Step 6: Commit**

```bash
git add .gitignore THIRD_PARTY_NOTICES.md PUBLIC_RELEASE.md \
  "Docs/Development/Artifact Policy.md" \
  "Docs/Evidence/M11 - Economic resolution.md" \
  yf/experiments/results/m11-economic-resolution \
  yf/tests/realistic_falsification/test_public_economic_evidence.py
git commit -m "docs: define public evidence boundary"
```

### Task 4: Rewrite the repository landing page as a semantic retrospective

**Files:**

- Modify: `README.md`
- Modify: `Docs/Home.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Modify: `Docs/Research/Technical Sources.md`

**Step 1: Replace stale current-state language**

The README must use this information architecture:

1. `Status: paused research archive`;
2. `The idea`;
3. `Why test with perfect future information`;
4. `How we tested it` with M0-M11 grouped into semantic stages;
5. `What the final test found` with a B/F/K result table;
6. `Why this was not just a sample-size problem`;
7. `What we learned` with direct findings and clearly labeled bounded inferences;
8. `Why the project is on the back burner`;
9. `What would justify reopening it`;
10. `Evidence and reproducibility`;
11. `Repository guide and local verification`;
12. `Public-release and name status`.

Required semantic statements:

- M10 stopped productization and more virtual-oracle work but did not compute `0.54%`.
- M11 measured `0.536368330506%` full-future mean on Lectra, `0%` full-future on LOCo, and `0%` known-only on both.
- Lectra full-future had a zero median, 3/20 positive streams, and 95% interval `[0%, 1.098250947619%]`.
- The full-future threshold was `2.5%`; the known-only threshold was `1.5%` plus positive median/lower bound/majority prevalence gates.
- More same-distribution samples can narrow uncertainty but cannot create a missing deployable mechanism.
- The result falsifies the current modeled product/algorithms, not every possible algorithm, segment, or factory.
- Reopening begins with a materially new known-only mechanism, segment, or permissioned factory evidence—not rebuilding Sparrow or adding more similar streams.

Explain Sparrow/Spyrrow once: Sparrow was the underlying nesting capability accessed through the reproducible Spyrrow adapter/candidate archives; replacing it is a new investment, not a prerequisite for interpreting this result.

**Step 2: Update vault entry points without rewriting history**

- Link the M11 economic resolution and artifact/public-release notes from `Docs/Home.md`.
- Add an M11/archive postscript to the roadmap and mark old “next step” text as historical.
- Correct `Technical Sources.md` so it no longer claims that no external dataset entered the benchmark suite.

**Step 3: Validate semantics and links**

Run:

```bash
rg -n "0\.536368330506|known-only|sample.size|reopen|Sparrow|M11" README.md
git diff --check
```

Expected: every required concept appears, no stale “M5 is next” language remains in README, and the diff has no whitespace errors.

**Step 4: Commit**

```bash
git add README.md Docs/Home.md Docs/Milestones/Milestone\ Roadmap.md \
  Docs/Research/Technical\ Sources.md
git commit -m "docs: close out YieldForge research"
```

### Task 5: Remove public-facing machine-path residue

**Files:**

- Modify: `Docs/experiments/m8-goal-execution-ledger.md`
- Modify: `Docs/plans/2026-08-17-lectra-qualification.md`
- Modify: `Docs/plans/2026-08-24-m8-certificate-proof-implementation.md`
- Modify: `Docs/plans/2026-08-25-m8-factored-certificate-redesign-implementation.md`
- Modify: `Docs/plans/2026-08-26-m8-portable-common-fact-bundle-implementation.md`
- Modify: `Docs/plans/2026-08-28-m8-pass-goal-execution-spec.md`

**Step 1: Replace absolute paths semantically**

Replace legacy absolute repository paths and historical worktree paths with `<repository-root>`, `<historical-worktree>`, or repository-relative paths as appropriate. Keep commands understandable and preserve the fact that old work occurred in an isolated worktree.

Do not rewrite Git author metadata or delete the historical plans.

**Step 2: Verify current-tree path hygiene**

Run:

```bash
git grep -n -I -E '/[U]sers/' -- .
git grep -n -I -E '/[h]ome/' -- .
```

Expected: neither command finds a local user-home path in the current tracked tree.

**Step 3: Commit**

```bash
git add Docs/experiments/m8-goal-execution-ledger.md Docs/plans
git commit -m "docs: remove local machine paths"
```

### Task 6: Verify the closeout branch

**Files:** none unless a verification defect is found and handled through a new fail-first task.

**Step 1: Run Python verification in stable partitions**

Run the broad suite excluding the two source-bound Postgres files and three tight timeout cases, then run those groups separately with the pinned local audit input and Postgres service available. This covers all 3,238 collected tests without making sub-second/one-second process deadlines compete with the 35-minute suite.

Run whole-tree Ruff lint after pytest, excluding only the exact content-addressed audit notebook
`notebooks/m11-economic-resolution.ipynb`. Run Ruff's format check on the closeout-owned Python
policy tests. Record the pre-exclusion 70-artifact formatting drift—the immutable notebook plus 69
historical Python files—rather than mechanically rewriting it, and do not claim a whole-tree format
pass.

Expected: all tests pass; only the three registered environment skips remain; whole-tree Ruff lint
and the closeout-owned format check exit 0.

**Step 2: Run web verification**

Run:

```bash
cd yf/web
npm test
npm run typecheck
npm run build
```

Expected: 56 tests pass, typecheck exits 0, and Vite builds successfully.

**Step 3: Verify the sdist and documentation**

Build the sdist into a fresh `/tmp` directory, inspect all members, and assert no excluded local path is present. Resolve every tracked Markdown inline link and wikilink. Run current-tree secret, absolute-path, oversized-object, and status scans.

**Step 4: Request two-stage review**

Request a specification review against this plan, then a code/document quality review. Fix every Critical or Important finding and re-run the affected verification.

### Task 7: Preserve local work, integrate, and clean Git state

**Files outside Git:** `<local-archive-root>/`

**Step 1: Build a verified local archive**

Preserve:

- the complete untracked M11 evidence packet and both oversized raw artifacts;
- a binary-safe patch of the four unique M8 worktree modifications;
- an archive of the lowercase Obsidian scaffold plus its README patch; and
- a manifest recording source worktree, branch tip, file counts, byte sizes, and SHA-256 hashes.

Write outside the repository so a later visibility change cannot expose private/source-rich data. Verify the archived hashes before removing anything.

**Step 2: Confirm remote freshness and ancestry**

Run `git fetch origin`, verify remote `main` is still the expected ancestor, and verify every committed topic branch is an ancestor of the closeout tip.

Expected: closeout is a strict linear descendant; no committed branch remains unmerged.

**Step 3: Fast-forward main and verify again**

Fast-forward local `main` to the closeout tip. Re-run the relevant Python partitions, Ruff, web suite/typecheck/build, public-evidence hashes, sdist inspection, link scan, and status checks on `main`.

**Step 4: Push main**

Push only after local and remote ancestry plus merged-main verification pass. Do not change repository visibility.

**Step 5: Remove only preserved/merged worktrees and branches**

Remove historical worktrees after archive verification. Delete only branches proven merged into `main`. Preserve the external archive and report its exact location.

**Step 6: Clean reproducible local output**

Remove confirmed generated build/cache output such as the unsafe 624 MiB sdist, worktree-local venvs/node modules, and test/build caches. Do not blanket-delete `yf/var` or any unclassified evidence.

**Step 7: Final state audit**

Run:

```bash
git status --short --branch
git branch --no-merged main
git worktree list --porcelain
git ls-remote --symref origin HEAD 'refs/heads/*'
```

Expected: local and remote `main` point to the verified closeout tip, no intended committed branch is unmerged, the primary checkout is clean, and only the primary worktree remains.
