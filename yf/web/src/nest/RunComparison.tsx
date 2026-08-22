import type { CompletedRun } from "../contracts";

interface RunComparisonProps {
  runs: CompletedRun[];
  runA: CompletedRun | null;
  runB: CompletedRun | null;
  runBId: string | null;
  disabled: boolean;
  onRunBChange: (jobId: string | null) => void;
}

interface ComparisonRow {
  label: string;
  runA: string;
  runB: string;
  mono?: boolean;
}

function completionTimestamp(value: string): string {
  return new Date(value).toISOString().replace(".000Z", "Z").replace("T", " ");
}

function assumptions(run: CompletedRun): string {
  const codes = run.job.source_task_binding?.acknowledged_assumption_codes ?? [];
  return codes.length > 0 ? codes.join(", ") : "none";
}

function dataset(run: CompletedRun): string {
  return run.job.source_task_binding?.dataset_id ?? "unavailable";
}

function sourceSliceHash(run: CompletedRun): string {
  return run.job.source_task_binding?.source_slice_sha256 ?? "unavailable";
}

function comparisonRows(runA: CompletedRun, runB: CompletedRun): ComparisonRow[] {
  return [
    { label: "Job ID", runA: runA.job.job_id, runB: runB.job.job_id, mono: true },
    {
      label: "Completed at",
      runA: completionTimestamp(runA.job.updated_at),
      runB: completionTimestamp(runB.job.updated_at),
      mono: true,
    },
    { label: "Seed", runA: String(runA.settings.seed), runB: String(runB.settings.seed) },
    {
      label: "Computation budget",
      runA: `${runA.settings.total_computation_time}s`,
      runB: `${runB.settings.total_computation_time}s`,
    },
    {
      label: "Hard runtime limit",
      runA: `${runA.settings.max_runtime_seconds}s`,
      runB: `${runB.settings.max_runtime_seconds}s`,
    },
    {
      label: "Workers",
      runA: String(runA.settings.num_workers),
      runB: String(runB.settings.num_workers),
    },
    {
      label: "Early termination",
      runA: runA.settings.early_termination ? "on" : "off",
      runB: runB.settings.early_termination ? "on" : "off",
    },
    {
      label: "Minimum separation",
      runA: runA.settings.min_items_separation?.toString() ?? "none",
      runB: runB.settings.min_items_separation?.toString() ?? "none",
    },
    {
      label: "Archived candidates",
      runA: String(runA.job.candidate_count),
      runB: String(runB.job.candidate_count),
    },
    { label: "Acknowledged assumptions", runA: assumptions(runA), runB: assumptions(runB) },
    { label: "Dataset", runA: dataset(runA), runB: dataset(runB), mono: true },
    {
      label: "Source slice SHA-256",
      runA: sourceSliceHash(runA),
      runB: sourceSliceHash(runB),
      mono: true,
    },
    {
      label: "Archive schema",
      runA: runA.archive.schema_version,
      runB: runB.archive.schema_version,
      mono: true,
    },
    {
      label: "Archive SHA-256",
      runA: runA.archive.batch_sha256,
      runB: runB.archive.batch_sha256,
      mono: true,
    },
  ];
}

export function RunComparison({
  runs,
  runA,
  runB,
  runBId,
  disabled,
  onRunBChange,
}: RunComparisonProps) {
  const availableRuns = runs.filter((run) => run.job.job_id !== runA?.job.job_id);

  return (
    <section
      className="run-comparison"
      role="region"
      aria-labelledby="run-comparison-heading"
    >
      <h3 id="run-comparison-heading">Read-only run comparison</h3>
      <p>
        Exact immutable run evidence only. Same and Different are descriptive, not rankings.
      </p>
      {runs.length < 2 ? (
        <p className="run-comparison__empty">
          Complete another archive run for this task to compare two records.
        </p>
      ) : (
        <label className="run-comparison__controls">
          Compare open run with
          <select
            value={runBId ?? ""}
            disabled={disabled || runA === null}
            onChange={(event) => onRunBChange(event.target.value || null)}
          >
            <option value="">Choose a completed run</option>
            {availableRuns.map((run) => (
              <option key={run.job.job_id} value={run.job.job_id}>
                {run.job.job_id} · seed {run.settings.seed} · {completionTimestamp(run.job.updated_at)}
              </option>
            ))}
          </select>
        </label>
      )}
      {runA && runB ? (
        <div className="run-comparison__table-wrap">
          <table aria-label="Recorded run evidence">
            <caption>
              Exact recorded fields. Archived candidate count is inventory, not quality.
            </caption>
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Run A · open</th>
                <th scope="col">Run B · comparison</th>
                <th scope="col">Relation</th>
              </tr>
            </thead>
            <tbody>
              {comparisonRows(runA, runB).map((row) => {
                const relation = row.runA === row.runB ? "Same" : "Different";
                return (
                  <tr key={row.label}>
                    <th scope="row">{row.label}</th>
                    <td className={row.mono ? "mono" : undefined}>{row.runA}</td>
                    <td className={row.mono ? "mono" : undefined}>{row.runB}</td>
                    <td className="run-comparison__relation">{relation}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
