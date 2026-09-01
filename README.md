# YieldForge

> **Status: paused research archive.** YieldForge tested a specific material-nesting hypothesis
> thoroughly enough to decide against further investment in the current modeled system. The later
> M11 test found no known-only savings in either tested segment and only `0.536368330506%` mean
> savings from perfect future information in one segment. That is not enough to justify rebuilding
> the nesting layer, integrating a factory system, or productizing the idea.

This is a research record, not a production application or a claim that future-aware nesting can
never work. It preserves the idea, the tests that progressively reduced uncertainty, the negative
result, and the evidence that would be needed to reopen the hypothesis.

The repository is **not currently cleared for public visibility**. Publication still requires the
rights and owner decisions listed in [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md), and no project license
has been selected.

## The idea

Irregular 2D nesting normally rewards the layout that uses a sheet most efficiently for the job in
front of it. YieldForge asked whether that is sometimes shortsighted. Two layouts can use almost the
same area now but leave differently shaped remnants; one residual shape may fit a later order while
the other may have little reuse value.

The product hypothesis was that a system could generate several strong, near-tied nests, value the
future usefulness of their exact residual geometry, and choose the layout that lowers total
purchased-material cost across a sequence of jobs. A useful system would eventually have to make
that choice from information actually known at the time, reconcile remnant inventory and costs,
and beat a strong conventional policy often enough to pay for itself.

## Why test with perfect future information

Perfect future information was an intentionally favorable upper-bound test, not a proposed product
feature. Giving the research policy every later order removes forecasting error and asks a simpler
question first: **does this residual-selection mechanism contain enough value even with an unfair
informational advantage?**

This was an information upper bound for the tested executor, not a proof of globally optimal
material cost. The search checks were deliberately finite and bounded.

If the full-future arm cannot produce a material, repeatable advantage within the same candidate,
inventory, and cost model, there is no evidence that adding forecasts and production integration to
the same mechanism will create the missing headroom. A positive result would still have required a
deployable known-only policy and real factory evidence; a weak result lets us stop before making
those larger investments.

Sparrow supplied the underlying nesting capability through a reproducible Spyrrow adapter and
immutable candidate archives. Replacing or substantially rebuilding that layer would be a new
engineering investment, not a prerequisite for interpreting the completed experiment.

## How we tested it

The work advanced through progressively more expensive falsification stages. Each stage had a
bounded claim: technical feasibility was never treated as economic or commercial proof.

| Milestones | Question tested | What the stage established |
|---|---|---|
| M0 | What counts as savings or failure? | Froze information boundaries, net-cost accounting, candidate parity, statistical reporting, and decision thresholds before confirmatory outcomes. |
| M1–M2 | Can every component speak the same language and reproduce candidate nests? | Built the canonical model and a deterministic, content-addressed Sparrow/Spyrrow candidate boundary. |
| M3–M4 | Do near-tied nests actually leave different useful geometry? | Reconciled exact residuals, found exact differences in 202 of 203 tested pairs, and found one mechanically valid later reuse witness. |
| M5–M7 | Can choices be replayed through time against a credible baseline? | Built deterministic inventory replay, controlled temporal streams, and selected the M7 baseline from five registered policies on separate calibration streams. |
| M8–M9 | Is the oracle/search machinery trustworthy enough to inform a decision? | M8 exposed a formal performance hold. M9 found one one-step search counterexample, then a bounded two-ply repair selected the exact-optimal first action in all 45 registered finite cases. Neither result established universal optimality or savings. |
| M10 | Had the virtual program earned more investment? | Stopped productization and further virtual-oracle work and recommended real manufacturer evidence if work continued. M10's economic band was `not_computed`; it did **not** produce the later 0.54% result. |
| M11 | Does a repaired, cost-complete test retain the hypothesis? | Selected its baseline from six registered policies on separate calibration streams, then ran paired baseline/full-future/known-only comparisons on 40 held-out streams across Lectra and LOCo-derived segments. It resolved the current modeled product and algorithms as economically insufficient. |

