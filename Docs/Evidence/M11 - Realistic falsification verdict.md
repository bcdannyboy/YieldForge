# M11 realistic falsification verdict

Date: 2026-08-30

## Verdict

**ABANDON YieldForge as a product-investment hypothesis. Do not productize it and do not spend more
engineering time trying to make this semi-synthetic test pass.**

The frozen decision model produced:

| Field | Value |
|---|---|
| Evidence state | `invalid_test` |
| Repair count | `1` of `1` permitted |
| Invalid category | `other_validity_failure` |
| Invalid reason | `accounting_reconciliation_failure` |
| Forced action | `ABANDON` |
| Verdict ID | `yfm11r-96caaf276503db4a54decf5d` |
| Verdict semantic SHA-256 | `sha256:96caaf276503db4a54decf5d4593db970e144cec6aefddd87f2fa890ab4211ee` |
| Productization authorized | `false` |

This is a decision to stop investing, not a claim that the underlying economic effect was measured
and found to be zero. M11 stopped at calibration because the test was invalid. The result says the
project did not produce a credible, executable falsification test within its preregistered one-repair
budget. Under the contract agreed before confirmation, that is enough to abandon the hypothesis as
an investment candidate.

## What ran

The official runner authenticated the frozen contract, population, Gate 1, Gate 2, revised Gate 3
configuration, and adapter runtime before executing the registered 96-attempt calibration matrix:
two corpora, six baseline policies, and eight streams per corpus.

| Corpus | Attempts | Successful | Failed | Result |
|---|---:|---:|---:|---|
| Lectra M3/M4 | 48 | 48 | 0 | Complete |
| LOCo 2D-ICS | 48 | 12 | 36 | Invalid |
| Total | 96 | 60 | 36 | Invalid |

The six failing LOCo streams failed under every one of the six policies. Every failure was a strict
`ReuseAccounting` validation error: `reuse accounting delta does not match material categories`.
The two remaining LOCo streams succeeded under all six policies. A streaming post-run census of the
published artifact found exactly 96 calibration-attempt records, 60 null failure types, 36 Pydantic
validation failures, and 36 matching reuse-accounting messages.

Because calibration failed:

- no baseline was frozen;
- none of the 6 hard-null, 40 shuffled-twin, or 12 exact-audit validity controls ran;
- no LOCo, Lectra, or equal-corpus central confirmation stream ran;
- no adverse, terminal-credit, eligibility, support, or deployable-capture stage ran;
- no Gate 3 central/deployable savings, unknown-future contribution, ROI, or pilot-readiness
  conclusion was produced.

The published Gate 3 result reports `status=invalid_test`, `disposition=INVALID_NONZERO`,
`terminal=true`, `productization_authorized=false`, and empty central/downstream outcomes.

## Artifact and readback audit

| Evidence | Semantic identity | Raw SHA-256 | Bytes | Readback status |
|---|---|---|---:|---|
| Gate 1 | `yfm11g1run-c35f10fa4f4d7b6b01c59c29` | `e592141b0342a27cbfed56198670fd5fbeaeebccfcadad40a2302d14762128c2` | 54,980,780 | Authenticated before Gate 3 |
| Gate 2 | `yfm11g2run-7419e46b74e411aff5c27ee1` | `551b67c7bad8e044455502a37b44bc2d250d9024c1ae6dc2e84b648de2ad2d43` | 234,726,973 | Authenticated before Gate 3 |
| Gate 3 config | `yfm11g3c-795010e6747d2c11d556ef82` | `5f1c3dbf880ba8977a3a4ff864833a44d82136c29427dcd6f70bee3d2b870a8d` | 15,980 | Authenticated before Gate 3 |
| Gate 3 early run | `yfm11g3run-3dd87efab6f64ada4c5bd09c` | `e5757919ddd9251bf374d1664be25faf175963e78478b223ea0d7e22f7439199` | 2,270,455,752 | Rejected: exceeds frozen 512 MiB bound |

The Gate 3 file was atomically published and exposes these content-addressed boundary identities:

- run semantic SHA-256:
  `sha256:3dd87efab6f64ada4c5bd09c0580a1696017b3115ccdce3ce041b4221c89a89f`;
- nested result ID: `yfm11g3early-478a1fe787e701d15b5ae65d`;
- nested result semantic SHA-256:
  `sha256:478a1fe787e701d15b5ae65d6e44d594dae6933e7e2ea1784aeb24d4297e9451`;
- adapter runtime SHA-256:
  `sha256:9006a711a465e3b57b97f703cea5ea90b0b06fa8922a5330b95ef0979fe1e0a1`.

Mandatory strict readback did **not** pass: the 2.1 GiB artifact exceeds the runner's frozen 512 MiB
regular-file ceiling. Therefore the file is preserved as failed raw evidence and is not described as
a valid canonical Gate 3 artifact. Its raw hash, boundary fields, and calibration census were checked
without weakening the loader. Oversize publication is an additional implementation failure; it does
not rescue or change the already-invalid calibration result.

## Why the decision is final

The only allowed same-contract repair was already consumed by the bounded-catalog integrity repair.
That repair corrected a mismatch between Gate 3's original no-truncation claim and the registered
256-candidate product search, before central confirmation output was executed or inspected. The
original failure remains in the audit trail.

The accounting-reconciliation failure is classified as repair-ineligible and therefore forces
`ABANDON` independently. The already-consumed repair and the later oversize-artifact readback failure
are additional stop conditions, not substitutes for that primary causal failure.

The frozen rule is unambiguous:

```text
invalid_test + repair_count 0 + repair-eligible defect -> ONE_REPAIR_AND_RERUN
invalid_test + repair-ineligible validity failure       -> ABANDON
invalid_test + repair_count 1                          -> ABANDON
```

Running `build_m11_verdict(...)` against the frozen contract with the explicit
`accounting_reconciliation_failure` reason and `repair_count=1` mechanically generated the verdict
ID and action above. Repairing reuse accounting, compressing or thinning the evidence envelope, and
running again would be a new experiment under a new contract. It may be technically possible, but
it is outside this decision and is not warranted for deciding whether to keep funding YieldForge.

## Evidence provenance and claim ceiling

| Provenance | Fields used by the test |
|---|---|
| Source-observed | Geometry references and source demand from Lectra and LOCo |
| Externally anchored | Lectra candidate references |
| Derived | Family identity and quantity |
| Generated | Chronology, customer/job identity, release/known/due times, priority, fallback layouts, LOCo candidates, and stock boundaries |
| Assumed | Material identity and all economics |

The result can govern whether to abandon the modeled hypothesis or, after a fully valid Green test,
authorize only a bounded real-history/operator pilot. It cannot prove factory representativeness,
realized ROI, buyer demand, adoption, integration reliability, commercial viability, or
productization readiness.

## Final action

Archive YieldForge as a technically interesting but unvalidated research prototype. Reuse its
geometry, replay, M8 feasibility, or M9 search components only if another independently justified
project needs them. Do not continue the YieldForge product roadmap unless genuinely observed
factory chronology, costs, and operator access arrive as new external evidence strong enough to
justify a newly scoped real-world pilot on their own.
