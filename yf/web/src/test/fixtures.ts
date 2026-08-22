import type {
  CompletedRun,
  CorpusSource,
  CorpusSummary,
  JobView,
  OrderBook,
  SolveCapability,
  TaskDetail,
  TaskPage,
  TaskSummary,
} from "../contracts";

export const source: CorpusSource = {
  dataset_id: "lectra-7030786-v1.1",
  doi: "10.5281/zenodo.7030786",
  license: "CC-BY-4.0",
  conversion_ruleset_version: "lectra-slice-rules.v1",
  source_checksums: [],
  source_manifest_sha256: "a".repeat(64),
  audit_report_sha256: "b".repeat(64),
  slice_sha256: "d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8",
  evidence_status: "content_pinned_with_manifest_identity",
};

export const capability = (canSolve: boolean): SolveCapability => ({
  can_solve: canSolve,
  requires_assumption_acknowledgement: canSolve,
  normalization_status: "source_lossless",
  support_status: canSolve ? "runnable_with_explicit_assumptions" : "view_only",
  projection_status: canSolve ? "eligible" : "blocked",
  reason_codes: canSolve ? [] : ["contains_non_s1_constraints"],
  assumption_codes: canSolve
    ? ["interpret_s1_degenerate_entries_as_allowed_rotations"]
    : [],
});

export const taskSummary = (tasksIndex: number): TaskSummary => ({
  task: {
    source_row_index: tasksIndex,
    duration: 304,
    efficiency: 80.5,
    sheet_width: tasksIndex === 13958 ? 16920 : 14800,
    sheet_length: tasksIndex === 13958 ? 80040 : 140000,
    sheet_type: 0,
    tasks_index: tasksIndex,
    is_train: true,
    is_val: false,
    is_test: false,
  },
  tasks_index: tasksIndex,
  part_count: tasksIndex === 13958 ? 34 : 32,
  shape_count: tasksIndex === 13958 ? 8 : 11,
  constraint_count: tasksIndex === 13958 ? 34 : 52,
  constraint_types: tasksIndex === 13958 ? ["s1"] : ["c8", "s1"],
  solve_capability: capability(tasksIndex === 13958),
});

export const taskDetail = (tasksIndex: number): TaskDetail => ({
  schema_version: "yieldforge.task-detail.v1",
  source,
  coordinate_unit: { literal_label: "m^-4", interpretation: null },
  summary: taskSummary(tasksIndex),
  parts: [
    {
      source_row_index: 1,
      tasks_index: tasksIndex,
      part_id: 10,
      shape_hash: "-8727500516347896752",
    },
  ],
  shapes: [
    {
      source_row_index: 2,
      shape_hash: "-8727500516347896752",
      raw: ["0", "0", "10", "0", "10", "10", "0", "10", "0", "0"],
      sizes: [10],
    },
  ],
  constraints: [
    {
      source_row_index: 3,
      tasks_index: tasksIndex,
      type: tasksIndex === 13958 ? "s1" : "c8",
      values: [
        { kind: "sequence", items: [{ kind: "integer", value: "10" }] },
        { kind: "missing" },
      ],
    },
  ],
  constraint_value_columns: ["parts_1", "parts_2"],
  derived_geometry: [
    {
      shape_hash: "-8727500516347896752",
      paired_points: [
        ["0", "0"],
        ["10", "0"],
        ["10", "10"],
        ["0", "10"],
        ["0", "0"],
      ],
      closed_ring: [
        ["0", "0"],
        ["10", "0"],
        ["10", "10"],
        ["0", "10"],
        ["0", "0"],
      ],
      raw_scalar_count: 10,
      ring_closure_added: false,
      is_simple: true,
      is_valid: true,
      has_nonzero_area: true,
      area: 100,
      bounds: ["0", "0", "10", "10"],
    },
  ],
  provenance: [
    { kind: "source_real", field_paths: ["/parts"], note: "Published rows" },
    { kind: "derived", field_paths: ["/derived_geometry"], note: "Reversible pairing" },
    {
      kind: "assumed",
      field_paths: ["/task_dispositions/assumption_codes"],
      note: "Explicit orientation interpretation",
    },
  ],
});

