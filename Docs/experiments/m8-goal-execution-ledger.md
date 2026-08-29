# M8 Goal Execution Ledger

This ledger records the task, verification, evidence, and authorization state for
`Docs/plans/2026-08-28-m8-pass-goal-execution-spec.md`. Historical artifacts remain immutable.
Only one row may be `in_progress`; a component result never authorizes a later gate by implication.

## Execution identity

| Field | Value |
|---|---|
| Worktree | `/Users/danielbloom/Desktop/YieldForge/.worktrees/m6-temporal-benchmark` |
| Branch | `codex/m8-rollout-preparation` |
| Accepted source base | `47c1eaf` |
| Committed execution specification | `6b8453706641f7ffac4e8b56c6ce0ca91683a8f9` |
| Specification SHA-256 | `89fc4604075ad0a6f943c54ec21d1c488497e69ad6671b80b8637ee85d83d0c6` |
| Goal boundary | Official technical M8 pass through G10; evaluation remains sealed |

`SELF` in a completion-commit field means the ledger-only Git commit containing that row. This is
used because a commit cannot contain its own hash; the value is resolved unambiguously from history.

## Task state

| Task | Status | Start commit | Completion commit | Authorized successor |
|---|---|---|---|---|
| G0.1 preserve and inventory inherited Task-2 draft | completed | `8278e6e01101d2b3eae4fe25ffe31b1040931a19` | `SELF` | G0.2 integrity audit and repairs |
| G0.2 repair known integrity blockers | completed | `f09c2253e995d4d429ee96a45bf23902dba37b2d` | `2c4d723eebde183eebbfeae97521522413315584` | G0.3 independent acceptance |
| G0.3 independently accept and commit Task 2 | completed | `f09c2253e995d4d429ee96a45bf23902dba37b2d` | `2c4d723eebde183eebbfeae97521522413315584` | G0.4 hardened publisher |
| G0.4 generalize and seal hardened publisher | in_progress | `2c4d723eebde183eebbfeae97521522413315584` | — | G0.5 compute lease |

## G0.1 — Preserve and inventory inherited Task-2 draft

### Scope and ownership

- Recorded at `2026-08-28T21:42:09-0700`.
- Ledger-only owned path: `Docs/experiments/m8-goal-execution-ledger.md`.
- No source, test, dependency, artifact, or evaluation input was edited.
- Existing isolated worktree is registered by Git and parent `.worktrees/` is ignored by
  `/Users/danielbloom/Desktop/YieldForge/.gitignore`.

### Observed evidence

- `HEAD`: `8278e6e01101d2b3eae4fe25ffe31b1040931a19`.
- Branch: `codex/m8-rollout-preparation`.
- Pre-ledger index: empty.
- `git diff --check`: pass with no output.
- Exact inherited modified paths:
  - `yf/src/yieldforge/oracle/certificates.py`
  - `yf/src/yieldforge/oracle/compiled.py`
  - `yf/src/yieldforge/oracle/prepared.py`
  - `yf/tests/oracle/test_compiled.py`
  - `yf/tests/oracle/test_fact_capture.py`
- Diff stat: five files, 682 insertions, zero deletions.

### Generated recovery evidence

- Recovery patch: `/tmp/yieldforge-m8-task2-47c1eaf.patch`.
- Patch SHA-256: `40ce44c454a1e3b1fab8d917b8dd4645099876c42ecc4b99c82e6895577524f1`.
- Patch size: 31,338 bytes / 772 lines.
- The patch is a recovery aid, not accepted implementation or gate evidence.

### TDD, verification, and review status

- RED/GREEN: inherited-work exception; the draft's original RED/GREEN history is unavailable and is
  not inferred from its presence.
- Focused/regression/full-suite/lint/native tests: not run in G0.1 because this task made no behavior
  change; G0.2/G0.3 must independently test the draft and every new repair.
- Specification review: the committed plan was independently approved by three read-only reviewers
  at SHA-256 `df5eb3a9583a99b90f78361e05ac97015aad2d69d8d4ee1390ecfaa9afd3b79b`.
- Code-quality review: not applicable to this ledger-only preservation task.

### Classification and decision