The first M11 attempt invalidated itself on reuse-accounting reconciliation and evidence-readback
rules before producing an economic result. That failure remains in the audit trail. The numbers
below come only from the later, separately bound repair-lineage test after those non-economic
execution and evidence defects were corrected.

The M11 chronology and economics were generated or assumed around source-observed geometry and
source demand. The result is therefore a semi-synthetic disposition of the modeled hypothesis, not
a measurement of representative factory ROI.

## What the final test found

Each held-out stream used the same released jobs, candidate catalog, event sequence, inventory
rules, and cost ledger for three arms:

- **B — baseline:** the frozen `age_regularity` policy, using only released information;
- **F — full future:** the tested YieldForge executor with complete future visibility; and
- **K — known only:** the tested YieldForge executor using only information available when each
  decision was made.

| Segment | Arm | Mean savings vs. B | 95% interval | Median | Positive streams | Result |
|---|---|---:|---:|---:|---:|---|
| LOCo 2D-ICS | B | Reference | — | — | — | Frozen baseline |
| LOCo 2D-ICS | F | `0%` | `[0%, 0%]` | `0%` | 0/20 | Red |
| LOCo 2D-ICS | K | `0%` | `[0%, 0%]` | `0%` | 0/20 | Red |
| Lectra M3/M4 | B | Reference | — | — | — | Frozen baseline |
| Lectra M3/M4 | F | `0.536368330506%` | `[0%, 1.098250947619%]` | `0%` | 3/20 | Red |
| Lectra M3/M4 | K | `0%` | `[0%, 0%]` | `0%` | 0/20 | Red |

The preregistered F gate required at least `2.5%` mean savings. The deployable K gate required at
least `1.5%`. Both also required a 95% lower confidence bound above zero, a positive median, and
savings on more than half of streams. Every segment/arm failed every applicable magnitude and
reliability gate.

The terminal M11 disposition was `INSUFFICIENT_CURRENT_MODELED_VALUE`: no bounded pilot and no
productization were authorized.

## Why this was not just a sample-size problem

The decision did not rest on a single p-value or an imprecise mean.

1. **The measured effect was far below the precommitted bar.** Even the upper end of Lectra F's
   interval, `1.098250947619%`, was below the `2.5%` full-future threshold. LOCo F was exactly zero.
2. **The effect was not reliable across streams.** Lectra F had a zero median and helped in only
   3/20 streams, far from the required positive majority. The lower confidence bound was zero.
3. **The deployable mechanism produced no measured effect.** K matched B in all 40 streams across
   both segments, so its mean, interval, median, and positive-stream count were all zero.
4. **Magnitude and prevalence were separate gates.** More same-distribution streams can narrow
   uncertainty or reveal rare cases, but they cannot create a missing known-only decision
   mechanism. A larger sample would change this decision only by exhibiting a materially different
   effect and prevalence pattern; the current evidence provides no such signal.

This evidence falsifies the **current modeled product and tested algorithms**. It does not prove
that every future algorithm, material segment, or factory would behave the same way. A materially
better algorithm or different population would be a new hypothesis requiring a new prospective
test, not a sample-size reinterpretation of this one.

## What we learned

### Direct findings

- Near-tied nests often did leave measurably different exact residual geometry: 202/203 M3 pairs
  differed.
- At least one exact M4 remnant could satisfy a later source-shape role in the declared toy state.
  That proves the modeled reuse mechanism can occur, not how frequently it occurs or whether it
  saves money in production.
- Deterministic chronology, inventory conservation, cost reconciliation, baseline selection, and
  bounded search checks were all needed before geometry differences could be interpreted
  economically.
- In the final cost-complete test, those geometric possibilities did not translate into any
  measured known-only savings. Full-future value was absent on LOCo and small and sparse on Lectra.

### Bounded inferences

- **Mechanism versus value:** the geometry mechanism is real within the model, but a possible reuse
  event is not the same as frequent, reliable, deployable economic value.
- **Possible segment dependence:** the Lectra-only full-future signal is consistent with narrow
  forecast headroom or segment dependence. With a zero median, only 3/20 positive streams, and no K
  effect, it is not evidence for a product or even for a pilot.
