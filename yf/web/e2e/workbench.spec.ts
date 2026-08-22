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
    await page.goto("/?view=corpus&task=25801");
    await expect(page.getByText("m^-4 · interpretation unresolved")).toBeVisible();
    await expect(page.getByText("Blocked from solver projection")).toBeVisible();
    await expect(page.getByRole("button", { name: "Solve task 25801" })).toBeDisabled();

    await page.getByRole("button", { name: "13958" }).click();
    await expect(page.getByRole("article").getByText("Assumption-backed")).toBeVisible();
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

  test("runs Spyrrow, reopens its archive, and generates a deterministic order book", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "The real solver mutation runs once.");
    test.setTimeout(60_000);

    await page.goto("/?view=nest&task=13958");
    await expect(
      page.getByText("interpret_s1_degenerate_entries_as_allowed_rotations"),
    ).toBeVisible();
    await page.getByRole("checkbox", { name: /acknowledge exact assumption/i }).check();
    await page.getByRole("spinbutton", { name: "Computation seconds" }).fill("3");

    const createResponsePromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/solver-jobs") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Start solver job" }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status()).toBe(202);
    const created = (await createResponse.json()) as {
      job_id: string;
      source_task_binding: {
        tasks_index: number;
        acknowledged_assumption_codes: string[];
      };
    };
    expect(created.source_task_binding).toMatchObject({
      tasks_index: 13958,
      acknowledged_assumption_codes: [
        "interpret_s1_degenerate_entries_as_allowed_rotations",
      ],
    });

    await expect(page.getByText(/Status: (queued|running)/)).toBeVisible();
    await expect(page.getByText("live sample").first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Status: completed")).toBeVisible({ timeout: 30_000 });
    const candidates = page.getByRole("group", { name: "Candidates" }).getByRole("button");
    await expect(candidates.first()).toBeVisible();
    expect(await candidates.count()).toBeGreaterThan(0);
    await expect(page.getByRole("img", { name: /placement geometry/i })).toBeVisible();

    const jobResponse = await page.request.get(`/api/solver-jobs/${created.job_id}`);
    expect(jobResponse.status()).toBe(200);
    const completed = (await jobResponse.json()) as {
      status: string;
      candidate_count: number;
      archive_available: boolean;
    };
    expect(completed.status).toBe("completed");
    expect(completed.candidate_count).toBeGreaterThan(0);
    expect(completed.archive_available).toBe(true);

    await page.reload();
    await expect(page.getByText("Status: completed")).toBeVisible();
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
