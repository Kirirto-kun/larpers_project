// HTTP shapes match server/campus/dto.py. No inferred readiness or telemetry.
export interface PoolEvent {
  event_id: number | string;
  occurred_at: string;
  entity_type: string;
  entity_id: string;
  type: string;
  payload: Record<string, unknown>;
}
export interface Session {
  id: string;
  name: string;
  email: string | null;
  role: "owner" | "admin" | "member";
  pool_id: string;
  csrf_token?: string;
}
export interface Budget {
  cpu_millis: number;
  memory_bytes: number;
  disk_bytes: number;
  gpu_slots: number;
  vram_bytes: number;
}
export interface Capabilities {
  os: string;
  arch: string;
  cpu_logical: number;
  memory_available_bytes: number;
  disk_available_bytes: number;
  runtime: { type: string; version: string; healthy: boolean };
  gpus: {
    id: string;
    vendor: string;
    vram_bytes: number;
    driver: string;
    cuda_verified: boolean;
  }[];
  power_state: string;
  offered_budget: Budget;
  allow_on_battery: boolean;
}
export type Mode = "low" | "medium" | "max";
export type DesiredState = "running" | "paused" | "draining";
export interface Node {
  id: string;
  owner_id: string;
  name: string;
  state: string;
  last_seen_at: string | null;
  capabilities: Capabilities | null;
  accepted_units: number;
  active_attempts: number;
  settings: { revision: number; mode: Mode; desired_state: DesiredState };
  applied_revision: number;
  applied_mode: Mode | null;
  actual_state: string | null;
  settings_error: string | null;
  supports_web_controls: boolean;
  agent_contact_at: string | null;
}
export interface Artifact {
  id: string;
  name: string;
  size_bytes: number;
  sha256: string;
  media_type: string;
}
export interface Task {
  id: string;
  stage_id: string;
  state: string;
  generation: number;
  units: number;
  error_code: string | null;
  attempt_id: string | null;
  node_id: string | null;
  verification_level: string | null;
}
export interface Job {
  id: string;
  owner_id: string;
  state: string;
  purpose: string;
  created_at: string;
  accepted_units: number;
  failed_units: number;
  total_units: number;
  tasks: Task[];
  artifacts: Artifact[];
  search_result: {
    matches: {
      item_id: string;
      source: string;
      excerpt: string;
      score: number;
    }[];
  } | null;
}
export interface Stage {
  stage_id: string;
  adapter: { id: string; version: string };
  variants: { accelerator: string; resource_requirements: Budget }[];
  outputs: { path: string; required: boolean; max_bytes: number }[];
  task_deadline_ms: number;
  resume_policy: string;
  argv: string[];
}
export interface Preview {
  id: string;
  state: string;
  plan_revision: number;
  plan_digest: string | null;
  plan: {
    plan_revision: number;
    execution_kind: string;
    stages: Stage[];
    job_deadline_at: string;
    assumptions: string[];
    evidence: { source: string; detail: string }[];
  } | null;
  question: { question_id: string; prompt: string } | null;
  error_code: string | null;
  error_message: string | null;
}
export interface Stats {
  devices_total: number;
  devices_online: number;
  accepted_units: number;
  contributed_seconds: number | null;
  jobs_by_state: Record<string, number>;
  recent_results: Job[];
  history: { date: string; accepted_units: number; amount: number }[];
}
export interface Summary {
  pool_id: string;
  nodes: number;
  ready_nodes: number;
  accepted_units: number;
  jobs_by_state: Record<string, number>;
}
export interface Benchmark {
  id: string;
  status: string;
  config: {
    preview_id: string;
    plan_revision: number;
    node_ids: string[];
    concurrency: number;
    repetitions: number;
    warm: boolean;
    batch_policy: Record<string, number>;
  };
  config_digest: string;
  manifest_digest: string;
  standalone_results: {
    node_id: string;
    units: number;
    duration_ms: number;
    verification_ms: number;
    utilization: {
      sequence: number;
      phase: string;
      cpu_percent: number | null;
      gpu_percent: number | null;
      memory_bytes: number | null;
    }[];
  }[];
  service_job_ids: string[];
  standalone_units_per_second: Record<string, number>;
  pooled_units_per_second: number | null;
  efficiency_percent: number | null;
  speedup: number | null;
  service_runs: Record<string, unknown>[];
}
export interface Release {
  version: string;
  downloads: {
    os: "windows" | "darwin" | "linux";
    label: string;
    url: string;
    sha256?: string;
  }[];
  requirements: { docker_manual: boolean };
}
export interface Invitation {
  id: string;
  token: string;
  kind: string;
  expires_at: string;
}
