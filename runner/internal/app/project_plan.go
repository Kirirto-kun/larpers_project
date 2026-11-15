package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/packaging"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
)

func equalProjectJSON(a, b any) bool {
	left, err := json.Marshal(a)
	if err != nil {
		return false
	}
	right, err := json.Marshal(b)
	if err != nil {
		return false
	}
	var x, y any
	if json.Unmarshal(left, &x) != nil || json.Unmarshal(right, &y) != nil {
		return false
	}
	return reflect.DeepEqual(x, y)
}
func intOr(value *int64, fallback int64) int64 {
	if value == nil {
		return fallback
	}
	return *value
}

// validateProjectPlan checks every executable choice independently of the
// coordinator's planner. Explanations may vary; declared execution cannot.
func validateProjectPlan(analysis preview, state projectSubmission) error {
	var plan generated.JobPlan
	if !validRevision(analysis.Revision) || json.Unmarshal(analysis.Plan, &plan) != nil {
		return errors.New("missing or malformed project plan/revision")
	}
	var revision int64
	json.Unmarshal(analysis.Revision, &revision)
	c := state.Request.ProjectConfig
	kind := "dag"
	if len(c.Stages) == 1 {
		kind = "single"
		if c.Stages[0].Kind == "map" {
			kind = "map"
		}
	}
	if plan.PlanRevision != revision || plan.ProjectDigest == nil || *plan.ProjectDigest != state.ProjectDigest || !client.DigestPattern.MatchString(plan.InputManifestDigest) || plan.ExecutionKind != kind || len(plan.Stages) != len(c.Stages) || len(plan.Questions) != 0 {
		return errors.New("plan revision, project bytes, execution shape or stages differ")
	}
	if plan.SchemaVersion != nil && *plan.SchemaVersion != "1" {
		return errors.New("unsupported project plan schema")
	}
	if intOr(plan.RetryPolicy.MaxAttempts, 3) != 3 || intOr(plan.RetryPolicy.BackoffMs, 1000) != 1000 {
		return errors.New("project retry policy differs")
	}
	for i, contract := range c.Stages {
		stage := plan.Stages[i]
		if stage.StageID != contract.ID || stage.Adapter.ID != "campus.project-"+contract.Kind || stage.Adapter.Version != "1" || !equalProjectJSON(stage.Argv, contract.Command) || !equalProjectJSON(nonNilStrings(stage.DependsOn), contract.DependsOn) || stage.WorkingDir != "/workspace" || stage.NetworkPolicy == nil || *stage.NetworkPolicy != "none" || stage.TaskDeadlineMs != contract.TimeoutSeconds*1000 || stage.ValidationPolicy != "structural" || stage.ResumePolicy != "restart" {
			return errors.New("stage command, adapter, dependencies or execution policy differs")
		}
		if len(stage.Parameters) != 1 || !equalProjectJSON(stage.Parameters["project_stage"], contract) {
			return errors.New("stage parameters differ from the declared project contract")
		}
		if len(stage.Outputs) != len(contract.Outputs) {
			return errors.New("stage outputs differ")
		}
		for j, out := range contract.Outputs {
			actual := stage.Outputs[j]
			if actual.Path != out.Path || actual.MaxBytes != out.MaxBytes || (actual.Required == nil || *actual.Required) != out.Required {
				return errors.New("stage output path, size or required flag differs")
			}
		}
		expected := map[binding]bool{{ArtifactID: state.Request.ProjectArtifactID, MountPath: "/workspace", Archive: true}: true}
		for _, input := range state.Request.Inputs {
			for _, pattern := range contract.Inputs {
				if packaging.MatchGlob(pattern, strings.TrimPrefix(input.MountPath, "/inputs/")) {
					expected[binding{ArtifactID: input.ArtifactID, MountPath: input.MountPath, Archive: false}] = true
					break
				}
			}
		}
		if len(stage.Bindings) != len(expected) {
			return errors.New("stage input subset differs")
		}
		for _, actual := range stage.Bindings {
			key := binding{ArtifactID: actual.ArtifactID, MountPath: actual.MountPath, Archive: actual.Archive != nil && *actual.Archive}
			if !expected[key] {
				return errors.New("stage input bindings differ")
			}
			delete(expected, key)
		}
		if len(stage.Variants) == 0 || len(stage.Variants) > 8 {
			return errors.New("stage has no bounded execution variants")
		}
		variants := map[string]bool{}
		accelerators := map[string]bool{}
		for _, v := range stage.Variants {
			if variants[v.VariantID] || v.VariantID == "" || v.Accelerator != "cpu" && v.Accelerator != "cuda" || contract.Accelerator != "auto" && contract.Accelerator != v.Accelerator || !strings.Contains(v.ImageDigest, "@sha256:") || v.Platform != "linux/amd64" && v.Platform != "linux/arm64" {
				return errors.New("stage execution accelerator, image or platform differs")
			}
			variants[v.VariantID] = true
			accelerators[v.Accelerator] = true
			r := v.ResourceRequirements
			gpu, vram := int64(0), int64(0)
			if v.Accelerator == "cuda" {
				gpu = 1
				vram = contract.Resources.VRAMBytes
				if vram == 0 {
					return errors.New("CUDA variant has no declared VRAM")
				}
			}
			if r.CPUMillis != contract.Resources.CPUMillis || r.MemoryBytes != contract.Resources.MemoryBytes || r.DiskBytes != contract.Resources.DiskBytes || intOr(r.GPUSlots, 0) != gpu || intOr(r.VRAMBytes, 0) != vram {
				return errors.New("stage resource requirements differ")
			}
		}
		if contract.Accelerator == "auto" && (!accelerators["cpu"] || contract.Resources.VRAMBytes > 0 && !accelerators["cuda"]) {
			return errors.New("auto plan dropped a declared accelerator")
		}
	}
	return nil
}
func nonNilStrings(values []string) []string {
	if values == nil {
		return []string{}
	}
	return values
}
