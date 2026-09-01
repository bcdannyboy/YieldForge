# YieldForge Notebook

> **Status: paused research archive.** M11 resolved the current modeled product and algorithms as
> `INSUFFICIENT_CURRENT_MODELED_VALUE`. The tested known-only policy saved `0%` in both segments;
> no pilot or productization is authorized.

This repository preserves the YieldForge proposal, implementation lineage, and falsification
evidence. It is not an active product roadmap.

## The workflow-first idea

YieldForge was not intended to replace a manufacturer's existing nesting system or nesting
workflow. The incumbent optimizer would keep producing strong, process-feasible nests for each
job. YieldForge would act as a selection layer over multiple near-tied alternative nests, choosing
between them based on whether their residual geometry could lower cumulative material cost over
later jobs.

Sparrow was the first candidate source because it is a strong immediate-space optimizer and its
progressive reports could be drained by the Spyrrow adapter into immutable archives of intermediate
and alternative nests. That made the hypothesis testable without rebuilding the core optimizer.
Using another incumbent would require equivalent candidate access and an adapter. This is
integration work, but it is not a replacement nesting workflow. The research tested only the
Sparrow/Spyrrow path; it did not establish arbitrary vendor compatibility, live inventory sync,
operator acceptance, or literally zero integration.

## Start here

- [[Proposal Contents]] — the complete proposal, split into manageable section notes.
- [[Milestone Roadmap]] — the historical implementation sequence in plain language.
- [[Current Work]] — a preserved working-state note, not current authorization.
- [[Development/Getting Started|Developer setup]] — install, test, and run the current system.
- [[Development/Research Workbench|Research workbench]] — local UI/API workflow and evidence boundary.
- [[Development/Spyrrow Adapter|Spyrrow adapter]] — the candidate boundary and archive semantics.
- [[Research/Technical Sources|Technical sources]] — upstream projects and dataset leads.

## Closeout evidence and release boundary

- [[Evidence/M11 - Economic resolution|M11 economic resolution]] — the final B/F/K economic result,
  decision gates, provenance, and bounded conclusion.
- [[Milestones/M10 - Experiment and verdict|M10 investment verdict]] — the earlier non-numeric
  decision to stop productization and additional virtual-oracle work.
- [[Development/Artifact Policy|Artifact policy]] — what is tracked, what remains in the private
  replay packet, and what a clean clone can reproduce.
- [Public release gate](../PUBLIC_RELEASE.md) — unresolved rights, licensing, identity, history, and
  visibility decisions. The repository is not currently cleared for publication.
- [Third-party notices](../THIRD_PARTY_NOTICES.md) — source attribution and redistribution limits.

## Important distinction

The notes under `Proposal/` are a faithful conversion of the original DOCX and should be treated as
source material. The notes under `Milestones/` preserve the historical working plan and checkpoint
language. Their old “next step” statements are not current authorization; the M11 closeout above is
the current decision record.

All product and research code lives under `yf/`. That is the single implementation tree across every milestone.

## Source

The original document is preserved at [[Source/YieldForge Proposal and Perfect-Information MVP]].
