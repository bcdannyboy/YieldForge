import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.describe("real local research workbench", () => {
  test.skip(
    process.env.YIELDFORGE_E2E_REAL_API !== "true",
    "Set YIELDFORGE_E2E_REAL_API=true with the FastAPI workbench running on port 8000.",
  );

  test("inspects blocked and assumption-backed tasks, then opens the exact solve gate", async ({
    page,
  }) => {
    await page.goto("/?view=corpus");
    await expect(page.getByText("Loaded 50 of 256 tasks")).toBeVisible();
    await page.getByRole("button", { name: "Load next 50 tasks" }).click();
    await expect(page.getByText("Loaded 100 of 256 tasks")).toBeVisible();

    await page.getByRole("button", { name: "16369" }).click();
    await expect(page.getByRole("heading", { name: "Task 16369" })).toBeVisible();
    await expect(
      page.getByLabel("Source polygon geometry").getByRole("img").first(),
    ).toBeVisible();

    await page.getByRole("button", { name: "25801" }).click();
    await expect(page.getByText("m^-4 · interpretation unresolved")).toBeVisible();
    await expect(page.getByText("Blocked from solver projection")).toBeVisible();
    await expect(page.getByRole("button", { name: "Solve task 25801" })).toBeDisabled();

    await page.getByRole("button", { name: "13958" }).click();
    await expect(
      page.getByRole("article").getByText("Assumption-backed", { exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Solve task 13958" }).click();
    await expect(page).toHaveURL(/view=nest&task=13958/);
    await expect(page.getByRole("button", { name: "Start solver job" })).toBeDisabled();
    await page.getByRole("checkbox", { name: /acknowledge exact assumption/i }).check();
    await expect(page.getByRole("button", { name: "Start solver job" })).toBeEnabled();

    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
  });

  test("renders all three deep-linked views at desktop and mobile widths", async ({ page }) => {
    for (const path of ["/?view=corpus", "/?view=nest&task=13958", "/?view=order-books"]) {
      await page.goto(path);
      await expect(page.locator("main")).toBeVisible();
      await expect(page.getByText("m^-4 · interpretation unresolved")).toBeVisible();
    }
  });

  test("runs matched flip projections through Spyrrow, browses both archives, and generates a deterministic order book", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "The real solver mutation runs once.");
    test.setTimeout(90_000);

    await page.goto("/?view=corpus");
    const loadMore = page.getByRole("button", { name: "Load next 50 tasks" });
    for (const loadedCount of [100, 150, 200, 250, 256]) {
      await loadMore.click();
      await expect(page.getByText(`Loaded ${loadedCount} of 256 tasks`)).toBeVisible();
    }
    await page.getByRole("button", { name: "6669" }).click();
    await expect(page.getByRole("heading", { name: "Task 6669" })).toBeVisible();
    await expect(page.getByText("6 parts use recorded flip_x = 1")).toBeVisible();
    await expect(page.getByText("Recorded transform and no-flip ablation available")).toBeVisible();
    await page.getByRole("link", { name: "Solve task 6669" }).click();

    await expect(page.getByText(/6 source parts carry recorded flip_x = 1/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "Run matched experiment" })).toBeDisabled();
    await page.getByRole("checkbox", { name: /acknowledge exact assumptions/i }).check();
    await page
      .getByRole("checkbox", { name: /acknowledge derived no-flip intervention/i })
      .check();
    await page.getByRole("spinbutton", { name: "Computation seconds" }).fill("2");

    const createResponsePromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/matched-solver-jobs") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Run matched experiment" }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status()).toBe(202);
    const created = (await createResponse.json()) as {
      experiment_pair_id: string;
      source_as_recorded: {
        job_id: string;
        experiment_pair_id: string;
        experiment_arm: string;
        source_task_binding: {
          tasks_index: number;
          solver_projection: {
            mode: string;
            projection_sha256: string;
            assumption_codes: string[];
            intervention_codes: string[];
            source_flip_part_count: number;
          };
        };
      };
      force_flip_x_zero: {
        job_id: string;
        experiment_pair_id: string;
        experiment_arm: string;
        source_task_binding: {
          solver_projection: {
            mode: string;
            projection_sha256: string;
            intervention_codes: string[];
          };
        };
      };
    };
    expect(created.source_as_recorded.source_task_binding).toMatchObject({
      tasks_index: 6669,
      solver_projection: {
        mode: "source_as_recorded",
        assumption_codes: [
          "interpret_s1_degenerate_entries_as_allowed_rotations",
          "interpret_s1_flip_x_as_local_x_coordinate_negation_before_rotation",
        ],
        intervention_codes: [],
        source_flip_part_count: 6,
      },
    });
    expect(created.force_flip_x_zero.source_task_binding.solver_projection).toMatchObject({
      mode: "force_flip_x_zero",
      intervention_codes: ["force_s1_flip_x_zero_for_ablation"],
    });
    expect(created.source_as_recorded.job_id).not.toBe(created.force_flip_x_zero.job_id);
    expect(created.source_as_recorded.experiment_pair_id).toBe(created.experiment_pair_id);
    expect(created.force_flip_x_zero.experiment_pair_id).toBe(created.experiment_pair_id);
    expect(created.source_as_recorded.source_task_binding.solver_projection.projection_sha256).toMatch(
      /^[0-9a-f]{64}$/,
    );
    expect(created.force_flip_x_zero.source_task_binding.solver_projection.projection_sha256).toMatch(
      /^[0-9a-f]{64}$/,
    );

    await expect(page.getByText(`Matched pair ${created.experiment_pair_id}`)).toBeVisible();
    await expect(page.getByText(created.source_as_recorded.job_id)).toBeVisible();
    await expect(page.getByText(created.force_flip_x_zero.job_id)).toBeVisible();
    await expect(page.getByText("live sample").first()).toBeVisible({ timeout: 20_000 });
    const recordedRun = page.getByRole("button", {
      name: `Open completed run ${created.source_as_recorded.job_id}`,
    });
    const ablationRun = page.getByRole("button", {
      name: `Open completed run ${created.force_flip_x_zero.job_id}`,
    });
    await expect(recordedRun).toBeVisible({ timeout: 30_000 });
    await expect(ablationRun).toBeVisible({ timeout: 30_000 });
    await expect(recordedRun).toHaveAttribute("aria-pressed", "true");
    const candidates = page.getByRole("group", { name: "Candidates" }).getByRole("button");
    await expect(candidates.first()).toBeVisible();
    expect(await candidates.count()).toBeGreaterThan(0);
    await expect(page.getByRole("img", { name: /placement geometry/i })).toBeVisible();

    const comparison = page.getByRole("region", { name: "Read-only run comparison" });
    const comparisonSelector = comparison.getByRole("combobox", {
      name: "Compare open run with",
    });
    await expect(comparisonSelector).toHaveValue(created.force_flip_x_zero.job_id);
    const evidence = comparison.getByRole("table", { name: "Recorded run evidence" });
    await expect(
      evidence.getByRole("row", {
        name: /Projection mode source_as_recorded force_flip_x_zero Different/i,
      }),
    ).toBeVisible();
    await expect(
      evidence.getByRole("row", {
        name: /Interventions none force_s1_flip_x_zero_for_ablation Different/i,
      }),
    ).toBeVisible();
    await expect(
      evidence.getByRole("row", {
        name: new RegExp(
          `Experiment pair ID ${created.experiment_pair_id} ${created.experiment_pair_id} Same`,
          "i",
        ),
      }),
    ).toBeVisible();
    await expect(evidence.getByRole("row", { name: /Seed 23 23 Same/i })).toBeVisible();
    await expect(comparison).not.toContainText(/better|winner|improvement|optimal|savings/i);

    const comparisonAccessibility = await new AxeBuilder({ page }).analyze();
    expect(comparisonAccessibility.violations).toEqual([]);

    for (const jobId of [
      created.source_as_recorded.job_id,
      created.force_flip_x_zero.job_id,
    ]) {
      const jobResponse = await page.request.get(`/api/solver-jobs/${jobId}`);
      expect(jobResponse.status()).toBe(200);
      const completed = (await jobResponse.json()) as {
        status: string;
        candidate_count: number;
        archive_available: boolean;
      };
      expect(completed.status).toBe("completed");
      expect(completed.candidate_count).toBeGreaterThan(0);
      expect(completed.archive_available).toBe(true);
    }

    const ablationArchiveRequest = page.waitForResponse((response) =>
      response.url().includes(
        `/api/solver-jobs/${created.force_flip_x_zero.job_id}/candidates`,
      ),
    );
    await ablationRun.click();
    expect((await ablationArchiveRequest).status()).toBe(200);
    await expect(ablationRun).toHaveAttribute("aria-pressed", "true");
    await expect(comparisonSelector).toHaveValue(created.source_as_recorded.job_id);
    await expect(
      evidence.getByRole("row", {
        name: /Projection mode force_flip_x_zero source_as_recorded Different/i,
      }),
    ).toBeVisible();
    await expect(
      page.getByRole("group", { name: "Candidates" }).getByRole("button").first(),
    ).toBeVisible();
    await expect(page.getByRole("img", { name: /placement geometry/i })).toBeVisible();

    await page.reload();
    await expect(page.getByText("Status: completed")).toBeVisible();
    await expect(recordedRun.or(ablationRun).first()).toBeVisible();
    await expect(
      page.getByRole("group", { name: "Candidates" }).getByRole("button").first(),
    ).toBeVisible();
    await expect(page.getByRole("img", { name: /placement geometry/i })).toBeVisible();

    await page.getByRole("link", { name: "Order Book Lab" }).click();
    await expect(page.getByRole("heading", { name: "Order Book Lab" })).toBeVisible();
    const committedBookId = "yfob-bf049e9141623c98654a2255";
    await page.locator(".book-list").getByText(committedBookId).click();
    await expect(page.getByRole("heading", { name: committedBookId })).toBeVisible();
    const provenance = page.getByRole("table", { name: "Field provenance" });
    await expect(provenance.getByLabel("SRC Source real").first()).toBeVisible();
    await expect(provenance.getByLabel("GEN Generated").first()).toBeVisible();
    await expect(provenance.getByLabel("ASM Assumed").first()).toBeVisible();

    await page.getByRole("spinbutton", { name: "Events" }).fill("3");
    await page.getByRole("spinbutton", { name: "Seed" }).fill("424242");
    await page.getByLabel("Starts at").fill("2026-02-03T04:05");
    await page.getByRole("spinbutton", { name: "Interval minutes" }).fill("37");
    const firstGenerationPromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/order-books") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Generate deterministic book" }).click();
    const firstGeneration = await firstGenerationPromise;
    expect(firstGeneration.status()).toBe(201);
    const generated = (await firstGeneration.json()) as {
      order_book_id: string;
      content_sha256: string;
    };
    await expect(page.getByRole("heading", { name: generated.order_book_id })).toBeVisible();
    await expect(page.getByText(generated.content_sha256)).toBeVisible();

    await page.goto(`/?view=order-books&book=${generated.order_book_id}`);
    await expect(page.getByRole("heading", { name: generated.order_book_id })).toBeVisible();
    await expect(page.getByText(generated.content_sha256)).toBeVisible();
    await page.getByRole("spinbutton", { name: "Events" }).fill("3");
    await page.getByRole("spinbutton", { name: "Seed" }).fill("424242");
    await page.getByLabel("Starts at").fill("2026-02-03T04:05");
    await page.getByRole("spinbutton", { name: "Interval minutes" }).fill("37");
    const secondGenerationPromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/order-books") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Generate deterministic book" }).click();
    const secondGeneration = await secondGenerationPromise;
    expect(secondGeneration.status()).toBe(201);
    expect(await secondGeneration.json()).toMatchObject(generated);
  });
});
