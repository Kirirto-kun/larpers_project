import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import { createWriteStream } from "node:fs";
import { spawn } from "node:child_process";
import crypto from "node:crypto";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const require = createRequire(
  pathToFileURL(path.join(root, "apps/portal/package.json")),
);
const { chromium, expect } = require("@playwright/test");
const origin = "https://10.4.146.76:8443";
const runtime = "C:/Users/jafar/AppData/Local/Campus/server-96bfe6d";
const state = path.join(runtime, "web-member-managed");
const ca = path.join(runtime, "campus-local-ca.pem");
const cache = path.join(root, ".git/campus-runner/web-product/member-cache");
const evidence = path.join(
  root,
  ".git/campus-runner/web-product/live/member-contribution",
);
const binary = path.join(
  root,
  ".git/campus-runner/web-product/binaries-r7/campus-windows-amd64.exe",
);
const name = "Проверка пользователя CPU";
await Promise.all([
  fs.mkdir(state, { recursive: true }),
  fs.mkdir(cache, { recursive: true }),
  fs.mkdir(evidence, { recursive: true }),
]);
const report = {
  started_at: new Date().toISOString(),
  origin,
  checks: [],
  settings_checks: [],
  gpu_opt_in: false,
};
let browser,
  context,
  page,
  child,
  nativeLog,
  stage = "login",
  ownedNode,
  token = "",
  latestJob,
  exitCode,
  exitSignal;
const pending = new Set();
const log = (value) => console.log(JSON.stringify(value));
async function nodes() {
  const r = await context.request.get(origin + "/api/v1/nodes");
  if (r.status() !== 200) throw new Error("member nodes unavailable");
  return r.json();
}
async function waitNode(predicate, timeout = 120000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    ownedNode = (await nodes()).find((n) => n.name === name);
    if (ownedNode && predicate(ownedNode)) return ownedNode;
    if (exitCode !== undefined)
      throw new Error("native installer exited code " + exitCode);
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("owned node acknowledgement timeout");
}
async function snapshot(label) {
  report.settings_checks.push({
    label,
    id: ownedNode.id,
    state: ownedNode.state,
    settings: ownedNode.settings,
    applied_revision: ownedNode.applied_revision,
    actual_state: ownedNode.actual_state,
    applied_mode: ownedNode.applied_mode,
    active_attempts: ownedNode.active_attempts,
    offered_budget: ownedNode.capabilities?.offered_budget,
  });
  log({
    stage: label,
    node: ownedNode.id,
    state: ownedNode.state,
    revision: ownedNode.applied_revision,
  });
}
const acknowledged = (n) =>
  n.applied_revision === n.settings.revision && !n.settings_error;
