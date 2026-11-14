package worker

import (
	"campus/runner/internal/telemetry"
	"context"
	"time"
)

func (r *Runner) nodeMetrics(ctx context.Context) {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	start := time.Now()
	previous, _ := telemetry.ProcessStats()
	var sequence int64
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
			end := time.Now()
			cpu, rss := telemetry.ProcessStats()
			sequence++
			values := map[string]any{"runner_cpu_percent_one_core": 100 * float64(cpu-previous) / float64(end.Sub(start).Nanoseconds()), "runner_rss_bytes": rss, "rss_available": rss > 0, "runtime_overhead_included": false}
			sample := telemetry.NewSample(sequence, "node/runner", start, end, values)
			sendCtx, cancel := context.WithTimeout(ctx, 900*time.Millisecond)
			r.Client.Do(sendCtx, "POST", "/node-sessions/"+r.Client.SessionID+"/telemetry", sample, nil, "")
			cancel()
			previous = cpu
			start = end
		}
	}
}
