package telemetry

import (
	"context"
	"encoding/csv"
	"os/exec"
	"strconv"
	"strings"
)

type GPUUsage struct {
	ID                 string   `json:"id"`
	UtilizationPercent *float64 `json:"utilization_percent"`
	MemoryUsedBytes    *int64   `json:"memory_used_bytes"`
}

func ParseGPU(data []byte, ids []string) ([]GPUUsage, error) {
	reader := csv.NewReader(strings.NewReader(string(data)))
	reader.TrimLeadingSpace = true
	records, e := reader.ReadAll()
	if e != nil {
		return nil, e
	}
	wanted := map[string]bool{}
	for _, id := range ids {
		wanted[id] = true
	}
	result := []GPUUsage{}
	for _, record := range records {
		if len(record) != 3 || !wanted[strings.TrimSpace(record[0])] {
			continue
		}
		sample := GPUUsage{ID: strings.TrimSpace(record[0])}
		if value, e := strconv.ParseFloat(strings.TrimSpace(record[1]), 64); e == nil && value >= 0 && value <= 100 {
			sample.UtilizationPercent = &value
		}
		if value, e := strconv.ParseInt(strings.TrimSpace(record[2]), 10, 64); e == nil && value >= 0 && value < 1<<42 {
			bytes := value << 20
			sample.MemoryUsedBytes = &bytes
		}
		result = append(result, sample)
	}
	return result, nil
}
func SampleGPU(ctx context.Context, ids []string) ([]GPUUsage, error) {
	out, e := exec.CommandContext(ctx, "nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used", "--format=csv,noheader,nounits").Output()
	if e != nil {
		return nil, e
	}
	return ParseGPU(out, ids)
}
