export const MAX_SAFE_JSON_INTEGER = Number.MAX_SAFE_INTEGER;

export type ProvenanceKind = "source_real" | "derived" | "generated" | "assumed";
export type NormalizationStatus = "source_lossless" | "rejected";
export type SupportStatus =
  | "directly_supported"
  | "runnable_with_explicit_assumptions"
  | "view_only";
export type ProjectionStatus = "not_attempted" | "eligible" | "blocked" | "projected";
export type JobStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "cancelled"
  | "timed_out"
  | "failed"
  | "completed";

export interface CorpusSource {
  dataset_id: "lectra-7030786-v1.1";
  doi: string;
  license: string;
  conversion_ruleset_version: string;
  source_checksums: Array<{ name: string; checksum_algorithm: "md5"; checksum: string }>;
  source_manifest_sha256: string;
  audit_report_sha256: string;
  slice_sha256: string;
  evidence_status:
    | "content_pinned_with_manifest_identity"
    | "fully_bound_to_local_audit_evidence";
}

export interface CoordinateUnit {
  literal_label: "m^-4";
  interpretation: null;
}

export interface SolveCapability {
  can_solve: boolean;
  requires_assumption_acknowledgement: boolean;
  normalization_status: NormalizationStatus;
  support_status: SupportStatus;
  projection_status: ProjectionStatus;
  reason_codes: string[];
  assumption_codes: string[];
}

export interface TaskSource {
  source_row_index: number;
  duration: number;
  efficiency: number;
  sheet_width: number;
  sheet_length: number;
  sheet_type: number;
  tasks_index: number;
  is_train: boolean;
  is_val: boolean;
  is_test: boolean;
}

export interface TaskSummary {
  task: TaskSource;
  tasks_index: number;
  part_count: number;
  shape_count: number;
  constraint_count: number;
  constraint_types: string[];
  solve_capability: SolveCapability;
}

export interface CorpusSummary {
  schema_version: "yieldforge.corpus-summary.v1";
  source: CorpusSource;
  coordinate_unit: CoordinateUnit;
  task_count: number;
  part_count: number;
  shape_count: number;
  constraint_count: number;
  support_status_counts: Array<{ name: SupportStatus; count: number }>;
  constraint_type_counts: Array<{ name: string; count: number }>;
  solve_capability: {
    eligible_task_count: number;
    blocked_task_count: number;
    directly_supported_task_count: number;
  };
}

export interface TaskPage {
  schema_version: "yieldforge.task-page.v1";
  items: TaskSummary[];
  next_cursor: string | null;
}

export type BrowserNumber = number | string;
export type OpaqueValue =
  | { kind: "missing" }
  | { kind: "boolean"; value: boolean }
  | { kind: "integer"; value: string }
  | { kind: "number"; value: number }
  | { kind: "string"; value: string }
  | { kind: "sequence"; items: OpaqueValue[] };

export interface TaskDetail {
  schema_version: "yieldforge.task-detail.v1";
  source: CorpusSource;
  coordinate_unit: CoordinateUnit;
  summary: TaskSummary;
  parts: Array<{
    source_row_index: number;
    tasks_index: number;
    part_id: number;
    shape_hash: string;
  }>;
  shapes: Array<{
    source_row_index: number;
    shape_hash: string;
    raw: BrowserNumber[];
    sizes: number[];
  }>;
  constraints: Array<{
    source_row_index: number;
    tasks_index: number;
    type: string;
    values: OpaqueValue[];
  }>;
  constraint_value_columns: string[];
  derived_geometry: Array<{
    shape_hash: string;
    paired_points: Array<[BrowserNumber, BrowserNumber]>;
    closed_ring: Array<[BrowserNumber, BrowserNumber]>;
    raw_scalar_count: number;
    ring_closure_added: boolean;
    is_simple: boolean;
    is_valid: boolean;
    has_nonzero_area: boolean;
    area: BrowserNumber;
    bounds: [BrowserNumber, BrowserNumber, BrowserNumber, BrowserNumber];
  }>;
  provenance: Array<{ kind: ProvenanceKind; field_paths: string[]; note: string }>;
}

export interface CreateJobInput {
  schema_version: "yieldforge.api-solver-job-request.v1";
  tasks_index: number;
  acknowledged_assumption_codes: string[];
  seed: number;
  total_computation_time: number;
  early_termination: boolean;
  min_items_separation: number | null;
  max_runtime_seconds: number;
}

