import { test, expect } from "@playwright/test";
import { fixtures } from "./fixtures";

test("public landing opens registration and returns to the requested task", async ({
  page,
}) => {
  const f = await fixtures(page, { anonymous: true });
  await page.goto("/");
  await page
    .getByRole("link", { name: "Run a task", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/\/register\?next=%2Fnew/);
  await page.getByLabel("Name", { exact: true }).fill("Anna");
  await page.getByLabel("Email").fill("anna@example.com");
  await page.getByLabel("Password", { exact: true }).fill("a-strong-password");
  await page.getByRole("button", { name: "Sign up", exact: true }).click();
  await expect(page).toHaveURL(/\/new$/);
  await expect(page.getByLabel("What should the task do?")).toBeVisible();
  expect(f.requests.some((r) => r.path === "/auth/register")).toBeTruthy();
});

test("mobile navigation exposes contribution and FAQ without private data", async ({
  page,
}) => {
  const f = await fixtures(page, { anonymous: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open menu" }).click();
  await page
    .getByRole("link", { name: "Questions", exact: true })
    .first()
    .click();
  await page
    .locator("summary")
    .filter({ hasText: "Can I pause my contribution?" })
    .click();
  await expect(
    page.getByText("Yes. Pause your device from your workspace at any time."),
  ).toBeVisible();
  expect(f.requests.filter((r) => r.path !== "/auth/me")).toHaveLength(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("direct device link survives sign-in", async ({ page }) => {
  await fixtures(page, { anonymous: true });
  await page.goto("/contribute");
  await page.getByLabel("Email").fill("anna@example.com");
  await page.getByLabel("Password", { exact: true }).fill("a-strong-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/contribute$/);
  await expect(page.getByLabel("Computer name")).toBeVisible();
});

test("account links cannot redirect a user to an external site", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("/login?next=https%3A%2F%2Fexample.com");
  await expect(page).toHaveURL(/127\.0\.0\.1:5173\/$/);
  await expect(
    page.getByRole("heading", { name: "Your workspace" }),
  ).toBeVisible();
});

test("creating an account from a direct device link keeps the destination", async ({
  page,
}) => {
  await fixtures(page, { anonymous: true });
  await page.goto("/contribute");
  await page.getByRole("link", { name: "Create account", exact: true }).click();
  await expect(page).toHaveURL(/\/register\?next=%2Fcontribute$/);
  await page.getByLabel("Name", { exact: true }).fill("Anna");
  await page.getByLabel("Email").fill("anna@example.com");
  await page.getByLabel("Password", { exact: true }).fill("a-strong-password");
  await page.getByRole("button", { name: "Sign up", exact: true }).click();
  await expect(page).toHaveURL(/\/contribute$/);
  await expect(page.getByLabel("Computer name")).toBeVisible();
});
