package discovery

import (
	"campus/runner/internal/client"
	"context"
	"testing"
)

func TestMissingRuntimeDoesNotOfferUnverifiedGPU(t *testing.T) {
	c, e := DiscoverWith(context.Background(), func(ctx context.Context, name string, args ...string) ([]byte, error) {
		return nil, context.DeadlineExceeded
	})
	if e != nil {
		t.Fatal(e)
	}
	if c.Runtime.Healthy || len(c.GPUs) != 0 || len(c.Diagnostics) == 0 || len(c.OfferedBudget.GPUIDs) != 0 {
		t.Fatalf("bad preflight %+v", c)
	}
}
func TestBudgetReservesOwnerResourcesAndPausesBattery(t *testing.T) {
	c := client.Capabilities{CPULogical: 8, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 20 << 30, PowerState: "ac", GPUs: []client.GPU{{ID: "0", VRAMBytes: 8 << 30, CUDAVerified: false}}}
	b := Budget(c, false)
	if b.CPUMillis != 2000 || b.MemoryBytes != 2<<30 || len(b.GPUIDs) != 0 {
		t.Fatalf("aggressive default %+v", b)
	}
	c.PowerState = "battery"
	if Budget(c, true).CPUMillis != 0 {
		t.Fatal("offered compute on battery")
	}
	c.PowerState = "ac"
	if len(Budget(c, true).GPUIDs) != 0 {
		t.Fatal("unverified CUDA admitted")
	}
	c.GPUs[0].CUDAVerified = true
	if len(Budget(c, true).GPUIDs) != 1 {
		t.Fatal("verified opt-in GPU missing")
	}
}

func TestMacAvailableMemoryUsesReportedPageSize(t *testing.T) {
	value := parseVMStat("Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free: 2.\nPages inactive: 3.\nPages speculative: 1.\nPages active: 100.\n")
	if value != 24576 {
		t.Fatal("memory estimate used total or wrong page size", value)
	}
}
