import { useEffect, useState } from "react";

import type { TaskDetail, TaskPage } from "../contracts";
import type { TaskFilters, WorkbenchClient } from "../api";
import { ProvenanceMark } from "../components/Provenance";
import { SourceGeometry } from "../components/SourceGeometry";

function taskHref(view: "corpus" | "nest", tasksIndex: number) {
  return `/?view=${view}&task=${tasksIndex}`;
}

export function CorpusExplorer({
  client,
  initialTask,
  navigate,
}: {
  client: WorkbenchClient;
  initialTask: number | null;
  navigate: (url: string) => void;
}) {
  const [page, setPage] = useState<TaskPage | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [filters, setFilters] = useState<TaskFilters>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    setPage(null);
    setDetail(null);
    client
      .listTasks(filters)
      .then((value) => {
        if (!active) return;
        setPage(value);
        const selected = initialTask !== null && value.items.some((item) => item.tasks_index === initialTask)
          ? initialTask
          : value.items[0]?.tasks_index;
        if (selected !== undefined) return client.getTask(selected);
        return null;
      })
      .then((value) => {
        if (active) setDetail(value ?? null);
      })
      .catch((reason: unknown) => active && setError(String(reason)));
    return () => {
      active = false;
    };
  }, [client, filters, initialTask]);

  const selectTask = (tasksIndex: number) => {
    navigate(taskHref("corpus", tasksIndex));
    void client.getTask(tasksIndex).then(setDetail).catch((reason: unknown) => setError(String(reason)));
  };

  return (
    <section aria-labelledby="corpus-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Normalized source slice</p>
          <h1 id="corpus-heading">Corpus Explorer</h1>
        </div>
        <ProvenanceMark kind="source_real" />
      </div>

      <form
        className="filter-bar"
        aria-label="Task filters"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const taskId = String(data.get("taskId") ?? "").trim();
          const minParts = String(data.get("minParts") ?? "").trim();
          const status = String(data.get("status") ?? "");
          const constraintType = String(data.get("constraintType") ?? "");
          setFilters({
            ...(status ? { status } : {}),
            ...(constraintType ? { constraintType } : {}),
            ...(taskId ? { taskId: Number(taskId) } : {}),
            ...(minParts ? { minParts: Number(minParts) } : {}),
          });
        }}
      >
        <label>
          Task ID
          <input name="taskId" inputMode="numeric" />
        </label>
        <label>
          Status
          <select name="status" defaultValue="">
            <option value="">All</option>
            <option value="runnable_with_explicit_assumptions">Assumption-backed</option>
            <option value="view_only">View only</option>
          </select>
        </label>
        <label>
          Constraint
          <select name="constraintType" defaultValue="">
            <option value="">All</option>
            <option value="s1">s1</option>
            <option value="c8">c8</option>
          </select>
        </label>
        <label>
          Min parts
          <input name="minParts" type="number" min="0" />
        </label>
        <button type="submit">Apply filters</button>
      </form>

      {error ? <p className="notice notice--error">{error}</p> : null}
      <div className="workbench-grid workbench-grid--corpus">
        <div className="panel table-panel">
          <table aria-label="Corpus tasks">
            <caption>Tasks in stable source order</caption>
            <thead>
              <tr>
                <th scope="col">Task</th>
                <th scope="col">Parts</th>
                <th scope="col">Constraints</th>
                <th scope="col">Support</th>
              </tr>
            </thead>
            <tbody>
              {page?.items.map((item) => (
                <tr key={item.tasks_index} data-selected={detail?.summary.tasks_index === item.tasks_index}>
                  <th scope="row">
                    <button className="link-button" onClick={() => selectTask(item.tasks_index)}>
                      {item.tasks_index}
                    </button>
                  </th>
                  <td>{item.part_count}</td>
                  <td>{item.constraint_types.join(", ")}</td>
                  <td>{item.solve_capability.can_solve ? "ASM · eligible" : "blocked"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <article className="panel inspector">
          {detail ? (
            <>
              <div className="inspector-title">
                <div>
                  <p className="eyebrow">Task source row {detail.summary.task.source_row_index}</p>
                  <h2>Task {detail.summary.tasks_index}</h2>
                </div>
                {detail.summary.solve_capability.can_solve ? (
                  <span className="status status--assumed">Assumption-backed</span>
                ) : (
                  <span className="status status--blocked">Blocked</span>
                )}
              </div>

              {detail.summary.solve_capability.can_solve ? (
                <div className="notice notice--assumed">
                  <ProvenanceMark kind="assumed" />
                  <p>{detail.summary.solve_capability.assumption_codes.join(", ")}</p>
                </div>
              ) : (
                <div className="notice notice--error">
                  <strong>Blocked from solver projection</strong>
                  <p>{detail.summary.solve_capability.reason_codes.join(", ")}</p>
                </div>
              )}

              <dl className="fact-grid">
                <div><dt>Sheet length</dt><dd>{detail.summary.task.sheet_length}</dd></div>
                <div><dt>Sheet width</dt><dd>{detail.summary.task.sheet_width}</dd></div>
                <div><dt>Parts</dt><dd>{detail.summary.part_count}</dd></div>
                <div><dt>Shapes</dt><dd>{detail.summary.shape_count}</dd></div>
              </dl>

              <h3><ProvenanceMark kind="derived" /> Polygon view</h3>
              <SourceGeometry detail={detail} />

              <div className="action-row">
                {detail.summary.solve_capability.can_solve ? (
                  <a className="button" href={taskHref("nest", detail.summary.tasks_index)}>
                    Solve task {detail.summary.tasks_index}
                  </a>
                ) : (
                  <button disabled aria-label={`Solve task ${detail.summary.tasks_index}`}>
                    Solve unavailable
                  </button>
                )}
              </div>

              <details>
                <summary>Source part rows ({detail.parts.length})</summary>
                <table aria-label="Source part rows">
                  <thead><tr><th>Row</th><th>Part</th><th>Shape hash</th></tr></thead>
                  <tbody>
                    {detail.parts.slice(0, 50).map((part) => (
                      <tr key={part.source_row_index}>
                        <td>{part.source_row_index}</td><td>{part.part_id}</td><td>{part.shape_hash}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
              <details>
                <summary>Constraint rows ({detail.constraints.length})</summary>
                <p className="mono">{detail.constraint_value_columns.join(" · ")}</p>
                <table aria-label="Source constraint rows">
                  <thead><tr><th>Row</th><th>Type</th><th>Task</th></tr></thead>
                  <tbody>
                    {detail.constraints.slice(0, 50).map((constraint) => (
                      <tr key={constraint.source_row_index}>
                        <td>{constraint.source_row_index}</td><td>{constraint.type}</td><td>{constraint.tasks_index}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </>
          ) : (
            <p>{page && page.items.length === 0 ? "No tasks match the current filters." : "Loading task evidence…"}</p>
          )}
        </article>
      </div>
    </section>
  );
}
