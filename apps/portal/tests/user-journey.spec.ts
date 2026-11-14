import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { fixtures } from "./fixtures";
async function prepare(page: import("@playwright/test").Page) {
  await page.goto("new");
  await page
    .getByRole("textbox", { name: "Request", exact: true })
    .fill("Index my documents 查找资料");
  await page.getByLabel("I reviewed").check();
  await page.getByRole("button", { name: "Prepare execution plan" }).click();
  await expect(
    page.getByRole("heading", { name: "Execution preview" }),
  ).toBeVisible();
}
test("safe retry keeps the same idempotency key and downloads own-code output", async ({
  page,
}) => {
  const f = await fixtures(page);
  await prepare(page);
  await page.getByRole("button", { name: "Run task", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Response lost");
  await page.getByRole("button", { name: "Run task", exact: true }).click();
  await expect(page).toHaveURL(/jobs\/job-1/);
  expect(f.keys.length).toBe(2);
  expect(f.keys[0]).toBe(f.keys[1]);
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "model.pt" }).click();
  const downloaded = await download;
  expect(downloaded.suggestedFilename()).toBe("model.pt");
  expect(await readFile((await downloaded.path())!, "utf8")).toBe(
    "SYNTHETIC MODEL FILE\n",
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Document corpus" }),
  ).toBeVisible();
  expect(f.started).toBe(2);
});
test("stale preview refreshes revision before retry", async ({ page }) => {
  const f = await fixtures(page, { stale: true });
  await prepare(page);
  await page.getByRole("button", { name: "Run task", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Plan changed");
  await page.getByRole("button", { name: "Run task", exact: true }).click();
  const starts = f.requests.filter((r) => r.path === "/jobs" && r.body);
  expect(
    starts.map((r) => (r.body as { plan_revision: number }).plan_revision),
  ).toEqual([1, 2]);
});
test("answer is bound to question and revision", async ({ page }) => {
  const f = await fixtures(page, { question: true });
  await prepare(page);
  await expect(
    page.getByRole("button", { name: "Run task", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Which dataset split?").fill("80/20");
  await page.getByRole("button", { name: "Send answer" }).click();
  await expect(
    page.getByRole("button", { name: "Run task", exact: true }),
  ).toBeEnabled();
  expect(f.requests.find((r) => r.path.endsWith("/answers"))?.body).toEqual({
    question_id: "q-1",
    plan_revision: 1,
    answer: "80/20",
  });
});
test("partial output and truncated logs stay explicit; disconnected events refresh snapshot", async ({
  page,
}) => {
  const f = await fixtures(page, { partial: true });
  await page.goto("jobs/job-1");
  await expect(
    page.getByText("Partial result:", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("Logs were truncated")).toBeVisible();
  await expect(page.getByText("Live connection interrupted")).toBeVisible();
  await expect
    .poll(() => f.requests.filter((r) => r.path === "/jobs/job-1").length)
    .toBeGreaterThan(1);
});
test("mobile home and explicit contribution consent", async ({ page }) => {
  await fixtures(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("contribute");
  await expect(
    page.getByRole("button", { name: "Generate join command" }),
  ).toBeDisabled();
  await page.getByLabel("I own").check();
  await page.getByRole("button", { name: "Generate join command" }).click();
  await expect(
    page.getByText("campus join --invitation test-only", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
test("secret files excluded and text disclosure is opt-in", async ({
  page,
}) => {
  const f = await fixtures(page);
  await page.goto("new");
  await page.getByLabel("Project or dataset files").setInputFiles([
    {
      name: ".env",
      mimeType: "text/plain",
      buffer: Buffer.from("SECRET=hidden"),
    },
    {
      name: "说明.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("safe Chinese document"),
    },
  ]);
  await expect(page.getByText("Excluded (secret or dependency)")).toBeVisible();
  await expect(page.getByLabel("Send this fragment")).not.toBeChecked();
  expect(f.requests.some((r) => r.path === "/uploads")).toBeFalsy();
});

test("interrupted upload resumes missing parts with checksums and excludes secrets", async ({
  page,
}) => {
  const f = await fixtures(page);
  const parts: number[] = [];
  const accepted: number[] = [];
  const checksums: Record<string, string> = {};
  let created = 0;
  let failed = false;
  await page.route("**/api/v1/uploads**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/uploads")) {
      created++;
      return route.fulfill({ json: { id: "upload-1" } });
    }
    if (path.endsWith("/finalize"))
      return route.fulfill({ json: { artifact_id: "input-1" } });
    if (request.method() === "GET")
      return route.fulfill({
        json: {
          part_size_bytes: 4,
          uploaded_parts: accepted,
          part_checksums: checksums,
        },
      });
    const part = Number(path.split("/").at(-1));
    parts.push(part);
    expect(request.headers()["x-content-sha256"]).toMatch(/^[a-f0-9]{64}$/);
    if (part === 1 && !failed) {
      failed = true;
      return route.fulfill({
        status: 503,
        json: { code: "TEMPORARY", message: "Upload connection lost" },
      });
    }
    accepted.push(part);
    checksums[String(part)] = request.headers()["x-content-sha256"];
    return route.fulfill({ json: {} });
  });
  await page.goto("new");
  await page
    .getByRole("textbox", { name: "Request", exact: true })
    .fill("Index corpus");
  await page.getByLabel("Project or dataset files").setInputFiles([
    {
      name: "corpus.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("abcdefgh"),
    },
    { name: ".env", mimeType: "text/plain", buffer: Buffer.from("private") },
  ]);
  await page.getByLabel("I reviewed").check();
  await page.getByRole("button", { name: "Prepare execution plan" }).click();
  await expect(page.getByRole("alert")).toContainText("Upload connection lost");
  await page.getByRole("button", { name: "Prepare execution plan" }).click();
  await expect(
    page.getByRole("heading", { name: "Execution preview" }),
  ).toBeVisible();
  expect(created).toBe(1);
  expect(parts).toEqual([0, 1, 1]);
  expect(
    f.requests.find((r) => r.path === "/job-previews")?.body,
  ).toMatchObject({ artifact_ids: ["input-1"], llm_fragments: [] });
});

test("editing input invalidates the ready plan", async ({ page }) => {
  await fixtures(page);
  await prepare(page);
  await page
    .getByRole("textbox", { name: "Request", exact: true })
    .fill("Different input");
  await expect(
    page.getByRole("button", { name: "Run task", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("I reviewed")).not.toBeChecked();
});

test("expired session returns to sign-in instead of trapping the member in a stale workspace", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("new");
  await page
    .getByRole("textbox", { name: "Request", exact: true })
    .fill("Index documents");
  await page.getByLabel("I reviewed").check();
  await page.route("**/api/v1/job-previews", (route) =>
    route.fulfill({
      status: 401,
      json: { code: "UNAUTHORIZED", message: "Session expired" },
    }),
  );
  await page.getByRole("button", { name: "Prepare execution plan" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome to Campus Pool" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeEnabled();
});
