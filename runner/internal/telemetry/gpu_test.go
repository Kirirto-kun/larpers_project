package telemetry

import (
	"testing"
)

func TestGPUReportsUseBytesAndKeepUnavailableSensorsUnknown(t *testing.T) {
	samples, e := ParseGPU([]byte("0, 37, 1024\n1, [N/A], [N/A]\n2, 90, 700\n"), []string{"0", "1"})
	if e != nil {
		t.Fatal(e)
	}
	if len(samples) != 2 || samples[0].UtilizationPercent == nil || *samples[0].UtilizationPercent != 37 || samples[0].MemoryUsedBytes == nil || *samples[0].MemoryUsedBytes != 1073741824 || samples[1].UtilizationPercent != nil || samples[1].MemoryUsedBytes != nil {
		t.Fatalf("wrong units, unoffered GPU, or fabricated metrics %+v", samples)
	}
}
