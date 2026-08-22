import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { WorkbenchClient } from "./api";
import {
  completedRun,
  corpusSummary,
  job,
  orderBook,
  taskDetail,
  taskPage,
  taskSummary,
} from "./test/fixtures";

const client = (): WorkbenchClient => ({
  getCorpusSummary: vi.fn().mockResolvedValue(corpusSummary),
  listTasks: vi.fn().mockResolvedValue(taskPage),
  getTask: vi.fn((id: number) => Promise.resolve(taskDetail(id))),
  createJob: vi.fn().mockResolvedValue(job),
  getJob: vi.fn().mockResolvedValue(job),
  cancelJob: vi.fn().mockResolvedValue({ ...job, status: "cancelled" }),
  streamJobEvents: vi.fn().mockResolvedValue(undefined),
  listCandidates: vi.fn().mockResolvedValue({
    schema_version: "yieldforge.api-candidate-page.v1",
    items: [],
    next_cursor: null,
  }),
  getCandidateGeometry: vi.fn(),
  listTaskJobs: vi.fn().mockResolvedValue({
    schema_version: "yieldforge.api-task-jobs.v1",
    items: [],
  }),
  listCompletedRuns: vi.fn().mockResolvedValue({
    schema_version: "yieldforge.api-completed-run-page.v1",
    items: [],
  }),
  listOrderBooks: vi.fn().mockResolvedValue({
    schema_version: "yieldforge.api-order-book-page.v1",
    items: [orderBook],
    next_cursor: null,
  }),
  getOrderBook: vi.fn().mockResolvedValue(orderBook),
  generateOrderBook: vi.fn().mockResolvedValue(orderBook),
});