export interface JobView {
  schema_version: "yieldforge.api-job.v1";
  job_id: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  latest_event_id: number;
  candidate_count: number;
  source_task_binding: {
    dataset_id: string;
    source_slice_sha256: string;
    tasks_index: number;
    acknowledged_assumption_codes: string[];
  } | null;
  archive_available: boolean;
  error_code: string | null;
  error_message: string | null;
}

export interface CompletedRunSettings {
  seed: number;
  total_computation_time: number;
  num_workers: 1;
  early_termination: boolean;
  min_items_separation: number | null;
  max_runtime_seconds: number;
}

export interface CompletedArchiveIdentity {
  schema_version: "yieldforge.candidate-archive.v1";
  batch_sha256: string;
}

export interface CompletedRun {
  schema_version: "yieldforge.api-completed-run.v1";
  job: JobView;
  settings: CompletedRunSettings;
  archive: CompletedArchiveIdentity;
}

export interface CompletedRunPage {
  schema_version: "yieldforge.api-completed-run-page.v1";
  items: CompletedRun[];
}

export interface JobEvent {
  schema_version: "yieldforge.api-job-event.v1";
  job_id: string;
  sequence: number;
  occurred_at: string;
  kind: "status" | "phase" | "candidate" | "terminal";
  status: JobStatus | null;
  phase: "solving" | null;
  candidate_id: string | null;
  candidate_count: number;
  archive_available: boolean;
  error_code: string | null;
  error_message: string | null;
}

export interface CandidateSummary {
  candidate_id: string;
  report_type: "exploration_feasible" | "compression_feasible" | "final";
  seed: number;
  width: number;
  density: number;
  placement_count: number;
}

export interface CandidatePage {
  schema_version: "yieldforge.api-candidate-page.v1";
  items: CandidateSummary[];
  next_cursor: string | null;
}

export interface CandidateGeometry {
  schema_version: "yieldforge.api-candidate-geometry.v1";
  candidate: CandidateSummary;
  sheet: { length: number; width: number };
  provenance: "derived";
  placements: Array<{
    part_id: string;
    rotation: number;
    translation: [number, number];
    projected_shape: Array<[number, number]>;
    svg_points: Array<[number, number]>;
  }>;
}

export interface OrderBook {
  schema_version: "yieldforge.api-order-book.v1";
  manifest_schema_version: "yieldforge.order-book.v1";
  analysis_scope: "analysis_only_full_manifest";
  analysis_warning: string;
  order_book_id: string;
  content_sha256: string;
  generator: { name: string; version: string; algorithm: string };
  source_slice: {
    dataset_id: string;
    repository_path: string;
    content_sha256: string;
    conversion_ruleset_version: string;
    doi: string;
  };
  request: {
    regime: "no_signal" | "exact_recurrence" | "high_mix";
    seed: number;
    event_count: number;
    starts_at: string;
    interval_minutes: number;
    source_slice_sha256: string;
    thresholds: Record<string, unknown> | null;
  };
  field_provenance: Array<{
    family: string;
    kind: "source_observed" | "derived" | "generated" | "assumed";
    explanation: string;
  }>;
  events: Array<{
    sequence: number;
    event_id: string;
    occurred_at: string;
    source_task: {
      dataset_id: string;
      tasks_index: number;
      task_source_row_index: number;
      part_ids: number[];
      part_source_row_indices: number[];
      shape_hashes: string[];
    };
    material: { material_code: string; thickness_index: number };
    economics: {
      priority_score: number;
      value_index: number;
      lead_time_minutes: number;
      value_unit: string;
    };
  }>;
  diagnostics: {
    unique_task_ref_count: number;
    max_task_concentration: number;
    shape_recurrence: number;
    chronological_load: Array<{
      sequence: number;
      occurred_at: string;
      tasks_index: number;
      part_count: number;
      unique_shape_count: number;
    }>;
    task_sizes: {
      event_count: number;
      total_part_references: number;
      minimum_parts: number;
      maximum_parts: number;
      mean_parts: number;
    };
    evaluated_thresholds: Record<string, unknown>;
  };
}

const decimalPattern = /^-?(0|[1-9][0-9]*)$/;

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(
  value: Record<string, unknown>,
  expected: readonly string[],
  label: string,
): void {
  const actual = Object.keys(value).sort();
  const canonical = [...expected].sort();
  if (actual.length !== canonical.length || actual.some((field, index) => field !== canonical[index])) {
    throw new TypeError(`${label} fields are invalid`);
  }
}

