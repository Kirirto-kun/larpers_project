// Authorized live own-code browser journey. Read only the synthetic account file.
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import crypto from "node:crypto";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const require = createRequire(
  pathToFileURL(path.join(root, "apps/portal/package.json")),
);
const { chromium, expect } = require("@playwright/test");
const { zipSync, strToU8 } = require("fflate");
const origin = "https://10.4.146.76:8443";
const evidence = path.join(
  root,
  ".git/campus-runner/web-product/live/ai-code-job",
  new Date().toISOString().replaceAll(":", "-"),
);
await fs.mkdir(evidence, { recursive: true });
const sha = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const n = 50000;
const expectedNode = process.argv.includes("--expect-node")
  ? process.argv[process.argv.indexOf("--expect-node") + 1]
  : "ec6c1cbe-125d-43e4-bda6-a82b0fcdc34a";
const code = `import json\nn = ${n}\nresult = {"n": n, "sum_squares": sum(i*i for i in range(n))}\nwith open("result.json", "w", encoding="utf-8") as f:\n    json.dump(result, f, sort_keys=True)\nprint("CAMPUS_OWN_CODE_SUM_SQUARES_DONE", flush=True)\n`;
const archive = Buffer.from(
  zipSync({
    "main.py": strToU8(code),
    "notes.txt": strToU8(
      "Synthetic test notes. This file must remain unselected for external AI sharing.",
    ),
  }),
);
const prompt = process.argv.includes("--natural")
  ? "Запусти main.py из моего ZIP-проекта без изменений. Он считает сумму квадратов целых чисел от 0 до 49999 и сохраняет result.json. Нужен один обычный CPU, 128 МиБ памяти, 34 МиБ диска, максимум 60 секунд, без интернета и без GPU. Используется только стандартная библиотека Python. Распакуй проект, выполни мой код и верни result.json как обязательный скачиваемый результат."
  : "Запусти мой загруженный проект Python. В корне проекта main.py, он вычисляет сумму квадратов целых чисел от 0 до 49999 и сохраняет result.json. Используй утверждённый адаптер campus.python версии 1 и рецепт python-script, обычный CPU, 1 CPU (1000 millicpu), 128 MiB RAM (134217728 bytes), 34 MiB disk (35651584 bytes), без GPU и без сети. Один шаг, лимит шага 60 секунд, рабочая папка /workspace. Проект ZIP нужно распаковать в /workspace. Выполни python /workspace/main.py. Обязательный выход result.json (не более 1 MiB), structural validation, restart при прерывании. Сохрани именно мой код, не заменяй его другим вычислением. Все нужные файлы уже загружены; у main.py нет зависимостей вне стандартной библиотеки. Верни простой исполняемый план с одним этапом и одним заданием.";
const report = {
  origin,
  started_at: new Date().toISOString(),
  expected_node_id: expectedNode,
  prompt_style: process.argv.includes("--natural")
    ? "natural"
    : "initial_explicit_resource_constraints",
  checks: [],
  preview_states: [],
  job_states: [],
  upload: { name: "own-code.zip", bytes: archive.length, sha256: sha(archive) },
  tls_note:
    "Isolated browser ignoreHTTPSErrors context; root independently verified local CA chain. No system trust changes.",
};
let stage = "read synthetic account",
  browser,
  page,
  context,
  latestPreview,
  latestJob,
  previewRequest,
  jobCreateRequest;