- Observed: Git/worktree identity, five-path status, diff counts, empty index, and clean diff check.
- Derived: five-file/682-insertion summary from Git's authoritative diff.
- Generated: recovery patch and this ledger row.
- Assumed: none.
- Decision: expected inherited state confirmed and recoverably sealed; authorize G0.2 only. This does
  not accept Task 2, pass C0, or open evaluation.

## G0.2/G0.3 — Repair and independently accept Task 2

### Scope and ownership

- Completed at `2026-08-29T05:24:58-07:00` in source commit
  `2c4d723eebde183eebbfeae97521522413315584`.
- The accepted allowlist contains exactly 19 paths: ten oracle source/package paths and nine oracle
  test paths recorded in the committed specification.
- Specification-only allowlist amendments were committed separately as `ab9aa22`, `c8246b1`,
  `79dc435`, `7082c39`, `8c84cbf`, and `6b84537`; none were mixed into the source commit.
- No dependency, lockfile, native binary, timing threshold, evaluation input, or machine-evidence
  artifact changed.

### Fresh RED/GREEN evidence

- Inherited Task-2 code retains the G0.1 inherited-work exception; every repair discovered during
  acceptance received a fresh public or production-path regression.
- Typed integrity now distinguishes malformed identity, ordering, membership, capability, registry,
  runtime, cursor, request, and prepared-evidence drift from legitimately incomplete `unsupported`
  rows, without hidden fallback or pre-rejection cursor mutation.
- Public common-transition validation now physically attests and detaches the complete submitted
  nested graph. Three adversarial action, cursor/remnant, and policy-scalar cases reject before
  snapshot/proof authority, fallback, capability issuance, cursor mutation, or registry change.
- Public unchecked bundle visibility is one-shot before output construction. The reproduced
  frame-scanning late bundle swap changed the requested freeze on the RED bytes; the GREEN path
  records one callback and zero late hits. A callback-free final guard separately rejects late
  request storage, oracle storage, freeze-claim, and cursor-state drift.
- Public reference request/action capture rejects hostile action identifiers, visibility leakage,
  truncated full-realized suffixes, and request/cursor/runtime drift before authority. The package
  facade now exports `score_reference_actions` consistently with the reference module.
- Prepared C0 row authority, capability issuance, registry lifecycle, complete multi-item
  eligibility, frontier audit detachment, checker proof/source binding, and portable initial-cursor
  fidelity are exercised by production callers and mutation regressions.

### Root verification

- Frozen acceptance partition 1: `662 passed in 67.67s` for compiled, fact capture, fact checker,
  factored generator, certificates, and frontier suites.
- Frozen acceptance partition 2: `138 passed in 11.52s` for sparse, checker, reference, and M8
  profiling suites.
- Exact Ruff allowlist: pass with `All checks passed!`.
- `git diff --check`: pass with no output.
- Exact accepted unstaged and staged binary diff SHA-256:
  `f8f8ea2ad1239ed859784f5f86ed485fb0c54745087d0660570324d89f84dd0c`.
- The staged path list matched the 19-path amended allowlist exactly; the committed stat is 21,283
  insertions and 1,045 deletions.

### Independent review

- Specification/adversarial review: PASS on exact diff
  `f8f8ea2ad1239ed859784f5f86ed485fb0c54745087d0660570324d89f84dd0c`; 37 focused adversarial
  checks plus manual replay of the prior late-output swap found no reproducible P0/P1.
- Separate pass-gate/code-quality review: PASS on the same exact diff and base `6b84537`; the prior
  nested common-fact exploit, one-shot visibility boundary, strict visibility modes, callback-free
  source guard, and package facade all held with no reproducible P0/P1.
- A separate reference-facade slice also passed 58/58 reference tests on the accepted bytes.

### Classification and decision

- Observed: exact Git diff/index hashes, test/lint output, staged allowlist, reviewer verdicts, and
  committed source identity.
- Derived: path/source-test counts and insertion/deletion totals from Git.
- Generated: regression code, implementation repairs, specification amendments, and this ledger
  record.
- Assumed: none.
- Decision: G0 Task-2 integrity acceptance passes and authorizes G0.4 implementation only. This is
  not a C0 performance pass, Phase-C authorization, savings result, physical validation, buyer
  evidence, commercial evidence, or permission to open evaluation.
