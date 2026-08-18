# Lectra corpus audit

**Status:** Qualified source corpus; normalization and runnable projection not yet approved

**Dataset:** `lectra-7030786-v1.1`

**Evidence:** Canonical passive report generated 2026-08-17 and validated against the pinned manifest

This note is the capability census for the [Lectra/Lallier 100,000-task release](https://zenodo.org/records/7030786). It records what the isolated audit proved, what it did not prove, and the boundary for YieldForge's first visible nest. It should be read with [[2026-08-17-dataset-workbench-design]] and [[2026-08-17-lectra-qualification]].

## Decision

The corpus is internally coherent enough to become YieldForge's primary source-real geometry and task reservoir:

- every declared key and audited join resolves;
- every shape record has a well-formed flat, even-length numeric `raw` sequence;
- all 100,000 tasks contain at least one part row;
- exact shapes recur enough to support later recurrence-regime construction.

It is **not** yet a directly supported Spyrrow corpus. The public release identifies 15 constraint codes, but the audit does not establish their semantics or a lossless mapping to the current adapter. The literal coordinate-unit label is `m^-4`; no scale interpretation has been approved. A runnable nest therefore needs two linked representations:

1. `source_lossless` — preserves the observed task, part, shape, sheet, and opaque constraint records without reinterpretation;
2. `runnable_with_explicit_assumptions` — a separate projection that states each unproved choice, including coordinate scaling, sheet-length handling, allowed orientations, and excluded constraint behavior.

The first Lectra-backed nest must carry both representations and the assumptions connecting them. It must not be labeled `directly_supported`.

## Source identity

The committed source manifest pins release 1.1, DOI `10.5281/zenodo.7030786`, license `CC-BY-4.0`, four files, and 187,809,072 compressed bytes.

| File | Bytes | Pinned and observed MD5 |
| --- | ---: | --- |
| `parts.gz` | 22,476,589 | `d8b51403f0cab79ec990b95a40911c1c` |
| `constraints.gz` | 16,299,551 | `e12581851bd2a357145a9dfccdad5363` |
| `shapes.gz` | 147,824,458 | `ff1623f24adf031710450a30e72984f2` |
| `tasks.gz` | 1,208,474 | `ac18fc58408a3fc832cfd6757b4b16ca` |

The generated report is ignored working evidence under `yf/var/data/reports/`; the manifest, report schema, and this human-readable census are the durable repository documentation.

## Observed tables and schema

| Table | Rows | Observed columns |
| --- | ---: | --- |
| `tasks` | 100,000 | `duration`, `efficiency`, `sheet_width`, `sheet_length`, `sheet_type`, `tasks_index`, `is_train`, `is_val`, `is_test` |
| `parts` | 7,360,718 | `part_id`, `shape_hash`, `tasks_index` |
| `shapes` | 399,746 | `shape_hash`, `raw`, `sizes` |
| `constraints` | 7,394,464 | `parts_1`, `p1_x`, `p1_y`, `r1_start`, `r1_end`, `r1_flip_x`, `parts_2`, `p2_x`, `p2_y`, `x_offset`, `y_offset`, `motif_order`, `x_alignment_type`, `y_alignment_type`, `proximity_type`, `max_distance`, `y_min`, `y_max`, `groups_relative_orientation`, `is_frozen`, `tasks_index`, `type` |

The public source schema names `parts_id`; the actual release column is singular `part_id`. The audit contract and qualifier follow the observed column. This is a source-document discrepancy, not a source-data repair.

The source unit is recorded exactly as the literal label `m^-4`. This audit does not translate it to metres, millimetres, or any power-of-ten scale.

## Integrity result

All required columns were present and there were no unexpected columns. The following audited counts are all zero:

- missing or invalid task, shape, part-composite, and constraint-task keys;
- duplicate task keys, duplicate shape keys, and repeated `(tasks_index, part_id)` composite keys beyond the first;
- part rows missing a task or shape;
- constraint rows missing a task;
- integral part-reference occurrences in `parts_1` or `parts_2` that fail to resolve within the constraint's task;
- shape records or task records unused by the parts table;
- malformed `raw`, `sizes`, constraint type, or constraint-reference cells;
- non-integral constraint-reference elements;
- invalid sheet dimensions, missing sheet types, or invalid train/validation/test assignments.

The partition flags are mutually exclusive and exhaustive: 70,000 training tasks, 15,000 validation tasks, and 15,000 test tasks.

## Geometry encoding evidence

Every one of the 399,746 shape rows has:

- a `raw` value classified as a flat numeric sequence with even scalar length;
- a well-formed positive-integer `sizes` sequence;
- exactly one source-declared subshape entry, because `len(sizes) == 1`;
- `sum(sizes) == len(raw)`.

That is the full claim. The audit does not prove whether the single declared subshape is an exterior contour, whether any source-level convention could encode holes, how coordinates are paired beyond their flat even structure, or which winding convention applies. Normalization must preserve `raw` and `sizes` before producing any polygon projection, and must round-trip the source values exactly.

## Sheets and task composition

`sheet_width` is finite and positive for all tasks: minimum 500, median 14,500, 95th percentile 17,200, and maximum 24,000 in the unresolved source unit.

`sheet_length` equals the documented `-1` unconstrained sentinel for 95,342 tasks (95.342%). The remaining 4,658 tasks (4.658%) have a finite positive length. Across the literal column, the maximum is 1,031,750; the median and 95th percentile are both `-1` because the sentinel dominates. A bounded-sheet runnable projection must never silently replace `-1`; it needs an explicit, archived length rule.

Observed sheet-type codes are:

| Code | Tasks |
| --- | ---: |
| `0` | 99,403 |
| `2` | 570 |
| `3` | 27 |

The audit found 1 to 5,454 part rows per task (median 35, mean 73.60718, 95th percentile 264), and 1 to 170 distinct shapes per task (median 9, mean 10.88254, 95th percentile 29). Repeated-shape part rows—part rows minus distinct shape hashes within a task—range from 0 to 5,453, with median 23, mean 62.72464, and 95th percentile 258.

## Constraint census

All 7,394,464 constraint rows have a non-missing code. The audit observed exactly these frequencies:

| Type | Rows | Type | Rows | Type | Rows |
| --- | ---: | --- | ---: | --- | ---: |
| `c1` | 2,205 | `c2` | 296 | `c3` | 348 |
| `c4` | 10 | `c5` | 57 | `c6` | 62 |
| `c7` | 32 | `c8` | 1,853 | `g1` | 7,303 |
| `g2` | 15,029 | `g3` | 2,562 | `g4` | 3,584 |
| `s1` | 7,360,718 | `t1` | 204 | `t2` | 201 |

`s1` accounts for 7,360,718 rows, exactly equal to the total number of part rows; the other 14 codes account for 33,746 rows. Equality of these two global counts is evidence worth preserving, but it is not by itself proof that each `s1` row has a one-to-one semantic relationship with one part.

The parameter columns are sparse and type-dependent. Examples from the passive inventory include 7,619 present `p1_x`/`p1_y` values, 2,585 present `p2_x`/`p2_y` values, 15,297 present `y_min`/`y_max` values, 9,508 present `groups_relative_orientation` values, and 32 present `max_distance`/`proximity_type` values. Sequence lengths also vary substantially for some fields. These observations support preserving the opaque record; they do not supply the missing meaning of a type code.

No public semantic mapping has yet been accepted for `c1`–`c8`, `g1`–`g4`, `s1`, or `t1`–`t2`. Consequently, YieldForge cannot claim that ignoring, flattening, or guessing any of them is lossless. Release 1.1 also changed the `c8`/`g4` labeling according to the upstream release history, which is another reason to bind interpretation to the exact source version.

## Shape recurrence

Exact `shape_hash` values recur both within and across tasks:

| Metric per shape hash | Min | p25 | Median | p75 | p95 | Mean | Max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Part-row occurrences | 1 | 2 | 4 | 10 | 73 | 18.41349 | 21,211 |
| Distinct tasks | 1 | 1 | 1 | 3 | 8 | 2.72236 | 6,647 |

These distributions establish a real recurrence reservoir. They do not establish chronological recurrence: `tasks_index`, row order, and the train/validation/test split are not treated as event time. Later hybrid order books may arrange source tasks into generated chronology only with field-level provenance and a deterministic generator manifest.

## Current Spyrrow coverage

The current canonical `Part` contract carries one closed polygon path per part definition; the adapter also requires a positive strip height, a positive physical sheet length, positive demand, and an explicit list of allowed orientations. It has no Lectra constraint-code mapping and cannot represent an unconstrained `-1` physical sheet length.

A source task may be certified `directly_supported` only if all of the following are proven without assumptions:

1. its `raw`/`sizes` values map losslessly to simple polygons accepted by the adapter;
2. its coordinate scale and axis interpretation are established;
3. its sheet dimensions map directly to positive solver dimensions;
4. every constraint affecting the task has known semantics and a lossless adapter mapping;
5. allowed orientations are source-grounded rather than selected by YieldForge.

The aggregate audit cannot apply that rule task by task, and the repository contains no accepted Lectra constraint mapping. The current evidence therefore certifies **zero tasks as directly supported**; this is a certification floor, not a claim that zero tasks are intrinsically solvable. Publishing a percentage estimate from this aggregate report would be false precision.

The first runnable slice should instead select a task through a bounded, isolated export that proves its own rows and joins, passes explicit polygon-validity checks without repair, and emits both `source_lossless` and `runnable_with_explicit_assumptions`. The runnable projection may use only assumptions named in its manifest and displayed in the workbench.

## Claim ceiling and unresolved questions

This census supports these claims:

- the release contains 100,000 coherent static nesting tasks derived from the published source;
- task composition and exact source-shape recurrence are measurable;
- the four source tables reconcile under the audited keys and joins;
- the corpus is suitable for a lossless normalized evidence layer.

It does **not** support claims about manufacturing chronology, material identity, costs, remnants, future arrivals, task-index time order, direct Spyrrow compatibility, constraint semantics, coordinate scale, hole semantics, or factory-wide recurrence rates.

The following questions block a `directly_supported` classification and must remain visible in the workbench:

- What physical scale does the literal `m^-4` label denote, and what source evidence fixes it?
- Does `sizes` describe flattened scalar counts, point counts, or another upstream shape boundary convention despite the observed equality to `len(raw)`?
- What exact semantics and parameter rules belong to each of the 15 version-1.1 constraint codes?
- How do `s1` rows relate to parts, orientation intervals, and horizontal flips?
- What source-grounded solver boundary should replace `sheet_length == -1` for a runnable experiment?
- Which source tasks pass lossless polygon construction and a per-task constraint classification?

The aggregate report deliberately contains no representative task IDs. None should be invented from row counts or distributions. Selecting the first visible slice requires a separate, bounded passive export from the locked qualifier, followed by a documented selection rule.

## Qualification execution evidence

The canonical report was produced in the locked qualifier, not in the normal YieldForge process. The source directory was mounted read-only into a network-disabled, read-only, non-root container. Each archive was verified against its pinned size and MD5 while being copied to a sealed Linux memory file; pandas read the same sealed handle. The container had no host-writable report mount. A trusted host runner bounded stdout, stderr, time, processes, CPU, and memory; recursively parsed and validated the single passive JSON result; bound it to the pinned manifest; and published canonical bytes with no-clobber semantics.

The canonical production run completed under a 16 GiB memory limit and a 900-second timeout. Its final cgroup telemetry reported `memory.current = 9,126,522,880` bytes and `memory.peak = 9,721,896,960` bytes—9.72 GB decimal, or 9.05 GiB. This is the authoritative peak for this report. An earlier diagnostic probe recorded 9,534,488,576 bytes; it is not substituted for the canonical-run value.

Outside the container, the passive report revalidated against the pinned manifest as:

```text
Validated lectra-7030786-v1.1 audit: tasks=100000, parts=7360718, shapes=399746, constraints=7394464
```

## Primary sources

- [Lectra/Lallier release 1.1 and files](https://zenodo.org/records/7030786)
- [Associated real-customer dataset paper](https://link.springer.com/article/10.1007/s10845-023-02084-6)
- [Pinned repository source manifest](../../yf/datasets/sources/lectra-7030786-v1.1.json)
- [Locked qualifier operating boundary](../../yf/tools/lectra/README.md)
