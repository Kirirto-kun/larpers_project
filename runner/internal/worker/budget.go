package worker

import (
	"campus/runner/internal/client"
	"errors"
	"sync"
)

type Budget struct {
	mu                            sync.Mutex
	limit                         client.ResourceLimits
	usedCPU, usedMemory, usedVRAM int64
	gpus                          map[string]bool
}

func NewBudget(limit client.ResourceLimits) *Budget {
	return &Budget{limit: limit, gpus: map[string]bool{}}
}
func (b *Budget) Update(limit client.ResourceLimits) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.limit = limit
}

// Constrain applies only reductions while a capability publication is pending.
// Existing reservations survive; Acquire admits nothing while they exceed the
// new limit. An unacknowledged increase can never expand admission.
func (b *Budget) Constrain(next client.ResourceLimits) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.limit.CPUMillis = min(b.limit.CPUMillis, next.CPUMillis)
	b.limit.MemoryBytes = min(b.limit.MemoryBytes, next.MemoryBytes)
	b.limit.VRAMBytes = min(b.limit.VRAMBytes, next.VRAMBytes)
	allowed := map[string]bool{}
	for _, id := range next.GPUIDs {
		allowed[id] = true
	}
	retained := []string{}
	for _, id := range b.limit.GPUIDs {
		if allowed[id] {
			retained = append(retained, id)
		}
	}
	b.limit.GPUIDs = retained
}
func (b *Budget) Acquire(r client.ResourceLimits) (func(), error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if r.CPUMillis <= 0 || r.MemoryBytes <= 0 || r.CPUMillis > b.limit.CPUMillis-b.usedCPU || r.MemoryBytes > b.limit.MemoryBytes-b.usedMemory || r.VRAMBytes < 0 || r.VRAMBytes > b.limit.VRAMBytes-b.usedVRAM {
		return nil, errors.New("shared CPU/RAM/VRAM budget exhausted")
	}
	allowed := map[string]bool{}
	for _, g := range b.limit.GPUIDs {
		allowed[g] = true
	}
	seen := map[string]bool{}
	for _, g := range r.GPUIDs {
		if !allowed[g] || b.gpus[g] || seen[g] {
			return nil, errors.New("GPU unavailable or not opted in")
		}
		seen[g] = true
	}
	if len(r.GPUIDs) == 0 && r.VRAMBytes != 0 {
		return nil, errors.New("VRAM requested without a GPU")
	}
	b.usedCPU += r.CPUMillis
	b.usedMemory += r.MemoryBytes
	b.usedVRAM += r.VRAMBytes
	for _, g := range r.GPUIDs {
		b.gpus[g] = true
	}
	var once sync.Once
	return func() {
		once.Do(func() {
			b.mu.Lock()
			defer b.mu.Unlock()
			b.usedCPU -= r.CPUMillis
			b.usedMemory -= r.MemoryBytes
			b.usedVRAM -= r.VRAMBytes
			for _, g := range r.GPUIDs {
				delete(b.gpus, g)
			}
		})
	}, nil
}
