# M8 — Rollout oracle

**Status:** Prepared — exact full-horizon design frozen; implementation not started

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

The approved design uses the complete remaining registered stream for every current action, frozen
M7 for each hypothetical continuation, the exact common action set, the M7 action as the tie-preferred
fallback, M0 scrap-only terminal treatment, and strict paired failure handling. Naive branch-by-branch
replay is operationally rejected. The primary engine compiles inventory-independent M7 standard
winners, evaluates exact inventory deltas, skips only intervals covered by complete no-fit
certificates, and uses registered exact search for every survivor.

The implementation plan begins by extracting an arbitrary-state M7 transition seam and proving that
published M7 identities do not change. A slow full-replay reference then audits the sparse engine on
toys and registered calibration prefixes. Zero mismatch, at least 20x speedup, and a conservative
held-out projection no greater than seven days are hard gates before persistent caching, the
six-stream pilot, or M8 evaluation.

See [[plans/2026-08-24-m8-rollout-oracle-design]] and
[[plans/2026-08-24-m8-rollout-oracle-implementation]].
