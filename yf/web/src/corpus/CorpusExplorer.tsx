import { useCallback, useEffect, useRef, useState } from "react";

import type { TaskFilters, WorkbenchClient } from "../api";
import type {
  CorpusSummary,
  SupportStatus,
  TaskDetail,
  TaskSummary,
} from "../contracts";
import { ProvenanceMark } from "../components/Provenance";
import { SourceGeometry } from "../components/SourceGeometry";

type ListPhase = "idle" | "loading" | "loading-more" | "ready";
type DetailPhase = "idle" | "loading" | "ready";

function taskHref(view: "corpus" | "nest", tasksIndex: number) {
  return `/?view=${view}&task=${tasksIndex}`;
}

function supportLabel(status: SupportStatus) {
  switch (status) {
    case "directly_supported":
      return "Directly supported";
    case "runnable_with_explicit_assumptions":
      return "Assumption-backed";
    case "view_only":
      return "View only";
  }
}

function tableSupportLabel(item: TaskSummary) {
  return item.solve_capability.support_status === "view_only"
    ? "View only · blocked"
    : supportLabel(item.solve_capability.support_status);
}

function appendUnique(current: TaskSummary[], incoming: TaskSummary[]) {
  const known = new Set(current.map((item) => item.tasks_index));
  return [...current, ...incoming.filter((item) => !known.has(item.tasks_index))];
}

