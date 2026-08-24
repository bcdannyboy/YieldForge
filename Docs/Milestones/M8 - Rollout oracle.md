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
fallback, M0 scrap-only terminal treatment, and strict paired failure handling. Only content-addressed
exact caches and byte-equivalent state coalescing may accelerate the primary arm.

The implementation plan begins by extracting an arbitrary-state M7 transition seam and proving that
published M7 identities do not change. It then adds persistent exact caching, isolated full and
known-only visibility providers, hand-computed acceptance cases, and a six-stream calibration-only
runtime pilot. M8 evaluation remains closed until that pilot supports a frozen exact execution
manifest.

See [[plans/2026-08-24-m8-rollout-oracle-design]] and
[[plans/2026-08-24-m8-rollout-oracle-implementation]].
