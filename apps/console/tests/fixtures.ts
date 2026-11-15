import type { Page } from "@playwright/test";
export const node = {
  id: "node-1",
  owner_id: "owner-1",
  name: "Laboratory",
  state: "ready",
  last_seen_at: "2026-11-15T00:00:00Z",
  capabilities: {
    os: "linux",
    arch: "amd64",
    cpu_logical: 8,
    memory_available_bytes: 8589934592,
    disk_available_bytes: 10000000000,
    power_state: "ac",
    runtime: { type: "docker", version: "27", healthy: true },
    gpus: [
      {
        id: "gpu-1",
        vendor: "NVIDIA",
        vram_bytes: 8589934592,
        cuda_verified: true,
      },
    ],
    offered_budget: {
      cpu_millis: 4000,
      memory_bytes: 4294967296,
      disk_bytes: 10000000,
      gpu_slots: 1,
      vram_bytes: 8589934592,
    },
  },
  accepted_units: 10,
  active_attempts: 1,
  settings: { revision: 1, mode: "medium", desired_state: "running" },
  applied_revision: 1,
  supports_web_controls: true,
  actual_state: "ready",
  settings_error: null,
  agent_contact_at: "2026-11-15T00:00:00Z",
};
export const benchmark = {
  id: "bench-1",
  status: "complete",
  config: {
    preview_id: "preview-1",
    plan_revision: 1,
    node_ids: ["node-1", "node-2"],
    concurrency: 2,
    repetitions: 3,
    warm: true,
    batch_policy: {},
  },
  config_digest: "a".repeat(64),
  manifest_digest: "b".repeat(64),
  standalone_results: [
    {
      node_id: "node-1",
      units: 10,
      duration_ms: 1000,
      verification_ms: 30,
      utilization: [
        {
          sequence: 1,
          phase: "compute",
          cpu_percent: 50,
          gpu_percent: null,
          memory_bytes: 1000,
        },
      ],
    },
  ],
  service_job_ids: ["job-1"],
  standalone_units_per_second: { "node-1": 10, "node-2": 12 },
  pooled_units_per_second: 26,
  efficiency_percent: 118.2,
  speedup: 2.16,
  service_runs: [],
};
export async function fixtures(
  page: Page,
  options: {
    role?: string;
    empty?: boolean;
    summaryUnavailable?: boolean;
    missingCapabilities?: boolean;
  } = {},
) {
  const requests: { path: string; body: any }[] = [];
  await page.route("**/api/v1/**", (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname.replace("/api/v1", "");
    let body;
    try {
      body = req.postDataJSON();
    } catch {}
    requests.push({ path, body });
    const json = (v: any) => route.fulfill({ json: v });
    if (path === "/auth/me")
      return json({
        id: "owner-1",
        name: "Administrator",
        email: "admin@example.com",
        role: options.role || "owner",
        pool_id: "pool-1",
        csrf_token: "csrf",
      });
    if (path === "/pool/summary" && options.summaryUnavailable)
      return route.fulfill({
        status: 503,
        json: { code: "UNAVAILABLE", message: "Pool summary unavailable." },
      });
    if (path === "/pool/summary")
      return json({
        pool_id: "pool-1",
        nodes: options.empty ? 0 : 1,
        ready_nodes: options.empty ? 0 : 1,
        accepted_units: options.empty ? 0 : 10,
        jobs_by_state: options.empty
          ? {}
          : { running: 2, succeeded: 3, failed: 1 },
      });
    if (path === "/nodes")
      return json(
        options.empty
          ? []
          : [
              {
                ...node,
                capabilities: options.missingCapabilities
                  ? null
                  : node.capabilities,
              },
            ],
      );
    if (path === "/jobs") return json([]);
    if (path === "/benchmarks") return json(options.empty ? [] : [benchmark]);
    if (path.includes("/control") || path.includes("/settings"))
      return json(node);
    return route.fulfill({
      status: 404,
      json: { code: "NOT_FOUND", message: path },
    });
  });
  return { requests };
}
