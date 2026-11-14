package app

import (
	"campus/runner/internal/generated"
	"campus/runner/internal/packaging"
	"encoding/json"
	"errors"
	"reflect"
	"unicode/utf8"
)

func commandPrompt(intent runRequest) (string, error) {
	raw, err := json.Marshal(intent)
	if err != nil {
		return "", err
	}
	prompt := "Prepare exactly one generic.project version 1 command stage for this explicit JSON command intent. Preserve every argv element literally, without a shell wrapper. Preserve working_dir, archive bindings and network_policy=none. All variants must use the requested accelerator (requires_gpu means cuda; otherwise cpu). Include every declared output as required; capture changed/new workspace files within the bounded output quota. Do not infer extra commands or substitute inputs. Ask one question if any execution detail is unresolved. Command intent: " + string(raw)
	if utf8.RuneCountInString(prompt) > 8000 {
		return "", usageError("command intent exceeds the planner's 8000-character prompt limit")
	}
	return prompt, nil
}

func validateCommandPlan(analysis preview, intent runRequest) error {
	var plan generated.JobPlan
	if err := json.Unmarshal(analysis.Plan, &plan); err != nil {
		return errors.New("missing or malformed command plan")
	}
	var revision int64
	if json.Unmarshal(analysis.Revision, &revision) != nil || plan.PlanRevision != revision || len(plan.Stages) != 1 || plan.ExecutionKind != "single" {
		return errors.New("expected one command stage at the accepted revision")
	}
	stage := plan.Stages[0]
	if stage.Adapter.ID != "generic.project" || stage.Adapter.Version != "1" || !reflect.DeepEqual(stage.Argv, intent.Argv) || stage.WorkingDir != intent.WorkingDir {
		return errors.New("argv, working directory or adapter differs")
	}
	if stage.NetworkPolicy != nil && *stage.NetworkPolicy != "none" {
		return errors.New("network policy differs")
	}
	if len(stage.Variants) == 0 {
		return errors.New("command plan has no execution variant")
	}
	accelerator := "cpu"
	if intent.RequiresGPU {
		accelerator = "cuda"
	}
	for _, variant := range stage.Variants {
		if variant.Accelerator != accelerator {
			return errors.New("accelerator requirement differs")
		}
	}
	if len(stage.Bindings) != len(intent.Bindings) {
		return errors.New("input bindings differ")
	}
	expected := map[binding]bool{}
	for _, item := range intent.Bindings {
		expected[item] = true
	}
	for _, item := range stage.Bindings {
		key := binding{ArtifactID: item.ArtifactID, MountPath: item.MountPath, Archive: item.Archive != nil && *item.Archive}
		if !expected[key] {
			return errors.New("input bindings differ")
		}
		delete(expected, key)
	}
	if len(stage.Outputs) == 0 {
		return errors.New("command plan declares no bounded outputs")
	}
	outputs := map[string]bool{}
	for _, output := range stage.Outputs {
		if err := packaging.SafePath(output.Path); err != nil || output.MaxBytes <= 0 {
			return errors.New("unsafe or unbounded output declaration")
		}
		outputs[output.Path] = output.Required == nil || *output.Required
	}
	for _, path := range intent.Outputs {
		if !outputs[path] {
			return errors.New("a declared output is missing or optional")
		}
	}
	return nil
}