export const corpusSummary: CorpusSummary = {
  schema_version: "yieldforge.corpus-summary.v1",
  source,
  coordinate_unit: { literal_label: "m^-4", interpretation: null },
  task_count: 2,
  part_count: 66,
  shape_count: 19,
  constraint_count: 86,
  support_status_counts: [
    { name: "runnable_with_explicit_assumptions", count: 1 },
    { name: "view_only", count: 1 },
  ],
  constraint_type_counts: [
    { name: "c8", count: 20 },
    { name: "s1", count: 66 },
  ],
  solve_capability: {
    eligible_task_count: 1,
    blocked_task_count: 1,
    directly_supported_task_count: 0,
  },
};

export const taskPage: TaskPage = {
  schema_version: "yieldforge.task-page.v1",
  items: [taskSummary(13958), taskSummary(25801)],
  next_cursor: null,
};

export const job: JobView = {
  schema_version: "yieldforge.api-job.v1",
  job_id: "job-1",
  status: "queued",
  created_at: "2026-08-18T00:00:00Z",
  updated_at: "2026-08-18T00:00:00Z",
  latest_event_id: 1,
  candidate_count: 0,
  source_task_binding: {
    dataset_id: "lectra-7030786-v1.1",
    source_slice_sha256: source.slice_sha256,
    tasks_index: 13958,
    acknowledged_assumption_codes: [
      "interpret_s1_degenerate_entries_as_allowed_rotations",
    ],
  },
  archive_available: false,
  error_code: null,
  error_message: null,
};

export const completedRun = (jobId = "job-1"): CompletedRun => ({
  schema_version: "yieldforge.api-completed-run.v1",
  job: {
    ...job,
    job_id: jobId,
    status: "completed",
    latest_event_id: 5,
    candidate_count: 1,
    archive_available: true,
  },
  settings: {
    seed: 23,
    total_computation_time: 5,
    num_workers: 1,
    early_termination: false,
    min_items_separation: null,
    max_runtime_seconds: 6,
  },
  archive: {
    schema_version: "yieldforge.candidate-archive.v1",
    batch_sha256: "c".repeat(64),
  },
});

export const orderBook: OrderBook = {
  schema_version: "yieldforge.api-order-book.v1",
  manifest_schema_version: "yieldforge.order-book.v1",
  analysis_scope: "analysis_only_full_manifest",
  analysis_warning: "Analysis-only full manifest; future events and generator-only regime labels are excluded from baseline-facing views.",
  order_book_id: "yfob-demo",
  content_sha256: `sha256:${"c".repeat(64)}`,
  generator: {
    name: "yieldforge.hybrid-order-book",
    version: "1.0.0",
    algorithm: "python-mt19937-canonical-v1",
  },
  source_slice: {
    dataset_id: "lectra-7030786-v1.1",
    repository_path: "datasets/fixtures/lectra-representative-slice.json",
    content_sha256: `sha256:${source.slice_sha256}`,
    conversion_ruleset_version: "lectra-slice-rules.v1",
    doi: "10.5281/zenodo.7030786",
  },
  request: {
    regime: "exact_recurrence",
    seed: 7,
    event_count: 2,
    starts_at: "2026-01-01T00:00:00Z",
    interval_minutes: 60,
    source_slice_sha256: source.slice_sha256,
    thresholds: null,
  },
  field_provenance: [
    { family: "geometry", kind: "source_observed", explanation: "Observed shapes" },
    { family: "chronology", kind: "generated", explanation: "Generated time" },
    { family: "material", kind: "assumed", explanation: "Synthetic material" },
  ],
  events: [
    {
      sequence: 0,
      event_id: "evt-aaaaaaaaaaaaaaaaaaaa",
      occurred_at: "2026-01-01T00:00:00Z",
      source_task: {
        dataset_id: "lectra-7030786-v1.1",
        tasks_index: 13958,
        task_source_row_index: 13958,
        part_ids: [1],
        part_source_row_indices: [1],
        shape_hashes: ["-8727500516347896752"],
      },
      material: { material_code: "synthetic-mat-a", thickness_index: 1 },
      economics: {
        priority_score: 0.5,
        value_index: 10,
        lead_time_minutes: 60,
        value_unit: "synthetic_index",
      },
    },
  ],
  diagnostics: {
    unique_task_ref_count: 1,
    max_task_concentration: 1,
    shape_recurrence: 1,
    chronological_load: [
      {
        sequence: 0,
        occurred_at: "2026-01-01T00:00:00Z",
        tasks_index: 13958,
        part_count: 1,
        unique_shape_count: 1,
      },
    ],
    task_sizes: {
      event_count: 1,
      total_part_references: 1,
      minimum_parts: 1,
      maximum_parts: 1,
      mean_parts: 1,
    },
    evaluated_thresholds: {},
  },
};
