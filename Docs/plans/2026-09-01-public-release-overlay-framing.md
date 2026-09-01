# Public Release Overlay Framing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prepare a coherent private release candidate that leads with YieldForge's workflow-preserving overlay thesis and reflects the owner's approval of all retained publication content.

**Architecture:** Treat the incumbent nesting system as the candidate generator and YieldForge as a future-aware selection layer over its strong alternative nests. Update every tracked publication-boundary statement to retain factual provenance while removing superseded blocker language; keep raw local evidence outside Git and leave remote visibility private.

**Tech Stack:** Markdown/Obsidian, Git, Python 3.12, pytest, Ruff, Hatchling, GitHub CLI.

---

### Task 1: Capture the fail-first semantic baseline

**Files:** none

**Step 1: Confirm the workflow-overlay idea is currently incomplete**

Run:

```bash
rg -n -i "keep.*existing|existing.*workflow|progressive.*nest|strong.*space|selection layer" \
  README.md Docs/Home.md
```

Expected: the current entry points mention candidate selection and Sparrow, but do not clearly lead
with keeping the incumbent nesting system/workflow or explain that Sparrow was chosen because it is
strong and exposes progressive alternative nests.

**Step 2: Confirm superseded blocker language is still present**

Run:

```bash
rg -n -i \
  "not (currently )?cleared|not authorized for public|no express redistribution|LOCo permission|sanitized history|no (project )?license|no license has been (chosen|selected)|rights clearance|publication authorization" \
  --glob '*.md' \
  --glob '!Docs/plans/2026-09-01-public-release-overlay-framing.md' \
  .
```

Expected: FAIL the intended release-candidate boundary by finding matches in current entry points,
release notes, source/evidence notes, and earlier closeout plans.

### Task 2: Make the incumbent-preserving workflow thesis explicit

**Files:**

- Modify: `README.md`
- Modify: `Docs/Home.md`

**Step 1: Rewrite the opening idea in workflow-first language**

State, near the top of both entry points:

- manufacturers were meant to keep their existing nesting system and core nesting workflow;
- that incumbent remains responsible for generating process-feasible, space-efficient nests;
- YieldForge asks for several strong alternative/near-tied nests and adds a decision layer that
  selects among them using future residual usefulness and cumulative material cost; and
- the value proposition is incremental intelligence on top of the incumbent, not migration to a
  replacement nesting engine.

**Step 2: Explain why Sparrow was the first substrate**

State that Sparrow was chosen because it is a strong immediate-space optimizer and, through
Spyrrow's progressive reports, exposes intermediate/alternative nests that can be normalized into
candidate archives. Clarify that another incumbent system would need to expose equivalent
candidates, but that this is an integration requirement rather than a workflow-replacement thesis.

**Step 3: Preserve the claim ceiling**

Keep a nearby, secondary caveat: the research tested one reproducible adapter/candidate boundary;
it did not prove arbitrary vendor compatibility, live inventory synchronization, operator
acceptance, or literally zero integration work.

**Step 4: Run the semantic check**

Run:

```bash
for entry_point in README.md Docs/Home.md; do
  for required_phrase in \
    "existing nesting" \
    "nesting workflow" \
    "selection layer" \
    "progressive" \
    "alternative nests" \
    "integration"
  do
    rg -n -i "$required_phrase" "$entry_point" || exit 1
  done
done
```

Expected: every required phrase is present independently in both files, and each entry point uses
those phrases to express the workflow benefit, Sparrow rationale, and bounded integration caveat
without describing YieldForge as a replacement solver.

**Step 5: Commit**

```bash
git add README.md Docs/Home.md
git commit -m "docs: lead with the incumbent workflow overlay"
```

### Task 3: Replace the superseded publication gate with an owner-approved boundary

**Files:**

- Modify: `PUBLIC_RELEASE.md`
- Modify: `THIRD_PARTY_NOTICES.md`
- Modify: `README.md`
- Modify: `Docs/Home.md`
- Modify: `Docs/Research/Technical Sources.md`
- Modify: `Docs/Development/Artifact Policy.md`
- Modify: `Docs/Evidence/M11 - Economic resolution.md`
- Modify: `Docs/plans/2026-09-01-repository-closeout-design.md`
- Modify: `Docs/plans/2026-09-01-repository-closeout.md`

**Step 1: Turn `PUBLIC_RELEASE.md` into a release-candidate record**

