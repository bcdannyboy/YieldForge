import { describe, expect, it } from "vitest";

import { reconcileCandidates } from "./reconcile";

describe("candidate reconciliation", () => {
  it("merges sampled IDs with authoritative terminal records by candidate ID", () => {
    expect(
      reconcileCandidates(["candidate-a", "candidate-b"], [
        {
          candidate_id: "candidate-b",
          report_type: "final",
          seed: 3,
          width: 10,
          density: 0.8,
          placement_count: 4,
        },
        {
          candidate_id: "candidate-c",
          report_type: "compression_feasible",
          seed: 3,
          width: 11,
          density: 0.7,
          placement_count: 4,
        },
      ]),
    ).toEqual([
      { candidate_id: "candidate-a", live_only: true },
      expect.objectContaining({ candidate_id: "candidate-b", report_type: "final" }),
      expect.objectContaining({ candidate_id: "candidate-c" }),
    ]);
  });
});
