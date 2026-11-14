import type { Page } from "@playwright/test";
export const identity = {
  id: "user-1",
  name: "Анна",
  email: "anna@example.com",
  role: "member",
  pool_id: "pool-1",
  csrf_token: "csrf-test",
};
export const node = {
  id: "node-1",
  owner_id: "user-1",
  name: "Мой ноутбук",
  state: "offline",
  last_seen_at: null,
  capabilities: null,
  accepted_units: 0,
  active_attempts: 0,
  settings: { revision: 2, mode: "medium", desired_state: "running" },
  applied_revision: 1,
  supports_web_controls: true,
  agent_contact_at: null,
  actual_state: null,
  settings_error: null,
};
export const preview = {
  id: "preview-1",
  state: "ready",
  plan_revision: 1,
  plan_digest: "a".repeat(64),
  question: null,
  error_code: null,
  error_message: null,
  plan: {
    plan_revision: 1,
    execution_kind: "single",
    assumptions: ["Тестовая задача CPU"],
    evidence: [],
    questions: [],
    job_deadline_at: "2030-01-01T00:00:00Z",
    stages: [
      {
        stage_id: "cpu",
        adapter: { id: "cpu", version: "1" },
        variants: [
          {
            accelerator: "cpu",
            resource_requirements: {
              cpu_millis: 1000,
              memory_bytes: 268435456,
              disk_bytes: 1048576,
              gpu_slots: 0,
            },
          },
        ],
        outputs: [{ path: "result.txt", required: true, max_bytes: 1000 }],
        task_deadline_ms: 60000,
        resume_policy: "restart",
        argv: ["python", "main.py"],
      },
    ],
  },
};
export const job = {
  id: "job-1",
  owner_id: "user-1",
  purpose: "work",
  state: "succeeded",
  created_at: "2026-11-15T00:00:00Z",
  accepted_units: 1,
  failed_units: 0,
  total_units: 1,
  tasks: [
    {
      id: "task-1",
      stage_id: "cpu",
      state: "succeeded",
      generation: 1,
      units: 1,
      node_id: "node-1",
      verification_level: "server",
      error_code: null,
    },
  ],
  artifacts: [
    {
      id: "artifact-1",
      name: "result.txt",
      size_bytes: 3,
      sha256: "b".repeat(64),
      media_type: "text/plain",
    },
  ],
  search_result: null,
};
export async function fixtures(
  page: Page,
  options: {
    anonymous?: boolean;
    nodes?: boolean;
    role?: string;
    retry?: boolean;
    question?: boolean;
    partial?: boolean;
    stale?: boolean;
    empty?: boolean;
  } = {},
) {
  let logged = !options.anonymous,
    starts = 0,
    revision = 1,
    answered = !options.question;
  const requests: {
    path: string;
    body: any;
    headers: Record<string, string>;
  }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname.replace("/api/v1", "");
    let body;
    try {
      body = req.postDataJSON();
    } catch {}
    requests.push({ path, body, headers: req.headers() });
    const json = (v: unknown, status = 200) =>
      route.fulfill({ status, json: v });
    if (path === "/auth/me")
      return logged
        ? json({ ...identity, role: options.role || "member" })
        : json({ code: "UNAUTHORIZED", message: "Sign in" }, 401);
    if (["/auth/register", "/auth/login", "/auth/account"].includes(path)) {
      logged = true;
      return json(identity);
    }
    if (path === "/auth/logout") {
      logged = false;
      return route.fulfill({ status: 204 });
    }
    if (path === "/nodes") return json(options.nodes ? [node] : []);
    if (path.includes("/settings"))
      return json({
        ...node,
        settings: { ...node.settings, ...body, revision: 3 },
      });
    if (path === "/client-release")
      return json({
        version: "r5",
        downloads: [
          { os: "windows", label: "Windows", url: "/downloads/start.cmd" },
          { os: "darwin", label: "macOS", url: "/downloads/start.command" },
        ],
        requirements: { docker_manual: true },
      });
    if (path === "/invitations")
      return json({
        id: "invite-1",
        token: "private-device-invitation-1234567890",
        kind: "device",
        expires_at: "2030-01-01T00:00:00Z",
      });
    if (path === "/me/stats")
      return json({
        devices_total: options.nodes ? 1 : 0,
        devices_online: 0,
        accepted_units: 0,
        contributed_seconds: null,
        jobs_by_state: { succeeded: 1 },
        recent_results: [job],
        history: [],
      });
    if (path === "/recipes" || path === "/examples") return json([]);
    if (path.endsWith("/answers")) {
      answered = true;
      revision++;
      return json({ ...preview, plan_revision: revision });
    }
    if (
      path === "/job-previews" ||
      path.startsWith("/examples/") ||
      path.startsWith("/job-previews/")
    )
      return json({
        ...preview,
        plan_revision: revision,
        state: answered ? "ready" : "needs_input",
        question: answered
          ? null
          : { question_id: "q1", prompt: "Какой набор данных?" },
      });
    if (path.endsWith("/logs"))
      return json({
        entries: [
          {
            cursor: 1,
            attempt_id: "attempt-1",
            sequence: 1,
            occurred_at: "2026-11-15T00:00:00Z",
            text: "Готово",
            gap: false,
          },
        ],
        cursor: 1,
        truncated: true,
      });
    if (path === "/jobs" && req.method() === "POST") {
      starts++;
      if (options.retry && starts === 1)
        return json({ code: "TEMPORARY", message: "Соединение прервано" }, 503);
      return json(job, 201);
    }
    if (path === "/jobs") return json([job]);
    if (path.startsWith("/jobs/")) return json(job);
    if (path.startsWith("/artifacts/")) return route.continue();
    return json({ code: "NOT_FOUND", message: "No fixture for " + path }, 404);
  });
  return { requests };
}