function list(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new TypeError(`${label} must be an array`);
  return value;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`${label} must be a nonempty string`);
  }
  return value;
}

function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError(`${label} must be finite`);
  }
  return value;
}

function safeInteger(value: unknown, label: string): number {
  const number = finite(value, label);
  if (!Number.isSafeInteger(number)) throw new TypeError(`${label} must be a safe integer`);
  return number;
}

function nonnegativeSafeInteger(value: unknown, label: string): number {
  const number = safeInteger(value, label);
  if (number < 0) throw new TypeError(`${label} must be nonnegative`);
  return number;
}

function decimal(value: unknown, label: string): string {
  if (typeof value !== "string" || !decimalPattern.test(value)) {
    throw new TypeError(`${label} must be a decimal string`);
  }
  return value;
}

function bool(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new TypeError(`${label} must be boolean`);
  return value;
}

function nullableText(value: unknown, label: string): string | null {
  return value === null ? null : text(value, label);
}

function timestamp(value: unknown, label: string): string {
  const parsed = text(value, label);
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(parsed) ||
    Number.isNaN(Date.parse(parsed))
  ) {
    throw new TypeError(`${label} is invalid`);
  }
  return parsed;
}

function strings(value: unknown, label: string): string[] {
  return list(value, label).map((item, index) => text(item, `${label}[${index}]`));
}

function browserNumber(value: unknown, label: string): BrowserNumber {
  if (typeof value === "string") return decimal(value, label);
  return finite(value, label);
}

function coordinateUnit(value: unknown): CoordinateUnit {
  const item = record(value, "coordinate_unit");
  if (item.literal_label !== "m^-4" || item.interpretation !== null) {
    throw new TypeError("coordinate_unit must preserve m^-4 with null interpretation");
  }
  return item as unknown as CoordinateUnit;
}

function source(value: unknown): CorpusSource {
  const item = record(value, "source");
  text(item.dataset_id, "source.dataset_id");
  text(item.slice_sha256, "source.slice_sha256");
  text(item.source_manifest_sha256, "source.source_manifest_sha256");
  text(item.audit_report_sha256, "source.audit_report_sha256");
  if (
    item.evidence_status !== "content_pinned_with_manifest_identity" &&
    item.evidence_status !== "fully_bound_to_local_audit_evidence"
  ) {
    throw new TypeError("source.evidence_status is invalid");
  }
  return item as unknown as CorpusSource;
}

function capability(value: unknown): SolveCapability {
  const item = record(value, "solve_capability");
  const canSolve = bool(item.can_solve, "solve_capability.can_solve");
  const requiresAcknowledgement = bool(
    item.requires_assumption_acknowledgement,
    "solve_capability.requires_assumption_acknowledgement",
  );
  if (
    !new Set<NormalizationStatus>(["source_lossless", "rejected"]).has(
      item.normalization_status as NormalizationStatus,
    )
  ) {
    throw new TypeError("solve_capability.normalization_status is invalid");
  }
  const supportStatus = supportStatusValue(
    item.support_status,
    "solve_capability.support_status",
  );
  if (
    !new Set<ProjectionStatus>(["not_attempted", "eligible", "blocked", "projected"]).has(
      item.projection_status as ProjectionStatus,
    )
  ) {
    throw new TypeError("solve_capability.projection_status is invalid");
  }
  const reasonCodes = strings(item.reason_codes, "solve_capability.reason_codes");
  const assumptionCodes = strings(item.assumption_codes, "solve_capability.assumption_codes");
  const projectionEligible =
    item.projection_status === "eligible" || item.projection_status === "projected";
  if (canSolve !== projectionEligible) {
    throw new TypeError("solve_capability.can_solve must match projection_status");
  }
  if (requiresAcknowledgement !== (projectionEligible && assumptionCodes.length > 0)) {
    throw new TypeError("solve_capability acknowledgement requirement is invalid");
  }
  if (supportStatus === "directly_supported" && assumptionCodes.length > 0) {
    throw new TypeError("directly supported capability cannot carry assumptions");
  }
  if (supportStatus === "runnable_with_explicit_assumptions" && assumptionCodes.length === 0) {
    throw new TypeError("assumption-backed capability requires assumption codes");
  }
  if (
    (supportStatus === "view_only" || item.projection_status === "blocked") &&
    reasonCodes.length === 0
  ) {
    throw new TypeError("blocked capability requires reason codes");
  }
  return item as unknown as SolveCapability;
}

