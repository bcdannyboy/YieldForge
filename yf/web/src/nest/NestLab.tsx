import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import type { WorkbenchClient } from "../api";
import type {
  CandidateGeometry,
  CandidateSummary,
  CompletedRun,
  JobView,
  ProjectionMode,
  TaskDetail,
} from "../contracts";
import { ProvenanceMark } from "../components/Provenance";
import { toSvgPoints, transformPlacedPoints } from "../geometry";
import { initialJobState, jobReducer } from "../jobs/jobReducer";
import { reconcileCandidates } from "../jobs/reconcile";
import { RunComparison } from "./RunComparison";

const terminal = new Set(["cancelled", "timed_out", "failed", "completed"]);

function completionTimestamp(value: string): string {
  return new Date(value).toISOString().replace(".000Z", "Z").replace("T", " ");
}

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
  const [interventionAcknowledged, setInterventionAcknowledged] = useState(false);
  const [job, setJob] = useState<JobView | null>(null);
  const [pairedJob, setPairedJob] = useState<JobView | null>(null);
  const [activePairId, setActivePairId] = useState<string | null>(null);
  const [completedRuns, setCompletedRuns] = useState<CompletedRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [comparisonRunId, setComparisonRunId] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [stream, dispatch] = useReducer(jobReducer, initialJobState);
  const [terminalCandidates, setTerminalCandidates] = useState<CandidateSummary[]>([]);
  const [selectedCandidate, setSelectedCandidate] = useState<string | null>(null);
  const [geometry, setGeometry] = useState<CandidateGeometry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [geometryError, setGeometryError] = useState<string | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const manualSelection = useRef(false);
  const lastSequence = useRef(0);
  const candidateRequestGeneration = useRef(0);
  const geometryRequestGeneration = useRef(0);
  const runSelectionGeneration = useRef(0);
  const selectedRunIdRef = useRef<string | null>(null);
  const refreshedCompletedJob = useRef<string | null>(null);
  const refreshedCompletedPair = useRef<string | null>(null);
  const effectiveStatus = job
    ? stream.status === "idle"
      ? job.status
      : stream.status
    : "idle";
  const hasActiveJob = Boolean(
    (job && !terminal.has(effectiveStatus)) ||
      (pairedJob && !terminal.has(pairedJob.status)),
  );

  const resetArchiveView = useCallback(() => {
    candidateRequestGeneration.current += 1;
    geometryRequestGeneration.current += 1;
    runSelectionGeneration.current += 1;
    dispatch({ type: "reset" });
    lastSequence.current = 0;
    setTerminalCandidates([]);
    setSelectedCandidate(null);
    setGeometry(null);
    setArchiveError(null);
    setGeometryError(null);
    manualSelection.current = false;
    setReconnectAttempt(0);
  }, []);

  const selectCompletedRun = useCallback((run: CompletedRun) => {
    const nextRunId = run.job.job_id;
    const previousRunId = selectedRunIdRef.current;
    setComparisonRunId((currentRunId) =>
      currentRunId === nextRunId && previousRunId && previousRunId !== nextRunId
        ? previousRunId
        : currentRunId,
    );
    selectedRunIdRef.current = nextRunId;
    resetArchiveView();
    setSelectedRunId(nextRunId);
    setJob(run.job);
  }, [resetArchiveView]);

  useEffect(() => {
    if (tasksIndex === null) return;
    let active = true;
    setAcknowledged(false);
    setInterventionAcknowledged(false);
    setDetail(null);
    setJob(null);
    setPairedJob(null);
    setActivePairId(null);
    setCompletedRuns([]);
    selectedRunIdRef.current = null;
    setSelectedRunId(null);
    setComparisonRunId(null);
    setHistoryLoaded(false);
    setHistoryError(null);
    refreshedCompletedJob.current = null;
    refreshedCompletedPair.current = null;
    resetArchiveView();
    const initialSelectionGeneration = runSelectionGeneration.current;
    setError(null);
    void client.getTask(tasksIndex)
      .then((task) => active && setDetail(task))
      .catch((reason: unknown) => active && setError(`Task: ${String(reason)}`));
    void client.listCompletedRuns(tasksIndex)
      .then((page) => {
        if (!active) return;
        setCompletedRuns(page.items);
        setHistoryLoaded(true);
        const newest = page.items[0];
        if (newest && runSelectionGeneration.current === initialSelectionGeneration) {
          selectCompletedRun(newest);
        }
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setHistoryLoaded(true);
        setHistoryError(`Run history: ${String(reason)}`);
      });
    return () => {
      active = false;
    };
  }, [client, resetArchiveView, selectCompletedRun, tasksIndex]);

  useEffect(() => {
    if (
      comparisonRunId !== null &&
      (comparisonRunId === selectedRunId ||
        !completedRuns.some((run) => run.job.job_id === comparisonRunId))
    ) {
      setComparisonRunId(null);
    }
  }, [completedRuns, comparisonRunId, selectedRunId]);

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
    if (!pairedJob || terminal.has(pairedJob.status)) return;
    let active = true;
    let timer: number | null = null;
    const poll = () => {
      void client
        .getJob(pairedJob.job_id)
        .then((next) => {
          if (!active) return;
          setPairedJob(next);
          if (!terminal.has(next.status)) timer = window.setTimeout(poll, 300);
        })
        .catch((reason: unknown) => {
          if (!active) return;
          setError(`Paired job: ${String(reason)}`);
          timer = window.setTimeout(poll, 600);
        });
    };
    poll();
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [client, pairedJob?.job_id]);

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
    if (!job || effectiveStatus !== "completed" || selectedRunId !== job.job_id) return;
    const generation = ++candidateRequestGeneration.current;
    const jobId = job.job_id;
    setArchiveError(null);
    void (async () => {
      const all: CandidateSummary[] = [];
      let cursor: string | undefined;
      do {
        const page = await client.listCandidates(jobId, cursor);
        if (candidateRequestGeneration.current !== generation) return;
        all.push(...page.items);
        cursor = page.next_cursor ?? undefined;
      } while (cursor);
      if (candidateRequestGeneration.current !== generation) return;
      setTerminalCandidates(all);
      if (!manualSelection.current) setSelectedCandidate(all.at(-1)?.candidate_id ?? null);
    })().catch((reason: unknown) => {
      if (candidateRequestGeneration.current === generation) {
        setArchiveError(`Archive ${jobId}: ${String(reason)}`);
      }
    });
    return () => {
      if (candidateRequestGeneration.current === generation) {
        candidateRequestGeneration.current += 1;
      }
    };
  }, [client, effectiveStatus, job, selectedRunId]);

  useEffect(() => {
    if (!job || !selectedCandidate || effectiveStatus !== "completed") return;
    const generation = ++geometryRequestGeneration.current;
    const jobId = job.job_id;
    const candidateId = selectedCandidate;
    setGeometry(null);
    setGeometryError(null);
    void client
      .getCandidateGeometry(jobId, candidateId)
      .then((value) => {
        if (geometryRequestGeneration.current === generation) setGeometry(value);
      })
      .catch((reason: unknown) => {
        if (geometryRequestGeneration.current === generation) {
          setGeometryError(`Geometry ${jobId}/${candidateId}: ${String(reason)}`);
        }
      });
    return () => {
      if (geometryRequestGeneration.current === generation) {
        geometryRequestGeneration.current += 1;
      }
    };
  }, [client, effectiveStatus, job, selectedCandidate]);

  useEffect(() => {
    if (
      tasksIndex === null ||
      !job ||
      activePairId !== null ||
      selectedRunId !== null ||
      effectiveStatus !== "completed" ||
      refreshedCompletedJob.current === job.job_id
    ) {
      return;
    }
    const completedJobId = job.job_id;
    let active = true;
    refreshedCompletedJob.current = completedJobId;
    void client.listCompletedRuns(tasksIndex)
      .then((page) => {
        if (!active) return;
        setCompletedRuns(page.items);
        setHistoryLoaded(true);
        setHistoryError(null);
        const completedRun = page.items.find((run) => run.job.job_id === completedJobId);
        if (completedRun) {
          selectCompletedRun(completedRun);
        } else {
          setHistoryError(`Run history: completed archive ${completedJobId} was not returned`);
        }
      })
      .catch((reason: unknown) => {
        if (active) setHistoryError(`Run history: ${String(reason)}`);
      });
    return () => {
      active = false;
    };
  }, [activePairId, client, effectiveStatus, job, selectCompletedRun, selectedRunId, tasksIndex]);

  useEffect(() => {
    if (
      tasksIndex === null ||
      activePairId === null ||
      !job ||
      !pairedJob ||
      !terminal.has(effectiveStatus) ||
      !terminal.has(pairedJob.status) ||
      refreshedCompletedPair.current === activePairId
    ) {
      return;
    }
    const pairId = activePairId;
    let active = true;
    refreshedCompletedPair.current = pairId;
    void client
      .listCompletedRuns(tasksIndex)
      .then((page) => {
        if (!active) return;
        setCompletedRuns(page.items);
        setHistoryLoaded(true);
        setHistoryError(null);
        const recorded = page.items.find(
          (run) =>
            run.job.experiment_pair_id === pairId &&
            run.job.experiment_arm === "source_as_recorded",
        );
        const ablation = page.items.find(
          (run) =>
            run.job.experiment_pair_id === pairId &&
            run.job.experiment_arm === "force_flip_x_zero",
        );
        if (!recorded || !ablation) {
          setHistoryError(`Run history: completed matched pair ${pairId} was not returned`);
          return;
        }
        selectCompletedRun(recorded);
        setComparisonRunId(ablation.job.job_id);
        setActivePairId(null);
        setPairedJob(null);
      })
      .catch((reason: unknown) => {
        if (active) setHistoryError(`Run history: ${String(reason)}`);
      });
    return () => {
      active = false;
    };
  }, [
    activePairId,
    client,
    effectiveStatus,
    job,
    pairedJob,
    selectCompletedRun,
    tasksIndex,
  ]);

  const candidates = useMemo(
    () => reconcileCandidates(stream.liveCandidateIds, terminalCandidates),
    [stream.liveCandidateIds, terminalCandidates],
  );
  const selectedCompletedRun = useMemo(
    () => completedRuns.find((run) => run.job.job_id === selectedRunId) ?? null,
    [completedRuns, selectedRunId],
  );
  const comparisonRun = useMemo(
    () => completedRuns.find((run) => run.job.job_id === comparisonRunId) ?? null,
    [completedRuns, comparisonRunId],
  );
  const assumptionCodes = detail?.summary.solve_capability.assumption_codes ?? [];
  const projectionOptions = detail?.summary.solve_capability.projection_options ?? [];
  const recordedOption = projectionOptions.find((option) => option.mode === "source_as_recorded");
  const ablationOption = projectionOptions.find((option) => option.mode === "force_flip_x_zero");
  const canSubmit = Boolean(
    detail?.summary.solve_capability.can_solve &&
      (assumptionCodes.length === 0 || acknowledged),
  );
  const canSubmitAblation = Boolean(canSubmit && ablationOption && interventionAcknowledged);
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
                {ablationOption ? (
                  <p>{detail.s1_projection_diagnostics.flip_part_count} source parts carry recorded flip_x = 1.</p>
                ) : null}
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
                const submitter = (event.nativeEvent as SubmitEvent)
                  .submitter as HTMLButtonElement | null;
                const action = submitter?.value ?? "source_as_recorded";
                if (!canSubmit || (action !== "source_as_recorded" && !canSubmitAblation)) return;
                const data = new FormData(event.currentTarget);
                const assumptions = detail.summary.solve_capability.assumption_codes;
                const shared = {
                  tasks_index: tasksIndex,
                  acknowledged_assumption_codes: [...assumptions],
                  acknowledged_intervention_codes:
                    action === "source_as_recorded"
                      ? []
                      : [...(ablationOption?.intervention_codes ?? [])],
                  seed: Number(data.get("seed")),
                  total_computation_time: Number(data.get("seconds")),
                  early_termination: data.get("early") === "on",
                  min_items_separation: null,
                  max_runtime_seconds: Math.min(10, Number(data.get("seconds")) + 3),
                };
                const prepareActiveRun = () => {
                  resetArchiveView();
                  selectedRunIdRef.current = null;
                  setSelectedRunId(null);
                  setComparisonRunId(null);
                  refreshedCompletedJob.current = null;
                  refreshedCompletedPair.current = null;
                };
                if (action === "matched") {
                  void client
                    .createMatchedJobs({
                      schema_version: "yieldforge.api-matched-solver-jobs-request.v1",
                      ...shared,
                    })
                    .then((created) => {
                      prepareActiveRun();
                      setActivePairId(created.experiment_pair_id);
                      setJob(created.source_as_recorded);
                      setPairedJob(created.force_flip_x_zero);
                    })
                    .catch((reason: unknown) => setError(String(reason)));
                  return;
                }
                const mode = action as ProjectionMode;
                void client
                  .createJob({
                    schema_version: "yieldforge.api-solver-job-request.v2",
                    projection_mode: mode,
                    ...shared,
                  })
                  .then((created) => {
                    prepareActiveRun();
                    setActivePairId(null);
                    setPairedJob(null);
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
                  /> Acknowledge exact {assumptionCodes.length === 1 ? "assumption" : "assumptions"}
                </label>
              ) : null}
              {ablationOption ? (
                <label>
                  <input
                    type="checkbox"
                    checked={interventionAcknowledged}
                    onChange={(event) => setInterventionAcknowledged(event.target.checked)}
                  /> Acknowledge derived no-flip intervention
                </label>
              ) : null}
              <label>Seed<input name="seed" type="number" defaultValue="23" required /></label>
              <label>Computation seconds<input name="seconds" type="number" min="1" max="9" defaultValue="5" required /></label>
              <label>Workers<input value="1" readOnly aria-label="Workers" /></label>
              <label><input name="early" type="checkbox" /> Early termination</label>
              {ablationOption ? (
                <div className="projection-actions">
                  <button
                    type="submit"
                    name="projection-action"
                    value={recordedOption?.mode ?? "source_as_recorded"}
                    disabled={!canSubmit || hasActiveJob}
                  >
                    Run recorded projection
                  </button>
                  <button
                    type="submit"
                    name="projection-action"
                    value="force_flip_x_zero"
                    disabled={!canSubmitAblation || hasActiveJob}
                  >
                    Run no-flip ablation
                  </button>
                  <button
                    type="submit"
                    name="projection-action"
                    value="matched"
                    disabled={!canSubmitAblation || hasActiveJob}
                  >
                    Run matched experiment
                  </button>
                </div>
              ) : (
                <button
                  type="submit"
                  name="projection-action"
                  value="source_as_recorded"
                  disabled={!canSubmit || hasActiveJob}
                >
                  Start solver job
                </button>
              )}
            </form>

            {job ? (
              <div className="job-status">
                <p className="sr-only" aria-live="polite">
                  Job status {effectiveStatus}; {stream.liveCandidateIds.length} candidates observed
                </p>
                {activePairId ? <p><strong>Matched pair {activePairId}</strong></p> : null}
                <p>
                  <strong>{activePairId ? "Recorded projection" : "Job"}</strong>{" "}
                  <span className="mono">{job.job_id}</span>
                </p>
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
                  >{activePairId ? "Cancel recorded projection" : "Cancel job"}</button>
                ) : null}
                {pairedJob ? (
                  <div className="paired-job-status">
                    <p>
                      <strong>No-flip ablation</strong>{" "}
                      <span className="mono">{pairedJob.job_id}</span>
                    </p>
                    <p>Status: {pairedJob.status}</p>
                    <p>Candidates archived: {pairedJob.candidate_count}</p>
                    {!terminal.has(pairedJob.status) ? (
                      <button
                        type="button"
                        className="button--danger"
                        onClick={() => {
                          void client.cancelJob(pairedJob.job_id).then(setPairedJob);
                        }}
                      >
                        Cancel no-flip ablation
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
          </aside>

          <section
            className="panel run-history-panel"
            role="region"
            aria-labelledby="run-history-heading"
          >
            <div className="run-history-heading">
              <div>
                <p className="eyebrow">Task-bound evidence</p>
                <h2 id="run-history-heading">Completed run history</h2>
              </div>
              <span>{completedRuns.length}</span>
            </div>
            <p className="run-history-intro">
              Verified immutable candidate archives, newest first. Selecting a run only browses
              recorded output; it does not rerun or rank it.
            </p>
            {historyError ? <p className="notice notice--error">{historyError}</p> : null}
            <RunComparison
              runs={completedRuns}
              runA={selectedCompletedRun}
              runB={comparisonRun}
              runBId={comparisonRunId}
              disabled={hasActiveJob}
              onRunBChange={setComparisonRunId}
            />
            {!historyLoaded ? (
              <p className="run-history-empty">Loading completed runs…</p>
            ) : completedRuns.length === 0 && !historyError ? (
              <p className="run-history-empty">No completed archive runs for this task yet.</p>
            ) : (
              <div className="run-history-list">
                {completedRuns.map((run) => {
                  const selected = selectedRunId === run.job.job_id;
                  const assumptions =
                    run.job.source_task_binding?.acknowledged_assumption_codes ?? [];
                  const projection = run.job.source_task_binding?.solver_projection ?? null;
                  return (
                    <button
                      key={run.job.job_id}
                      type="button"
                      className="run-history-card"
                      aria-label={`Open completed run ${run.job.job_id}`}
                      aria-pressed={selected}
                      disabled={hasActiveJob}
                      data-selected={selected}
                      onClick={() => selectCompletedRun(run)}
                    >
                      <span className="run-history-card__topline">
                        <strong>{selected ? "Selected archive" : "Completed archive"}</strong>
                        <time dateTime={run.job.updated_at}>
                          {completionTimestamp(run.job.updated_at)}
                        </time>
                      </span>
                      <span className="mono run-history-card__job">{run.job.job_id}</span>
                      <span className="run-history-card__facts">
                        <span>Seed {run.settings.seed}</span>
                        <span>Computation {run.settings.total_computation_time}s</span>
                        <span>Runtime limit {run.settings.max_runtime_seconds}s</span>
                        <span>Workers {run.settings.num_workers}</span>
                        <span>Candidates {run.job.candidate_count}</span>
                        <span>
                          Early termination {run.settings.early_termination ? "on" : "off"}
                        </span>
                        <span>
                          Min separation {run.settings.min_items_separation ?? "none"}
                        </span>
                      </span>
                      <span className="run-history-card__assumptions">
                        Assumptions: {assumptions.length > 0 ? assumptions.join(", ") : "none"}
                      </span>
                      <span className="run-history-card__projection">
                        Projection: {projection?.mode ?? "legacy unavailable"}
                        {projection
                          ? ` · ${projection.projection_sha256} · interventions ${
                              projection.intervention_codes.length > 0
                                ? projection.intervention_codes.join(", ")
                                : "none"
                            }`
                          : ""}
                      </span>
                      {run.job.experiment_pair_id ? (
                        <span className="run-history-card__pair">
                          Pair {run.job.experiment_pair_id} · arm {run.job.experiment_arm}
                        </span>
                      ) : null}
                      <span className="mono run-history-card__hash">
                        {run.archive.batch_sha256}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
            {hasActiveJob ? (
              <p className="field-note">History is locked until the active solver job is terminal.</p>
            ) : null}
          </section>

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
            {archiveError ? <p className="notice notice--error">{archiveError}</p> : null}
            {geometryError ? <p className="notice notice--error">{geometryError}</p> : null}
            {geometry ? <CandidateCanvas geometry={geometry} /> : <p className="empty-state">Run a solve or select a completed candidate.</p>}
          </div>
        </div>
      ) : <p>Loading task…</p>}
    </section>
  );
}
