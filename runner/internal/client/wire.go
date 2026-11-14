// These runner-owned wire types implement the plan's proposed v1 protocol.
// They are NOT generated DTOs; reconcile them with foundation before integration.
package client

import (
	"campus/runner/internal/packaging"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"
)

const Version = "0.1.0"

type Fence struct {
	AttemptID  string `json:"attempt_id"`
	Generation int64  `json:"generation"`
	LeaseToken string `json:"lease_token"`
}
type Artifact struct {
	ArtifactID string `json:"artifact_id"`
	Digest     string `json:"digest"`
	SizeBytes  int64  `json:"size_bytes"`
	Path       string `json:"path,omitempty"`
	Kind       string `json:"kind,omitempty"`
}
type Adapter struct {
	ID      string `json:"id"`
	Version string `json:"version"`
}
type ResourceLimits struct {
	CPUMillis       int64    `json:"cpu_millis"`
	MemoryBytes     int64    `json:"memory_bytes"`
	PIDs            int64    `json:"pids"`
	WorkspaceBytes  int64    `json:"workspace_bytes"`
	OutputBytes     int64    `json:"output_bytes"`
	CheckpointBytes int64    `json:"checkpoint_bytes"`
	TmpBytes        int64    `json:"tmp_bytes"`
	GPUIDs          []string `json:"gpu_ids"`
	VRAMBytes       int64    `json:"vram_bytes"`
}
type Assignment struct {
	ReceivedAt time.Time `json:"-"`
	Fence
	JobID               string         `json:"job_id"`
	StageID             string         `json:"stage_id"`
	TaskID              string         `json:"task_id"`
	VariantID           string         `json:"variant_id"`
	LeaseExpiresAt      string         `json:"lease_expires_at"`
	LeaseRemainingMS    int64          `json:"lease_remaining_ms"`
	HeartbeatIntervalMS int64          `json:"heartbeat_interval_ms"`
	TaskDeadlineAt      string         `json:"task_deadline_at"`
	TaskRemainingMS     int64          `json:"task_remaining_ms"`
	ManifestDigest      string         `json:"manifest_digest"`
	Adapter             Adapter        `json:"adapter"`
	ImageDigest         string         `json:"image_digest"`
	Platform            string         `json:"platform"`
	Argv                []string       `json:"argv"`
	WorkingDir          string         `json:"working_dir"`
	Inputs              []Artifact     `json:"inputs"`
	Outputs             []string       `json:"outputs"`
	ResourceLimits      ResourceLimits `json:"resource_limits"`
	NetworkPolicy       string         `json:"network_policy"`
	ResumeCheckpoint    *Artifact      `json:"resume_checkpoint"`
	ValidatorPolicy     map[string]any `json:"validator_policy"`
}
type Receipt struct {
	AttemptID string `json:"attempt_id"`
	State     string `json:"state"`
	ReceiptID string `json:"receipt_id"`
}
type Renewal struct {
	LeaseRemainingMS int64  `json:"lease_remaining_ms"`
	TaskRemainingMS  int64  `json:"task_remaining_ms"`
	Control          string `json:"control,omitempty"`
}
type Runtime struct {
	Type    string `json:"type"`
	Version string `json:"version"`
	Healthy bool   `json:"healthy"`
}
type GPU struct {
	ID           string `json:"id"`
	Vendor       string `json:"vendor"`
	VRAMBytes    int64  `json:"vram_bytes"`
	Driver       string `json:"driver"`
	CUDAVerified bool   `json:"cuda_verified"`
}
type Capabilities struct {
	OS                   string         `json:"os"`
	Arch                 string         `json:"arch"`
	CPULogical           int            `json:"cpu_logical"`
	MemoryAvailableBytes int64          `json:"memory_available_bytes"`
	DiskAvailableBytes   int64          `json:"disk_available_bytes"`
	Runtime              Runtime        `json:"runtime"`
	GPUs                 []GPU          `json:"gpus"`
	PowerState           string         `json:"power_state"`
	OfferedBudget        ResourceLimits `json:"offered_budget"`
	PreflightRevision    string         `json:"preflight_revision"`
	Diagnostics          []string       `json:"diagnostics,omitempty"`
}

var DigestPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var ImagePattern = regexp.MustCompile(`^(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@)?sha256:[a-f0-9]{64}$`)
var IDPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$`)

func (a Assignment) Validate() error {
	for _, id := range []string{a.AttemptID, a.JobID, a.StageID, a.TaskID, a.VariantID} {
		if !IDPattern.MatchString(id) {
			return errors.New("invalid assignment identity")
		}
	}
	if a.Generation < 1 || a.LeaseToken == "" || a.LeaseRemainingMS < 100 || a.HeartbeatIntervalMS < 10 || a.HeartbeatIntervalMS >= a.LeaseRemainingMS || a.TaskRemainingMS <= 0 {
		return errors.New("invalid lease or runtime bounds")
	}
	if !DigestPattern.MatchString(a.ManifestDigest) || !ImagePattern.MatchString(a.ImageDigest) {
		return errors.New("assignment requires immutable SHA-256 manifest/image")
	}
	if a.NetworkPolicy != "none" {
		return errors.New("only network_policy=none is approved")
	}
	if a.Platform != "linux/amd64" && a.Platform != "linux/arm64" {
		return errors.New("unsupported execution platform")
	}
	if a.WorkingDir != "/workspace" && !strings.HasPrefix(a.WorkingDir, "/workspace/") {
		return errors.New("working_dir must be inside /workspace")
	}
	if a.WorkingDir != "/workspace" {
		if err := packaging.SafePath(strings.TrimPrefix(a.WorkingDir, "/workspace/")); err != nil {
			return err
		}
	}
	if len(a.Argv) == 0 || len(a.Argv) > 1024 {
		return errors.New("argv is required")
	}
	total := 0
	for _, arg := range a.Argv {
		total += len(arg)
		if strings.ContainsRune(arg, 0) {
			return errors.New("NUL in argv")
		}
	}
	if total > 65536 {
		return errors.New("argv too large")
	}
	r := a.ResourceLimits
	if r.CPUMillis <= 0 || r.MemoryBytes < 32<<20 || r.PIDs < 1 || r.PIDs > 4096 || r.WorkspaceBytes <= 0 || r.OutputBytes <= 0 || r.CheckpointBytes <= 0 || r.TmpBytes <= 0 {
		return errors.New("missing or invalid resource limits")
	}
	seen := map[string]bool{}
	for _, in := range a.Inputs {
		if !IDPattern.MatchString(in.ArtifactID) || !DigestPattern.MatchString(in.Digest) || in.SizeBytes < 0 {
			return errors.New("invalid input artifact")
		}
		if err := packaging.SafePath(in.Path); err != nil {
			return err
		}
		key := strings.ToLower(in.Path)
		if seen[key] {
			return errors.New("duplicate input")
		}
		seen[key] = true
	}
	for _, out := range a.Outputs {
		if err := packaging.SafePath(out); err != nil {
			return err
		}
	}
	if a.Adapter.ID == "" || a.Adapter.Version == "" {
		return fmt.Errorf("adapter identity is required")
	}
	return nil
}
