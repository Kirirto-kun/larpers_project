import { test, expect } from "@playwright/test";
import { fixtures } from "./fixtures";

test("sign-in returns to the requested console page", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      status: 401,
      json: { code: "UNAUTHORIZED", message: "Sign in required" },
    }),
  );
  await page.goto("benchmarks");
  await expect(
    page.getByRole("link", { name: "Sign in", exact: true }),
  ).toHaveAttribute("href", "/login?next=%2Fadmin%2Fbenchmarks");
});

test("member never fetches admin data", async ({ page }) => {
  const f = await fixtures(page, { role: "member" });
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "Admin access required" }),
  ).toBeVisible();
  expect(f.requests.filter((r) => r.path !== "/auth/me")).toHaveLength(0);
});

test("overview uses actual summary and hardware", async ({ page }) => {
  await fixtures(page);
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("8 CPU threads · 1 GPU")).toBeVisible();
  await expect(
    page.getByRole("meter", { name: "Running", exact: true }),
  ).toHaveAttribute("value", "2");
  await expect(page.getByRole("img", { name: "Ready: 1" })).toBeVisible();
  await expect(
    page
      .locator(".metric")
      .filter({ hasText: "Accepted units" })
      .locator("strong"),
  ).toHaveText("10");
  await expect(page.locator("body")).not.toContainText(/[А-Яа-яЁё]/);
});

test("overview distinguishes unavailable data from measured zero", async ({
  page,
}) => {
  await fixtures(page, { summaryUnavailable: true, missingCapabilities: true });
  await page.goto("./");
  await expect(page.getByRole("alert")).toContainText(
    "Pool summary unavailable.",
  );
  await expect(
    page.getByText("Workload unavailable.", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".metric")
      .filter({ hasText: "Running tasks" })
      .locator("strong"),
  ).toHaveText("—");
  const capacity = page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Offered capacity" }) });
  await expect(capacity.locator("dd")).toHaveText(["—", "—", "—"]);
  await expect(capacity).toContainText(
    "Some device capabilities have not been reported.",
  );
});

test("empty overview reports real zeros", async ({ page }) => {
  await fixtures(page, { empty: true });
  await page.goto("./");
  await expect(page.locator(".metric strong")).toHaveText(["0", "0", "0", "0"]);
  await expect(page.getByText("No tasks yet.", { exact: true })).toBeVisible();
  await expect(page.locator(".availability-donut")).toHaveCount(0);
});

test("admin hardware and control with visible server failure", async ({
  page,
}) => {
  await fixtures(page);
  await page.route("**/api/v1/nodes/*/control", (route) =>
    route.fulfill({
      status: 403,
      json: { code: "FORBIDDEN", message: "Denied", request_id: "req-123" },
    }),
  );
  await page.goto("nodes");
  await expect(page.getByText("NVIDIA")).toBeVisible();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("req-123");
  await expect(page.getByRole("alert")).toContainText(
    "You do not have permission",
  );
  await expect(page.getByText("Ready", { exact: true })).toBeVisible();
});

test("benchmark displays server figures and raw samples honestly", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("benchmarks");
  await page.getByRole("button", { name: "Benchmark bench-1" }).click();
  await expect(page.getByText("118.2%")).toBeVisible();
  await page.getByText("node-1 · 10 units / 1000 ms").click();
  await expect(
    page.getByRole("cell", { name: "50%", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Not measured", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "CPU and GPU utilization by device sample" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Download JSON" }),
  ).toHaveAttribute("href", "/api/v1/benchmarks/bench-1/export");
  await expect(page.locator("body")).not.toContainText(/[А-Яа-яЁё]/);
});

test("empty mobile remains within viewport", async ({ page }) => {
  await fixtures(page, { empty: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("benchmarks");
  await expect(page.getByText("No benchmarks yet.")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
