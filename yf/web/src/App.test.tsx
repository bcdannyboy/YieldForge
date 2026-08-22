import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { WorkbenchClient } from "./api";
import { corpusSummary, job, orderBook, taskDetail, taskPage } from "./test/fixtures";

const client = (): WorkbenchClient => ({
  getCorpusSummary: vi.fn().mockResolvedValue(corpusSummary),
  listTasks: vi.fn().mockResolvedValue(taskPage),
  getTask: vi.fn((id: number) => Promise.resolve(taskDetail(id as 13958 | 25801))),
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
