# M8 Gate 2 — Fast common transition

**Decision:** No-go on 2026-08-25. Do not proceed to the fact-DAG schema yet.

## What was implemented

The M8 common-transition path now evaluates the retained Pareto rejection frontier before launching
exact remnant placement searches. It uses the fast path only when every inventory remnant is proven
to reject every candidate before translation generation. The resulting standard-only catalog
preserves the authoritative action, policy context, event identity, fit-search counts, inventory,
and accounting exactly. Empty-inventory events use the same exact standard-only path.

Prepared proof contexts compile the frozen standard winner and its exact standard profiles once.
Development differential mode recomputes the old authoritative transition and fails on the first
mismatch. Area-only or otherwise uncertain scalar results fall back to the unchanged authoritative
catalog; uncertainty is never promoted to rejection.

The profiler now records frontier-rejected transitions, standard-only materializations,
full-authoritative fallbacks, and differential mismatches separately.

## Gate 2 evidence

Both probes used calibration seed `2026082300`, two events, the unchanged frozen M7 baseline and
Jagua/Shapely boundary, and no evaluation access.

| Probe | Old common CPU | New common CPU | Speedup | Frontier fast paths | Full fallbacks | Semantic result |
|---|---:|---:|---:|---:|---:|---|
| `no_signal` | 108.969059 s | 0.258885 s | 420.916851x | 2/2 | 0/2 | 428/428 valid; reference equal |
| `regime_shift` | 1883.890537 s | 1879.281241 s | 1.002453x | 0/2 | 2/2 | 459/459 valid; reference equal |

The no-signal arm clears the required 10x heavy-path improvement with zero mismatch. The
regime-shift arm does not: every common transition contains at least one scalar survivor, so the
implementation correctly invokes the full authoritative search. Its total process time was
3122.698090 seconds versus 3120.847244 seconds before the change, confirming that Gate 2 did not
move materially on the representative hard case.

The local ignored evidence files and their SHA-256 hashes are:

- `yf/var/experiments/m8-factored-profiles/no_signal.json` —
  `79bfe2873c1da5a351cbb396d8e03a9e67bcc700fae06865bcff06de1a3400a0`
- `yf/var/experiments/m8-factored-profiles/no_signal-fast2.json` —
  `b83ad3ec6cf30b0935e5fc72312d2623dd542797b04f5eeb7773a72ac7eb8646`
- `yf/var/experiments/m8-factored-profiles/regime_shift.json` —
  `58dbfad2f262a99b0fe6c5351920bf62b0849b5ff5b9d01462eaa0301a5e2ec6`
- `yf/var/experiments/m8-factored-profiles/regime_shift-fast2.json` —
  `2a46d8befa493755be4604115cd9ab12dd2be2f9bf0277e249f2ffb544a85330`

## Semantic diagnosis

The frontier solves complete no-fit events, but it cannot decide a survivor. In `regime_shift`, at
least one candidate passes the necessary material/area/width/height inequalities for every common
transition. Deciding whether that candidate really fits still requires the registered exact
translation/collision search, and deciding the frozen policy winner still requires its exact action
evidence. The expensive work is therefore not redundant candidate rejection in this arm; it is the
survivor proof itself.

This is a coverage boundary of the current mathematics, not a collision-backend failure and not a
semantic mismatch.

## Next bounded redesign

Before adding the v2 fact schema, derive and test a survivor-specific policy-dominance bound. It
must compare an optimistic lower bound for every possible remnant action against the exact compiled
standard winner under the frozen policy. Only a complete bound may skip exact search. If the bound
cannot classify the hard calibration cases, the next alternative is to retain exact survivor
witnesses at their authoritative M7 production boundary and prove safe reuse, then remeasure the
same two probes.

Gate 2 remains unchanged: zero semantic mismatches, at least 10x common-path improvement on both
representative probes, and a low enough authoritative fallback rate for the official 20x/seven-day
gate to remain plausible.

This is calibration software evidence only. It is not an oracle-advantage, material-savings,
physical-feasibility, production-readiness, buyer-demand, or commercial result.