let priorReport;
try {
  priorReport = JSON.parse(
    await fs.readFile(path.join(evidence, "report.json"), "utf8"),
  );
  if (!priorReport.node_id)
    priorReport = JSON.parse(
      await fs.readFile(
        path.join(evidence, "first-attempt-report.json"),
        "utf8",
      ),
    );
  await fs.copyFile(
    path.join(evidence, "report.json"),
    path.join(evidence, "first-attempt-report.json"),
    1,
  );
} catch {}
async function cleanup() {
  if (!child) return;
  if (context && ownedNode) {
    try {
      await page.goto(origin + "/contribute");
      const card = page
        .locator("article.device-card")
        .filter({ has: page.getByRole("heading", { name, exact: true }) });
      await card.getByRole("button", { name: "Пауза", exact: true }).click();
      await waitNode(
        (n) => acknowledged(n) && n.actual_state === "paused",
        30000,
      );
      report.cleanup_web_pause = true;
    } catch {
      report.cleanup_web_pause = false;
    }
  }
  const stop = spawn(
    binary,
    ["--ca-file", ca, "--state-dir", state, "leave", "--stop"],
    { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  stop.stdout.resume();
  stop.stderr.resume();
  const result = await new Promise((resolve) => {
    stop.on("exit", (code) => resolve(code));
    stop.on("error", () => resolve(-1));
  });
  report.local_leave_exit = result;
  const end = Date.now() + 20000;
  while (exitCode === undefined && Date.now() < end)
    await new Promise((r) => setTimeout(r, 250));
  if (exitCode === undefined) {
    report.cleanup_forced_own_pid = child.pid;
    child.kill();
    await new Promise((r) => setTimeout(r, 1000));
  }
  report.native_exit_code = exitCode ?? null;
  report.native_exit_signal = exitSignal ?? null;
  report.native_exited = exitCode !== undefined;
  nativeLog?.end();
}
try {
  const account = JSON.parse(
    await fs.readFile(path.join(runtime, "web-test-account.json"), "utf8"),
  );
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  });
  page = await context.newPage();
  page.on("pageerror", () => (report.browser_runtime_error = true));
  page.on("response", (r) => {
    const operation = (async () => {
      const url = new URL(r.url());
      if (
        url.origin !== origin ||
        !r.ok() ||
        !/^\/api\/v1\/jobs(?:\/[^/]+)?$/.test(url.pathname)
      )
        return;
      const j = await r.json();
      if (Array.isArray(j) || !j.id) return;
      latestJob = j;
      if (report.last_job_state !== j.state) {
        report.last_job_state = j.state;
        log({ stage: "job", id: j.id, state: j.state });
      }
    })().catch(() => {});
    pending.add(operation);
    operation.finally(() => pending.delete(operation));
  });
  await page.goto(origin);
  await page.getByLabel("Электронная почта").fill(account.email);
  await page.getByLabel("Пароль", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Ваше рабочее пространство" }),
  ).toBeVisible();
  const me = await (
    await context.request.get(origin + "/api/v1/auth/me")
  ).json();
  if (me.role !== "member")
    throw new Error("test account is not ordinary member");
  report.member_id = me.id;
  stage = "create private invitation in actual member UI";
  await page.getByRole("link", { name: "Устройства", exact: true }).click();
  await page.getByLabel("Название компьютера").fill(name);
  const existing = (await nodes()).find(
    (n) => n.name === name && n.owner_id === me.id,
  );
  if (existing) {
    if (priorReport?.node_id !== existing.id || !priorReport.invitation)
      throw new Error(
        "existing enrollment lacks prior actual invitation evidence",
      );
    report.invitation = priorReport.invitation;
    report.reused_member_enrollment = true;
    report.checks.push(
      "actual member UI invitation and native redemption retained from first-attempt-report.json; reconnect uses same private test identity",
    );
  } else {
    const inviteResponse = page.waitForResponse(
      (r) =>
        r.url() === origin + "/api/v1/invitations" &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Создать приглашение", exact: true })
      .click();
    const invite = await (await inviteResponse).json();
    token = await page.getByLabel("Приватное приглашение").inputValue();
    if (!token || token !== invite.token)
      throw new Error("invitation UI response mismatch");
    report.invitation = {
      id: invite.id,
      kind: invite.kind,
      expires_at: invite.expires_at,
    };
    report.checks.push(
      "ordinary member created a private device invitation through actual UI",
    );
  }
  // Clear component-held invitation before any screenshot; the token never goes to argv/logs.
  await page.getByRole("link", { name: "Обзор", exact: true }).click();
  await page.getByRole("link", { name: "Устройства", exact: true }).click();
  stage = "install and redeem CPU-only managed native client";
  nativeLog = createWriteStream(path.join(state, "native.log"), { flags: "a" });
  child = spawn(
    binary,
    [
      "--ca-file",
      ca,
      "--state-dir",
      state,
      "install",
      "--catalog-url",
      origin + "/downloads/runner-20261115-r7/catalog.json",
      "--catalog-sha256",
      "d4ae67e63482cb67303cabfabdabf233b1adc52af598c5608d07492d04390f48",
      "--gpu=false",
      "--managed",
      "--cache-dir",
      cache,
    ],
    { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] },
  );
  report.native_pid = child.pid;
  await fs.writeFile(
    path.join(evidence, "native-pid.json"),
    JSON.stringify({ pid: child.pid, state_dir: state, gpu: false }),
  );
  child.stdout.on("data", (bytes) =>
    nativeLog.write(
      token
        ? bytes.toString().replaceAll(token, "[redacted-invitation]")
        : bytes,
    ),
  );
  child.stderr.on("data", (bytes) =>
    nativeLog.write(
      token
        ? bytes.toString().replaceAll(token, "[redacted-invitation]")
        : bytes,
    ),
  );
  child.on("exit", (code, signal) => {
    exitCode = code ?? -1;
    exitSignal = signal;
  });
  child.on("error", () => {
    exitCode = -1;
  });
  child.stdin.end(token ? token + "\n" : "");
  log({ stage: "native installer launched", pid: child.pid, gpu: false });
  if (report.reused_member_enrollment) {
    await waitNode(
      (n) => n.owner_id === me.id && n.supports_web_controls && acknowledged(n),
      30000,
    );
    if (ownedNode.settings.desired_state === "paused") {
      const existingCard = page
        .locator("article.device-card")
        .filter({ has: page.getByRole("heading", { name, exact: true }) });
      await existingCard
        .getByRole("button", { name: "Подключить / продолжить", exact: true })
        .click();
    }
  }
  await waitNode(
    (n) =>
      n.owner_id === me.id &&
      n.state === "ready" &&
      n.supports_web_controls &&
      acknowledged(n),
    180000,
  );
  if (ownedNode.capabilities.offered_budget.gpu_slots !== 0)
    throw new Error("CPU-only installer unexpectedly offers GPU");
  report.node_id = ownedNode.id;
  await snapshot("owned node ready acknowledged");
  report.checks.push(
    report.reused_member_enrollment
      ? "same member-owned native enrollment reconnected and acknowledged CPU-only"
      : "member invitation redeemed by real R7 install via stdin, CPU-only node ready and acknowledged under member ownership",
  );
  const card = () =>
    page
      .locator("article.device-card")
      .filter({ has: page.getByRole("heading", { name, exact: true }) });
  await expect(card().getByText("Готово", { exact: true })).toBeVisible({
    timeout: 15000,
  });
  stage = "member UI pause resume and contribution modes";
  await card().getByRole("button", { name: "Пауза", exact: true }).click();
  await waitNode((n) => acknowledged(n) && n.actual_state === "paused");
  await snapshot("pause");
  await card()
    .getByRole("button", { name: "Подключить / продолжить", exact: true })
    .click();
  await waitNode(
    (n) =>
      acknowledged(n) && n.actual_state === "running" && n.state === "ready",
  );
  await snapshot("resume");
  await card().locator("input[type=radio]").nth(0).click();
  await waitNode(
    (n) => acknowledged(n) && n.applied_mode === "low" && n.state === "ready",
  );
  await expect(card().locator("input[type=radio]").nth(0)).toBeChecked({
    timeout: 15000,
  });
  await snapshot("low");
  await card().locator("input[type=radio]").nth(1).click();
  await waitNode(
    (n) =>
      acknowledged(n) && n.applied_mode === "medium" && n.state === "ready",
  );
  await expect(card().locator("input[type=radio]").nth(1)).toBeChecked({
    timeout: 15000,
  });
  await expect(
    card().getByText("Настройки применены клиентом", { exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await snapshot("medium");
  await page.screenshot({
    path: path.join(evidence, "01-member-owned-device-controls.png"),
    fullPage: true,
  });
  report.checks.push(
    "ordinary member UI pause/resume and low/medium settings applied by actual native acknowledgements",
  );
  stage = "member example review and physical CPU execution";
  await page.getByRole("link", { name: "Новая задача", exact: true }).click();
  await page
    .getByRole("button", { name: "Посмотреть план CPU", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Запустить задачу", exact: true }),
  ).toBeEnabled({ timeout: 30000 });
  await page.screenshot({
    path: path.join(evidence, "02-member-reviewed-example.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Запустить задачу", exact: true })
    .click();
  await page.waitForURL(/\/jobs\/[^/]+$/);
  report.job_id = new URL(page.url()).pathname.split("/").at(-1);
  const end = Date.now() + 120000;
  while (Date.now() < end) {
    if (
      latestJob?.id === report.job_id &&
      ["succeeded", "failed", "partial", "cancelled"].includes(latestJob.state)
    )
      break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  if (latestJob?.state !== "succeeded") {
    report.task_errors = latestJob?.tasks?.map((t) => t.error_code);
    throw new Error("member CPU example did not succeed");
  }
  report.execution_nodes = [...new Set(latestJob.tasks.map((t) => t.node_id))];
  if (!report.execution_nodes.includes(ownedNode.id))
    throw new Error("example did not prove contribution on member-owned node");
  const artifact = latestJob.artifacts.find((a) =>
    a.name.endsWith("model.json"),
  );
  if (!artifact) throw new Error("CPU model output absent");
  const wait = page.waitForEvent("download");
  await page.getByRole("link", { name: artifact.name, exact: true }).click();
  const download = await wait;
  const output = path.join(evidence, "model.json");
  await download.saveAs(output);
  const bytes = await fs.readFile(output);
  const hash = crypto.createHash("sha256").update(bytes).digest("hex");
  if (hash !== artifact.sha256 || bytes.length !== artifact.size_bytes)
    throw new Error("member artifact checksum/size mismatch");
  report.artifact = {
    id: artifact.id,
    name: artifact.name,
    size_bytes: bytes.length,
    sha256: hash,
  };
  const verifier = spawn(
    "python",
    [
      "-c",
      "import json,sys;from bootstrap.control_plane_compat.reference import verify;print(json.dumps(verify(json.load(open(sys.argv[1])))))",
      output,
    ],
    { cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  let verified = "";
  verifier.stdout.on("data", (b) => (verified += b));
  verifier.stderr.resume();
  const code = await new Promise((resolve) => verifier.on("exit", resolve));
  if (code !== 0)
    throw new Error("independent exact CPU reference rejected result");
  report.reference = JSON.parse(verified);
  await page.screenshot({
    path: path.join(evidence, "03-member-completed-CPU-result.png"),
    fullPage: true,
  });
  report.checks.push(
    "member-reviewed CPU example executed on member-owned physical node; UI download checksum and independent exact rational reference passed",
  );
  stage = "verify member contribution statistics";
  report.stats = await (
    await context.request.get(origin + "/api/v1/me/stats")
  ).json();
  if (
    report.stats.devices_total < 1 ||
    report.stats.accepted_units < 1 ||
    report.stats.jobs_by_state.succeeded < 1
  )
    throw new Error(
      "member statistics do not reflect own contribution and result",
    );
  await page.getByRole("link", { name: "Обзор", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Ваше рабочее пространство" }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(evidence, "04-member-own-contribution-stats.png"),
    fullPage: true,
  });
  report.checks.push(
    "real member statistics show owned device, accepted contribution and completed own job",
  );
  if (report.browser_runtime_error) throw new Error("browser runtime error");
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failed_stage = stage;
  report.error =
    error.name === "Error"
      ? String(error.message).split("\n")[0].slice(0, 160)
      : error.name;
  process.exitCode = 1;
  await page
    ?.screenshot({ path: path.join(evidence, "failure.png"), fullPage: true })
    .catch(() => {});
} finally {
  await cleanup().catch(() => {
    report.cleanup_error = true;
    process.exitCode = 1;
  });
  await Promise.allSettled([...pending]);
  await browser?.close();
  report.finished_at = new Date().toISOString();
  if (!report.native_exited || report.local_leave_exit !== 0) {
    report.passed = false;
    process.exitCode = 1;
  }
  await fs.writeFile(
    path.join(evidence, "report.json"),
    JSON.stringify(report, null, 2),
  );
  log({
    passed: report.passed,
    failed_stage: report.failed_stage,
    error: report.error,
    node_id: report.node_id,
    job_id: report.job_id,
    artifact: report.artifact,
    reference: report.reference,
    stats: report.stats
      ? {
          devices_total: report.stats.devices_total,
          accepted_units: report.stats.accepted_units,
          jobs_by_state: report.stats.jobs_by_state,
        }
      : undefined,
    cleanup: {
      web_pause: report.cleanup_web_pause,
      local_leave_exit: report.local_leave_exit,
      native_exited: report.native_exited,
      native_exit_code: report.native_exit_code,
    },
    checks: report.checks,
  });
}