describe("research workbench", () => {
  it("paginates with the exact active filters and keeps appended rows while selecting", async () => {
    const api = client();
    const user = userEvent.setup();
    vi.mocked(api.getCorpusSummary).mockResolvedValue({ ...corpusSummary, task_count: 256 });
    vi.mocked(api.listTasks)
      .mockResolvedValueOnce({
        schema_version: "yieldforge.task-page.v1",
        items: [taskSummary(13958)],
        next_cursor: "unfiltered-cursor",
      })
      .mockResolvedValueOnce({
        schema_version: "yieldforge.task-page.v1",
        items: [taskSummary(13958)],
        next_cursor: "filtered-cursor-50",
      })
      .mockResolvedValueOnce({
        schema_version: "yieldforge.task-page.v1",
        items: [taskSummary(13958), taskSummary(25801)],
        next_cursor: "cursor-100",
      });
    window.history.replaceState({}, "", "/?view=corpus");
    render(<App client={api} />);

    expect(await screen.findByText("Loaded 1 of 256 tasks")).toBeVisible();
    await user.selectOptions(screen.getByRole("combobox", { name: "Constraint" }), "s1");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() =>
      expect(api.listTasks).toHaveBeenNthCalledWith(2, { constraintType: "s1" }),
    );
    await user.click(screen.getByRole("button", { name: "Load next 50 tasks" }));

    expect(await screen.findByText("Loaded 2 of 256 tasks")).toBeVisible();
    expect(api.listTasks).toHaveBeenNthCalledWith(1, {});
    expect(api.listTasks).toHaveBeenNthCalledWith(3, {
      constraintType: "s1",
      cursor: "filtered-cursor-50",
    });
    const table = screen.getByRole("table", { name: /corpus tasks/i });
    expect(within(table).getAllByText("13958")).toHaveLength(1);
    await user.click(within(table).getByRole("button", { name: "25801" }));
    expect(await screen.findByRole("heading", { name: "Task 25801" })).toBeVisible();
    expect(api.listTasks).toHaveBeenCalledTimes(3);
  });

  it("resets pagination for filters, forwards maximum parts, and clears filters", async () => {
    const api = client();
    const user = userEvent.setup();
    vi.mocked(api.getCorpusSummary).mockResolvedValue({ ...corpusSummary, task_count: 256 });
    vi.mocked(api.listTasks).mockImplementation((filters) => {
      if (filters?.status === "view_only") {
        return Promise.resolve({
          schema_version: "yieldforge.task-page.v1",
          items: [taskSummary(25801)],
          next_cursor: null,
        });
      }
      return Promise.resolve({
        schema_version: "yieldforge.task-page.v1",
        items: [taskSummary(13958)],
        next_cursor: "cursor-50",
      });
    });
    window.history.replaceState({}, "", "/?view=corpus");
    render(<App client={api} />);

    await screen.findByText("Loaded 1 of 256 tasks");
    await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "view_only");
    await user.type(screen.getByRole("spinbutton", { name: "Min parts" }), "10");
    await user.type(screen.getByRole("spinbutton", { name: "Max parts" }), "40");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() =>
      expect(api.listTasks).toHaveBeenLastCalledWith({
        status: "view_only",
        minParts: 10,
        maxParts: 40,
      }),
    );
    expect(screen.queryByRole("button", { name: "Load next 50 tasks" })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Task 25801" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(api.listTasks).toHaveBeenLastCalledWith({}));
    expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("");
    expect(screen.getByRole("spinbutton", { name: "Max parts" })).toHaveValue(null);
    expect(await screen.findByRole("heading", { name: "Task 13958" })).toBeVisible();
  });

  it("ignores stale list responses after filters change", async () => {
    const api = client();
    const user = userEvent.setup();
    let resolveInitial!: (value: typeof taskPage) => void;
    const initial = new Promise<typeof taskPage>((resolve) => {
      resolveInitial = resolve;
    });
    vi.mocked(api.listTasks)
      .mockReturnValueOnce(initial)
      .mockResolvedValueOnce({
        schema_version: "yieldforge.task-page.v1",
        items: [taskSummary(25801)],
        next_cursor: null,
      });
    window.history.replaceState({}, "", "/?view=corpus");
    render(<App client={api} />);

    await screen.findByRole("option", { name: "View only (1)" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "view_only");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByRole("button", { name: "25801" })).toBeVisible();
    await act(async () => resolveInitial(taskPage));

    const table = screen.getByRole("table", { name: /corpus tasks/i });
    expect(within(table).getByRole("button", { name: "25801" })).toBeVisible();
    expect(within(table).queryByRole("button", { name: "13958" })).not.toBeInTheDocument();
  });

  it("loads deep-linked detail independently and ignores stale detail responses", async () => {
    const api = client();
    const user = userEvent.setup();
    let resolveOld!: (value: ReturnType<typeof taskDetail>) => void;
    const oldDetail = new Promise<ReturnType<typeof taskDetail>>((resolve) => {
      resolveOld = resolve;
    });
    vi.mocked(api.listTasks).mockResolvedValue({
      ...taskPage,
      items: [taskSummary(25801)],
    });
    vi.mocked(api.getTask).mockImplementation((id) =>
      id === 13958 ? oldDetail : Promise.resolve(taskDetail(id)),
    );
    window.history.replaceState({}, "", "/?view=corpus&task=13958");
    render(<App client={api} />);

    expect(await screen.findByRole("button", { name: "25801" })).toBeVisible();
    expect(api.listTasks).toHaveBeenCalledTimes(1);
    expect(api.getTask).toHaveBeenCalledWith(13958);
    await user.click(screen.getByRole("button", { name: "25801" }));
    expect(await screen.findByRole("heading", { name: "Task 25801" })).toBeVisible();
    await act(async () => resolveOld(taskDetail(13958)));

    expect(screen.getByRole("heading", { name: "Task 25801" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Task 13958" })).not.toBeInTheDocument();
    expect(api.listTasks).toHaveBeenCalledTimes(1);
  });

  it("renders distinct support labels from dynamic summary facets", async () => {
    const api = client();
    const direct = taskSummary(101);
    direct.solve_capability = {
      can_solve: true,
      requires_assumption_acknowledgement: false,
      normalization_status: "source_lossless",
      support_status: "directly_supported",
      projection_status: "eligible",
      reason_codes: [],
      assumption_codes: [],
    };
    vi.mocked(api.getCorpusSummary).mockResolvedValue({
      ...corpusSummary,
      task_count: 3,
      support_status_counts: [
        { name: "directly_supported", count: 1 },
        { name: "runnable_with_explicit_assumptions", count: 1 },
        { name: "view_only", count: 1 },
      ],
      constraint_type_counts: [
        { name: "s1", count: 66 },
        { name: "c8", count: 20 },
        { name: "g2", count: 4 },
      ],
    });
    vi.mocked(api.listTasks).mockResolvedValue({
      schema_version: "yieldforge.task-page.v1",
      items: [direct, taskSummary(13958), taskSummary(25801)],
      next_cursor: null,
    });
    vi.mocked(api.getTask).mockResolvedValue({ ...taskDetail(101), summary: direct });
    window.history.replaceState({}, "", "/?view=corpus");
    render(<App client={api} />);

    const table = await screen.findByRole("table", { name: /corpus tasks/i });
    expect(within(table).getByText("Directly supported")).toBeVisible();
    expect(within(table).getByText("Assumption-backed")).toBeVisible();
    expect(within(table).getByText("View only · blocked")).toBeVisible();
    expect(screen.getByRole("option", { name: "Directly supported (1)" })).toBeVisible();
    expect(screen.getByRole("option", { name: "g2 (4 rows)" })).toBeVisible();
  });

  it("does not require acknowledgement for a directly supported task", async () => {
    const api = client();
    const direct = taskSummary(101);
    direct.solve_capability = {
      can_solve: true,
      requires_assumption_acknowledgement: false,
      normalization_status: "source_lossless",
      support_status: "directly_supported",
      projection_status: "eligible",
      reason_codes: [],
      assumption_codes: [],
    };
    vi.mocked(api.getTask).mockResolvedValue({ ...taskDetail(101), summary: direct });
    window.history.replaceState({}, "", "/?view=nest&task=101");
    render(<App client={api} />);

    const submit = await screen.findByRole("button", { name: /start solver job/i });
    expect(submit).toBeEnabled();
    expect(screen.queryByRole("checkbox", { name: /acknowledge exact assumption/i })).not.toBeInTheDocument();
    expect(screen.getByText("Directly supported projection")).toBeVisible();
  });

  it("keeps summary, list, and detail failures in independent UI states", async () => {
    const api = client();
    vi.mocked(api.getCorpusSummary).mockRejectedValue(new Error("summary unavailable"));
    vi.mocked(api.listTasks).mockRejectedValue(new Error("list unavailable"));
    window.history.replaceState({}, "", "/?view=corpus&task=25801");
    render(<App client={api} />);

    expect(await screen.findByText(/Corpus summary: Error: summary unavailable/)).toBeVisible();
    expect(await screen.findByText(/Task list: Error: list unavailable/)).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Task 25801" })).toBeVisible();
    expect(screen.getByText("Blocked from solver projection")).toBeVisible();
  });

  it("keeps task rows available when an independent detail request fails", async () => {
    const api = client();
    vi.mocked(api.getTask).mockRejectedValue(new Error("detail unavailable"));
    window.history.replaceState({}, "", "/?view=corpus&task=25801");
    render(<App client={api} />);

    expect(await screen.findByRole("button", { name: "13958" })).toBeVisible();
    expect(await screen.findByText(/Task detail: Error: detail unavailable/)).toBeVisible();
    expect(screen.getByRole("button", { name: "25801" })).toBeVisible();
  });

  it("shows the coordinate warning and both authoritative corpus task states", async () => {
    window.history.replaceState({}, "", "/?view=corpus&task=25801");
    render(<App client={client()} />);

    expect(screen.getByText("m^-4 · interpretation unresolved")).toBeVisible();
    const table = await screen.findByRole("table", { name: /corpus tasks/i });
    expect(within(table).getByText("13958")).toBeVisible();
    expect(within(table).getByText("25801")).toBeVisible();
    expect(await screen.findByText("Blocked from solver projection")).toBeVisible();
    expect(screen.getByRole("button", { name: /solve task 25801/i })).toBeDisabled();
    expect(document.querySelector(".workbench-grid")).toBeTruthy();
  });

  it("keeps the corpus inspector inside filtered results and clears it for no matches", async () => {
    const api = client();
    const user = userEvent.setup();
    vi.mocked(api.listTasks).mockImplementation((filters) => {
      if (filters?.taskId === 999) {
        return Promise.resolve({ ...taskPage, items: [] });
      }
      if (filters?.status === "view_only") {
        return Promise.resolve({ ...taskPage, items: [taskPage.items[1]!] });
      }
      return Promise.resolve(taskPage);
    });
    window.history.replaceState({}, "", "/?view=corpus&task=13958");
    render(<App client={api} />);

    expect(await screen.findByRole("heading", { name: "Task 13958" })).toBeVisible();
    await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "view_only");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(await screen.findByRole("heading", { name: "Task 25801" })).toBeVisible();
    expect(api.getTask).toHaveBeenLastCalledWith(25801);

    await user.type(screen.getByRole("textbox", { name: "Task ID" }), "999");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(await screen.findByText("No tasks match the current filters.")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Task 25801" })).not.toBeInTheDocument();
  });

  it("keeps the exact assumption warning and sends it only after acknowledgement", async () => {
    const api = client();
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    render(<App client={api} />);

    const submit = await screen.findByRole("button", { name: /start solver job/i });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/interpret_s1_degenerate_entries_as_allowed_rotations/)).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: /acknowledge exact assumption/i }));
    expect(submit).toBeEnabled();
    await user.click(submit);

    await waitFor(() => expect(api.createJob).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/interpret_s1_degenerate_entries_as_allowed_rotations/)).toBeVisible();
    expect(api.createJob).toHaveBeenCalledWith(
      expect.objectContaining({
        tasks_index: 13958,
        acknowledged_assumption_codes: [
          "interpret_s1_degenerate_entries_as_allowed_rotations",
        ],
        total_computation_time: 5,
        max_runtime_seconds: 8,
      }),
    );
  });

  it("caps the solver supervision margin at ten seconds", async () => {
    const api = client();
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    render(<App client={api} />);

    await user.click(await screen.findByRole("checkbox", {
      name: /acknowledge exact assumption/i,
    }));
    const seconds = screen.getByRole("spinbutton", { name: "Computation seconds" });
    await user.clear(seconds);
    await user.type(seconds, "9");
    await user.click(screen.getByRole("button", { name: /start solver job/i }));

    await waitFor(() => expect(api.createJob).toHaveBeenCalledWith(
      expect.objectContaining({
        total_computation_time: 9,
        max_runtime_seconds: 10,
      }),
    ));
  });

  it("defaults to the newest completed archive and browses an older run", async () => {
    const api = client();
    const newer = completedRun("job-newer");
    newer.job.updated_at = "2026-08-18T01:00:00Z";
    newer.settings.seed = 29;
    newer.archive.batch_sha256 = "d".repeat(64);
    const older = completedRun("job-older");
    older.settings.early_termination = true;
    older.settings.min_items_separation = 0.25;
    vi.mocked(api.listCompletedRuns).mockResolvedValue({
      schema_version: "yieldforge.api-completed-run-page.v1",
      items: [newer, older],
    });
    vi.mocked(api.listCandidates).mockImplementation((jobId) => Promise.resolve({
      schema_version: "yieldforge.api-candidate-page.v1",
      items: [{
        candidate_id: `${jobId}-candidate`,
        report_type: "final",
        seed: jobId === "job-newer" ? 29 : 23,
        width: 42,
        density: 0.75,
        placement_count: 1,
      }],
      next_cursor: null,
    }));
    vi.mocked(api.getCandidateGeometry).mockImplementation((_jobId, candidateId) => Promise.resolve({
      schema_version: "yieldforge.api-candidate-geometry.v1",
      candidate: {
        candidate_id: candidateId,
        report_type: "final",
        seed: 23,
        width: 42,
        density: 0.75,
        placement_count: 1,
      },
      sheet: { length: 100, width: 50 },
      provenance: "derived",
      placements: [{
        part_id: "part-1",
        rotation: 0,
        translation: [10, 10],
        projected_shape: [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
        svg_points: [[10, 40], [11, 40], [11, 39], [10, 39], [10, 40]],
      }],
    }));
    window.history.replaceState({}, "", "/?view=nest&task=13958");

    render(<App client={api} />);

    const history = await screen.findByRole("region", { name: "Completed run history" });
    const runButtons = within(history).getAllByRole("button");
    expect(runButtons.map((button) => button.getAttribute("aria-label"))).toEqual([
      "Open completed run job-newer",
      "Open completed run job-older",
    ]);
    expect(runButtons[0]).toHaveAttribute("aria-pressed", "true");
    expect(within(history).getByText("d".repeat(64))).toBeVisible();
    expect(within(runButtons[0]!).getByText(/Seed 29/)).toBeVisible();
    expect(await screen.findByText("job-newer-candidate")).toBeVisible();

    await userEvent.setup().click(runButtons[1]!);

    expect(await screen.findByText("job-older-candidate")).toBeVisible();
    expect(runButtons[1]).toHaveAttribute("aria-pressed", "true");
    expect(
      await screen.findByRole("img", { name: /candidate job-older-candidate placement geometry/i }),
    ).toBeVisible();
    expect(api.listCompletedRuns).toHaveBeenCalledWith(13958);
    expect(api.listCandidates).toHaveBeenCalledWith("job-older", undefined);
  });

  it("compares two completed runs using exact neutral archive evidence", async () => {
    const api = client();
    const newer = completedRun("job-newer");
    newer.job.updated_at = "2026-08-18T01:00:00Z";
    newer.settings.seed = 29;
    newer.archive.batch_sha256 = "d".repeat(64);
    const older = completedRun("job-older");
    older.job.candidate_count = 2;
    older.settings.early_termination = true;
    older.settings.min_items_separation = 0.25;
    vi.mocked(api.listCompletedRuns).mockResolvedValue({
      schema_version: "yieldforge.api-completed-run-page.v1",
      items: [newer, older],
    });
    window.history.replaceState({}, "", "/?view=nest&task=13958");

    render(<App client={api} />);

    const comparison = await screen.findByRole("region", {
      name: "Read-only run comparison",
    });
    const selector = within(comparison).getByRole("combobox", {
      name: "Compare open run with",
    });
    await userEvent.setup().selectOptions(selector, "job-older");

    const evidence = within(comparison).getByRole("table", {
      name: "Recorded run evidence",
    });
    expect(evidence.parentElement).toHaveAttribute("tabindex", "0");
    expect(within(evidence).getByText(
      "Exact recorded fields. Archived candidate count is inventory, not quality.",
    )).toBeVisible();
    expect(within(evidence).getByRole("columnheader", { name: "Run A · open" })).toBeVisible();
    expect(within(evidence).getByRole("columnheader", {
      name: "Run B · comparison",
    })).toBeVisible();
    expect(within(evidence).getByRole("columnheader", { name: "Relation" })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Job ID job-newer job-older Different/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Completed at 2026-08-18 01:00:00Z 2026-08-18 00:00:00Z Different/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", { name: /Seed 29 23 Different/i })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Computation budget 5s 5s Same/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Hard runtime limit 6s 6s Same/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", { name: /Workers 1 1 Same/i })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Early termination off on Different/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Minimum separation none 0.25 Different/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Archived candidates 1 2 Different/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Acknowledged assumptions interpret_s1_degenerate_entries_as_allowed_rotations interpret_s1_degenerate_entries_as_allowed_rotations Same/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Dataset lectra-7030786-v1.1 lectra-7030786-v1.1 Same/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Source slice SHA-256 d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8 d1e6d6d6aa300f9699cc8d9ffb63cee1747735f640f2b5501298d383ea1402e8 Same/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: /Archive schema yieldforge.candidate-archive.v1 yieldforge.candidate-archive.v1 Same/i,
    })).toBeVisible();
    expect(within(evidence).getByRole("row", {
      name: new RegExp(`Archive SHA-256 ${"d".repeat(64)} ${"c".repeat(64)} Different`, "i"),
    })).toBeVisible();
    expect(
      within(comparison).queryByText(/better|winner|improvement|optimal|savings/i),
    ).not.toBeInTheDocument();
  });

  it("preserves the run comparison pair when Run B is opened as Run A", async () => {
    const api = client();
    const newer = completedRun("job-newer");
    newer.settings.seed = 29;
    const older = completedRun("job-older");
    vi.mocked(api.listCompletedRuns).mockResolvedValue({
      schema_version: "yieldforge.api-completed-run-page.v1",
      items: [newer, older],
    });
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    render(<App client={api} />);
    const user = userEvent.setup();

    const comparison = await screen.findByRole("region", {
      name: "Read-only run comparison",
    });
    const selector = within(comparison).getByRole("combobox", {
      name: "Compare open run with",
    });
    await user.selectOptions(selector, "job-older");
    await user.click(screen.getByRole("button", { name: "Open completed run job-older" }));

    expect(selector).toHaveValue("job-newer");
    expect(within(comparison).getByRole("row", {
      name: /Job ID job-older job-newer Different/i,
    })).toBeVisible();
    expect(within(comparison).getByRole("row", {
      name: /Seed 23 29 Different/i,
    })).toBeVisible();
  });

  it("clears and locks run comparison while a new solver job is active", async () => {
    const api = client();
    const newer = completedRun("job-newer");
    const older = completedRun("job-older");
    vi.mocked(api.listCompletedRuns).mockResolvedValue({
      schema_version: "yieldforge.api-completed-run-page.v1",
      items: [newer, older],
    });
    vi.mocked(api.createJob).mockResolvedValue({ ...job, job_id: "job-active" });
    vi.mocked(api.streamJobEvents).mockImplementation(
      () => new Promise<void>(() => undefined),
    );
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    render(<App client={api} />);
    const user = userEvent.setup();

    const comparison = await screen.findByRole("region", {
      name: "Read-only run comparison",
    });
    const selector = within(comparison).getByRole("combobox", {
      name: "Compare open run with",
    });
    await user.selectOptions(selector, "job-older");
    await user.click(screen.getByRole("checkbox", { name: /acknowledge exact assumption/i }));
    await user.click(screen.getByRole("button", { name: /start solver job/i }));

    await waitFor(() => expect(selector).toBeDisabled());
    expect(selector).toHaveValue("");
    expect(within(comparison).queryByRole("table", {
      name: "Recorded run evidence",
    })).not.toBeInTheDocument();
  });

  it("shows completed-run empty and independent error states", async () => {
    const emptyApi = client();
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    const { unmount } = render(<App client={emptyApi} />);

    expect(await screen.findByText("No completed archive runs for this task yet.")).toBeVisible();
    expect(screen.getByText(
      "Complete another archive run for this task to compare two records.",
    )).toBeVisible();
    expect(screen.getByRole("button", { name: /start solver job/i })).toBeDisabled();
    unmount();

    const errorApi = client();
    vi.mocked(errorApi.listCompletedRuns).mockRejectedValue(new Error("history unavailable"));
    render(<App client={errorApi} />);

    expect(await screen.findByText(/Run history: Error: history unavailable/)).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Task 13958" })).toBeVisible();
    expect(screen.getByRole("checkbox", { name: /acknowledge exact assumption/i })).toBeVisible();
  });

  it("locks history during an active job and selects it after archive completion", async () => {
    const api = client();
    const older = completedRun("job-older");
    const fresh = completedRun("job-fresh");
    fresh.settings.seed = 31;
    fresh.archive.batch_sha256 = "e".repeat(64);
    vi.mocked(api.listCompletedRuns)
      .mockResolvedValueOnce({
        schema_version: "yieldforge.api-completed-run-page.v1",
        items: [older],
      })
      .mockResolvedValue({
        schema_version: "yieldforge.api-completed-run-page.v1",
        items: [fresh, older],
      });
    vi.mocked(api.createJob).mockResolvedValue({
      ...job,
      job_id: "job-fresh",
      source_task_binding: older.job.source_task_binding,
    });
    let releaseStream: (() => void) | undefined;
    vi.mocked(api.streamJobEvents).mockImplementation(
      (jobId, _after, _signal, onEvent) => new Promise<void>((resolve) => {
        releaseStream = () => {
          onEvent({
            schema_version: "yieldforge.api-job-event.v1",
            job_id: jobId,
            sequence: 1,
            occurred_at: "2026-08-18T02:00:00Z",
            kind: "status",
            status: "running",
            phase: null,
            candidate_id: null,
            candidate_count: 0,
            archive_available: false,
            error_code: null,
            error_message: null,
          });
          onEvent({
            schema_version: "yieldforge.api-job-event.v1",
            job_id: jobId,
            sequence: 2,
            occurred_at: "2026-08-18T02:00:01Z",
            kind: "terminal",
            status: "completed",
            phase: null,
            candidate_id: null,
            candidate_count: 1,
            archive_available: true,
            error_code: null,
            error_message: null,
          });
          resolve();
        };
      }),
    );
    vi.mocked(api.listCandidates).mockResolvedValue({
      schema_version: "yieldforge.api-candidate-page.v1",
      items: [],
      next_cursor: null,
    });
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    render(<App client={api} />);
    const user = userEvent.setup();

    const oldButton = await screen.findByRole("button", { name: "Open completed run job-older" });
    await user.click(screen.getByRole("checkbox", { name: /acknowledge exact assumption/i }));
    await user.clear(screen.getByRole("spinbutton", { name: "Seed" }));
    await user.type(screen.getByRole("spinbutton", { name: "Seed" }), "31");
    await user.click(screen.getByRole("button", { name: /start solver job/i }));

    await waitFor(() => expect(oldButton).toBeDisabled());
    expect(oldButton).toHaveAttribute("aria-pressed", "false");
    await act(async () => releaseStream?.());

    const freshButton = await screen.findByRole("button", { name: "Open completed run job-fresh" });
    expect(freshButton).toHaveAttribute("aria-pressed", "true");
    expect(freshButton).toBeEnabled();
    expect(api.listCompletedRuns).toHaveBeenCalledTimes(2);
  });

  it("does not let a late initial history response replace an active job", async () => {
    const api = client();
    let resolveHistory: ((value: Awaited<ReturnType<WorkbenchClient["listCompletedRuns"]>>) => void) | undefined;
    vi.mocked(api.listCompletedRuns).mockImplementation(() =>
      new Promise((resolve) => { resolveHistory = resolve; }));
    vi.mocked(api.createJob).mockResolvedValue({
      ...job,
      job_id: "job-active",
    });
    vi.mocked(api.streamJobEvents).mockImplementation(
      () => new Promise<void>(() => undefined),
    );
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    render(<App client={api} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: /acknowledge exact assumption/i }));
    await user.click(screen.getByRole("button", { name: /start solver job/i }));
    expect(await screen.findByText("job-active")).toBeVisible();

    await act(async () => resolveHistory?.({
      schema_version: "yieldforge.api-completed-run-page.v1",
      items: [completedRun("job-older")],
    }));

    const older = await screen.findByRole("button", { name: "Open completed run job-older" });
    expect(older).toBeDisabled();
    expect(older).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("job-active")).toBeVisible();
  });

  it("does not let a stale archive response cross a run selection", async () => {
    const api = client();
    const newer = completedRun("job-newer");
    const older = completedRun("job-older");
    vi.mocked(api.listCompletedRuns).mockResolvedValue({
      schema_version: "yieldforge.api-completed-run-page.v1",
      items: [newer, older],
    });
    let resolveNewCandidates: ((value: Awaited<ReturnType<WorkbenchClient["listCandidates"]>>) => void) | undefined;
    vi.mocked(api.listCandidates).mockImplementation((jobId) => {
      if (jobId === "job-newer") {
        return new Promise((resolve) => { resolveNewCandidates = resolve; });
      }
      return Promise.resolve({
        schema_version: "yieldforge.api-candidate-page.v1",
        items: [{ candidate_id: "older-candidate", report_type: "final", seed: 23, width: 4, density: 0.5, placement_count: 1 }],
        next_cursor: null,
      });
    });
    const geometryFor = (candidateId: string) => ({
      schema_version: "yieldforge.api-candidate-geometry.v1" as const,
      candidate: { candidate_id: candidateId, report_type: "final" as const, seed: 23, width: 4, density: 0.5, placement_count: 1 },
      sheet: { length: 10, width: 5 },
      provenance: "derived" as const,
      placements: [{ part_id: "part", rotation: 0, translation: [0, 0] as [number, number], projected_shape: [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]] as Array<[number, number]>, svg_points: [[0, 5], [1, 5], [1, 4], [0, 4], [0, 5]] as Array<[number, number]> }],
    });
    vi.mocked(api.getCandidateGeometry).mockImplementation((_jobId, candidateId) =>
      Promise.resolve(geometryFor(candidateId)));
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    render(<App client={api} />);

    await userEvent.setup().click(
      await screen.findByRole("button", { name: "Open completed run job-older" }),
    );
    expect(await screen.findByText("older-candidate")).toBeVisible();
    expect(await screen.findByRole("img", { name: /older-candidate placement geometry/i })).toBeVisible();

    await act(async () => resolveNewCandidates?.({
      schema_version: "yieldforge.api-candidate-page.v1",
      items: [{ candidate_id: "newer-candidate", report_type: "final", seed: 29, width: 3, density: 0.6, placement_count: 1 }],
      next_cursor: null,
    }));
    expect(screen.queryByText("newer-candidate")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /older-candidate placement geometry/i })).toBeVisible();
  });

  it("does not let stale geometry cross a run selection", async () => {
    const api = client();
    const newer = completedRun("job-newer");
    const older = completedRun("job-older");
    vi.mocked(api.listCompletedRuns).mockResolvedValue({
      schema_version: "yieldforge.api-completed-run-page.v1",
      items: [newer, older],
    });
    const candidateFor = (jobId: string) => ({
      candidate_id: `${jobId}-candidate`,
      report_type: "final" as const,
      seed: 23,
      width: 4,
      density: 0.5,
      placement_count: 1,
    });
    vi.mocked(api.listCandidates).mockImplementation((jobId) => Promise.resolve({
      schema_version: "yieldforge.api-candidate-page.v1",
      items: [candidateFor(jobId)],
      next_cursor: null,
    }));
    const geometryFor = (candidateId: string) => ({
      schema_version: "yieldforge.api-candidate-geometry.v1" as const,
      candidate: { ...candidateFor(candidateId.replace("-candidate", "")), candidate_id: candidateId },
      sheet: { length: 10, width: 5 },
      provenance: "derived" as const,
      placements: [{ part_id: "part", rotation: 0, translation: [0, 0] as [number, number], projected_shape: [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]] as Array<[number, number]>, svg_points: [[0, 5], [1, 5], [1, 4], [0, 4], [0, 5]] as Array<[number, number]> }],
    });
    let resolveNewGeometry: ((value: Awaited<ReturnType<WorkbenchClient["getCandidateGeometry"]>>) => void) | undefined;
    vi.mocked(api.getCandidateGeometry).mockImplementation((jobId, candidateId) => {
      if (jobId === "job-newer") {
        return new Promise((resolve) => { resolveNewGeometry = resolve; });
      }
      return Promise.resolve(geometryFor(candidateId));
    });
    window.history.replaceState({}, "", "/?view=nest&task=13958");
    render(<App client={api} />);

    await waitFor(() => expect(api.getCandidateGeometry).toHaveBeenCalledWith(
      "job-newer",
      "job-newer-candidate",
    ));
    await userEvent.setup().click(
      screen.getByRole("button", { name: "Open completed run job-older" }),
    );
    expect(await screen.findByRole("img", { name: /job-older-candidate placement geometry/i })).toBeVisible();

    await act(async () => resolveNewGeometry?.(geometryFor("job-newer-candidate")));

    expect(screen.queryByRole("img", { name: /job-newer-candidate placement geometry/i })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /job-older-candidate placement geometry/i })).toBeVisible();
  });

  it("shows immutable order-book identity, provenance, diagnostics, and task navigation", async () => {
    window.history.replaceState({}, "", "/?view=order-books&book=yfob-demo");
    render(<App client={client()} />);

    expect(
      await screen.findByRole("heading", { name: "Available immutable books" }),
    ).toBeVisible();
    expect(await screen.findByText("Analysis-only full manifest")).toBeVisible();
    expect(screen.getByText(orderBook.content_sha256)).toBeVisible();
    expect(screen.getByRole("table", { name: /field provenance/i })).toBeVisible();
    expect(screen.getByText("Shape recurrence")).toBeVisible();
    expect(screen.getByRole("link", { name: /open corpus task 13958/i })).toHaveAttribute(
      "href",
      "/?view=corpus&task=13958",
    );
    expect(await screen.findByRole("link", { name: /open nest lab for task 13958/i })).toHaveAttribute(
      "href",
      "/?view=nest&task=13958",
    );
  });

  it("deep-links views without a router", async () => {
    window.history.replaceState({}, "", "/?view=corpus");
    render(<App client={client()} />);
    await screen.findByRole("heading", { name: "Corpus Explorer" });
    expect(screen.getByRole("link", { name: "Nest Lab" })).toHaveAttribute(
      "href",
      "/?view=nest",
    );
    expect(screen.getByRole("link", { name: "Order Book Lab" })).toHaveAttribute(
      "href",
      "/?view=order-books",
    );
  });

  it("caps no-signal and high-mix generation at the truthful two-event slice limit", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?view=order-books");
    render(<App client={client()} />);
    const regime = await screen.findByRole("combobox", { name: "Regime" });
    await user.selectOptions(regime, "no_signal");
    expect(screen.getByRole("spinbutton", { name: "Events" })).toHaveAttribute("max", "2");
    expect(screen.getByText(/two-task slice supports only a truthful 2-event no_signal book/i)).toBeVisible();
    await user.selectOptions(regime, "high_mix");
    expect(screen.getByRole("spinbutton", { name: "Events" })).toHaveAttribute("max", "2");
  });
});
