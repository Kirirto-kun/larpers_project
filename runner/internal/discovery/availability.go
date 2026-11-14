package discovery

import (
	"campus/runner/internal/client"
	"context"
	"encoding/json"
	"errors"
	"time"
)

func sampleIdleCPU(ctx context.Context, cores int, interval time.Duration, read func() (uint64, uint64, error)) (int64, error) {
	idle, total, e := read()
	if e != nil {
		return 0, e
	}
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return 0, ctx.Err()
	case <-timer.C:
	}
	nextIdle, nextTotal, e := read()
	if e != nil {
		return 0, e
	}
	if nextTotal <= total || nextIdle < idle || nextIdle-idle > nextTotal-total || cores < 1 {
		return 0, errors.New("CPU availability sample unavailable")
	}
	return int64(float64(nextIdle-idle) / float64(nextTotal-total) * float64(cores) * 1000), nil
}
func applyDockerCapacity(c *client.Capabilities, raw []byte) {
	c.DockerCPUMillis = 0
	c.DockerMemoryBytes = 0
	var info struct {
		NCPU     int64
		MemTotal int64
	}
	if len(raw) > 65536 || json.Unmarshal(raw, &info) != nil || info.NCPU < 1 || info.NCPU > 1024 || info.MemTotal < 1 {
		return
	}
	c.DockerCPUMillis = info.NCPU * 1000
	c.DockerMemoryBytes = info.MemTotal
}
