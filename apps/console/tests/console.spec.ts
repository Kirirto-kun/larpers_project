import { test, expect } from "@playwright/test";
import { fixtures } from "./fixtures";
test("member never fetches admin data", async ({ page }) => {
  const f = await fixtures(page, { role: "member" });
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "Доступ для администратора" }),
  ).toBeVisible();
  expect(f.requests.filter((r) => r.path !== "/auth/me")).toHaveLength(0);
});
test("overview uses actual summary and hardware", async ({ page }) => {
  await fixtures(page);
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "Пул под контролем" }),
  ).toBeVisible();
  await expect(page.getByText("8 CPU")).toBeVisible();
  await expect(page.getByText("Выполняется: 2")).toBeVisible();
});
test("admin hardware and control with visible server failure", async ({
  page,
}) => {
  await fixtures(page);
  await page.route("**/api/v1/nodes/*/control", (r) =>
    r.fulfill({
      status: 403,
      json: { code: "FORBIDDEN", message: "Denied", request_id: "req-123" },
    }),
  );
  await page.goto("nodes");
  await expect(page.getByText("NVIDIA")).toBeVisible();
  await page.getByRole("button", { name: "Остановить", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("req-123");
  await expect(page.getByText("Готово", { exact: true })).toBeVisible();
});
test("benchmark displays server figures and raw samples honestly", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("benchmarks");
  await page.getByRole("button", { name: "Измерение bench-1" }).click();
  await expect(page.getByText("118.2%")).toBeVisible();
  await page.getByText("node-1 · 10 ед. / 1000 мс").click();
  await expect(
    page.getByRole("cell", { name: "50%", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "Загрузка CPU и GPU по отсчётам клиента" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Скачать JSON" }),
  ).toHaveAttribute("href", "/api/v1/benchmarks/bench-1/export");
});
test("empty mobile remains within viewport", async ({ page }) => {
  await fixtures(page, { empty: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("benchmarks");
  await expect(page.getByText("Измерений пока нет")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
