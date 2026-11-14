package discovery

import (
	"campus/runner/internal/client"
	"context"
	"testing"
	"time"
)

func TestIdleCPUSampleUsesDeltaAndFailsClosed(t *testing.T) {
	var calls int
	read := func() (uint64, uint64, error) {
		calls++
		if calls == 1 {
			return 100, 1000, nil
		}
		return 125, 1100, nil
	}
	n, e := sampleIdleCPU(context.Background(), 8, time.Millisecond, read)
	if e != nil || n != 2000 {
		t.Fatal(n, e)
	}
	calls = 0
	n, e = sampleIdleCPU(context.Background(), 8, time.Millisecond, func() (uint64, uint64, error) { return 100, 1000, nil })
	if e == nil || n != 0 {
		t.Fatal("stale measurement offered CPU", n, e)
	}
}
func TestDockerCapacityBoundsAreReadWithoutOfferingUnknownCapacity(t *testing.T) {
	c := client.Capabilities{}
	applyDockerCapacity(&c, []byte(`{"NCPU":4,"MemTotal":4294967296}`))
	if c.DockerCPUMillis != 4000 || c.DockerMemoryBytes != 4<<30 {
		t.Fatal(c)
	}
	applyDockerCapacity(&c, []byte(`{"NCPU":-2,"MemTotal":-1}`))
	if c.DockerCPUMillis != 0 || c.DockerMemoryBytes != 0 {
		t.Fatal("invalid Docker capacity retained", c)
	}
}
