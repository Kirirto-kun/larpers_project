package telemetry

import (
	"campus/runner/internal/generated"
	"encoding/json"
	"math"
	"strconv"
	"strings"
)

// Wire projects a precise local sample onto foundation's intentionally smaller
// DTO. CPU capacity is logical cores for nodes, allocated CPU millis / 1000 for
// attempts. Local one-core values and windows are never changed. Foundation has
// no window, sensor-source, device-ID, or availability fields.
func (s Sample) Wire(phase string, cpuCapacity float64) generated.Telemetry {
	wire := generated.Telemetry{Sequence: s.Sequence, Phase: phase}
	if cpuCapacity > 0 && !math.IsInf(cpuCapacity, 0) {
		for _, key := range []string{"runner_cpu_percent_one_core", "cpu_percent", "CPUPerc"} {
			if cpu, ok := metricNumber(s.Values[key]); ok && cpu >= 0 {
				value := math.Min(100, cpu/cpuCapacity)
				wire.CPUPercent = &value
				break
			}
		}
	}
	for _, key := range []string{"runner_rss_bytes", "memory_bytes"} {
		if memory, ok := metricNumber(s.Values[key]); ok && memory >= 0 && memory < math.MaxInt64 && math.Trunc(memory) == memory {
			value := int64(memory)
			wire.MemoryBytes = &value
			break
		}
	}
	if wire.MemoryBytes == nil {
		if usage, ok := s.Values["MemUsage"].(string); ok {
			wire.MemoryBytes = dockerMemory(strings.TrimSpace(strings.SplitN(usage, "/", 2)[0]))
		}
	}
	if gpus, ok := s.Values["gpus"].([]GPUUsage); ok && len(gpus) > 0 {
		var sum float64
		for _, gpu := range gpus {
			if gpu.UtilizationPercent == nil || math.IsNaN(*gpu.UtilizationPercent) || *gpu.UtilizationPercent < 0 || *gpu.UtilizationPercent > 100 {
				return wire
			}
			sum += *gpu.UtilizationPercent
		}
		// This is device utilization averaged across the selected devices. It
		// includes other processes on those devices; it is not process GPU use.
		value := sum / float64(len(gpus))
		wire.GPUPercent = &value
	}
	return wire
}

func metricNumber(value any) (float64, bool) {
	var number float64
	switch value := value.(type) {
	case float64:
		number = value
	case int64:
		number = float64(value)
	case int:
		number = float64(value)
	case json.Number:
		var err error
		number, err = value.Float64()
		if err != nil {
			return 0, false
		}
	case string:
		var err error
		number, err = strconv.ParseFloat(strings.TrimSuffix(strings.TrimSpace(value), "%"), 64)
		if err != nil {
			return 0, false
		}
	default:
		return 0, false
	}
	return number, !math.IsNaN(number) && !math.IsInf(number, 0)
}

func dockerMemory(text string) *int64 {
	units := []struct {
		suffix string
		factor float64
	}{
		{"TiB", 1 << 40}, {"GiB", 1 << 30}, {"MiB", 1 << 20}, {"KiB", 1 << 10},
		{"TB", 1e12}, {"GB", 1e9}, {"MB", 1e6}, {"kB", 1e3}, {"B", 1},
	}
	for _, unit := range units {
		if strings.HasSuffix(text, unit.suffix) {
			number, err := strconv.ParseFloat(strings.TrimSpace(strings.TrimSuffix(text, unit.suffix)), 64)
			bytes := number * unit.factor
			if err == nil && !math.IsNaN(bytes) && bytes >= 0 && bytes < math.MaxInt64 {
				value := int64(bytes)
				return &value
			}
			return nil
		}
	}
	return nil
}
