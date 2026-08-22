import type { JobEvent, JobStatus } from "../contracts";

export interface JobStreamState {
  events: JobEvent[];
  lastSequence: number;
  connection: "idle" | "connected" | "reconnecting" | "closed";
  status: JobStatus | "idle";
  gapAfter: number | null;
  liveCandidateIds: string[];
}

export const initialJobState: JobStreamState = {
  events: [],
  lastSequence: 0,
  connection: "idle",
  status: "idle",
  gapAfter: null,
  liveCandidateIds: [],
};

export type JobAction =
  | { type: "reset" }
  | { type: "connected" }
  | { type: "disconnected" }
  | { type: "cancel_requested" }
  | { type: "event"; event: JobEvent };

const terminal = new Set<JobStatus>(["cancelled", "timed_out", "failed", "completed"]);

export function jobReducer(state: JobStreamState, action: JobAction): JobStreamState {
  if (action.type === "reset") return initialJobState;
  if (action.type === "connected") return { ...state, connection: "connected" };
  if (action.type === "disconnected") {
    return state.connection === "closed" ? state : { ...state, connection: "reconnecting" };
  }
  if (action.type === "cancel_requested") return { ...state, status: "cancelling" };

  const next = action.event;
  if (next.sequence <= state.lastSequence) return state;
  if (next.sequence !== state.lastSequence + 1) {
    return { ...state, connection: "reconnecting", gapAfter: state.lastSequence };
  }
  const status = next.status ?? state.status;
  const liveCandidateIds =
    next.candidate_id && !state.liveCandidateIds.includes(next.candidate_id)
      ? [...state.liveCandidateIds, next.candidate_id]
      : state.liveCandidateIds;
  return {
    events: [...state.events, next],
    lastSequence: next.sequence,
    connection: status !== "idle" && terminal.has(status) ? "closed" : "connected",
    status,
    gapAfter: null,
    liveCandidateIds,
  };
}
