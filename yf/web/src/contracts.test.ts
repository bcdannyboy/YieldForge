import { describe, expect, it } from "vitest";

import {
  parseCorpusSummary,
  parseJobEvent,
  parseOrderBook,
  parseTaskDetail,
} from "./contracts";
import { corpusSummary, orderBook, taskDetail } from "./test/fixtures";

describe("browser transport validation", () => {
  it("accepts decimal-string shape hashes and opaque integers", () => {
    const parsed = parseTaskDetail(taskDetail(13958));
    expect(parsed.parts[0]?.shape_hash).toBe("-8727500516347896752");
    expect(parsed.constraints[0]?.values[0]).toMatchObject({
      kind: "sequence",
      items: [{ kind: "integer", value: "10" }],
    });
  });

  it("rejects unsafe JSON numbers and numeric shape hashes", () => {
    const numericHash = structuredClone(taskDetail(13958));
    // This is exactly the failure mode the server string DTO prevents.
    numericHash.parts[0]!.shape_hash = 9_121_199_544_198_855_000 as never;
    expect(() => parseTaskDetail(numericHash)).toThrow(/shape_hash.*decimal string/i);

    const unsafeCount = structuredClone(taskDetail(13958));
    unsafeCount.summary.part_count = 2 ** 53 as never;
    expect(() => parseTaskDetail(unsafeCount)).toThrow(/safe integer/i);
  });

  it("rejects numeric order-book shape hashes", () => {
    const unsafe = structuredClone(orderBook);
    unsafe.events[0]!.source_task.shape_hashes[0] = 9_121_199_544_198_855_000 as never;
    expect(() => parseOrderBook(unsafe)).toThrow(/shape hash.*decimal string/i);
  });

  it("normalizes optional fields omitted by the real SSE transport", () => {
    const parsed = parseJobEvent({
      schema_version: "yieldforge.api-job-event.v1",
      job_id: "job-0123456789abcdef01234567",
      sequence: 2,
      occurred_at: "2026-08-21T20:00:00Z",
      kind: "phase",
      phase: "solving",
      candidate_count: 0,
      archive_available: false,
    });

    expect(parsed).toMatchObject({
      status: null,
      phase: "solving",
      candidate_id: null,
      error_code: null,
      error_message: null,
    });
  });

  it("accepts every authoritative support status and rejects unknown statuses", () => {
    for (const supportStatus of [
      "directly_supported",
      "runnable_with_explicit_assumptions",
      "view_only",
    ] as const) {
      const detail = structuredClone(taskDetail(supportStatus === "view_only" ? 25801 : 13958));
      detail.summary.solve_capability.support_status = supportStatus;
      if (supportStatus === "directly_supported") {
        detail.summary.solve_capability.requires_assumption_acknowledgement = false;
        detail.summary.solve_capability.assumption_codes = [];
      }
      expect(parseTaskDetail(detail).summary.solve_capability.support_status).toBe(supportStatus);
    }

    const detail = structuredClone(taskDetail(13958));
    detail.summary.solve_capability.support_status = "unreviewed" as never;
    expect(() => parseTaskDetail(detail)).toThrow(/support_status.*invalid/i);
  });

  it("strictly validates summary filter facets", () => {
    const parsed = parseCorpusSummary(corpusSummary);
    expect(parsed.support_status_counts[0]).toEqual({
      name: "runnable_with_explicit_assumptions",
      count: 1,
    });

    const unknownStatus = structuredClone(corpusSummary);
    unknownStatus.support_status_counts[0]!.name = "unreviewed" as never;
    expect(() => parseCorpusSummary(unknownStatus)).toThrow(/support_status_counts.*invalid/i);

    const unsafeConstraintCount = structuredClone(corpusSummary);
    unsafeConstraintCount.constraint_type_counts[0]!.count = 2 ** 53;
    expect(() => parseCorpusSummary(unsafeConstraintCount)).toThrow(/safe integer/i);

    const negativeSupportCount = structuredClone(corpusSummary);
    negativeSupportCount.support_status_counts[0]!.count = -1;
    expect(() => parseCorpusSummary(negativeSupportCount)).toThrow(/nonnegative/i);
  });
});
