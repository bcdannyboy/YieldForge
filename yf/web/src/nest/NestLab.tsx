import { useEffect, useMemo, useReducer, useRef, useState } from "react";

import type { WorkbenchClient } from "../api";
import type { CandidateGeometry, CandidateSummary, JobView, TaskDetail } from "../contracts";
import { ProvenanceMark } from "../components/Provenance";
import { toSvgPoints, transformPlacedPoints } from "../geometry";
import { initialJobState, jobReducer } from "../jobs/jobReducer";
import { reconcileCandidates } from "../jobs/reconcile";

const terminal = new Set(["cancelled", "timed_out", "failed", "completed"]);

function CandidateCanvas({ geometry }: { geometry: CandidateGeometry }) {
  return (
    <figure className="nest-canvas">
      <svg
        viewBox={`0 0 ${geometry.sheet.length} ${geometry.sheet.width}`}
        role="img"
        aria-label={`Candidate ${geometry.candidate.candidate_id} placement geometry`}
        preserveAspectRatio="xMinYMin meet"
      >
        <rect x="0" y="0" width={geometry.sheet.length} height={geometry.sheet.width} />
        {geometry.placements.map((placement) => {
          const points = transformPlacedPoints(
            placement.projected_shape,
            placement.rotation,
            placement.translation,
            geometry.sheet.width,
          );
          return <polygon key={placement.part_id} points={toSvgPoints(points)} />;
        })}
      </svg>
      <figcaption><ProvenanceMark kind="derived" /> Explicit rotate → translate → Y-flip</figcaption>
    </figure>
  );
}

