import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { WorkbenchClient } from "./api";
import {
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
        max_runtime_seconds: 6,
      }),
    );
  });

  it("rediscovers and renders a completed immutable candidate archive", async () => {
    const api = client();
    const completedJob = {
      ...job,
      status: "completed" as const,
      latest_event_id: 5,
      candidate_count: 1,
      archive_available: true,
    };
    const candidate = {
      candidate_id: "candidate-archived",
      report_type: "final" as const,
      seed: 23,
      width: 42,
      density: 0.75,
      placement_count: 1,
    };
    vi.mocked(api.listTaskJobs).mockResolvedValue({
      schema_version: "yieldforge.api-task-jobs.v1",
      items: [completedJob],
    });
    vi.mocked(api.listCandidates).mockResolvedValue({
      schema_version: "yieldforge.api-candidate-page.v1",
      items: [candidate],
      next_cursor: null,
    });
    vi.mocked(api.getCandidateGeometry).mockResolvedValue({
      schema_version: "yieldforge.api-candidate-geometry.v1",
      candidate,
      sheet: { length: 100, width: 50 },
      provenance: "derived",
      placements: [
        {
          part_id: "part-1",
          rotation: 0,
          translation: [10, 10],
          projected_shape: [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
          svg_points: [[10, 40], [11, 40], [11, 39], [10, 39], [10, 40]],
        },
      ],
    });
    window.history.replaceState({}, "", "/?view=nest&task=13958");

    render(<App client={api} />);

    expect(await screen.findByText("candidate-archived")).toBeVisible();
    expect(
      await screen.findByRole("img", { name: /candidate candidate-archived placement geometry/i }),
    ).toBeVisible();
    expect(api.listTaskJobs).toHaveBeenCalledWith(13958);
    expect(api.listCandidates).toHaveBeenCalledWith("job-1", undefined);
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
