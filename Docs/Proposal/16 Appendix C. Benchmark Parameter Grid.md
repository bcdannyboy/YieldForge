---
source: YieldForge Proposal v1
status: source
converted: 2026-08-17
---

> [!note] Source status
> This note is a working Markdown conversion of the original DOCX. The preserved DOCX remains the source artifact.

# Appendix C. Benchmark Parameter Grid

Proposed screening and confirmation design.

## C.1 Screening phase

| Dimension | Screening setting |
| --- | --- |
| Geometry corpora | At least 5 distinct irregular benchmark families. |
| Temporal regimes | No-signal, exact recurrence, variant recurrence, bundles, high-mix, repetitive. |
| Streams per cell | Initial 20-30 paired streams; expand promising cells. |
| Epsilon | 0%, 0.25%, 0.5%, 1%, 2%. |
| Remnant rules | Permissive geometric, nominal operational, conservative operational. |
| Candidate budgets | Low and medium; high on a diagnostic subset. |
| Terminal rule | Primary scrap-only; sensitivity zero credit and bounded continuation value. |

## C.2 Confirmation phase

The confirmation phase freezes the most plausible customer regimes and runs at least 100 paired streams per selected condition, subject to compute. It reports confidence intervals, median savings, positive-stream rate, and outlier concentration. The final verdict is based on confirmation, not the screening maximum.

## C.3 Required ablations

| Ablation | Comparison | Diagnostic |
| --- | --- | --- |
| Remove future information | Oracle -> strong baseline | Confirms measured value is attributable to information. |
| Remove remnant storage cost | Nominal -> zero | Measures how much friction suppresses theoretical opportunity. |
| Permissive -> conservative remnants | Geometry upper bound -> operational policy | Quantifies recoverability gap. |
| Ordinary -> expanded candidates | Same policy, larger/topology-guided action set | Quantifies search gap. |
| Rollout -> beam | One-step future evaluation -> sequential future optimization | Quantifies long-horizon interaction. |
| Strong -> simple baseline | Composite baseline -> myopic Sparrow | Shows how much easy value incumbents already capture. |