Record these resolved owner decisions:

- the tracked proposal, diagrams, source-derived research material, project name, and existing Git
  author metadata are approved for publication;
- no project-level permissions file will be added or treated as a release requirement;
- retained source material keeps factual citations and transformation provenance;
- the tracked current tree and compact manifests are publishable;
- ignored `yf/var`, the external archive, raw/resumable packets, credentials, environments, and
  caches are excluded; and
- visibility remains private until a separate explicit action.

Keep the development workbench warning: publishing source does not authorize exposing its local
services to untrusted networks.

**Step 2: Reduce `THIRD_PARTY_NOTICES.md` to attribution and provenance**

Preserve Lectra authors, DOI, version, CC BY 4.0 attribution, and YieldForge transformations.
Preserve the LOCo citation, official source, pinned checksum, and transformation description.
Remove the superseded language that presents retained LOCo-derived content as a publication
blocker.

**Step 3: Align every current-tree reference**

Update the remaining listed files so they link to the prepared release boundary, describe
separately preserved raw parents as a reproducibility limitation rather than a rights blocker, and
contain no unresolved owner/license/name/history gate. In the earlier closeout plans, replace the
superseded gate requirements with attribution/provenance and owner-approved publication-boundary
requirements; do not alter experimental outcomes or artifact identities.

**Step 4: Run the whole-tree blocker scan**

Run the Task 1 blocker scan again.

Expected: zero matches.

**Step 5: Commit**

```bash
git add PUBLIC_RELEASE.md THIRD_PARTY_NOTICES.md README.md Docs/Home.md \
  "Docs/Research/Technical Sources.md" "Docs/Development/Artifact Policy.md" \
  "Docs/Evidence/M11 - Economic resolution.md" \
  Docs/plans/2026-09-01-repository-closeout-design.md \
  Docs/plans/2026-09-01-repository-closeout.md
git commit -m "docs: record the prepared public release boundary"
```

### Task 4: Review and verify the prepared candidate

**Files:** none unless review finds a defect

**Step 1: Request independent semantic review**

Use `@superpowers:subagent-driven-development` to have one reviewer compare the final entry points
against the approved design and another reviewer check publication-boundary consistency. Resolve
all Critical or Important findings.

**Step 2: Verify focused executable policies**

Run from `yf/`:

```bash
uv sync --locked --all-groups
uv run --all-groups pytest \
  tests/test_packaging_policy.py \
  tests/test_tooling_policy.py \
  tests/realistic_falsification/test_public_economic_evidence.py -q
uv run --all-groups ruff check .
uv run --all-groups ruff format --check \
  tests/test_packaging_policy.py \
  tests/test_tooling_policy.py \
  tests/realistic_falsification/test_public_economic_evidence.py
```

Expected: 7 focused tests pass; Ruff lint and targeted format checks exit 0.

**Step 3: Build and inspect a fresh source distribution**

Build into a new `/tmp` directory and confirm no `var`, raw packet, environment, dependency,
browser, cache, test-result, or native-target member is present.

**Step 4: Run documentation and repository scans**

Validate all tracked Markdown inline links and wikilinks. Run `git diff --check`, `/Users/` and
`/home/` scans, high-confidence credential/private-key scans, tracked-object size inventory, the
whole-tree blocker scan, and `git status`.

Expected: links resolve; no blocker language, local path, secret, or object at/above GitHub's 100
MiB hard limit; worktree clean.

### Task 5: Integrate the prepared candidate without publishing it

**Files:** none

**Step 1: Confirm private remote freshness and ancestry**

Fetch `origin`, confirm remote `main` is the branch base, and confirm GitHub visibility is still
private.

**Step 2: Fast-forward `main` and run affected checks again**

Fast-forward local `main` to `codex/public-release-framing`. Re-run the whole-tree blocker scan,
link/path/secret scans, focused 7 tests, Ruff checks, source-distribution inspection, and clean
status checks at the exact merged commit.

**Step 3: Push private `main`**

Push only after verification. Do not change GitHub visibility.

**Step 4: Remove the merged worktree and branch**

After push and final remote verification, remove `.worktrees/public-release-framing` and delete
only the merged `codex/public-release-framing` branch.

**Step 5: Report the prepared public candidate**

Report the exact commit, publishable/excluded boundary, verification, advisory large-file findings,
and that visibility remains private pending a separate explicit command.
