package worker

import (
	"campus/runner/internal/telemetry"
	"context"
	"os"
	"runtime"
	"time"
)

func (r *Runner) nodeMetrics(ctx context.Context) {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	start := time.Now()
	previous, _ := telemetry.ReadProcessStats(os.Getpid())
	var sequence int64
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
			end := time.Now()
			current, _ := telemetry.ReadProcessStats(os.Getpid())
			sequence++
			values := map[string]any{"rss_available": current.RSSBytes != nil, "runtime_overhead_included": false}
			if current.CPUTimeNS != nil && previous.CPUTimeNS != nil && *current.CPUTimeNS >= *previous.CPUTimeNS {
				values["runner_cpu_percent_one_core"] = 100 * float64(*current.CPUTimeNS-*previous.CPUTimeNS) / float64(end.Sub(start).Nanoseconds())
			}
			if current.RSSBytes != nil {
				values["runner_rss_bytes"] = *current.RSSBytes
			}
			sample := telemetry.NewSample(sequence, "node/runner", start, end, values)
			sendCtx, cancel := context.WithTimeout(ctx, 900*time.Millisecond)
			r.Client.Do(sendCtx, "POST", "/node-sessions/"+r.Client.SessionID+"/telemetry", sample.Wire("idle", float64(runtime.NumCPU())), nil, "")
			cancel()
			previous = current
			start = end
		}
	}
}
