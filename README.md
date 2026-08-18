# YieldForge

YieldForge is a research project testing whether future demand should change which high-quality 2D nesting layout a manufacturer chooses today.

> **Current status:** M0—the experiment contract—is active. The reusable `yf/` development foundation and Spyrrow candidate adapter are working, but M0 has not passed and no savings result or commercial claim exists.

## The idea

Traditional nesting optimizes immediate utilization: fit the current parts compactly and minimize today's waste. But two layouts with nearly identical immediate utilization can leave very different residual shapes. One remnant may fit valuable future work while another may be practically useless.

YieldForge asks whether that future geometric utility is large and frequent enough to matter economically.

The first program is deliberately not a production application. It is a perfect-information research MVP designed to answer one question:

> If every future order were known, how much net purchased-material cost could be avoided by choosing future-aware nests and remnants instead of the strongest policy using only information available at the time?

If the advantage is too small or fragile, the correct outcome is to stop before investing in forecasting, CAM integration, and shop-floor deployment.

## Validation approach

The MVP will:

1. Generate several feasible, near-tied layouts rather than accepting one solver answer.
2. Calculate the exact residual geometry and material accounting for each layout.
3. Reuse retained remnants as irregular stock in later orders.
4. Replay timestamped order streams through a deterministic inventory simulator.
5. Compare a strong as-of-time baseline with a perfect-information rollout oracle using the same candidates and compute rules.
6. Validate scalable search against exhaustive solutions on small cases.
7. Produce a stop, real-data, or continue decision under thresholds defined before evaluation.

Synthetic benchmarks can test the mechanism, but they cannot establish physical recoverability, operator adoption, integration reliability, customer ROI, or market demand. A positive virtual result would justify acquiring real manufacturer history—not launching a product.

## Working milestones

| Stage | Purpose |
| --- | --- |
| M0–M2 | Freeze the experiment, establish the shared data model, and make Sparrow a reproducible candidate source. |
| M3–M5 | Prove exact residual accounting, remnant reuse, and deterministic chronological replay. |
| M6–M8 | Build controlled temporal benchmarks, freeze a strong baseline, and measure perfect-information value. |
| M9–M10 | Validate search quality, run the experiment, and issue the investment verdict. |

Detailed planning happens one milestone at a time. See the [Milestone Roadmap](Docs/Milestones/Milestone%20Roadmap.md) for the current sequence and semantic explanation of every milestone.

## MVP boundary

The perfect-information MVP intentionally excludes:

- demand forecasting or learned value functions;
- quoting, purchasing optimization, and order scheduling;
- multi-machine dispatch and shop-floor control;
- CAM postprocessing, NC generation, and cut sequencing;
- production UI, authentication, hosted services, and customer dashboards;
- claims that synthetic results prove real-world or commercial value.

## Repository documentation

The repository's primary project and developer documentation is an Obsidian vault under [`Docs/`](Docs/Home.md). The original DOCX is preserved inside the vault, while its contents are split into section-sized Markdown notes for easier navigation and maintenance.

- [Notebook Home](Docs/Home.md)
- [Current Work](Docs/Current%20Work.md)
- [Developer setup](Docs/Development/Getting%20Started.md)
- [Proposal Contents](Docs/Proposal%20Contents.md)
- [Proposal at a Glance](Docs/Proposal/03%201.%20Proposal%20at%20a%20Glance.md)
- [Validation Thesis](Docs/Proposal/07%205.%20Validation%20Thesis%20and%20Falsifiable%20Hypotheses.md)
- [Perfect-Information MVP](Docs/Proposal/08%206.%20The%20Perfect-Information%20MVP.md)
- [Milestone Roadmap](Docs/Milestones/Milestone%20Roadmap.md)
- [Go/No-Go Framework](Docs/Proposal/11%209.%20Go%20-%20No-Go%20Decision%20Framework.md)

To use the notebook in Obsidian, open the repository's `Docs/` directory as a vault. The Markdown files remain readable in GitHub and ordinary editors without Obsidian.

## Developer quick start

All implementation work accumulates in one directory: `yf/`. Milestones extend this package; they do not create `yf0`, `yf1`, or parallel application trees.

```bash
cd yf
uv sync
uv run pytest
uv run yieldforge candidates generate \
  --input benchmarks/static/m0-smoke.json \
  --output var/archives/m0-smoke-seed-0 \
  --seed 0 --seconds 1 --workers 1
```

Candidate output directories are immutable. Choose a new output path for every run; committed source and tests never depend on generated archives.

## Current next step

Work through [M0—Experiment Contract](Docs/Milestones/M0%20-%20Experiment%20contract.md), beginning with the primary outcome and comparison: what “better” means, which costs count, and how the oracle will be compared with the strongest legitimate baseline.

## Name status

“YieldForge” is a provisional internal working name. Naming, domain, trademark, and market-confusion checks are required before external use.
