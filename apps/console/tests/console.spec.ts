import { test, expect } from "@playwright/test";
import { fixtures } from "./fixtures";
test("overview uses saved metrics and explicit unmeasured costs", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "Shared compute. Visible progress." }),
  ).toBeVisible();
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
