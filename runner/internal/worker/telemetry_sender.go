package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/telemetry"
	"context"
	"sync"
)

// CPU and GPU observations share one sequence and one in-flight send. Atomic
// allocation alone would allow a faster GPU request to overtake a CPU request.
type attemptTelemetrySender struct {
	mu       sync.Mutex
	sequence int64
	client   *client.Client
	fence    client.Fence
}

func newAttemptTelemetrySender(c *client.Client, fence client.Fence) *attemptTelemetrySender {
	return &attemptTelemetrySender{client: c, fence: fence}
}

func (s *attemptTelemetrySender) Send(ctx context.Context, sample telemetry.Sample, phase string, cpuCapacity float64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	s.sequence++
	wire := sample.Wire(phase, cpuCapacity)
	body := generated.AttemptTelemetry{Sequence: s.sequence, Phase: wire.Phase, CPUPercent: wire.CPUPercent, GPUPercent: wire.GPUPercent, MemoryBytes: wire.MemoryBytes, Generation: s.fence.Generation, LeaseToken: s.fence.LeaseToken}
	return s.client.Do(ctx, "POST", "/attempts/"+s.fence.AttemptID+"/telemetry", body, nil, "")
}
