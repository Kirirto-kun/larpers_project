import type { Benchmark } from "./types";
export function median(v: number[]) {
  if (v.some((value) => !Number.isFinite(value) || value < 0)) return null;
  const a = [...v].sort((a, b) => a - b);
  return a.length
    ? a.length % 2
      ? a[(a.length - 1) / 2]
      : (a[a.length / 2 - 1] + a[a.length / 2]) / 2
    : null;
}
export function comparison(b: Benchmark) {
  if (
    new Set(b.node_ids).size !== b.node_ids.length ||
    new Set(b.standalone.map((s) => s.node_id)).size !== b.standalone.length
  )
    return null;
  const rates = b.node_ids.map((id) =>
    median(b.standalone.find((s) => s.node_id === id)?.rates || []),
  );
  const pool = median(b.pool_rates);
  if (
    !rates.length ||
    rates.some((r) => r === null || r <= 0) ||
    pool === null ||
    pool < 0
  )
    return null;
  const numbers = rates as number[];
  return {
    efficiency: (pool / numbers.reduce((a, c) => a + c, 0)) * 100,
    speedup: pool / Math.max(...numbers),
    pool,
  };
}
