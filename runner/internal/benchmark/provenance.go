package benchmark

import (
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"campus/runner/internal/worker"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func combinedInputs(manifest Manifest) []LocalInput {
	inputs := append([]LocalInput(nil), manifest.Inputs...)
	if manifest.Validator != nil {
		inputs = append(inputs, manifest.Validator.Inputs...)
	}
	return inputs
}

func trustedAdapterPaths(dir string, manifest Manifest, base string) (containers.Paths, error) {
	for _, input := range combinedInputs(manifest) {
		if input.Path == "campus-verify-request.json" || input.Path == "benchmark-results" || strings.HasPrefix(input.Path, "benchmark-results/") {
			return containers.Paths{}, errors.New("trusted adapter input uses a reserved path")
		}
	}
	for _, input := range manifest.Validator.Inputs {
		if input.Path == "project.zip" || input.Kind == "project" {
			return containers.Paths{}, errors.New("validator-only inputs cannot replace the compute project")
		}
	}
	return prepare(dir, combinedInputs(manifest), base, manifest.Assignment.ResourceLimits.WorkspaceBytes)
}

func inspectProvenance(ctx context.Context, runtime worker.Runtime, stateDir string, manifest *Manifest, base string) (map[string]any, error) {
	dir, err := os.MkdirTemp(stateDir, "provenance-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	p, err := trustedAdapterPaths(filepath.Join(dir, "inspect"), *manifest, base)
	if err != nil {
		return nil, err
	}
	result, err := invokeAdapter(ctx, runtime, *manifest, p, "validate_input", map[string]any{"manifest": manifest.Validator.Manifest})
	if err != nil {
		return nil, err
	}
	if valid, ok := result["valid"].(bool); !ok || !valid {
		return nil, errors.New("registered adapter did not validate the prepared benchmark input")
	}
	if err = bindProvenance(manifest, result, true); err != nil {
		return nil, err
	}
	return result, nil
}

// Formal labels come from the immutable registered adapter, then remain frozen
// through output verification. A caller-provided digest is a claim to check.
func bindProvenance(manifest *Manifest, result map[string]any, deriveModel bool) error {
	for _, claim := range []struct{ field, want string }{{"manifest_digest", manifest.Assignment.ManifestDigest}, {"adapter_digest", manifest.AdapterDigest}} {
		actual, ok := result[claim.field].(string)
		if !ok || !client.DigestPattern.MatchString(actual) || actual != claim.want {
			return fmt.Errorf("registered validator %s provenance is missing or differs from the recorded claim", claim.field)
		}
	}
	if result["adapter_digest_kind"] != "trusted_source_lf_sha256" {
		return errors.New("registered validator adapter digest does not bind trusted immutable source")
	}
	expectedKind := map[string]string{"embeddings.e5": "pretrained_transformer", "training.image-classifier": "registered_training_recipe", "generic.project": "none"}[manifest.Assignment.Adapter.ID]
	if expectedKind == "" || result["model_kind"] != expectedKind {
		return errors.New("registered validator model provenance kind is missing or inconsistent")
	}
	model, ok := result["model_digest"].(string)
	if !ok || (expectedKind != "none" && !client.DigestPattern.MatchString(model)) || (expectedKind == "none" && model != "") {
		return errors.New("registered validator model_digest provenance is missing or invalid")
	}
	if deriveModel && manifest.ModelDigest == "" {
		manifest.ModelDigest = model
	}
	if model != manifest.ModelDigest {
		return errors.New("registered validator model_digest differs from the recorded claim")
	}
	return nil
}
