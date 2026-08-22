import { describe, expect, it } from "vitest";

import type { JobEvent, JobStatus } from "../contracts";
import { initialJobState, jobReducer } from "./jobReducer";

const event = (
  sequence: number,
  status: JobStatus,
  kind: JobEvent["kind"] = "status",
  candidateId: string | null = null,
): JobEvent => ({
  schema_version: "yieldforge.api-job-event.v1",
  job_id: "job-1",
  sequence,
  occurred_at: "2026-08-18T00:00:00Z",
  kind,
  status,
  phase: null,
  candidate_id: candidateId,
  candidate_count: candidateId ? 1 : 0,
  archive_available: status === "completed",
  error_code: status === "failed" ? "solver_failure" : null,
  error_message: status === "failed" ? "solver worker failed" : null,
});

describe("solver event reducer", () => {
  it("deduplicates replay and reconnects from the last contiguous sequence on a gap", () => {
    let state = jobReducer(initialJobState, { type: "connected" });
    state = jobReducer(state, { type: "event", event: event(1, "running") });
    state = jobReducer(state, { type: "event", event: event(1, "running") });
    state = jobReducer(state, { type: "event", event: event(3, "running") });

    expect(state.events).toHaveLength(1);
    expect(state.lastSequence).toBe(1);
    expect(state.connection).toBe("reconnecting");
    expect(state.gapAfter).toBe(1);

    state = jobReducer(state, { type: "event", event: event(2, "running") });
    state = jobReducer(state, { type: "event", event: event(3, "running") });
    expect(state.events.map((item) => item.sequence)).toEqual([1, 2, 3]);
  });

  it("does not turn disconnect into cancellation", () => {
    const running = jobReducer(initialJobState, {
      type: "event",
      event: event(1, "running"),
    });
    const disconnected = jobReducer(running, { type: "disconnected" });
    expect(disconnected.status).toBe("running");
    expect(disconnected.connection).toBe("reconnecting");
  });

  it("represents an explicit cancel request without inventing a terminal state", () => {
    const state = jobReducer(initialJobState, { type: "cancel_requested" });
    expect(state.status).toBe("cancelling");
    expect(state.connection).toBe("idle");
  });

  it.each<JobStatus>(["cancelled", "timed_out", "failed", "completed"])(
    "closes on terminal %s",
    (status: JobStatus) => {
      const state = jobReducer(initialJobState, {
        type: "event",
        event: event(1, status, "terminal"),
      });
      expect(state.status).toBe(status);
      expect(state.connection).toBe("closed");
    },
  );

  it("tracks candidate IDs without duplicates", () => {
    let state = jobReducer(initialJobState, {
      type: "event",
      event: event(1, "running", "candidate", "candidate-a"),
    });
    state = jobReducer(state, {
      type: "event",
      event: event(2, "running", "candidate", "candidate-a"),
    });
    expect(state.liveCandidateIds).toEqual(["candidate-a"]);
  });
});
