# M8 Gate 2 — Fast common transition

**Decision:** Pass on 2026-08-26. The bounded fact-DAG design may proceed; evaluation remains
sealed and the official M8 `20x`/seven-day gate has not been rerun.

## What was implemented

The M8 common-transition path first evaluates the retained Pareto rejection frontier. Proven
zero-generation rejects use an exact standard-only or mixed catalog. A new mixed catalog searches
only unresolved inventory while restoring the omitted zero-generation query counts, so its result
remains exactly equal to the frozen lazy M7 catalog.

The mixed catalog alone had zero coverage on `regime_shift`: both transitions still had one
inventory remnant that survived the zero-generation frontier. A collision-free diagnostic then
showed the actual boundary. All 459 candidate searches were independently impossible under the
registered scalar geometry rules, but the frozen M7 search would still generate translation
candidates before returning no witness. The expensive collision result was known; the exact
generated, duplicate, evaluated, and truncation counts were missing from the event identity.

The final path therefore:

1. independently proves every candidate no-fit with the registered scalar certificate;
2. asks the content-bound frozen Jagua binary only to enumerate the registered translation batches;
3. independently reconstructs the registered candidate-source sequence and audits Jagua's generated,
   duplicate, evaluated, and truncation counts in forked workers;
4. ignores Jagua's collision classifications and synthesizes exact no-witness search records only
   after that audit passes;
5. seeds those records into a fresh runtime and materializes the unchanged M7 catalog; and
6. fails closed to Python generation or authoritative exact search when representation or proof
   coverage is incomplete.

Jagua supplies bookkeeping, not the no-fit conclusion. The profiler separately records mixed
pruning, counted-no-fit transitions, exact survivors, fallbacks, and differential mismatches. A
separate authoritative replay must also match the optimized common fact's event position, event ID,
and content hash before the profile can publish.

## Gate 2 evidence

Both probes used calibration seed `2026082300`, two events, the unchanged frozen M7 baseline and
Jagua/Shapely boundary, and no evaluation access.

| Probe | Frozen common wall | Final common wall | Speedup | Fast classification | Full fallbacks | Semantic result |
|---|---:|---:|---:|---|---:|---|
| `no_signal` | 109.597188 s | 0.278631 s | 393.341866x | 2 frontier derivations | 0/2 | 428/428 valid; fact/reference equal |
| `regime_shift` | 1927.855596 s | 145.435589 s | 13.255735x | 2 counted-no-fit derivations / 918 searches | 0/2 | 459/459 valid; fact/reference equal |

Both arms now clear the required `10x` common-path improvement with zero checker failure, reference
equality, exact common-fact identity, zero authoritative fallback, and no evaluation access. Wall
time is the gate metric because it charges the four independent audit workers and Jagua child
processes; the earlier parent-process CPU result was invalidated during review. The hard audit's
three independent count reconstructions each took about 29–31 seconds. Full-profile elapsed time
also includes an unoptimized authoritative differential replay and brute reference, so it is not the
Gate 2 metric and is not evidence of the official M8 throughput target.

The exact mixed-pruning intermediate probe remained a useful no-go: it classified zero hard-arm
inventory items, fell back 2/2 times, and took `3146.917652` process-seconds. The counted-no-fit
redesign, rather than mixed pruning itself, produced the hard-arm result.

The local ignored evidence files and their SHA-256 hashes are:

- `yf/var/experiments/m8-factored-profiles/no_signal.json` —
  `79bfe2873c1da5a351cbb396d8e03a9e67bcc700fae06865bcff06de1a3400a0`
- `yf/var/experiments/m8-factored-profiles/no_signal-fast2.json` —
  `b83ad3ec6cf30b0935e5fc72312d2623dd542797b04f5eeb7773a72ac7eb8646`
- `yf/var/experiments/m8-factored-profiles/regime_shift.json` —
  `58dbfad2f262a99b0fe6c5351920bf62b0849b5ff5b9d01462eaa0301a5e2ec6`
- `yf/var/experiments/m8-factored-profiles/regime_shift-fast2.json` —
  `2a46d8befa493755be4604115cd9ab12dd2be2f9bf0277e249f2ffb544a85330`
- `yf/var/experiments/m8-factored-profiles/regime_shift-mixed1.json` —
  `11ede147c96dc5cbaf8a9374ec978a22f33739a0038a88066d84adf18b81b189`
- `yf/var/experiments/m8-factored-profiles/no_signal-counted-audited1.json` —
  `60442e21b38f75a70fa9d2972b9f1ff23bc2916c62c5d10d2065ea2fa1a1a8a1`
- `yf/var/experiments/m8-factored-profiles/regime_shift-counted-audited1.json` —
  `ef7b4b759f9a9b610e9d77b8a2fbeda1f9c517a82c91fd4e6a26c3dd4083e376`

The compact committed decision record is
`yf/experiments/results/m8-gate2-counted-no-fit-evidence-v1.json`. The earlier `counted1` profiles
are intentionally excluded: review showed that they neither independently audited Jagua's counts
nor charged child-process work to the gate metric.

## Semantic diagnosis

The original retained frontier answered a stricter question: whether M7 would reject before
translation generation. It could not classify the hard remnant because M7 still owed diagnostic
translation counts. Per-candidate scalar certification answered the decision question instead:
every hard-arm candidate was no-fit, so no collision query could change the action set. A tested
convex-hull translation relaxation was also prepared, but the calibration diagnostic assigned zero
hard-arm candidates to it because scalar proof already covered all 459.

A Python-only attempt to reconstruct the frozen counts remained too slow and was stopped after more
than eight minutes without publishing an artifact. An initial Jagua-count path was fast but failed
review because its counts were not independently certified. The corrected path reconstructs the
source sequence independently and parallelizes only that audit. One isolated hard transition then
measured `73.766205` wall-seconds, including a `31.730960`-second independent audit. The official
profile measured the two generator/checker derivations together at `145.435589` seconds.

## Next bounded redesign

Gate 2 now permits the next bounded phase: define the v2 fact-DAG schema around the exact common
transition and its content-bound counted-no-fit evidence, then prove generator/checker reuse without
weakening independent checking. The fact DAG must be benchmarked on calibration before the official
six-cell gate is rerun. The official gate still requires the registered witness coverage, `20x`
sampled speedup, and seven-day projection; this Gate 2 pass does not waive any of them.

This is calibration software evidence only. It is not an oracle-advantage, material-savings,
physical-feasibility, production-readiness, buyer-demand, or commercial result.

## Subsequent Gate 3 result — 2026-08-27

The permitted v2 fixed-layer bundle was implemented and completed both unchanged Gate 3 probes.
It produced two byte-stable generations, passed the fresh checker on 887/887 roots, and used zero
exact fallback. The complete 12-action four-way audit then passed with zero mismatch, and all 16
executed mutations were rejected.

This closes the Gate 2 reuse question but not M8 performance. Complete decision
`yfm8g3decision-c13ec320e9fcd02873bf649c` returned `hold_performance`: the charged
`686.535011`-second portable pipeline projects to `113.434097` held-out days, requiring another
`22.686819x` reduction to meet the fixed five-day boundary. The next bounded redesign is therefore
algorithmic action-traversal/checker acceleration, not six-cell execution or evaluation access.
