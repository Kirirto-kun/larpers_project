import { test, expect } from "@playwright/test";
import { fixtures } from "./fixtures";
test("overview uses saved metrics and explicit unmeasured costs", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("Not measured (estimate)")).toBeVisible();
  await expect(page.getByText("Live updates disconnected")).toBeVisible();
});
test("offline nodes and member authorization", async ({ page }) => {
  const f = await fixtures(page, { role: "member", offline: true });
  await page.goto("nodes");
  await expect(page.getByText("offline", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Lab laptop" }).click();
  await expect(page.getByRole("button", { name: "quarantine" })).toHaveCount(0);
  await expect(
    page.getByText("Not measured", { exact: true }).first(),
  ).toBeVisible();
  expect(f.requests.some((r) => r.path.includes("/control"))).toBeFalsy();
});
test("admin control is sent to server", async ({ page }) => {
  const f = await fixtures(page);
  await page.goto("nodes");
  await page.getByRole("button", { name: "Lab laptop" }).click();
  await page.getByRole("button", { name: "drain", exact: true }).click();
  await expect
    .poll(() => f.requests.filter((r) => r.path.endsWith("/control")).length)
    .toBe(1);
});
test("benchmarks show efficiency above 100 without clamping and raw evidence", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("benchmarks");
  await page.getByRole("button", { name: "embeddings · experiment-1" }).click();
  await expect(page.getByText("118.2%")).toBeVisible();
  await expect(
    page.getByText("Efficiency exceeds 100%.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Export JSON" })).toHaveAttribute(
    "href",
    /format=json/,
  );
  await expect(
    page.getByText("Synthetic test fixture", { exact: false }),
  ).toBeVisible();
});
test("empty state remains honest on mobile", async ({ page }) => {
  await fixtures(page, { empty: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("benchmarks");
  await expect(
    page.getByText("No measurements yet.", { exact: false }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("raw utilization gaps, timing stages and unknown means remain explicit", async ({
  page,
}) => {
  const { benchmark } = await import("./fixtures");
  await fixtures(page);
  await page.route("**/api/v1/benchmarks", (route) =>
    route.fulfill({
      json: [
        {
          ...benchmark,
          utilization: [
            {
              node_id: "node-1",
              cpu_percent: 0,
              gpu_percent: null,
              window_ms: 4000,
              gaps: 2,
              samples: [
                { elapsed_ms: 0, cpu_percent: 0, gpu_percent: null },
                { elapsed_ms: 1000, cpu_percent: 50, gpu_percent: null },
                { elapsed_ms: 4000, cpu_percent: 70, gpu_percent: null },
              ],
            },
          ],
        },
      ],
    }),
  );
  await page.goto("benchmarks");
  await page.getByRole("button", { name: "embeddings · experiment-1" }).click();
  await expect(
    page.getByRole("img", { name: "node-1 CPU and GPU utilization over time" }),
  ).toBeVisible();
  await expect(
    page.getByText("Window 4,000 ms · 2 gaps · 3 samples"),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Timing breakdown chart" }),
  ).toContainText("Not measured");
});

test("missing baseline and duplicate participants do not produce an efficiency claim", async ({
  page,
}) => {
  const { benchmark } = await import("./fixtures");
  await fixtures(page);
  await page.route("**/api/v1/benchmarks", (route) =>
    route.fulfill({
      json: [{ ...benchmark, node_ids: ["node-1", "node-1"], standalone: [] }],
    }),
  );
  await page.goto("benchmarks");
  await page.getByRole("button", { name: "embeddings · experiment-1" }).click();
  await expect(
    page.getByText("Insufficient data", { exact: true }),
  ).toBeVisible();
});

test("changed concurrency creates a distinct experiment without relabelling saved results", async ({
  page,
}) => {
  const { benchmark } = await import("./fixtures");
  await fixtures(page);
  const submissions: any[] = [];
  let reports = [benchmark];
  await page.route("**/api/v1/benchmarks", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      submissions.push(body);
      const b = {
        ...benchmark,
        ...body,
        id: "new-" + submissions.length,
        experiment_id: "experiment-" + (submissions.length + 1),
      };
      reports = [...reports, b];
      return route.fulfill({ status: 202, json: b });
    }
    return route.fulfill({ json: reports });
  });
  await page.goto("benchmarks");
  await page.getByLabel("Input digest").fill("sha256:immutable-corpus");
  await page.getByLabel("Lab laptop").check();
  await page.getByRole("button", { name: "Create experiment" }).click();
  await expect(
    page.getByRole("heading", { name: "embeddings / experiment-2" }),
  ).toBeVisible();
  await page.getByLabel("Concurrency", { exact: true }).fill("4");
  await page.getByRole("button", { name: "Create experiment" }).click();
  await expect(
    page.getByRole("heading", { name: "embeddings / experiment-3" }),
  ).toBeVisible();
  expect(submissions.map((s) => s.concurrency)).toEqual([2, 4]);
  expect(reports[0].concurrency).toBe(2);
});

test("server denial of an admin action remains visible and does not pretend success", async ({
  page,
}) => {
  await fixtures(page);
  await page.route("**/api/v1/nodes/*/control", (route) =>
    route.fulfill({
      status: 403,
      json: {
        code: "FORBIDDEN",
        message: "Only the owner may stop this device",
        request_id: "request-123",
      },
    }),
  );
  await page.goto("nodes");
  await page.getByRole("button", { name: "Lab laptop" }).click();
  await page.getByRole("button", { name: "stop", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Only the owner may stop this device",
  );
  await expect(page.getByRole("alert")).toContainText("request-123");
  await expect(page.getByText("ready", { exact: true })).toBeVisible();
});