- **Value of the falsification sequence:** the upper-bound test gave a rational stopping point
  before a Sparrow replacement, factory integration, or forecasting program. That avoided
  committing to a much larger build on the strength of geometry demonstrations alone; it is a
  decision implication, not a measured return on research spending.

## Why the project is on the back burner

The current deployable policy saved exactly `0%` in the model, while even perfect hindsight missed
the full-future magnitude and reliability gates. Rebuilding Sparrow specifically for YieldForge
would be a substantial new investment with no evidence that solver replacement is the bottleneck
between the current result and deployable value. The prudent action is to preserve the work and use
the result, rather than continue optimizing the experiment until it passes.

## What would justify reopening it

Reopening should begin with materially new evidence, not more versions of the same test. A credible
trigger would be at least one of:

- a new **known-only** decision mechanism that can plausibly change choices, exceed `1.5%` mean
  savings without future information, and save on most streams;
- a materially different segment with a concrete reason for higher remnant reuse frequency or
  value; or
- permissioned factory chronology, remnant history, material identities, and economics owned by a
  practitioner or buyer.

That trigger should face a prospectively frozen comparison against a current strong baseline, with
separate calibration and held-out streams and the same positive lower-bound, median, and majority
prevalence requirements. A retained result should then replicate in a second segment before a
bounded operator-owned pilot is considered.

Do not start by rebuilding Sparrow or adding more similar streams. First demonstrate why a new
mechanism or population should change the known-only economic result.

## Evidence and reproducibility

The primary closeout record is [M11 — Economic resolution](Docs/Evidence/M11%20-%20Economic%20resolution.md).
It preserves the B/F/K definitions, exact results, thresholds, provenance, artifact identities, and
claim ceiling. M10 remains separately preserved in
[M10 — Experiment and verdict](Docs/Milestones/M10%20-%20Experiment%20and%20verdict.md) so its
historical `not_computed` economic status is not rewritten after the fact.

The repository tracks three authenticated, non-reconstructive M11 manifests under
[`yf/experiments/results/m11-economic-resolution/`](yf/experiments/results/m11-economic-resolution/).
A clean clone can inspect and authenticate those selected records. It cannot rerun the independent
notebook's complete raw reconciliation without the separately preserved, permissioned source-bound
parents and raw sidecars. See the [Artifact Policy](Docs/Development/Artifact%20Policy.md) for the
tracked/private boundary and [Third-party notices](THIRD_PARTY_NOTICES.md) for source attribution
and redistribution limits.

## Repository guide and local verification

- [`Docs/`](Docs/Home.md) is the Obsidian-compatible research notebook and evidence map.
- [`yf/src/`](yf/src/) contains the Python package and experiment machinery.
- [`yf/tests/`](yf/tests/) contains policy, model, replay, and evidence tests.
- [`yf/notebooks/`](yf/notebooks/) contains independent analysis notebooks.
- [`yf/web/`](yf/web/) is a local research workbench, not a hardened hosted application.

Basic source verification starts with:

```bash
cd yf
uv sync --locked --all-groups
uv run --all-groups pytest
uv run --all-groups ruff check .
uv run --all-groups ruff format --check .

cd web
npm ci
npm test
npm run build
```

Some full evidence replays and source-bound checks require the separately permissioned local packet
or documented local services. See [Getting Started](Docs/Development/Getting%20Started.md) for the
current environment and verification details.

## Public-release and name status

Repository consolidation and publication are different decisions. The present history includes
LOCo-derived material that is not cleared for redistribution, and the selected M11 manifests'
non-reconstructive character does not grant publication rights. Before visibility changes, the
owner must resolve LOCo permission or sanitized history, proposal and diagram ownership, Git author
identity, the intended release history, and the repository name. See
[PUBLIC_RELEASE.md](PUBLIC_RELEASE.md) for the full gate.

No license has been chosen for YieldForge's own code or documentation. “YieldForge” is a
provisional working name; naming, trademark, and market-confusion checks have not been completed.
