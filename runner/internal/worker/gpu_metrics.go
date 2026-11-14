package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/telemetry"
	"context"
	"time"
)

func (r *Runner) gpuMetrics(ctx context.Context, a client.Assignment, sender *attemptTelemetrySender, fail func(error)) {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
			start := time.Now()
			sampleCtx, cancel := context.WithTimeout(ctx, 750*time.Millisecond)
			gpus, e := telemetry.SampleGPU(sampleCtx, a.ResourceLimits.GPUIDs)
			cancel()
			end := time.Now()
			values := map[string]any{"gpus": gpus, "available": e == nil, "desired_interval_ms": 1000, "source": "nvidia-smi device samples; collection window, not integrated energy"}
			sample := telemetry.NewSample(0, "attempt/gpu", start, end, values)
			sendCtx, close := context.WithTimeout(ctx, 900*time.Millisecond)
			e = sender.Send(sendCtx, sample, "compute", float64(a.ResourceLimits.CPUMillis)/1000)
			close()
			if client.Terminal(e) {
				fail(e)
				return
			}
		}
	}
}