export function CorpusExplorer({
  client,
  initialTask,
  navigate,
  summary,
  summaryLoading,
  summaryError,
}: {
  client: WorkbenchClient;
  initialTask: number | null;
  navigate: (url: string) => void;
  summary: CorpusSummary | null;
  summaryLoading: boolean;
  summaryError: string | null;
}) {
  const [items, setItems] = useState<TaskSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [filters, setFilters] = useState<TaskFilters>({});
  const [listPhase, setListPhase] = useState<ListPhase>("idle");
  const [listError, setListError] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [selectedTask, setSelectedTask] = useState<number | null>(initialTask);
  const [detailPhase, setDetailPhase] = useState<DetailPhase>("idle");
  const [detailError, setDetailError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const listGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const initialTaskRef = useRef(initialTask);
  const filtersRef = useRef(filters);
  initialTaskRef.current = initialTask;
  filtersRef.current = filters;

  const clearDetail = useCallback(() => {
    detailGeneration.current += 1;
    setSelectedTask(null);
    setDetail(null);
    setDetailPhase("idle");
    setDetailError(null);
  }, []);

  const requestDetail = useCallback(
    (tasksIndex: number) => {
      const generation = ++detailGeneration.current;
      setSelectedTask(tasksIndex);
      setDetail(null);
      setDetailPhase("loading");
      setDetailError(null);
      void client
        .getTask(tasksIndex)
        .then((value) => {
          if (generation !== detailGeneration.current) return;
          setDetail(value);
          setDetailPhase("ready");
        })
        .catch((reason: unknown) => {
          if (generation !== detailGeneration.current) return;
          setDetailPhase("idle");
          setDetailError(String(reason));
        });
    },
    [client],
  );

  useEffect(() => {
    if (initialTask !== null) requestDetail(initialTask);
  }, [initialTask, requestDetail]);

  useEffect(() => {
    const generation = ++listGeneration.current;
    setItems([]);
    setNextCursor(null);
    setListPhase("loading");
    setListError(null);
    void client
      .listTasks(filters)
      .then((page) => {
        if (generation !== listGeneration.current) return;
        setItems(page.items);
        setNextCursor(page.next_cursor);
        setListPhase("ready");
        if (initialTaskRef.current === null) {
          const firstTask = page.items[0]?.tasks_index;
          if (firstTask === undefined) clearDetail();
          else requestDetail(firstTask);
        }
      })
      .catch((reason: unknown) => {
        if (generation !== listGeneration.current) return;
        setListPhase("idle");
        setListError(String(reason));
      });
    return () => {
      if (listGeneration.current === generation) listGeneration.current += 1;
    };
  }, [clearDetail, client, filters, requestDetail]);

  const resetForFilters = (nextFilters: TaskFilters) => {
    listGeneration.current += 1;
    clearDetail();
    navigate("/?view=corpus");
    setFilters(nextFilters);
  };

  const loadNext = () => {
    if (nextCursor === null || listPhase === "loading-more") return;
    const generation = listGeneration.current;
    const cursor = nextCursor;
    setListPhase("loading-more");
    setListError(null);
    void client
      .listTasks({ ...filtersRef.current, cursor })
      .then((page) => {
        if (generation !== listGeneration.current) return;
        setItems((current) => appendUnique(current, page.items));
        setNextCursor(page.next_cursor);
        setListPhase("ready");
      })
      .catch((reason: unknown) => {
        if (generation !== listGeneration.current) return;
        setListPhase("ready");
        setListError(String(reason));
      });
  };

  return (
    <section aria-labelledby="corpus-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Qualified source catalog</p>
          <h1 id="corpus-heading">Corpus Explorer</h1>
        </div>
        <ProvenanceMark kind="source_real" />
      </div>

      {summaryLoading ? <p role="status">Loading corpus summary…</p> : null}
      {summaryError ? <p className="notice notice--error">Corpus summary: {summaryError}</p> : null}

      <form
        ref={formRef}
        className="filter-bar"
        aria-label="Task filters"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const taskId = String(data.get("taskId") ?? "").trim();
          const minParts = String(data.get("minParts") ?? "").trim();
          const maxParts = String(data.get("maxParts") ?? "").trim();
          const status = String(data.get("status") ?? "") as SupportStatus | "";
          const constraintType = String(data.get("constraintType") ?? "");
          resetForFilters({
            ...(status ? { status } : {}),
            ...(constraintType ? { constraintType } : {}),
            ...(taskId ? { taskId: Number(taskId) } : {}),
            ...(minParts ? { minParts: Number(minParts) } : {}),
            ...(maxParts ? { maxParts: Number(maxParts) } : {}),
          });
        }}
      >
        <label>
          Task ID
          <input name="taskId" inputMode="numeric" pattern="[0-9]*" />
        </label>
        <label>
          Status
          <select name="status" defaultValue="">
            <option value="">All</option>
            {summary?.support_status_counts.map((entry) => (
              <option key={entry.name} value={entry.name}>
                {supportLabel(entry.name)} ({entry.count})
              </option>
            ))}
          </select>
        </label>
        <label>
          Constraint
          <select name="constraintType" defaultValue="">
            <option value="">All</option>
            {summary?.constraint_type_counts.map((entry) => (
              <option key={entry.name} value={entry.name}>
                {entry.name} ({entry.count} rows)
              </option>
            ))}
          </select>
        </label>
        <label>
          Min parts
          <input name="minParts" type="number" min="0" />
        </label>
        <label>
          Max parts
          <input name="maxParts" type="number" min="0" />
        </label>
        <div className="filter-actions">
          <button type="submit">Apply filters</button>
          <button
            type="button"
            className="button--secondary"
            onClick={() => {
              formRef.current?.reset();
              resetForFilters({});
            }}
          >
            Clear filters
          </button>
        </div>
      </form>

      {listError ? <p className="notice notice--error">Task list: {listError}</p> : null}
      <div className="workbench-grid workbench-grid--corpus">
        <div className="panel table-panel">
          <div className="table-toolbar">
            <p aria-live="polite">
              {listPhase === "loading"
                ? "Loading first 50 tasks…"
                : `Loaded ${items.length} of ${summary?.task_count ?? "?"} tasks`}
            </p>
            {nextCursor !== null ? (
              <button type="button" disabled={listPhase === "loading-more"} onClick={loadNext}>
                {listPhase === "loading-more" ? "Loading next 50 tasks…" : "Load next 50 tasks"}
              </button>
            ) : null}
          </div>
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
              {items.map((item) => (
                <tr key={item.tasks_index} data-selected={selectedTask === item.tasks_index}>
                  <th scope="row">
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => navigate(taskHref("corpus", item.tasks_index))}
                    >
                      {item.tasks_index}
                    </button>
                  </th>
                  <td>{item.part_count}</td>
                  <td>{item.constraint_types.join(", ")}</td>
                  <td>{tableSupportLabel(item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <article className="panel inspector">
          {detailError ? <p className="notice notice--error">Task detail: {detailError}</p> : null}
          {detail ? (
            <>
              <div className="inspector-title">
                <div>
                  <p className="eyebrow">Task source row {detail.summary.task.source_row_index}</p>
                  <h2>Task {detail.summary.tasks_index}</h2>
                </div>
                {detail.summary.solve_capability.support_status === "directly_supported" ? (
                  <span className="status status--success">Directly supported</span>
                ) : detail.summary.solve_capability.support_status ===
                  "runnable_with_explicit_assumptions" ? (
                  <span className="status status--assumed">Assumption-backed</span>
                ) : (
                  <span className="status status--blocked">View only</span>
                )}
              </div>

              {detail.summary.solve_capability.assumption_codes.length > 0 ? (
                <div className="notice notice--assumed">
                  <ProvenanceMark kind="assumed" />
                  <p>{detail.summary.solve_capability.assumption_codes.join(", ")}</p>
                </div>
              ) : detail.summary.solve_capability.can_solve ? (
                <div className="notice notice--success">
                  <strong>Directly supported projection</strong>
                  <p>No assumption acknowledgement required.</p>
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
            <p>
              {items.length === 0 && listPhase === "ready"
                ? "No tasks match the current filters."
                : detailPhase === "loading"
                  ? "Loading task evidence…"
                  : "Select a task to inspect its evidence."}
            </p>
          )}
        </article>
      </div>
    </section>
  );
}
