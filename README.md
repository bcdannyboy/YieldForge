# YieldForge

> **Status: paused research archive.** YieldForge tested a specific material-nesting hypothesis
> thoroughly enough to decide against further investment in the current modeled system. The later
> M11 test found no known-only savings in either tested segment and only `0.536368330506%` mean
> savings from perfect future information in one segment. That is not enough to justify
> productizing the tested selection layer or its factory integration.

This is a research record, not a production application or a claim that future-aware nesting can
never work. It preserves the idea, the tests that progressively reduced uncertainty, the negative
result, and the evidence that would be needed to reopen the hypothesis.

The repository is **not currently cleared for public visibility**. Publication still requires the
rights and owner decisions listed in [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md), and no project license
has been selected.

## The idea

YieldForge was designed around the existing nesting workflow. A manufacturer would keep its
incumbent nesting system, and that system would continue doing the hard work of producing
process-feasible nests that use the current sheet efficiently. YieldForge would sit after that
optimizer as a selection layer: instead of accepting only its first answer, it would compare
multiple strong, near-tied alternative nests and select among them using the future usefulness of
their exact residual geometry. Operators would still receive an ordinary nest through their
familiar workflow; they would not have to replace the core optimizer or adopt a separate way to
nest each job.

The hypothesis was that immediate space efficiency can be nearly tied while cumulative material
cost is not. Two layouts can use almost the same area now but leave differently shaped remnants;
one residual shape may fit a later order while the other may have little reuse value. If the
selection layer could recognize that difference from information available at decision time, it
might reduce purchased material across a sequence of jobs while preserving the incumbent's strong
single-job optimization.

We started with Sparrow because it was a strong immediate-space optimizer whose progressive
reports could be drained by the Spyrrow adapter into immutable archives of intermediate and
alternative nests. Sparrow was therefore a practical first candidate source, not the intended
replacement target. Applying the idea to another incumbent would require an adapter or equivalent
interface that exposes multiple strong candidates. That is real integration work, but it is
different from replacing the manufacturer's nesting workflow.

A useful system would still have to reconcile remnant inventory and costs, make selections from
information actually known at the time, and beat a strong conventional policy often enough to pay
for itself. The experiment tested one candidate-producing adapter; it did not prove arbitrary
vendor compatibility, live inventory synchronization, operator acceptance, or literally zero
integration effort.

## Why test with perfect future information

Perfect future information was an intentionally favorable-information stress test, not a proposed
product feature. Giving the research policy every later order removes forecasting error and asks a
simpler question first: **does this residual-selection mechanism show meaningful headroom even with
an unfair informational advantage?**

F was not proven to be a mathematical upper bound on K. They were separately executed, finite
policies, so complete future visibility did not guarantee that F would dominate K on every stream;
nor was either arm a proof of globally optimal material cost. The search checks were deliberately
finite and bounded.

A red F arm alone could not stop the program under the registered protocol. It showed that the
tested mechanism had little favorable-information headroom, while the separately required K arm
tested whether any value was deployable without future information. The terminal stop required F
and K to be red in both segments. An F-green/K-red result would have opened the registered forecast
branch; pilot or product investment would still have required later deployable confirmation and
real factory evidence.

Sparrow supplied the underlying nesting capability through a reproducible Spyrrow adapter. Spyrrow
drained Sparrow's progressive reports and exposed intermediate or alternative nests as immutable
candidate archives for the YieldForge selection layer. This was the one tested integration path;
the experiment did not establish equivalent access to arbitrary commercial nesting systems.

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
below come only from a later, separately preregistered rerun after correcting those non-economic
execution and evidence defects.

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
productization were authorized. That terminal decision depended on both F and K being red in both
segments; F by itself was diagnostic, not sufficient to stop.

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

The F evidence alone was not terminal. The stopping inference comes from its weak or absent
favorable-information signal together with K's exact zero across both segments, under a reducer
that required both arms to fail in both segments.

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
- The implementation produced deterministic replays and reconciled inventory and cost ledgers;
  baseline selection used separate calibration streams, and bounded search was checked against
  exact finite cases.
- In the final cost-complete test, those geometric possibilities did not translate into any
  measured known-only savings. Full-future value was absent on LOCo and small and sparse on Lectra.

### Bounded inferences

- **Mechanism versus value:** the geometry mechanism is real within the model, but a possible reuse
  event is not the same as frequent, reliable, deployable economic value.
- **Possible segment dependence:** the Lectra-only full-future signal is consistent with narrow
  forecast headroom or segment dependence. With a zero median, only 3/20 positive streams, and no K
  effect, it is not evidence for a product or even for a pilot.
- **Value of the falsification sequence:** the favorable-information test helped establish a
  rational stopping point, when combined with the known-only result, before broader incumbent
  adapters, live factory integration, or a forecasting program. That avoided committing to a much
  larger build on the strength of geometry demonstrations alone; it is a decision implication, not
  a measured return on research spending.

## Why the project is on the back burner

The current deployable policy saved exactly `0%` in the model, while even perfect hindsight missed
the full-future magnitude and reliability gates. The intended workflow advantage was that an
incumbent optimizer could remain in place, but even an overlay needs candidate access, inventory
integration, and a selection mechanism valuable enough to justify deployment. Rebuilding Sparrow
specifically for YieldForge—or developing additional incumbent adapters—would be substantial new
investment with no evidence that candidate generation or solver replacement is the bottleneck
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
mechanism or population should change the known-only economic result. If that evidence appears,
resume with the workflow-first architecture: keep a strong existing nesting system, expose its
progressive or otherwise available alternative nests through a bounded adapter, and prospectively
test the selection layer before investing in broader integration.

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

These focused checks are safe to run from a clean clone:

```bash
cd yf
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

cd web
npm ci
npm test
npm run typecheck
npm run build
```

The whole-tree Ruff lint check excludes only the content-addressed M11 audit notebook, whose bytes
must remain unchanged. The targeted format check is intentionally limited to the closeout-owned
policy tests. A pre-exclusion whole-tree format audit found 70 artifacts Ruff would rewrite: the
immutable notebook and 69 historical Python files. Neither was mechanically rewritten during
closeout, and a whole-tree format pass is not claimed.

The full Python suite is a local evidence verification, not a clean-clone command: it needs the
ignored pinned audit input and a compatible local PostgreSQL service, and its tight process-timeout
cases are run separately from the broad partition. Some raw evidence replays additionally require
the separately permissioned local packet. See
[Getting Started](Docs/Development/Getting%20Started.md) for the current environment and
verification details.

## Public-release and name status

Repository consolidation and publication are different decisions. The present history includes
LOCo-derived material that is not cleared for redistribution, and the selected M11 manifests'
non-reconstructive character does not grant publication rights. Before visibility changes, the
owner must resolve LOCo permission or sanitized history, proposal and diagram ownership, Git author
identity, the intended release history, and the repository name. See
[PUBLIC_RELEASE.md](PUBLIC_RELEASE.md) for the full gate.

No license has been chosen for YieldForge's own code or documentation. “YieldForge” is a
provisional working name; naming, trademark, and market-confusion checks have not been completed.