const supportStatuses = new Set<SupportStatus>([
  "directly_supported",
  "runnable_with_explicit_assumptions",
  "view_only",
]);

function supportStatusValue(value: unknown, label: string): SupportStatus {
  if (typeof value !== "string" || !supportStatuses.has(value as SupportStatus)) {
    throw new TypeError(`${label} is invalid`);
  }
  return value as SupportStatus;
}

function namedCount(value: unknown, label: string): { name: string; count: number } {
  const item = record(value, label);
  return {
    name: text(item.name, `${label}.name`),
    count: nonnegativeSafeInteger(item.count, `${label}.count`),
  };
}

function taskSource(value: unknown): TaskSource {
  const item = record(value, "task");
  for (const field of ["source_row_index", "duration", "sheet_type", "tasks_index"] as const) {
    safeInteger(item[field], `task.${field}`);
  }
  for (const field of ["efficiency", "sheet_width", "sheet_length"] as const) {
    finite(item[field], `task.${field}`);
  }
  for (const field of ["is_train", "is_val", "is_test"] as const) {
    bool(item[field], `task.${field}`);
  }
  return item as unknown as TaskSource;
}

function taskSummary(value: unknown): TaskSummary {
  const item = record(value, "task summary");
  taskSource(item.task);
  for (const field of [
    "tasks_index",
    "part_count",
    "shape_count",
    "constraint_count",
  ] as const) {
    safeInteger(item[field], `task summary.${field}`);
  }
  strings(item.constraint_types, "task summary.constraint_types");
  capability(item.solve_capability);
  return item as unknown as TaskSummary;
}

function opaque(value: unknown, label: string): OpaqueValue {
  const item = record(value, label);
  switch (item.kind) {
    case "missing":
      return item as { kind: "missing" };
    case "boolean":
      bool(item.value, `${label}.value`);
      break;
    case "integer":
      decimal(item.value, `${label}.value`);
      break;
    case "number":
      finite(item.value, `${label}.value`);
      break;
    case "string":
      text(item.value, `${label}.value`);
      break;
    case "sequence":
      list(item.items, `${label}.items`).forEach((entry, index) =>
        opaque(entry, `${label}.items[${index}]`),
      );
      break;
    default:
      throw new TypeError(`${label}.kind is invalid`);
  }
  return item as unknown as OpaqueValue;
}

export function parseCorpusSummary(value: unknown): CorpusSummary {
  const item = record(value, "corpus summary");
  if (item.schema_version !== "yieldforge.corpus-summary.v1") {
    throw new TypeError("corpus summary schema_version is invalid");
  }
  source(item.source);
  coordinateUnit(item.coordinate_unit);
  for (const field of ["task_count", "part_count", "shape_count", "constraint_count"] as const) {
    nonnegativeSafeInteger(item[field], `corpus summary.${field}`);
  }
  list(item.support_status_counts, "corpus summary.support_status_counts").forEach(
    (entry, index) => {
      const count = namedCount(entry, `corpus summary.support_status_counts[${index}]`);
      supportStatusValue(count.name, `corpus summary.support_status_counts[${index}].name`);
    },
  );
  list(item.constraint_type_counts, "corpus summary.constraint_type_counts").forEach(
    (entry, index) => namedCount(entry, `corpus summary.constraint_type_counts[${index}]`),
  );
  const solveCapability = record(item.solve_capability, "corpus summary.solve_capability");
  for (const field of [
    "eligible_task_count",
    "blocked_task_count",
    "directly_supported_task_count",
  ] as const) {
    nonnegativeSafeInteger(solveCapability[field], `corpus summary.solve_capability.${field}`);
  }
  return item as unknown as CorpusSummary;
}

export function parseTaskPage(value: unknown): TaskPage {
  const item = record(value, "task page");
  if (item.schema_version !== "yieldforge.task-page.v1") {
    throw new TypeError("task page schema_version is invalid");
  }
  list(item.items, "task page.items").forEach(taskSummary);
  if (item.next_cursor !== null) text(item.next_cursor, "task page.next_cursor");
  return item as unknown as TaskPage;
}

