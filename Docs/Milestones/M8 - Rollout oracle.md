# M8 — Rollout oracle

**Status:** Ready for planning — M7 freeze and evaluation evidence are available

M8 measures the value of knowing the realized future when choosing today's action. For each current candidate, the oracle executes it virtually and replays the remainder under the frozen strong baseline.

> **Question:** Does future demand information change today's best nesting decision enough to matter?

## Acceptance boundary

Hand-computed toy cases, information-isolation tests, paired candidate checks, and no-signal sanity cases pass.

Rollout is not a mathematical upper bound and may miss value requiring coordinated future decisions.

## Entry state

- Frozen baseline: `yfm7freeze-5c13c3fe531828d8cd986c39`.
- Baseline evaluation: `yfm7eval-f2cb310c4b7e879d119e8f94`, 36 streams and 864 events reproduced
  twice with identical content.
- The rollout arm must receive the same candidate/action evidence while future access remains
  isolated from the baseline arm.
- No oracle advantage, paired savings estimate, or M8 implementation exists yet.

## Planning boundary

Specify the rollout horizon, terminal baseline behavior, candidate parity, cache identity, failure
treatment, paired estimand, and compute budget before implementation. Include hand-computed toy
cases, future-information isolation tests, and no-signal sanity cases as executable acceptance gates.
