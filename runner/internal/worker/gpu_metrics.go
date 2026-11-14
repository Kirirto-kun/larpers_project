package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/telemetry"
	"context"
	"sync/atomic"
	"time"
)

func (r *Runner) gpuMetrics(ctx context.Context, a client.Assignment, sequence *atomic.Int64, fail func(error)) {
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
			sample := telemetry.NewSample(sequence.Add(1), "attempt/gpu", start, end, values)
			sendCtx, close := context.WithTimeout(ctx, 900*time.Millisecond)
			e = r.Client.Do(sendCtx, "POST", "/attempts/"+a.AttemptID+"/telemetry", map[string]any{"attempt_id": a.AttemptID, "generation": a.Generation, "lease_token": a.LeaseToken, "sample": sample}, nil, "")
			close()
			if client.Terminal(e) {
				fail(e)
				return
			}
		}
	}
}