export function parseTaskDetail(value: unknown): TaskDetail {
  const item = record(value, "task detail");
  if (item.schema_version !== "yieldforge.task-detail.v1") {
    throw new TypeError("task detail schema_version is invalid");
  }
  source(item.source);
  coordinateUnit(item.coordinate_unit);
  taskSummary(item.summary);
  list(item.parts, "parts").forEach((entry, index) => {
    const part = record(entry, `parts[${index}]`);
    safeInteger(part.source_row_index, `parts[${index}].source_row_index`);
    safeInteger(part.tasks_index, `parts[${index}].tasks_index`);
    safeInteger(part.part_id, `parts[${index}].part_id`);
    decimal(part.shape_hash, `parts[${index}].shape_hash`);
  });
  list(item.shapes, "shapes").forEach((entry, index) => {
    const shape = record(entry, `shapes[${index}]`);
    decimal(shape.shape_hash, `shapes[${index}].shape_hash`);
    list(shape.raw, `shapes[${index}].raw`).forEach((number, numberIndex) =>
      browserNumber(number, `shapes[${index}].raw[${numberIndex}]`),
    );
  });
  list(item.constraints, "constraints").forEach((entry, index) => {
    const constraint = record(entry, `constraints[${index}]`);
    safeInteger(constraint.source_row_index, `constraints[${index}].source_row_index`);
    safeInteger(constraint.tasks_index, `constraints[${index}].tasks_index`);
    text(constraint.type, `constraints[${index}].type`);
    list(constraint.values, `constraints[${index}].values`).forEach((cell, cellIndex) =>
      opaque(cell, `constraints[${index}].values[${cellIndex}]`),
    );
  });
  list(item.derived_geometry, "derived_geometry").forEach((entry, index) => {
    const geometry = record(entry, `derived_geometry[${index}]`);
    decimal(geometry.shape_hash, `derived_geometry[${index}].shape_hash`);
    for (const field of ["paired_points", "closed_ring"] as const) {
      list(geometry[field], `derived_geometry[${index}].${field}`).forEach((rawPoint) => {
        const point = list(rawPoint, "geometry point");
        if (point.length !== 2) throw new TypeError("geometry point must contain two values");
        browserNumber(point[0], "geometry point x");
        browserNumber(point[1], "geometry point y");
      });
    }
  });
  return item as unknown as TaskDetail;
}

const statuses = new Set<JobStatus>([
  "queued",
  "running",
  "cancelling",
  "cancelled",
  "timed_out",
  "failed",
  "completed",
]);

function status(value: unknown, label: string): JobStatus {
  if (typeof value !== "string" || !statuses.has(value as JobStatus)) {
    throw new TypeError(`${label} is invalid`);
  }
  return value as JobStatus;
}

export function parseJobView(value: unknown): JobView {
  const item = record(value, "job");
  if (item.schema_version !== "yieldforge.api-job.v1") throw new TypeError("job schema invalid");
  text(item.job_id, "job.job_id");
  status(item.status, "job.status");
  safeInteger(item.latest_event_id, "job.latest_event_id");
  safeInteger(item.candidate_count, "job.candidate_count");
  bool(item.archive_available, "job.archive_available");
  nullableText(item.error_code, "job.error_code");
  nullableText(item.error_message, "job.error_message");
  return item as unknown as JobView;
}

const sha256Pattern = /^[0-9a-f]{64}$/;

function completedJob(value: unknown): JobView {
  const item = record(value, "completed run.job");
  exactFields(
    item,
    [
      "schema_version",
      "job_id",
      "status",
      "created_at",
      "updated_at",
      "latest_event_id",
      "candidate_count",
      "source_task_binding",
      "archive_available",
      "error_code",
      "error_message",
    ],
    "completed run.job",
  );
  const parsed = parseJobView(item);
  if (parsed.status !== "completed" || !parsed.archive_available) {
    throw new TypeError("completed run.job must identify an available completed archive");
  }
  timestamp(parsed.created_at, "completed run.job.created_at");
  timestamp(parsed.updated_at, "completed run.job.updated_at");
  if (parsed.source_task_binding === null) {
    throw new TypeError("completed run.job.source_task_binding is required");
  }
  const binding = record(parsed.source_task_binding, "completed run.job.source_task_binding");
  exactFields(
    binding,
    [
      "dataset_id",
      "source_slice_sha256",
      "tasks_index",
      "acknowledged_assumption_codes",
    ],
    "completed run.job.source_task_binding",
  );
  text(binding.dataset_id, "completed run.job.source_task_binding.dataset_id");
  if (
    typeof binding.source_slice_sha256 !== "string" ||
    !sha256Pattern.test(binding.source_slice_sha256)
  ) {
    throw new TypeError("completed run.job.source_task_binding.source_slice_sha256 is invalid");
  }
  nonnegativeSafeInteger(binding.tasks_index, "completed run.job.source_task_binding.tasks_index");
  strings(
    binding.acknowledged_assumption_codes,
    "completed run.job.source_task_binding.acknowledged_assumption_codes",
  );
  return parsed;
}

