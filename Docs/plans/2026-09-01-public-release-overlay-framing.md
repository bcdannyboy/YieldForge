# Public Release Overlay Framing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prepare a coherent private release candidate that leads with YieldForge's workflow-preserving overlay thesis and reflects the owner's approval of all retained publication content.

**Architecture:** Treat the incumbent nesting system as the candidate generator and YieldForge as a
future-aware selection layer over its strong alternative nests. Align every tracked publication
statement with the owner-approved boundary, keep factual source provenance, retain raw local
evidence outside Git, and leave remote visibility private.

**Tech Stack:** Markdown/Obsidian, Git, Python 3.12, pytest, Ruff, Hatchling, GitHub CLI.

---

### Task 1: Verify the target semantic boundary

**Files:** none

**Step 1: Confirm the workflow-overlay idea is explicit**

Run:

```bash
rg -n -i "keep.*existing|existing.*workflow|progressive.*nest|strong.*space|selection layer" \
  README.md Docs/Home.md
```

Expected: both entry points lead with keeping the incumbent nesting system and workflow, explain
selection over strong alternative nests, and identify Sparrow's progressive reports as the reason
it was a practical first substrate.

**Step 2: Confirm the release-candidate assertions**

Run:

```bash
rg -n -i "owner-approved|approved publication boundary|visibility remains private" \
  PUBLIC_RELEASE.md README.md Docs/Home.md
rg -n -i "attribution|transformation provenance" \
  THIRD_PARTY_NOTICES.md Docs/Research/Technical\ Sources.md
```

Expected: the entry points and release record describe the approved tracked boundary and separate
visibility action, while the source notes preserve attribution and transformation provenance.

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

### Task 3: Record the owner-approved publication boundary

**Files:**

- Modify: `PUBLIC_RELEASE.md`
- Modify: `THIRD_PARTY_NOTICES.md`
- Modify: `README.md`
- Modify: `Docs/Home.md`
- Modify: `Docs/Research/Technical Sources.md`
- Modify: `Docs/Development/Artifact Policy.md`
- Modify: `Docs/Evidence/M11 - Economic resolution.md`
- Modify: `Docs/Milestones/Milestone Roadmap.md`
- Modify: `Docs/plans/2026-09-01-existing-workflow-overlay-framing-design.md`
- Modify: `Docs/plans/2026-09-01-public-release-overlay-framing.md`
- Modify: `Docs/plans/2026-09-01-repository-closeout-design.md`
- Modify: `Docs/plans/2026-09-01-repository-closeout.md`

**Step 1: Turn `PUBLIC_RELEASE.md` into a release-candidate record**

Record these resolved owner decisions:

- the tracked proposal, diagrams, source-derived research material, project name, and existing Git
  author metadata are approved for publication;
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

**Step 3: Align every current-tree reference**

Update the remaining listed files so they link to the prepared release boundary, describe
separately preserved raw parents as a reproducibility limitation, and consistently reflect the
resolved owner decisions. In the earlier closeout plans, mark the later publication-boundary
amendment and preserve the experimental outcomes and artifact identities.

**Step 4: Verify the publication-boundary assertions**

Run the positive checks from Task 1 across the release record, entry points, source notices,
artifact policy, M11 report, roadmap, and amended closeout plans. Then ask an independent reviewer
to compare every tracked publication statement with the approved included/excluded boundary.

Expected: the reviewer finds no contradiction, unresolved owner decision, or claim beyond the
approved research-archive boundary.

**Step 5: Commit**

```bash
git add PUBLIC_RELEASE.md THIRD_PARTY_NOTICES.md README.md Docs/Home.md \
  "Docs/Milestones/Milestone Roadmap.md" \
  "Docs/Research/Technical Sources.md" "Docs/Development/Artifact Policy.md" \
  "Docs/Evidence/M11 - Economic resolution.md" \
  Docs/plans/2026-09-01-existing-workflow-overlay-framing-design.md \
  Docs/plans/2026-09-01-public-release-overlay-framing.md \
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

Validate all tracked Markdown inline links and wikilinks. Run `git diff --check`, macOS and Unix
absolute home-path scans, high-confidence credential/private-key scans, tracked-object size
inventory, the positive publication-boundary checks, and `git status`. Have an independent reviewer
inspect the tracked publication statements for consistency with the resolved owner decisions.

Expected: links resolve; the publication statements are consistent; there is no operational local
path, secret, or object at/above GitHub's 100 MiB hard limit; worktree clean.

### Task 5: Integrate the prepared candidate without publishing it

**Files:** none

**Step 1: Confirm private remote freshness and ancestry**

Fetch `origin`, confirm remote `main` is the branch base, and confirm GitHub visibility is still
private.

**Step 2: Fast-forward `main` and run affected checks again**

Fast-forward local `main` to `codex/public-release-framing`. Re-run the positive
publication-boundary review, link/path/secret scans, focused 7 tests, Ruff checks,
source-distribution inspection, and clean status checks at the exact merged commit.

**Step 3: Push private `main`**

Push only after verification. Do not change GitHub visibility.

**Step 4: Remove the merged worktree and branch**

After push and final remote verification, remove `.worktrees/public-release-framing` and delete
only the merged `codex/public-release-framing` branch.

**Step 5: Report the prepared public candidate**

Report the exact commit, publishable/excluded boundary, verification, advisory large-file findings,
and that visibility remains private pending a separate explicit command.
