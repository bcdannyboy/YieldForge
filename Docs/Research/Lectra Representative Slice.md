# Lectra representative slice

**Status:** Committed source-lossless evidence with one assumption-gated runnable projection

**Dataset:** `lectra-7030786-v1.1`

**Artifact:** `yf/datasets/fixtures/lectra-representative-slice.json`

This note records exactly what YieldForge preserved and classified in its first bounded Lectra slice. Read it with [[Lectra Corpus Audit]], [[2026-08-17-normalized-slice-and-workbench-implementation]], and [[Current Work]].

## What the artifact is

The committed JSON is a byte-for-byte promotion of the passive artifact emitted by the locked qualifier. It contains two selected source tasks, their part rows, their shape rows, their constraint rows in observed column order, and separately labeled derived polygon facts. It contains 66 part rows, 19 distinct shape rows, and 86 constraint rows in 162,231 bytes.

Both task records are `source_lossless`: the source task, part, shape, and opaque typed constraint values are preserved rather than rewritten into solver meanings. Only task `13958` has a separate solver projection, and that projection is `runnable_with_explicit_assumptions`, not `directly_supported`.

The artifact makes no claim that `tasks_index` or source row order is chronology. It does not establish time, arrivals, materials, costs, remnant history, or manufacturing sequence.

## Evidence binding

| Evidence | SHA-256 |
| --- | --- |
| Committed representative slice | `d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8` |
| Canonical passive audit report | `58d1978b59b8a023867f3906f1955b06ffceea52d24f92d31c3db3003091a857` |
| Pinned source manifest | `bfb5cc9e29fdbd81a642cbe6eed1b8c5ca9e153d23a17037489b9f63e6e05e89` |

The slice also repeats the four pinned source MD5 checksums:

| Source file | MD5 |
| --- | --- |
| `parts.gz` | `d8b51403f0cab79ec990b95a40911c1c` |
| `constraints.gz` | `e12581851bd2a357145a9dfccdad5363` |
| `shapes.gz` | `ff1623f24adf031710450a30e72984f2` |
| `tasks.gz` | `ac18fc58408a3fc832cfd6757b4b16ca` |

The source coordinate unit remains unresolved. The artifact preserves the literal source label `m^-4` and records its interpretation as `null`; neither this note nor the runnable projection converts it to a physical unit.

## Runnable task 13958

| Source fact | Exact value |
| --- | ---: |
| Split flags | train `true`; validation `false`; test `false` |
| Sheet type | `0` |
| Sheet width | `16920.0` |
| Sheet length | `80040.0` |
| Duration field | `304` |
| Efficiency field | `80.95153439163305` |
| Part rows | 34 |
| Distinct shape hashes | 8 |
| Constraint rows | 34 `s1` |

Part identity is scoped by `(tasks_index, part_id)`. This task contains part IDs `110000` through `110033`. Each part has exactly one well-formed `s1` row. Across those rows, `r1_start` and `r1_end` are equal: 16 rows contain the degenerate value `0.0` and 18 contain `180.0`; every `r1_flip_x` value is integer zero.

The source does not itself prove what that `s1` pattern means. YieldForge therefore projects the task only under the explicit assumption:

`interpret_s1_degenerate_entries_as_allowed_rotations`

That assumption treats the equal start/end entries as the allowed orientations used by the solver. It does not authorize orientation intervals, mirrors, free rotation, source-geometry repair, or a unit conversion. The source-lossless records remain alongside the derived projection so the interpretation is visible and reversible.

## View-only task 25801

| Source fact | Exact value |
| --- | ---: |
| Split flags | train `true`; validation `false`; test `false` |
| Sheet type | `0` |
| Sheet width | `14800.0` |
| Sheet length | `140000.0` |
| Duration field | `312` |
| Efficiency field | `77.37273078361599` |
| Part rows | 32 |
| Distinct shape hashes | 11 |
| Constraint rows | 32 `s1` plus 20 `c8` |