export function parseCompletedRunPage(value: unknown): CompletedRunPage {
  const page = record(value, "completed run page");
  exactFields(page, ["schema_version", "items"], "completed run page");
  if (page.schema_version !== "yieldforge.api-completed-run-page.v1") {
    throw new TypeError("completed run page schema is invalid");
  }
  const items = list(page.items, "completed run page.items").map((value, index) => {
    const run = record(value, `completed run page.items[${index}]`);
    exactFields(
      run,
      ["schema_version", "job", "settings", "archive"],
      `completed run page.items[${index}]`,
    );
    if (run.schema_version !== "yieldforge.api-completed-run.v1") {
      throw new TypeError(`completed run page.items[${index}] schema is invalid`);
    }
    const job = completedJob(run.job);
    const settings = record(run.settings, `completed run page.items[${index}].settings`);
    exactFields(
      settings,
      [
        "seed",
        "total_computation_time",
        "num_workers",
        "early_termination",
        "min_items_separation",
        "max_runtime_seconds",
      ],
      `completed run page.items[${index}].settings`,
    );
    safeInteger(settings.seed, "completed run.settings.seed");
    const computation = safeInteger(
      settings.total_computation_time,
      "completed run.settings.total_computation_time",
    );
    if (computation <= 0) throw new TypeError("completed run.settings.total_computation_time is invalid");
    if (settings.num_workers !== 1) {
      throw new TypeError("completed run.settings.num_workers must equal one");
    }
    bool(settings.early_termination, "completed run.settings.early_termination");
    if (settings.min_items_separation !== null) {
      const separation = finite(
        settings.min_items_separation,
        "completed run.settings.min_items_separation",
      );
      if (separation < 0) throw new TypeError("completed run.settings.min_items_separation is invalid");
    }
    const runtime = finite(settings.max_runtime_seconds, "completed run.settings.max_runtime_seconds");
    if (runtime <= 0 || runtime > 10 || computation > runtime) {
      throw new TypeError("completed run.settings.max_runtime_seconds is invalid");
    }
    const archive = record(run.archive, `completed run page.items[${index}].archive`);
    exactFields(
      archive,
      ["schema_version", "batch_sha256"],
      `completed run page.items[${index}].archive`,
    );
    if (
      archive.schema_version !== "yieldforge.candidate-archive.v1" ||
      typeof archive.batch_sha256 !== "string" ||
      !sha256Pattern.test(archive.batch_sha256)
    ) {
      throw new TypeError("completed run archive sha256 is invalid");
    }
    return {
      schema_version: "yieldforge.api-completed-run.v1" as const,
      job,
      settings: settings as unknown as CompletedRunSettings,
      archive: archive as unknown as CompletedArchiveIdentity,
    };
  });
  return {
    schema_version: "yieldforge.api-completed-run-page.v1",
    items,
  };
}

export function parseJobEvent(value: unknown): JobEvent {
  const item = record(value, "job event");
  if (item.schema_version !== "yieldforge.api-job-event.v1") {
    throw new TypeError("job event schema invalid");
  }
  const normalized = {
    ...item,
    status: item.status ?? null,
    phase: item.phase ?? null,
    candidate_id: item.candidate_id ?? null,
    error_code: item.error_code ?? null,
    error_message: item.error_message ?? null,
  };
  text(item.job_id, "job event.job_id");
  safeInteger(item.sequence, "job event.sequence");
  text(item.occurred_at, "job event.occurred_at");
  if (normalized.status !== null) status(normalized.status, "job event.status");
  if (!new Set(["status", "phase", "candidate", "terminal"]).has(String(item.kind))) {
    throw new TypeError("job event.kind is invalid");
  }
  if (normalized.phase !== null && normalized.phase !== "solving") {
    throw new TypeError("job event.phase is invalid");
  }
  safeInteger(item.candidate_count, "job event.candidate_count");
  if (normalized.candidate_id !== null) {
    text(normalized.candidate_id, "job event.candidate_id");
  }
  bool(item.archive_available, "job event.archive_available");
  nullableText(normalized.error_code, "job event.error_code");
  nullableText(normalized.error_message, "job event.error_message");
  return normalized as unknown as JobEvent;
}

