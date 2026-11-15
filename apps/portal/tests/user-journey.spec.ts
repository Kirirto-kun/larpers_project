import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { fixtures } from "./fixtures";
test("long job logs advance the cursor and retain new lines", async ({
  page,
}) => {
  await fixtures(page);
  await page.route("**/api/v1/jobs/job-1/logs*", (r) => {
    const cursor = Number(
      new URL(r.request().url()).searchParams.get("cursor") || 0,
    );
    const entries =
      cursor === 0
        ? Array.from({ length: 100 }, (_, i) => ({
            cursor: i + 1,
            text: "Line " + (i + 1),
            gap: false,
          }))
        : cursor === 100
          ? [{ cursor: 101, text: "Last line 101", gap: false }]
          : [];
    return r.fulfill({
      json: {
        entries,
        cursor: entries.at(-1)?.cursor || cursor,
        truncated: false,
      },
    });
  });
  await page.goto("/jobs/job-1");
  await expect(page.getByLabel("Execution log")).toContainText("Last line 101");
});
test("registers with password and real empty devices", async ({ page }) => {
  const f = await fixtures(page, { anonymous: true });
  await page.goto("/register");
  await page.getByLabel("Name", { exact: true }).fill("Anna");
  await page.getByLabel("Email").fill("anna@example.com");
  await page.getByLabel("Password", { exact: true }).fill("a-strong-password");
  await page.getByRole("button", { name: "Sign up", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Your workspace" }),
  ).toBeVisible();
  expect(f.requests.find((r) => r.path === "/auth/register")?.body).toEqual({
    name: "Anna",
    email: "anna@example.com",
    password: "a-strong-password",
  });
  await page.getByRole("link", { name: "Devices", exact: true }).click();
  await expect(page.getByText("No devices connected yet")).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
test("offline resume stays pending and private invitation is separate", async ({
  page,
}) => {
  const f = await fixtures(page, { nodes: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/contribute");
  await expect(page.getByText("Offline", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Connect / resume" }).click();
  await expect(page.getByText("Offline", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Waiting for device to apply settings", { exact: false }),
  ).toBeVisible();
  await page.getByLabel("Computer name").fill("My PC");
  await page.getByRole("button", { name: "Create invitation" }).click();
  await expect(page.getByLabel("Private invitation")).toHaveValue(
    "private-device-invitation-1234567890",
  );
  expect(f.requests.find((r) => r.path === "/invitations")?.body).toEqual({
    kind: "device",
    name: "My PC",
    ttl_seconds: 1800,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
test("preview review clarification safe retry and download use server contract", async ({
  page,
}) => {
  const f = await fixtures(page, { retry: true, question: true });
  await page.goto("/new");
  await page.getByLabel("What should the task do?").fill("Process data");
  await page.getByLabel("I allow my description").check();
  await page.getByRole("button", { name: "Prepare plan" }).click();
  await expect(
    page.getByRole("button", { name: "Start task", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Which dataset?").fill("All");
  await page.getByRole("button", { name: "Send answer" }).click();
  await page.getByRole("button", { name: "Start task", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Connection interrupted");
  await page.getByRole("button", { name: "Start task", exact: true }).click();
  await expect(page).toHaveURL(/jobs\/job-1/);
  const starts = f.requests.filter((r) => r.path === "/jobs" && r.body);
  expect(starts[0].headers["idempotency-key"]).toBe(
    starts[1].headers["idempotency-key"],
  );
  expect(starts[0].body).toEqual({ preview_id: "preview-1", plan_revision: 2 });
  await expect(page.getByText("Log truncated")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "result.txt" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("result.txt");
  expect(await readFile((await file.path())!, "utf8")).toBe("OK\n");
});
test("activation consumes hash and saves account", async ({ page }) => {
  const f = await fixtures(page, { anonymous: true });
  await page.goto("/#token=private-activation-token-1234567890");
  await expect(
    page.getByRole("heading", { name: "Finish your account" }),
  ).toBeVisible();
  expect(page.url()).not.toContain("token=");
  expect(f.requests.find((r) => r.path === "/auth/login")?.body).toEqual({
    token: "private-activation-token-1234567890",
  });
  await page.getByLabel("Name", { exact: true }).fill("Anna");
  await page.getByLabel("Email").fill("anna@example.com");
  await page.getByLabel("Password", { exact: true }).fill("a-strong-password");
  await page.getByRole("button", { name: "Save account" }).click();
  await expect(
    page.getByRole("heading", { name: "Your workspace" }),
  ).toBeVisible();
});

test("upload includes whole and part checksums, resumes and uses artifact input binding", async ({
  page,
}) => {
  const f = await fixtures(page);
  let created = 0,
    failed = false;
  const parts: any[] = [];
  const sent: number[] = [];
  await page.route("**/api/v1/uploads**", async (route) => {
    const r = route.request(),
      path = new URL(r.url()).pathname;
    const json = (v: any, status = 200) => route.fulfill({ json: v, status });
    if (path.endsWith("/uploads")) {
      created++;
      expect(r.postDataJSON()).toMatchObject({
        name: "main.py",
        kind: "input",
        sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
      });
      return json({ id: "up-1" });
    }
    if (path.endsWith("/finalize"))
      return json({
        id: "input-1",
        name: "main.py",
        sha256: "a".repeat(64),
        size_bytes: 8,
        media_type: "text/plain",
      });
    if (r.method() === "GET")
      return json({
        id: "up-1",
        state: "pending",
        part_size_bytes: 4,
        part_count: 2,
        parts,
        artifact: null,
      });
    const number = Number(path.split("/").at(-1));
    sent.push(number);
    expect(r.headers()["x-content-sha256"]).toMatch(/^[a-f0-9]{64}$/);
    if (number === 1 && !failed) {
      failed = true;
      return json({ code: "TEMPORARY", message: "Upload interrupted" }, 503);
    }
    parts.push({
      number,
      sha256: r.headers()["x-content-sha256"],
      size_bytes: 4,
    });
    return json(parts.at(-1));
  });
  await page.goto("/new");
  await page.getByLabel("What should the task do?").fill("Run main.py");
  await page.getByLabel("Code and data").setInputFiles([
    {
      name: "main.py",
      mimeType: "text/plain",
      buffer: Buffer.from("abcdefgh"),
    },
    { name: ".env", mimeType: "text/plain", buffer: Buffer.from("SECRET") },
  ]);
  await expect(page.getByText("Excluded: secret or system file")).toBeVisible();
  await page.getByLabel("I allow my description").check();
  await page.getByRole("button", { name: "Prepare plan" }).click();
  await expect(page.getByRole("alert")).toContainText("Upload interrupted");
  await page.getByRole("button", { name: "Prepare plan" }).click();
  await expect(
    page.getByRole("heading", { name: "Review the plan" }),
  ).toBeVisible();
  expect(created).toBe(1);
  expect(sent).toEqual([0, 1, 1]);
  expect(
    f.requests.find((r) => r.path === "/job-previews")?.body,
  ).toMatchObject({
    inputs: [
      { artifact_id: "input-1", mount_path: "/inputs/main.py", archive: false },
    ],
    share_with_llm: true,
    selected_files: [],
  });
});

test("password login and logout send credential and CSRF contracts", async ({
  page,
}) => {
  const f = await fixtures(page, { anonymous: true });
  await page.goto("/login");
  await page.getByLabel("Email").fill("anna@example.com");
  await page.getByLabel("Password", { exact: true }).fill("a-strong-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Your workspace" }),
  ).toBeVisible();
  expect(f.requests.find((r) => r.path === "/auth/login")?.body).toEqual({
    email: "anna@example.com",
    password: "a-strong-password",
  });
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(
    f.requests.find((r) => r.path === "/auth/logout")?.headers["x-csrf-token"],
  ).toBe("csrf-test");
});
test("example opens review without uploading or sharing with AI", async ({
  page,
}) => {
  const f = await fixtures(page);
  await page.route("**/api/v1/examples", (r) =>
    r.fulfill({
      json: [
        { id: "cpu", name: "CPU", description: "Example", requires_gpu: false },
      ],
    }),
  );
  await page.goto("/new");
  await page.getByRole("button", { name: "View plan CPU" }).click();
  await expect(
    page.getByRole("heading", { name: "Review the plan" }),
  ).toBeVisible();
  expect(
    f.requests.find((r) => r.path === "/examples/cpu/preview")?.body,
  ).toEqual({});
  expect(
    f.requests.filter(
      (r) => ["/job-previews", "/uploads", "/jobs"].includes(r.path) && r.body,
    ),
  ).toHaveLength(0);
});
test("clipboard refusal gives manual copy and macOS has shell fallback", async ({
  page,
}) => {
  await fixtures(page);
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: async () => {
          throw new Error("denied");
        },
      },
    }),
  );
  await page.goto("/contribute");
  await page.getByRole("button", { name: "Create invitation" }).click();
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(
    page.getByText("Select the invitation above and copy it manually."),
  ).toBeVisible();
  await page.getByText("macOS: download will not open?").click();
  await expect(
    page.getByText("sh ~/Downloads/Connect.command", { exact: true }),
  ).toBeVisible();
});
test("search uses selected index artifact and follows created search job", async ({
  page,
}) => {
  const f = await fixtures(page);
  await page.route("**/api/v1/jobs/job-1/search", (r) =>
    r.fulfill({ status: 202, json: { search_job_id: "search-1" } }),
  );
  await page.goto("/jobs/job-1");
  await page.getByText("Search results").click();
  await page.getByLabel("Search query").fill("data");
  await page.getByRole("button", { name: "Search index" }).click();
  await expect(page).toHaveURL(/jobs\/search-1/);
});
test("project text disclosure is explicit and secret entries are unavailable", async ({
  page,
}) => {
  const { zipSync, strToU8 } = await import("fflate");
  const f = await fixtures(page);
  await page.route("**/api/v1/uploads**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/uploads"))
      return route.fulfill({ json: { id: "project-upload" } });
    if (path.endsWith("/finalize"))
      return route.fulfill({
        json: {
          id: "project-artifact",
          name: "project.zip",
          sha256: "a".repeat(64),
          size_bytes: 200,
          media_type: "application/zip",
        },
      });
    if (route.request().method() === "GET")
      return route.fulfill({
        json: {
          state: "pending",
          part_size_bytes: 4194304,
          parts: [],
          artifact: null,
        },
      });
    return route.fulfill({ json: { number: 0 } });
  });
  await page.goto("/new");
  await page.getByLabel("What should the task do?").fill("Run main.py");
  await page.getByText("Add a project ZIP or code folder").click();
  await page.getByLabel("Project ZIP", { exact: true }).setInputFiles({
    name: "project.zip",
    mimeType: "application/zip",
    buffer: Buffer.from(
      zipSync({
        "main.py": strToU8("print(42)"),
        ".env": strToU8("SECRET=hide"),
      }),
    ),
  });
  await page.getByText("main.py", { exact: true }).click();
  await expect(
    page.getByLabel("Share this file’s text with DeepSeek"),
  ).not.toBeChecked();
  await expect(page.getByText("SECRET=hide")).toHaveCount(0);
  await page.getByLabel("Share this file’s text with DeepSeek").check();
  await page.getByLabel("I allow my description").check();
  await page.getByRole("button", { name: "Prepare plan" }).click();
  await expect(
    page.getByRole("heading", { name: "Review the plan" }),
  ).toBeVisible();
  expect(
    f.requests.find((r) => r.path === "/job-previews")?.body,
  ).toMatchObject({
    project_artifact_id: "project-artifact",
    selected_files: ["main.py"],
    share_with_llm: true,
    inputs: [
      {
        artifact_id: "project-artifact",
        mount_path: "/workspace",
        archive: true,
      },
    ],
  });
});

test("small screen keyboard and reduced motion retain usable account forms", async ({
  page,
}) => {
  await fixtures(page, { anonymous: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/login");
  await page.getByLabel("Email").focus();
  await page.keyboard.type("anna@example.com");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password", { exact: true })).toBeFocused();
  await page.keyboard.type("a-strong-password");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Your workspace" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../.git/campus-runner/web-product/portal-mobile.png",
    fullPage: true,
  });
});
test("stale revision requires refreshed review and a new submission key", async ({
  page,
}) => {
  const f = await fixtures(page);
  let starts = 0;
  const keys: string[] = [];
  const revisions: number[] = [];
  const { preview, job } = await import("./fixtures");
  await page.route("**/api/v1/jobs", (r) => {
    if (r.request().method() === "POST") {
      starts++;
      keys.push(r.request().headers()["idempotency-key"]);
      revisions.push(r.request().postDataJSON().plan_revision);
      return starts === 1
        ? r.fulfill({
            status: 409,
            json: { code: "STALE_PREVIEW", message: "stale" },
          })
        : r.fulfill({ json: job });
    }
    return r.fulfill({ json: [job] });
  });
  await page.goto("/new");
  await page.getByLabel("What should the task do?").fill("Task");
  await page.getByLabel("I allow my description").check();
  await page.getByRole("button", { name: "Prepare plan" }).click();
  await page.route("**/api/v1/job-previews/preview-1", (r) =>
    r.fulfill({ json: { ...preview, plan_revision: 2 } }),
  );
  await page.getByRole("button", { name: "Start task", exact: true }).click();
  await expect(
    page.getByText("Plan version: 2", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("The plan changed");
  await page.getByRole("button", { name: "Start task", exact: true }).click();
  await expect(page).toHaveURL(/jobs\/job-1/);
  expect(starts).toBe(2);
  expect(keys[0]).not.toBe(keys[1]);
  expect(revisions).toEqual([1, 2]);
});
