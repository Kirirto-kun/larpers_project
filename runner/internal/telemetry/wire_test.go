package telemetry

import (
	"encoding/json"
	"math"
	"testing"
	"time"
)

// These boundaries catch one-core CPU values being sent as node/allocated CPU
// percentages, which makes fully busy multi-core workloads exceed the API's 100.
func TestWireNormalizesCPUToDeclaredCapacityAndKeepsRawWindow(t *testing.T) {
	start := time.Date(2026, 11, 14, 0, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		name, key      string
		raw            any
		capacity, want float64
	}{
		{"node all four cores", "runner_cpu_percent_one_core", 400.0, 4, 100},
		{"container allocated two cores", "CPUPerc", "200.00%", 2, 100},
		{"fractional allocated core", "CPUPerc", "25.00%", .5, 50},
		{"overshoot saturates wire only", "CPUPerc", "200.50%", 2, 100},
		{"measured idle is zero", "cpu_percent", 0.0, 2, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := NewSample(9, "local/container", start, start.Add(time.Second), map[string]any{tc.key: tc.raw})
			wire := s.Wire("compute", tc.capacity)
			if wire.CPUPercent == nil || *wire.CPUPercent != tc.want || wire.Sequence != 9 || wire.Phase != "compute" {
				t.Fatalf("wrong wire normalization: %+v", wire)
			}
			if s.Values[tc.key] != tc.raw || s.DurationMS != 1000 || s.WindowStart != "2026-11-14T00:00:00Z" || s.WindowEnd != "2026-11-14T00:00:01Z" {
				t.Fatalf("wire conversion modified precise local sample: %+v", s)
			}
		})
	}
}

func TestWireLeavesUnavailableSensorsUnknown(t *testing.T) {
	for _, values := range []map[string]any{
		{}, {"CPUPerc": "--", "MemUsage": "-- / --"},
		{"cpu_percent": math.NaN(), "memory_bytes": int64(-1)},
		{"gpus": []GPUUsage{}}, {"gpus": []GPUUsage{{ID: "0"}}},
	} {
		wire := (Sample{Values: values}).Wire("idle", 4)
		if wire.CPUPercent != nil || wire.GPUPercent != nil || wire.MemoryBytes != nil {
			t.Fatalf("invented zero or invalid metric: %+v", wire)
		}
		b, err := json.Marshal(wire)
		if err != nil {
			t.Fatal(err)
		}
		var keys map[string]any
		json.Unmarshal(b, &keys)
		if len(keys) != 2 {
			t.Fatalf("missing metrics must be omitted: %s", b)
		}
	}
	if got := (Sample{Values: map[string]any{"cpu_percent": 50.0}}).Wire("compute", 0); got.CPUPercent != nil {
		t.Fatal("CPU without a declared denominator must be unknown")
	}
}

func TestWireUsesMeasuredMemoryAndDeviceGPUAverage(t *testing.T) {
	low, high := 0.0, 100.0
	s := Sample{Values: map[string]any{"MemUsage": "12.5MiB / 1GiB", "gpus": []GPUUsage{{ID: "0", UtilizationPercent: &low}, {ID: "1", UtilizationPercent: &high}}}}
	wire := s.Wire("compute", 2)
	if wire.MemoryBytes == nil || *wire.MemoryBytes != 13107200 || wire.GPUPercent == nil || *wire.GPUPercent != 50 {
		t.Fatalf("wrong memory units or GPU aggregation: %+v", wire)
	}
	s.Values["gpus"] = []GPUUsage{{ID: "0", UtilizationPercent: &low}, {ID: "1"}}
	if s.Wire("compute", 2).GPUPercent != nil {
		t.Fatal("partial GPU availability must remain unknown")
	}
}