This task contains part IDs `110000` through `110031`, again scoped to its own task. It is preserved for inspection but its solver projection is blocked with reason code `contains_non_s1_constraints`. YieldForge has not established the semantics of the 20 `c8` rows, so ignoring or guessing them would not be a lossless projection. No direct-support or runnable claim is made for this task.

The 32 `s1` rows do not remove that exclusion. Their degenerate start/end values split evenly between `0.0` and `180.0`, and 12 of their flip fields contain `1`. The exporter does not apply the runnable task's no-mirror interpretation to this view-only record.

## Geometry checks and claim boundary

All 19 selected source shapes retain their exact flat `raw` coordinate stream and `sizes` value. In a separate derived layer, the exporter paired adjacent scalars, closed each ring reversibly, and checked the resulting polygons without repairing them. All 19 derived polygons are valid, simple, and nonzero-area.

Those checks establish a mechanically reviewable polygon projection for this bounded slice. They do not establish source winding semantics, physical scale, hole semantics beyond the selected single-sequence records, or general compatibility for the remaining corpus. As [[Lectra Corpus Audit]] records, the current certification count for `directly_supported` Lectra tasks remains zero.

## Selection and execution evidence

Task `13958` was selected by the reviewed deterministic runnable rule: training split; sheet type `0`; positive, non-sentinel `sheet_length`; 20–50 part rows; finite, flat-even, single-sequence geometry; exact `sizes == [len(raw)]`; valid simple nonzero polygons without repair; exactly one narrow degenerate `s1` row per part; and no other constraint type. Eligible tasks were ranked by distance from the target composition, then by `tasks_index`.

Task `25801` was selected separately as a bounded, source-lossless example of the non-`s1` exclusion boundary. Selection does not imply chronology or representative prevalence.

The real slice export ran in the locked, network-disabled, read-only, non-root qualifier boundary described by [[2026-08-17-lectra-qualification]]. The trusted runner reported peak memory of `9,378,103,296` bytes. The committed artifact was then independently parsed and bound to the exact audit report and source manifest above.

## Qualified catalog expansion

The committed catalog at `yf/datasets/catalogs/lectra-7030786-v1.1/lectra-catalog.json` extends the same passive-evidence boundary to 256 fully display-safe tasks under `lectra-catalog-rules.v1`. It is 9,554,652 bytes with SHA-256 `4903e28be9b874460ab565b3fc17b06608a9ccce37b699d6bcda49c7eac03138`. The adjacent `catalog-manifest.json` binds that artifact to the exact source manifest, audit report, ruleset, byte size, and row counts.

The catalog contains 8,358 part rows, 745 distinct shape rows with 745 derived geometry records, and 8,398 constraint rows: 8,358 `s1` rows and 40 `c8` rows. All 256 tasks are normalized as `source_lossless`; none is `directly_supported`. Sixty-nine are `runnable_with_explicit_assumptions` and projection-eligible only under `interpret_s1_degenerate_entries_as_allowed_rotations`. The remaining 187 are view-only and projection-blocked: 185 with `s1_projection_requirements_not_met` and two with `contains_non_s1_constraints`.

The continuity classifications remain unchanged: task `13958` is assumption-gated and eligible, while task `25801` is view-only and blocked for its non-`s1` constraints. Two independent locked exports produced byte-identical catalog files. Their reported peak memory values were `9,376,354,304` and `9,377,169,408` bytes, respectively, and the promoted bytes passed the host's strict finite, duplicate-key-free, schema-valid, manifest-bound, and audit-bound passive-evidence validation.

This 256-task catalog is a bounded deterministic research selection, not a prevalence sample of the 100,000-task release. It adds browsing breadth without establishing chronology, materials, economics, manufacturing sequence, residual geometry, remnant reuse, simulator truth, oracle quality, or savings.

## Next use

The workbench may display both tasks, but it must preserve their different support states:

- task `13958`: source-lossless evidence plus a runnable, assumption-labeled projection;
- task `25801`: source-lossless, view-only evidence with its exclusion reason visible.

Any generated order book built later must label its chronology as generated and keep source-real fields distinct. See [[2026-08-17-dataset-workbench-design]] for that provenance model.