export function NestLab({ client, tasksIndex }: { client: WorkbenchClient; tasksIndex: number | null }) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [job, setJob] = useState<JobView | null>(null);
  const [stream, dispatch] = useReducer(jobReducer, initialJobState);
  const [terminalCandidates, setTerminalCandidates] = useState<CandidateSummary[]>([]);
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(null);
  const [geometry, setGeometry] = useState<CandidateGeometry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const manualSelection = useRef(false);
  const lastSequence = useRef(0);
  const effectiveStatus = job
    ? stream.status === "idle"
      ? job.status
      : stream.status
    : "idle";

  useEffect(() => {
    if (tasksIndex === null) return;
    let active = true;
    setAcknowledged(false);
    setDetail(null);
    setJob(null);
    dispatch({ type: "reset" });
    lastSequence.current = 0;
    setTerminalCandidates([]);
    setSelectedCandidate(null);
    setGeometry(null);
    manualSelection.current = false;
    setReconnectAttempt(0);
    setError(null);
    void Promise.all([client.getTask(tasksIndex), client.listTaskJobs(tasksIndex)])
      .then(([task, completedJobs]) => {
        if (!active) return;
        setDetail(task);
        setJob(completedJobs.items.at(-1) ?? null);
      })
      .catch((reason: unknown) => active && setError(String(reason)));
    return () => {
      active = false;
    };
  }, [client, tasksIndex]);

  useEffect(() => {
    if (!job || terminal.has(job.status)) return;
    const controller = new AbortController();
    let reconnectTimer: number | null = null;
    let reachedTerminal = false;
    const reconnect = () => {
      dispatch({ type: "disconnected" });
      reconnectTimer = window.setTimeout(() => setReconnectAttempt((value) => value + 1), 300);
    };
    dispatch({ type: "connected" });
    let cursor = lastSequence.current;
    void client
      .streamJobEvents(job.job_id, cursor, controller.signal, (event) => {
        if (event.sequence !== cursor + 1) {
          controller.abort();
          reconnect();
          return;
        }
        cursor = event.sequence;
        lastSequence.current = cursor;
        reachedTerminal = event.kind === "terminal";
        dispatch({ type: "event", event });
      })
      .then(() => {
        if (!controller.signal.aborted && !reachedTerminal) reconnect();
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          reconnect();
          setError(String(reason));
        }
      });
    return () => {
      controller.abort();
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    };
  }, [client, job?.job_id, reconnectAttempt]);

  useEffect(() => {
    if (!job || terminal.has(effectiveStatus)) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [effectiveStatus, job]);

  useEffect(() => {
    const latest = stream.liveCandidateIds.at(-1);
    if (latest && !manualSelection.current) setSelectedCandidate(latest);
  }, [stream.liveCandidateIds]);

  useEffect(() => {
    if (!job || effectiveStatus !== "completed") return;
    void (async () => {
      const all: CandidateSummary[] = [];
      let cursor: string | undefined;
      do {
        const page = await client.listCandidates(job.job_id, cursor);
        all.push(...page.items);
        cursor = page.next_cursor ?? undefined;
      } while (cursor);
      setTerminalCandidates(all);
      if (!manualSelection.current) setSelectedCandidate(all.at(-1)?.candidate_id ?? null);
    })().catch((reason: unknown) => setError(String(reason)));
  }, [client, effectiveStatus, job]);

  useEffect(() => {
    if (!job || !selectedCandidate || effectiveStatus !== "completed") return;
    void client
      .getCandidateGeometry(job.job_id, selectedCandidate)
      .then(setGeometry)
      .catch((reason: unknown) => setError(String(reason)));
  }, [client, effectiveStatus, job, selectedCandidate]);

  const candidates = useMemo(
    () => reconcileCandidates(stream.liveCandidateIds, terminalCandidates),
    [stream.liveCandidateIds, terminalCandidates],
  );
  const assumptionCodes = detail?.summary.solve_capability.assumption_codes ?? [];
  const canSubmit = Boolean(
    detail?.summary.solve_capability.can_solve &&
      (assumptionCodes.length === 0 || acknowledged),
  );
  const phase = [...stream.events].reverse().find((event) => event.phase)?.phase ?? "waiting";
  const elapsedSeconds = job
    ? Math.max(0, Math.floor((now - new Date(job.created_at).getTime()) / 1_000))
    : 0;

  if (tasksIndex === null) {
    return <section><h1>Nest Lab</h1><p>Select a corpus task before starting a solve.</p></section>;
  }

  return (
    <section aria-labelledby="nest-heading">
      <div className="section-heading">
        <div><p className="eyebrow">One worker · hard runtime ≤10s</p><h1 id="nest-heading">Nest Lab</h1></div>
        <ProvenanceMark kind="derived" />
      </div>
      {error ? <p className="notice notice--error">{error}</p> : null}
      {detail ? (
        <div className="workbench-grid workbench-grid--nest">
          <aside className="panel control-panel">
            <h2>Task {tasksIndex}</h2>
            {!detail.summary.solve_capability.can_solve ? (
              <div className="notice notice--error">
                <strong>Blocked from solver projection</strong>
                <p>{detail.summary.solve_capability.reason_codes.join(", ")}</p>
              </div>
            ) : assumptionCodes.length > 0 ? (
              <div className="notice notice--assumed">
                <ProvenanceMark kind="assumed" />
                <p className="mono">{assumptionCodes.join(", ")}</p>
              </div>
            ) : (
              <div className="notice notice--success">
                <strong>Directly supported projection</strong>
                <p>No assumption acknowledgement required.</p>
              </div>
            )}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!canSubmit) return;
                const data = new FormData(event.currentTarget);
                const assumptions = detail.summary.solve_capability.assumption_codes;
                void client
                  .createJob({
                    schema_version: "yieldforge.api-solver-job-request.v1",
                    tasks_index: tasksIndex,
                    acknowledged_assumption_codes: [...assumptions],
                    seed: Number(data.get("seed")),
                    total_computation_time: Number(data.get("seconds")),
                    early_termination: data.get("early") === "on",
                    min_items_separation: null,
                    max_runtime_seconds: Number(data.get("seconds")) + 1,
                  })
                  .then((created) => {
                    dispatch({ type: "reset" });
                    lastSequence.current = 0;
                    setTerminalCandidates([]);
                    setSelectedCandidate(null);
                    setGeometry(null);
                    manualSelection.current = false;
                    setJob(created);
                  })
                  .catch((reason: unknown) => setError(String(reason)));
              }}
            >
              {assumptionCodes.length > 0 ? (
                <label>
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(event) => setAcknowledged(event.target.checked)}
                  /> Acknowledge exact assumption
                </label>
              ) : null}
              <label>Seed<input name="seed" type="number" defaultValue="23" required /></label>
              <label>Computation seconds<input name="seconds" type="number" min="1" max="9" defaultValue="5" required /></label>
              <label>Workers<input value="1" readOnly aria-label="Workers" /></label>
              <label><input name="early" type="checkbox" /> Early termination</label>
              <button type="submit" disabled={!canSubmit || Boolean(job && !terminal.has(effectiveStatus))}>
                Start solver job
              </button>
            </form>

            {job ? (
              <div className="job-status">
                <p className="sr-only" aria-live="polite">
                  Job status {effectiveStatus}; {stream.liveCandidateIds.length} candidates observed
                </p>
                <p><strong>Job</strong> <span className="mono">{job.job_id}</span></p>
                <p>Status: {effectiveStatus}</p>
                <p>Phase: {phase}</p>
                <p>Elapsed: {elapsedSeconds}s</p>
                <p>Candidates observed: {stream.liveCandidateIds.length}</p>
                <p>Stream: {stream.connection}</p>
                {!terminal.has(effectiveStatus) ? (
                  <button
                    className="button--danger"
                    onClick={() => {
                      dispatch({ type: "cancel_requested" });
                      void client.cancelJob(job.job_id).then(setJob);
                    }}
                  >Cancel job</button>
                ) : null}
              </div>
            ) : null}
          </aside>

          <div className="panel result-panel">
            <div className="candidate-toolbar">
              <h2>Candidate batch</h2>
              <span>{candidates.length} observed</span>
            </div>
            <div className="candidate-list" role="group" aria-label="Candidates">
              {candidates.map((candidate) => (
                <button
                  key={candidate.candidate_id}
                  data-selected={selectedCandidate === candidate.candidate_id}
                  onClick={() => {
                    manualSelection.current = true;
                    setSelectedCandidate(candidate.candidate_id);
                  }}
                >
                  <span className="mono">{candidate.candidate_id}</span>
                  {"live_only" in candidate ? <span>live sample</span> : <span>{candidate.report_type} · width {candidate.width}</span>}
                </button>
              ))}
            </div>
            {geometry ? <CandidateCanvas geometry={geometry} /> : <p className="empty-state">Run a solve or select a completed candidate.</p>}
          </div>
        </div>
      ) : <p>Loading task…</p>}
    </section>
  );
}
