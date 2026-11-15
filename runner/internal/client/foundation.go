package client

import (
	"campus/runner/internal/generated"
	"context"
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"strconv"
)

// This contract gap must be resolved by foundation, not by substituting the
// laptop's wall clock or guessing missing sandbox quotas.
var ErrWorkerContractIncomplete = errors.New("worker contract incomplete: foundation must provide a relative hard task deadline and explicit bounded PID/workspace/output/checkpoint/tmp policy")

func (c *Client) ClaimWork(ctx context.Context, waitMS int64) (*Assignment, error) {
	if waitMS < 0 || waitMS > 25000 {
		return nil, errors.New("invalid work poll duration")
	}
	var raw json.RawMessage
	if err := c.Do(ctx, "POST", "/work/next", generated.WorkRequest{WaitMs: &waitMS}, &raw, ""); err != nil {
		return nil, err
	}
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return nil, err
	}
	if _, ok := fields["task_remaining_ms"]; !ok {
		return nil, ErrWorkerContractIncomplete
	}
	var a Assignment
	if err := json.Unmarshal(raw, &a); err != nil {
		return nil, err
	}
	if a.TaskRemainingMS <= 0 || a.ResourceLimits.PIDs <= 0 || a.ResourceLimits.WorkspaceBytes <= 0 || a.ResourceLimits.OutputBytes <= 0 || a.ResourceLimits.CheckpointBytes <= 0 || a.ResourceLimits.TmpBytes <= 0 {
		return nil, ErrWorkerContractIncomplete
	}
	return &a, nil
}

var RegistryImagePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$`)

func (f Fence) Proof() generated.AttemptProof {
	return generated.AttemptProof{Generation: f.Generation, LeaseToken: f.LeaseToken}
}

// Contract separates local discovery diagnostics and detailed sandbox policy
// from the deliberately smaller server admission DTO.
func (c Capabilities) Contract() (generated.Capabilities, error) {
	var disk int64
	for _, n := range []int64{c.OfferedBudget.WorkspaceBytes, c.OfferedBudget.OutputBytes, c.OfferedBudget.CheckpointBytes, c.OfferedBudget.TmpBytes} {
		if n < 0 || disk > math.MaxInt64-n {
			return generated.Capabilities{}, errors.New("invalid local disk budget")
		}
		disk += n
	}
	revision, err := strconv.ParseInt(c.PreflightRevision, 10, 64)
	if err != nil || revision < 1 || c.CPULogical < 1 || c.CPULogical > 1024 || c.OfferedBudget.CPUMillis < 0 || c.OfferedBudget.MemoryBytes < 0 || c.OfferedBudget.VRAMBytes < 0 || c.MemoryAvailableBytes < 0 || c.DiskAvailableBytes < 0 || len(c.GPUs) > 64 || len(c.OfferedBudget.GPUIDs) > 64 {
		return generated.Capabilities{}, errors.New("invalid capability bounds")
	}
	for _, image := range c.ImageDigests {
		if !RegistryImagePattern.MatchString(image) {
			return generated.Capabilities{}, errors.New("contract capabilities require canonical image repository digests")
		}
	}
	slots := int64(len(c.OfferedBudget.GPUIDs))
	vram := c.OfferedBudget.VRAMBytes
	result := generated.Capabilities{OS: c.OS, Arch: c.Arch, CPULogical: int64(c.CPULogical), MemoryAvailableBytes: c.MemoryAvailableBytes, DiskAvailableBytes: c.DiskAvailableBytes, Runtime: generated.Runtime{Type: c.Runtime.Type, Version: c.Runtime.Version, Healthy: c.Runtime.Healthy}, GPUs: []generated.GPU{}, PowerState: c.PowerState, PreflightRevision: revision, ImageDigests: append([]string{}, c.ImageDigests...), AllowOnBattery: &c.AllowOnBattery, OfferedBudget: generated.Budget{CPUMillis: c.OfferedBudget.CPUMillis, MemoryBytes: c.OfferedBudget.MemoryBytes, DiskBytes: disk, GPUSlots: &slots, VRAMBytes: &vram}}
	for _, g := range c.GPUs {
		result.GPUs = append(result.GPUs, generated.GPU{ID: g.ID, Vendor: g.Vendor, VRAMBytes: g.VRAMBytes, Driver: g.Driver, CUDAVerified: g.CUDAVerified})
	}
	return result, nil
}
