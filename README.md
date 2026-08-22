# YieldForge

YieldForge is a research project testing whether future demand should change which high-quality 2D nesting layout a manufacturer chooses today.

> **Current status:** M0 passed. The registered 51-task calibration completed all 612 cells with valid archives, selected 10 seconds per seed, and enabled confirmation. M2 is active; the 203-task confirmatory geometry evaluation is pending. No residual-geometry, remnant-reuse, simulator, oracle, savings, or commercial result exists.

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

## Local research workbench

The browser workbench under `yf/web/` is a local research instrument, not a production application:

- **Corpus Explorer** pages through the committed 256-task source-lossless Lectra catalog and its server-owned support states. Ruleset v2 classifies 254 tasks as runnable only with their exact listed assumptions and keeps the two non-`s1` tasks blocked. The catalog is a bounded research selection, not a prevalence sample of the full release.
- **Nest Lab** keeps task `25801` view-only, requires acknowledgement of `interpret_s1_degenerate_entries_as_allowed_rotations` for task `13958`, and does not discard recorded `s1` flips. Flip-bearing tasks such as `6669` require the additional local-x-negation-before-rotation assumption. They can run either the source-recorded projection or an explicitly acknowledged derived no-flip ablation, and can launch both as one matched pair with the same solver configuration. FastAPI supervises each arm across the Spyrrow worker boundary. Newest-first history records projection mode and identity, pair/arm identity, exact solver settings, and verified archive SHA-256 identities; researchers can reopen either immutable candidate archive, render its derived geometry, and inspect the pair. The side-by-side table reports exact recorded values plus neutral Same/Different relations; it does not select a winner, and candidate count is inventory rather than quality.
- **Order Book Lab** opens three committed deterministic books and can generate additional immutable local books. Geometry and task composition are source-observed; chronology and economics are generated; material is assumed.

The frozen M0 artifact defines net cost, information sets, event timing, candidate parity, remnant eligibility, failure treatment, statistical reporting, and go/no-go gates. Its companion geometry protocol binds the exact 254-task eligible population to a prelisted 51-task calibration set and 203-task evaluation set. Calibration completed with 100% archive validity and selected the registered 10-second reference budget; content-addressed protocol v2 now enables confirmation. This is candidate-generation evidence, not residual or economic evidence.

The expanded view requires the documented local Postgres service; without `YIELDFORGE_DATABASE_URL`, the API deliberately falls back to the original two-task fixture. The matched flip experiment is a controlled projection-sensitivity test, not a baseline/oracle comparison or a nesting-quality result. The workbench does not calculate residual geometry, reuse remnants, simulate inventory, compare a baseline with an oracle, or report savings. See [Research Workbench](Docs/Development/Research%20Workbench.md) for the exact source-evidence reproduction, database import, direct API, local runtime, and verification commands.

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
uv sync --locked --all-groups
uv run --all-groups pytest
uv run yieldforge candidates generate \
  --input benchmarks/static/m0-smoke.json \
  --output var/archives/m0-smoke-seed-0 \
  --seed 0 --seconds 1 --workers 1

cd web
npm ci
npm run playwright:install
npm test
npm run build
```

Candidate output directories are immutable. Choose a new output path for every run; committed source and tests never depend on generated archives.

## Current next step

Work through [M2—Static Data and Sparrow](Docs/Milestones/M2%20-%20Static%20data%20and%20Sparrow.md). The next task is the bounded 203-task confirmatory evaluation: run only seeds `0–3` at the selected 10 seconds under `source_as_recorded`, persist every attempt and verified archive, and apply the frozen M2 decision rule. The flip ablation remains a separate sensitivity, and arbitrary sheet input plus residual/simulator work remain deferred.

## Name status

“YieldForge” is a provisional internal working name. Naming, domain, trademark, and market-confusion checks are required before external use.