function candidate(value: unknown): CandidateSummary {
  const item = record(value, "candidate");
  text(item.candidate_id, "candidate.candidate_id");
  finite(item.width, "candidate.width");
  finite(item.density, "candidate.density");
  safeInteger(item.seed, "candidate.seed");
  safeInteger(item.placement_count, "candidate.placement_count");
  return item as unknown as CandidateSummary;
}

export function parseCandidatePage(value: unknown): CandidatePage {
  const item = record(value, "candidate page");
  if (item.schema_version !== "yieldforge.api-candidate-page.v1") {
    throw new TypeError("candidate page schema invalid");
  }
  list(item.items, "candidate page.items").forEach(candidate);
  return item as unknown as CandidatePage;
}

export function parseCandidateGeometry(value: unknown): CandidateGeometry {
  const item = record(value, "candidate geometry");
  if (item.schema_version !== "yieldforge.api-candidate-geometry.v1") {
    throw new TypeError("candidate geometry schema invalid");
  }
  candidate(item.candidate);
  const sheet = record(item.sheet, "candidate geometry.sheet");
  finite(sheet.length, "candidate geometry.sheet.length");
  finite(sheet.width, "candidate geometry.sheet.width");
  list(item.placements, "candidate geometry.placements").forEach((entry, index) => {
    const placement = record(entry, `placement[${index}]`);
    text(placement.part_id, `placement[${index}].part_id`);
    for (const field of ["translation", "projected_shape", "svg_points"] as const) {
      const points = field === "translation" ? [placement[field]] : list(placement[field], field);
      points.forEach((rawPoint) => {
        const point = list(rawPoint, field);
        if (point.length !== 2) throw new TypeError(`${field} point is invalid`);
        finite(point[0], `${field}.x`);
        finite(point[1], `${field}.y`);
      });
    }
  });
  return item as unknown as CandidateGeometry;
}

export function parseOrderBook(value: unknown): OrderBook {
  const item = record(value, "order book");
  if (
    item.schema_version !== "yieldforge.api-order-book.v1" ||
    item.manifest_schema_version !== "yieldforge.order-book.v1" ||
    item.analysis_scope !== "analysis_only_full_manifest"
  ) {
    throw new TypeError("order book analysis schema is invalid");
  }
  text(item.analysis_warning, "order book.analysis_warning");
  text(item.order_book_id, "order book.order_book_id");
  text(item.content_sha256, "order book.content_sha256");
  const request = record(item.request, "order book.request");
  if (!new Set(["no_signal", "exact_recurrence", "high_mix"]).has(String(request.regime))) {
    throw new TypeError("order book.request.regime is invalid");
  }
  safeInteger(request.event_count, "order book.request.event_count");
  list(item.field_provenance, "order book.field_provenance").forEach((entry, index) => {
    const provenance = record(entry, `field_provenance[${index}]`);
    text(provenance.family, `field_provenance[${index}].family`);
    text(provenance.kind, `field_provenance[${index}].kind`);
    text(provenance.explanation, `field_provenance[${index}].explanation`);
  });
  list(item.events, "order book.events").forEach((entry, index) => {
    const event = record(entry, `events[${index}]`);
    safeInteger(event.sequence, `events[${index}].sequence`);
    const task = record(event.source_task, `events[${index}].source_task`);
    safeInteger(task.tasks_index, `events[${index}].source_task.tasks_index`);
    list(task.shape_hashes, `events[${index}].source_task.shape_hashes`).forEach(
      (hash, hashIndex) =>
        decimal(hash, `events[${index}].source_task.shape hash[${hashIndex}]`),
    );
  });
  const diagnostics = record(item.diagnostics, "order book.diagnostics");
  finite(diagnostics.shape_recurrence, "order book.diagnostics.shape_recurrence");
  finite(
    diagnostics.max_task_concentration,
    "order book.diagnostics.max_task_concentration",
  );
  return item as unknown as OrderBook;
}
