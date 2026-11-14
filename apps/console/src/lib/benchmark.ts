import type { Benchmark } from "./types";
export function median(v: number[]) {
  const a = v.filter(Number.isFinite).sort((a, b) => a - b);
  return a.length
    ? a.length % 2
      ? a[(a.length - 1) / 2]
      : (a[a.length / 2 - 1] + a[a.length / 2]) / 2
    : null;
}
export function comparison(b: Benchmark) {
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
