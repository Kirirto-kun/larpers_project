import { type Page, type Route } from "@playwright/test";
export const node = {
  id: "node-1",
  name: "Lab laptop",
  state: "ready",
  roles: ["compute"],
  cpu_logical: 8,
  memory_available_bytes: 8589934592,
  gpus: [],
  offered_budget: {
    cpu_percent: 50,
    memory_bytes: 4294967296,
    gpu_allowed: false,
  },
  preflight: [{ name: "Docker", ok: true, message: "Runtime ready" }],
  last_seen_at: "2026-11-14T06:00:00Z",
  attempts: [],
};
export const job = {
  id: "job-1",
  name: "Document corpus",
  state: "succeeded",
  accepted_units: 100,
  failed_units: 0,
  total_units: 100,
  stages: [{ stage_id: "embed", state: "succeeded" }],
  artifacts: [
    { id: "model-1", name: "model.pt", size_bytes: 4096, ready: true },
  ],
  attempts: [],
  validation_level: "Fixture validation only",
  metrics: { validation_accuracy: 0.91 },
};
export const preview = {
  id: "preview-1",
  state: "ready",
  plan_revision: 1,
  questions: [],
  summary: "Create a searchable document index",
  accelerator: "cpu",
  resume_policy: "restart",
  outputs: ["index"],
  assumptions: ["Trusted pool"],
  evidence: ["Selected recipe"],
  data_location: "Local coordinator",
  duration_ms: null,
};
export const benchmark = {
  id: "bench-1",
  experiment_id: "experiment-1",
  state: "succeeded",
  workload: "embeddings",
  unit: "fragments",
  input_digest: "sha256:fixture",
  node_ids: ["node-1"],
  concurrency: 2,
  repetitions: 3,
  cold: false,
  git_sha: "test-fixture-not-real-measurement",
  standalone: [
    {
      node_id: "node-1",
      rates: [10, 11, 12],
      provenance: "Synthetic test fixture",
    },
  ],
  pool_rates: [12, 13, 14],
  single_service_rates: [9, 10, 11],
  timings_ms: {
    transfer: 100,
    queue: 20,
    compute: 1000,
    verify: 200,
    aggregate: 50,
  },
  utilization: [
    {
      node_id: "node-1",
      cpu_percent: 0,
      gpu_percent: null,
      window_ms: 10000,
      gaps: 1,
    },
  ],
  bottleneck: "Fixture only",
  flags: ["Synthetic data, not a measured result"],
};
export async function fixtures(
  page: Page,
  options: {
    role?: string;
    partial?: boolean;
    offline?: boolean;
    question?: boolean;
    stale?: boolean;
    empty?: boolean;
  } = {},
) {
  let revision = 1;
  let answered = !options.question;
  let started = 0;
  const keys: string[] = [];
  const requests: { path: string; body: unknown }[] = [];
  await page.context().route("**/api/v1/**", async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace("/api/v1", "");
    let body: unknown;
    try {
      body = req.postDataJSON();
    } catch {}
    requests.push({ path, body });
    const json = (data: unknown, status = 200) =>
      route.fulfill({ status, json: data });
    if (path.endsWith("/events"))
      return route.fulfill({
        contentType: "text/event-stream",
        body: "event: reset\ndata: {}\n\n",
      });
    if (path === "/auth/me")
      return json({
        name: "Test member",
        role: options.role || "owner",
        csrf_token: "test-csrf",
      });
    if (path === "/recipes")
      return json([
        { id: "embeddings", name: "Document search" },
        { id: "training", name: "Image classifier" },
      ]);
    if (path === "/nodes")
      return json(
        options.empty
          ? []
          : [{ ...node, state: options.offline ? "offline" : "ready" }],
      );
    if (path === "/pool/summary")
      return json({
        accepted_units: 100,
        queued_tasks: 2,
        running_tasks: 0,
        recovering_tasks: 1,
        events: [],
      });
    if (path === "/credits")
      return json({
        unit_version: "v1",
        accepted_units: 100,
        credits: 10,
        currency: "CNY",
        costs: [
          {
            name: "Energy",
            amount: null,
            estimated: true,
            assumption: "No power meter readings",
          },
        ],
      });
    if (path === "/benchmarks") return json(options.empty ? [] : [benchmark]);
    if (path.includes("/standalone-results")) return json({});
    if (path === "/invitations")
      return json({ command: "campus join --invitation test-only" });
    if (path.includes("/control")) return json({});
    if (path === "/job-previews")
      return json(
        {
          ...preview,
          state: answered ? "ready" : "needs_input",
          questions: answered
            ? []
            : [{ question_id: "q-1", message: "Which dataset split?" }],
        },
        202,
      );
    if (path.endsWith("/answers")) {
      answered = true;
      revision++;
      return json({ ...preview, plan_revision: revision });
    }
    if (path.startsWith("/job-previews/"))
      return json({
        ...preview,
        plan_revision: revision,
        state: answered ? "ready" : "needs_input",
        questions: answered
          ? []
          : [{ question_id: "q-1", message: "Which dataset split?" }],
      });
    if (path === "/jobs" && req.method() === "POST") {
      started++;
      keys.push(req.headers()["idempotency-key"]);
      if (options.stale && started === 1) {
        revision++;
        return json(
          {
            code: "STALE_PREVIEW",
            message: "Plan changed; review the current revision",
          },
          409,
        );
      }
      if (!options.stale && started === 1)
        return json(
          { code: "TEMPORARY", message: "Response lost. Retry safely." },
          503,
        );
      return json(job);
    }
    if (path === "/jobs") return json(options.empty ? [] : [job]);
    if (path.endsWith("/logs"))
      return json({ lines: ["Embedding complete"], truncated: true });
    if (path.startsWith("/jobs/"))
      return json({
        ...job,
        state: options.partial ? "partial" : job.state,
        failed_units: options.partial ? 3 : 0,
      });
    if (path.startsWith("/artifacts/"))
      return route.fulfill({
        body: "fixture-model",
        contentType: "application/octet-stream",
        headers: { "Content-Disposition": 'attachment; filename="model.pt"' },
      });
    return json({});
  });
  return {
    keys,
    requests,
    get started() {
      return started;
    },
  };
}
