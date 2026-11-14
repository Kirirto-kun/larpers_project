// Provisional UI models. Replace at integration with foundation-generated DTOs.
export type State = string;
export interface PoolEvent {
  event_id: string;
  occurred_at: string;
  entity_type: string;
  entity_id: string;
  type: string;
  payload: Record<string, unknown>;
}
export interface Artifact {
  id: string;
  name: string;
  size_bytes: number;
  ready: boolean;
  digest?: string;
}
export interface Attempt {
  attempt_id: string;
  state: string;
  node_id: string;
  occurred_at: string;
  message?: string;
  input_digest?: string;
  output_digest?: string;
}
export interface Job {
  id: string;
  name: string;
  state: State;
  accepted_units: number;
  failed_units: number;
  total_units: number;
  stages: { stage_id: string; state: State }[];
  artifacts: Artifact[];
  attempts: Attempt[];
  metrics?: Record<string, number>;
  validation_level?: string;
  errors?: string[];
  matches?: {
    source: string;
    excerpt: string;
    score: number;
    item_id: string;
  }[];
  index_artifact_id?: string;
}
export interface Node {
  id: string;
  name: string;
  state: State;
  roles: string[];
  cpu_logical: number;
  memory_available_bytes: number;
  gpus: {
    id: string;
    vendor: string;
    vram_bytes: number;
    cuda_verified: boolean;
  }[];
  offered_budget: {
    cpu_percent: number;
    memory_bytes: number;
    gpu_allowed: boolean;
  };
  preflight: { name: string; ok: boolean; message: string }[];
  current_job_id?: string;
  last_seen_at: string;
  cpu_utilization?: number;
  gpu_utilization?: number;
  attempts?: Attempt[];
}
export interface Preview {
  id: string;
  state: State;
  plan_revision: number;
  questions: { question_id: string; message: string }[];
  summary: string;
  accelerator: string;
  resume_policy: string;
  outputs: string[];
  assumptions: string[];
  evidence: string[];
  data_location: string;
  duration_ms: number | null;
}
export interface Session {
  name: string;
  role: "owner" | "admin" | "member";
  csrf_token?: string;
}
export interface Summary {
  accepted_units: number;
  queued_tasks: number;
  running_tasks: number;
  recovering_tasks: number;
  events: PoolEvent[];
}
export interface Benchmark {
  id: string;
  experiment_id: string;
  state: string;
  workload: string;
  unit: string;
  input_digest: string;
  node_ids: string[];
  concurrency: number;
  repetitions: number;
  cold: boolean;
  git_sha: string;
  standalone: { node_id: string; rates: number[]; provenance: string }[];
  pool_rates: number[];
  single_service_rates: number[];
  timings_ms: Record<string, number>;
  utilization: {
    node_id: string;
    cpu_percent: number | null;
    gpu_percent: number | null;
    window_ms: number;
    gaps: number;
  }[];
  bottleneck: string;
  flags: string[];
}
