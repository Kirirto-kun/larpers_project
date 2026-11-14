package discovery

import (
	"campus/runner/internal/client"
	"errors"
)

// ProfileBudget uses measured idle CPU and currently available RAM, constrained
// by the local Docker VM and optional owner ceilings. Missing measurements offer
// no compute. A web setting cannot grant GPU or battery consent.
func ProfileBudget(c client.Capabilities, gpu bool, mode string) (client.ResourceLimits, error) {
	percent := int64(0)
	switch mode {
	case "low":
		percent = 10
	case "medium":
		percent = 50
	case "max":
		percent = 90
	default:
		return client.ResourceLimits{}, errors.New("unknown contribution mode")
	}
	b := Budget(c, gpu)
	b.CPUMillis = 0
	b.MemoryBytes = 0
	b.GPUIDs = []string{}
	b.VRAMBytes = 0
	if (c.PowerState == "battery" && !c.AllowOnBattery) || c.MemoryAvailableBytes < 512<<20 || c.DiskAvailableBytes < 512<<20 || c.CPUAvailableMillis <= 0 || c.DockerCPUMillis <= 0 || c.DockerMemoryBytes <= 0 {
		return b, nil
	}
	b.CPUMillis = min(c.CPUAvailableMillis, int64(c.CPULogical)*1000, c.DockerCPUMillis) * percent / 100
	available := min(c.MemoryAvailableBytes, c.DockerMemoryBytes)
	b.MemoryBytes = (available/100)*percent + (available%100)*percent/100
	if c.OwnerCPUMillis > 0 {
		b.CPUMillis = min(b.CPUMillis, c.OwnerCPUMillis)
	}
	if c.OwnerMemoryBytes > 0 {
		b.MemoryBytes = min(b.MemoryBytes, c.OwnerMemoryBytes)
	}
	if gpu {
		for _, g := range c.GPUs {
			if g.CUDAVerified {
				b.GPUIDs = append(b.GPUIDs, g.ID)
				b.VRAMBytes += g.VRAMBytes
			}
		}
	}
	return b, nil
}
