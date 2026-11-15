package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/discovery"
	"campus/runner/internal/session"
	"context"
	"sync/atomic"
)

// ManagedControl is process-local. Remote settings never overwrite an owner's
// persistent leave command. Once draining, this session cannot resume claims.
type ManagedControl struct {
	Mode    string
	OnReady func(context.Context) error
	drain   atomic.Bool
}

func (m *ManagedControl) Drain() { m.drain.Store(true) }
func (r *Runner) participationAction() (string, error) {
	action, e := session.ReadControl(r.StateDir)
	if e != nil || action == "stop" || action == "drain" {
		return action, e
	}
	if r.ManagedControl != nil && r.ManagedControl.drain.Load() {
		return "drain", nil
	}
	return action, nil
}
func (r *Runner) action() string {
	action, _ := r.controlAction.Load().(string)
	if action == "stop" || action == "drain" {
		return action
	}
	if r.ManagedControl != nil && r.ManagedControl.drain.Load() {
		return "drain"
	}
	return action
}
func (r *Runner) refreshCapabilities(current, updated client.Capabilities, gpu bool) (client.Capabilities, error) {
	verified := map[string]bool{}
	for _, g := range current.GPUs {
		verified[g.ID] = g.CUDAVerified
	}
	for i := range updated.GPUs {
		updated.GPUs[i].CUDAVerified = verified[updated.GPUs[i].ID]
	}
	updated.ImageDigests = append([]string{}, current.ImageDigests...)
	updated.AllowOnBattery = current.AllowOnBattery
	updated.OwnerCPUMillis = current.OwnerCPUMillis
	updated.OwnerMemoryBytes = current.OwnerMemoryBytes
	if r.ManagedControl != nil {
		budget, e := discovery.ProfileBudget(updated, gpu, r.ManagedControl.Mode)
		updated.OfferedBudget = budget
		return updated, e
	}
	updated.OfferedBudget = discovery.Budget(updated, gpu)
	return updated, nil
}
