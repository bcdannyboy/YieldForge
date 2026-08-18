# M9 — Search validation

**Status:** Later

M9 determines whether the scalable oracle is strong enough to interpret. Tiny streams are solved exhaustively, rollout is compared with the exact optimum, and beam search is added only if longer-horizon reoptimization could change the decision.

> **Question:** Is the measured opportunity genuine, or could our search procedure be hiding or distorting it?

## Acceptance boundary

Exact small-instance references are independently checked, approximation gaps are reported, and any scalable search is adequate under a declared compute budget.
