package discovery

import (
	"campus/runner/internal/client"
	"testing"
)

func TestManagedProfilesUseAvailableResourcesWithoutCoreFloor(t *testing.T) {
	c := client.Capabilities{CPULogical: 8, CPUAvailableMillis: 2500, DockerCPUMillis: 8000, DockerMemoryBytes: 16 << 30, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 20 << 30, PowerState: "ac"}
	for _, tc := range []struct {
		mode        string
		cpu, memory int64
	}{{"low", 250, 858993459}, {"medium", 1250, 4 << 30}, {"max", 2250, 7730941132}} {
		b, e := ProfileBudget(c, false, tc.mode)
		if e != nil || b.CPUMillis != tc.cpu || b.MemoryBytes != tc.memory {
			t.Fatalf("%s: %+v %v", tc.mode, b, e)
		}
	}
	c.DockerCPUMillis = 1000
	c.DockerMemoryBytes = 2 << 30
	c.OwnerCPUMillis = 400
	c.OwnerMemoryBytes = 1 << 30
	b, e := ProfileBudget(c, false, "max")
	if e != nil || b.CPUMillis != 400 || b.MemoryBytes != 1<<30 {
		t.Fatalf("local ceilings ignored: %+v %v", b, e)
	}
}

func TestManagedProfilesFailClosedAndNeverGrantConsent(t *testing.T) {
	c := client.Capabilities{CPULogical: 8, CPUAvailableMillis: 4000, DockerCPUMillis: 4000, DockerMemoryBytes: 8 << 30, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 8 << 30, PowerState: "ac", GPUs: []client.GPU{{ID: "0", CUDAVerified: true, VRAMBytes: 8 << 30}}}
	for _, mode := range []string{"low", "medium", "max"} {
		b, e := ProfileBudget(c, false, mode)
		if e != nil || len(b.GPUIDs) != 0 {
			t.Fatal("remote profile enabled GPU", b, e)
		}
	}
	c.PowerState = "battery"
	b, _ := ProfileBudget(c, true, "max")
	if b.CPUMillis != 0 || len(b.GPUIDs) != 0 {
		t.Fatal("remote profile enabled battery work", b)
	}
	c.PowerState = "ac"
	c.CPUAvailableMillis = 0
	b, _ = ProfileBudget(c, true, "max")
	if b.CPUMillis != 0 {
		t.Fatal("missing CPU sample escalated", b)
	}
	if _, e := ProfileBudget(c, false, "turbo"); e == nil {
		t.Fatal("unknown mode accepted")
	}
}
