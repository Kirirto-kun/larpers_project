package worker

import (
	"campus/runner/internal/client"
	"testing"
)

func TestCPUAndGPUShareOneMemoryBudget(t *testing.T) {
	p := NewBudget(client.ResourceLimits{CPUMillis: 2000, MemoryBytes: 4 << 30, GPUIDs: []string{"0"}, VRAMBytes: 4 << 30})
	cpu := client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 3 << 30}
	gpu := client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 2 << 30, GPUIDs: []string{"0"}, VRAMBytes: 1 << 30}
	release, e := p.Acquire(cpu)
	if e != nil {
		t.Fatal(e)
	}
	if r, e := p.Acquire(gpu); e == nil {
		r()
		t.Fatal("separate GPU queue overcommitted shared RAM")
	}
	release()
	release, e = p.Acquire(gpu)
	if e != nil {
		t.Fatal(e)
	}
	if r, e := p.Acquire(gpu); e == nil {
		r()
		t.Fatal("GPU double booked")
	}
	release()
	if r, e := p.Acquire(cpu); e != nil {
		t.Fatal(e)
	} else {
		r()
	}
}

func TestChangedCapabilitiesReduceAdmissionWithoutLosingReservations(t *testing.T) {
	b := NewBudget(client.ResourceLimits{CPUMillis: 4000, MemoryBytes: 4 << 30})
	release, e := b.Acquire(client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 2 << 30})
	if e != nil {
		t.Fatal(e)
	}
	b.Update(client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 1 << 30})
	if r, e := b.Acquire(client.ResourceLimits{CPUMillis: 1, MemoryBytes: 1}); e == nil {
		r()
		t.Fatal("capability shrink ignored")
	}
	release()
	b.Update(client.ResourceLimits{CPUMillis: 2000, MemoryBytes: 2 << 30})
	if r, e := b.Acquire(client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 1 << 30}); e != nil {
		t.Fatal(e)
	} else {
		r()
	}
}