const pending = new Set();
const answeredQuestions = new Set();
try {
  const account = JSON.parse(
    await fs.readFile(
      "C:/Users/jafar/AppData/Local/Campus/server-96bfe6d/web-test-account.json",
      "utf8",
    ),
  );
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  });
  page = await context.newPage();
  page.on("pageerror", () => {
    report.browser_runtime_error = true;
  });
  page.on("response", (response) => {
    const job = (async () => {
      const u = new URL(response.url()),
        request = response.request();
      if (u.origin !== origin || !response.ok()) return;
      if (/^\/api\/v1\/uploads\/[^/]+\/finalize$/.test(u.pathname)) {
        const value = await response.json();
        report.uploaded_project = {
          id: value.id,
          name: value.name,
          size_bytes: value.size_bytes,
          sha256: value.sha256,
        };
      }
      if (/^\/api\/v1\/job-previews(?:\/[^/]+)?$/.test(u.pathname)) {
        const value = await response.json();
        if (!value.id) return;
        if (
          !latestPreview ||
          value.plan_revision >= latestPreview.plan_revision
        )
          latestPreview = value;
        if (report.preview_states.at(-1) !== value.state) {
          report.preview_states.push(value.state);
          console.log(
            JSON.stringify({
              stage: "preview",
              id: value.id,
              state: value.state,
            }),
          );
        }
        if (request.method() === "POST")
          previewRequest = request.postDataJSON();
      }
      if (/^\/api\/v1\/jobs(?:\/[^/]+)?$/.test(u.pathname)) {
        const value = await response.json();
        if (Array.isArray(value) || !value.id) return;
        latestJob = value;
        if (report.job_states.at(-1) !== value.state) {
          report.job_states.push(value.state);
          console.log(
            JSON.stringify({ stage: "job", id: value.id, state: value.state }),
          );
        }
        if (request.method() === "POST")
          jobCreateRequest = request.postDataJSON();
      }
    })().catch(() => {});
    pending.add(job);
    job.finally(() => pending.delete(job));
  });
  stage = "password login";
  await page.goto(origin, { waitUntil: "networkidle" });
  await page.getByLabel("Электронная почта").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Ваше рабочее пространство" }),
  ).toBeVisible({ timeout: 20000 });
  report.checks.push("real password login through deployed UI");
  if (process.argv[2] === "--inspect-preview") {
    const response = await context.request.get(
      origin + "/api/v1/job-previews/" + encodeURIComponent(process.argv[3]),
    );
    const value = await response.json();
    const safe = {
      id: value.id,
      state: value.state,
      error_code: value.error_code,
      error_message: value.error_message,
    };
    await fs.writeFile(
      path.join(evidence, "inspection.json"),
      JSON.stringify(safe, null, 2),
    );
    await browser.close();
    console.log(JSON.stringify(safe));
    process.exit(0);
  }
  stage = "upload own project and consent";
  await page.getByRole("link", { name: "Новая задача", exact: true }).click();
  await page.getByLabel("Что нужно сделать?").fill(prompt);
  await page.getByLabel("Шаблон задачи").selectOption("python-script");
  await page
    .getByText("Добавить проект ZIP или папку с кодом", { exact: true })
    .click();
  await page
    .getByLabel("Проект ZIP", { exact: true })
    .setInputFiles({
      name: "own-code.zip",
      mimeType: "application/zip",
      buffer: archive,
    });
  const mainDetails = page
    .locator("details")
    .filter({ has: page.locator("summary").filter({ hasText: /^main\.py$/ }) });
  await mainDetails.locator("summary").click();
  await mainDetails.getByLabel("Отправить текст этого файла в ИИ").check();
  const notesDetails = page
    .locator("details")
    .filter({
      has: page.locator("summary").filter({ hasText: /^notes\.txt$/ }),
    });
  await expect(
    notesDetails.getByLabel("Отправить текст этого файла в ИИ"),
  ).not.toBeChecked();
  await page
    .getByLabel("Разрешаю отправить описание и выбранный текст в DeepSeek")
    .check();
  await page.screenshot({
    path: path.join(evidence, "01-upload-consent.png"),
    fullPage: true,
  });
  const createdPreview = page.waitForResponse(
    (r) =>
      r.url() === origin + "/api/v1/job-previews" &&
      r.request().method() === "POST",
    { timeout: 120000 },
  );
  await page
    .getByRole("button", { name: "Подготовить план", exact: true })
    .click();
  const previewResponse = await createdPreview;
  if (previewResponse.status() !== 202)
    throw new Error("preview creation HTTP " + previewResponse.status());
  const initial = await previewResponse.json();
  report.preview_id = initial.id;
  report.checks.push(
    "actual checksummed project upload and planner preview request",
  );
  stage = "wait for actual DeepSeek plan";
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    if (latestPreview?.id === initial.id && latestPreview.state === "ready")
      break;
    if (latestPreview?.state === "failed") {
      report.planner_error_code = latestPreview.error_code;
      report.planner_error_message = latestPreview.error_message;
      throw new Error("planner failed " + latestPreview.error_code);
    }
    if (
      latestPreview?.state === "needs_input" &&
      latestPreview.question &&
      !answeredQuestions.has(
        latestPreview.plan_revision + ":" + latestPreview.question.question_id,
      )
    ) {
      const q = latestPreview.question;
      answeredQuestions.add(latestPreview.plan_revision + ":" + q.question_id);
      report.clarifications ??= [];
      report.clarifications.push({
        question_id: q.question_id,
        prompt: q.prompt,
        revision: latestPreview.plan_revision,
      });
      await page
        .getByLabel(q.prompt, { exact: true })
        .fill(
          "Используй загруженный main.py без изменений, входных данных и зависимостей не нужно. Один процесс CPU, 1 CPU, 128 MiB памяти, 34 MiB диска, 60 секунд, /workspace/main.py, result.json — обязательный результат. Диапазон 0..49999.",
        );
      await page
        .getByRole("button", { name: "Отправить ответ", exact: true })
        .click();
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  if (latestPreview?.state !== "ready" || !latestPreview.plan)
    throw new Error("planner ready deadline exceeded");
  stage = "review actual DeepSeek plan";
  report.checks.push(
    "actual DeepSeek returned a ready plan without prepared-plan fallback",
  );
  if (
    !previewRequest ||
    previewRequest.prepared_plan ||
    !previewRequest.share_with_llm ||
    previewRequest.selected_files?.join(",") !== "main.py" ||
    !previewRequest.project_artifact_id ||
    previewRequest.recipe_id !== "python-script"
  )
    throw new Error("AI consent/request contract mismatch");
  if (
    !previewRequest.inputs?.some(
      (b) =>
        b.artifact_id === previewRequest.project_artifact_id &&
        b.mount_path === "/workspace" &&
        b.archive,
    )
  )
    throw new Error(
      "project archive compute binding absent from actual preview request",
    );
  if (
    report.uploaded_project?.sha256 !== sha(archive) ||
    report.uploaded_project?.size_bytes !== archive.length
  )
    throw new Error("uploaded project metadata checksum/size mismatch");
  report.preview_request = {
    ...previewRequest,
    prompt: "[synthetic sum-squares request recorded by this QA script]",
  };
  report.reviewed_plan = latestPreview;
  await fs.writeFile(
    path.join(evidence, "reviewed-plan.json"),
    JSON.stringify(latestPreview, null, 2),
  );
  await expect(
    page.getByRole("heading", { name: "Проверьте план" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Запустить задачу", exact: true }),
  ).toBeEnabled();
  const plan = latestPreview.plan;
  if (
    !plan.stages.length ||
    plan.stages.some(
      (s) =>
        s.network_policy !== "none" ||
        !s.outputs.some((o) => o.path === "result.json" && o.required),
    )
  )
    throw new Error("review rejected unsafe or missing output plan");
  if (
    plan.stages.some(
      (s) =>
        s.adapter.id !== "campus.python" ||
        s.argv.includes("-c") ||
        !s.argv.some((a) => a === "main.py" || a === "/workspace/main.py") ||
        !s.bindings.some(
          (b) =>
            b.artifact_id === previewRequest.project_artifact_id &&
            b.archive &&
            b.mount_path === "/workspace",
        ),
    )
  )
    throw new Error(
      "review rejected a plan that does not execute the uploaded project code",
    );
  await page.screenshot({
    path: path.join(evidence, "02-reviewed-deepseek-plan.png"),
    fullPage: true,
  });
  report.checks.push(
    "actual DeepSeek ready plan, explicit main.py-only disclosure, no prepared-plan fallback, visible pre-run review",
  );
  stage = "run reviewed plan";
  await page
    .getByRole("button", { name: "Запустить задачу", exact: true })
    .click();
  await page.waitForURL(/\/jobs\/[^/]+$/, { timeout: 20000 });
  report.job_id = new URL(page.url()).pathname.split("/").at(-1);
  const executionDeadline = Date.now() + 240000;
  while (Date.now() < executionDeadline) {
    if (
      latestJob?.id === report.job_id &&
      ["succeeded", "partial", "failed", "cancelled"].includes(latestJob.state)
    )
      break;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  if (latestJob?.id !== report.job_id || latestJob.state !== "succeeded") {
    report.final_job_state = latestJob?.state;
    report.task_errors = latestJob?.tasks
      ?.map((t) => t.error_code)
      .filter(Boolean);
    throw new Error("physical job did not succeed");
  }
  report.job_create = jobCreateRequest;
  report.final_job_state = latestJob.state;
  report.nodes = [
    ...new Set(latestJob.tasks.map((t) => t.node_id).filter(Boolean)),
  ];
  report.task_verification = latestJob.tasks.map((t) => ({
    id: t.id,
    state: t.state,
    node_id: t.node_id,
    generation: t.generation,
    verification_level: t.verification_level,
  }));
  if (
    !report.nodes.includes(expectedNode) &&
    !report.nodes.includes("ec6c1cbe-125d-43e4-bda6-a82b0fcdc34a")
  )
    throw new Error("a known managed physical node did not execute own code");
  await expect(page.getByLabel("Журнал выполнения")).toContainText(
    "CAMPUS_OWN_CODE_SUM_SQUARES_DONE",
    { timeout: 15000 },
  );
  report.checks.push(
    "reviewed revision ran on expected managed physical node with actual task completion and native stdout",
  );
  stage = "download and independently verify result";
  const artifact = latestJob.artifacts.find((a) =>
    a.name.endsWith("result.json"),
  );
  if (!artifact) throw new Error("result artifact absent");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("link", { name: artifact.name, exact: true }).click();
  const download = await downloaded;
  await download.saveAs(path.join(evidence, "result.json"));
  const bytes = await fs.readFile(path.join(evidence, "result.json"));
  const parsed = JSON.parse(bytes.toString("utf8"));
  const expected = ((n - 1) * n * (2 * n - 1)) / 6;
  if (parsed.n !== n || parsed.sum_squares !== expected)
    throw new Error("independent numerical validation failed");
  if (sha(bytes) !== artifact.sha256 || bytes.length !== artifact.size_bytes)
    throw new Error("artifact metadata SHA256/size mismatch");
  report.artifact = {
    id: artifact.id,
    name: artifact.name,
    size_bytes: bytes.length,
    sha256: sha(bytes),
    metadata_sha256: artifact.sha256,
  };
  report.numerical = {
    n,
    expected_sum_squares: expected,
    actual_sum_squares: parsed.sum_squares,
    formula: "(n-1)*n*(2*n-1)/6",
    passed: true,
  };
  report.checks.push(
    "authenticated UI artifact download, SHA256/size equals API metadata, independent closed-form numerical check",
  );
  await page.screenshot({
    path: path.join(evidence, "03-completed-job-result.png"),
    fullPage: true,
  });
  if (report.browser_runtime_error)
    throw new Error("browser runtime error occurred");
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failed_stage = stage;
  // Never dump Playwright errors: their call logs can include filled credentials.
  report.error =
    error.name === "Error"
      ? String(error.message).split("\n")[0].slice(0, 180)
      : error.name;
  await page
    ?.screenshot({ path: path.join(evidence, "failure.png"), fullPage: true })
    .catch(() => {});
  process.exitCode = 1;
} finally {
  await Promise.allSettled([...pending]);
  report.finished_at = new Date().toISOString();
  await fs.writeFile(
    path.join(evidence, "report.json"),
    JSON.stringify(report, null, 2),
  );
  await browser?.close();
  console.log(
    JSON.stringify({
      passed: report.passed,
      failed_stage: report.failed_stage,
      error: report.error,
      preview_id: report.preview_id,
      job_id: report.job_id,
      nodes: report.nodes,
      artifact: report.artifact,
      numerical: report.numerical,
      checks: report.checks,
    }),
  );
}
