package benchmark

import (
	"campus/runner/internal/artifacts"
	"campus/runner/internal/client"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type LocalInput struct {
	client.Artifact
	Source string `json:"source"`
}

// Offline manifests are local file bindings, not server Artifact DTOs. Keep
// their archive kind/path stable when API artifact names change independently.
type localInputJSON struct {
	ArtifactID string `json:"artifact_id"`
	Digest     string `json:"digest"`
	SizeBytes  int64  `json:"size_bytes"`
	Path       string `json:"path"`
	Kind       string `json:"kind,omitempty"`
	Source     string `json:"source"`
}

func (in LocalInput) MarshalJSON() ([]byte, error) {
	return json.Marshal(localInputJSON{in.ArtifactID, in.Digest, in.SizeBytes, in.Path, in.Kind, in.Source})
}
func (in *LocalInput) UnmarshalJSON(raw []byte) error {
	var stored localInputJSON
	if err := json.Unmarshal(raw, &stored); err != nil {
		return err
	}
	in.Artifact = client.Artifact{ArtifactID: stored.ArtifactID, Digest: stored.Digest, SizeBytes: stored.SizeBytes, Path: stored.Path, Kind: stored.Kind}
	in.Source = stored.Source
	return nil
}

type Validator struct {
	Adapter     client.Adapter  `json:"adapter"`
	Manifest    json.RawMessage `json:"manifest"`
	Policy      map[string]any  `json:"policy"`
	OutputPaths []string        `json:"output_paths"`
	Inputs      []LocalInput    `json:"inputs"`
}
type Manifest struct {
	SchemaVersion string            `json:"schema_version"`
	Assignment    client.Assignment `json:"assignment"`
	Inputs        []LocalInput      `json:"inputs"`
	Validator     *Validator        `json:"validator,omitempty"`
	ModelDigest   string            `json:"model_digest,omitempty"`
	AdapterDigest string            `json:"adapter_digest"`
	Batch         int               `json:"batch"`
	Concurrency   int               `json:"concurrency"`
}

func registered(adapter client.Adapter) bool {
	if adapter.Version != "1" {
		return false
	}
	return adapter.ID == "embeddings.e5" || adapter.ID == "training.image-classifier" || adapter.ID == "generic.project"
}
func loadManifest(path string) (Manifest, string, error) {
	var manifest Manifest
	f, err := os.Open(path)
	if err != nil {
		return manifest, "", err
	}
	defer f.Close()
	raw, err := io.ReadAll(io.LimitReader(f, 8<<20+1))
	if err != nil {
		return manifest, "", err
	}
	if len(raw) > 8<<20 {
		return manifest, "", errors.New("local benchmark manifest exceeds 8 MiB")
	}
	if err = json.Unmarshal(raw, &manifest); err != nil {
		return manifest, "", err
	}
	if manifest.SchemaVersion != "1" || !client.DigestPattern.MatchString(manifest.AdapterDigest) || (manifest.ModelDigest != "" && !client.DigestPattern.MatchString(manifest.ModelDigest)) {
		return manifest, "", errors.New("benchmark requires schema_version=1 and SHA-256 adapter/model digests")
	}
	if !registered(manifest.Assignment.Adapter) {
		return manifest, "", errors.New("benchmark adapter id/version is not registered")
	}
	if manifest.Assignment.ResumeCheckpoint != nil {
		return manifest, "", errors.New("standalone baseline starts fresh; resume_checkpoint is not supported")
	}
	if manifest.Assignment.TaskRemainingMS < 1000 || manifest.Assignment.TaskRemainingMS > 86400000 {
		return manifest, "", errors.New("local task_remaining_ms must be between 1000 and 86400000")
	}
	if manifest.Batch <= 0 || manifest.Batch > 1000000 || manifest.Concurrency != 1 {
		return manifest, "", errors.New("local baseline requires positive batch and concurrency=1; concurrent baselines are not implemented")
	}
	manifest.Assignment.AttemptID = "local-" + session.UUID()
	manifest.Assignment.JobID = "local-job"
	manifest.Assignment.StageID = "local-stage"
	manifest.Assignment.TaskID = "local-task"
	manifest.Assignment.VariantID = "local-variant"
	manifest.Assignment.Generation = 1
	manifest.Assignment.LeaseToken = "local-no-network"
	manifest.Assignment.LeaseRemainingMS = manifest.Assignment.TaskRemainingMS
	manifest.Assignment.HeartbeatIntervalMS = 250
	manifest.Assignment.Inputs = nil
	for _, input := range manifest.Inputs {
		manifest.Assignment.Inputs = append(manifest.Assignment.Inputs, input.Artifact)
	}
	if err = manifest.Assignment.Validate(); err != nil {
		return manifest, "", err
	}
	if manifest.Validator != nil {
		v := manifest.Validator
		if !registered(v.Adapter) || v.Adapter != manifest.Assignment.Adapter || len(v.Manifest) == 0 || len(v.OutputPaths) > 1024 {
			return manifest, "", errors.New("validator must use the same registered adapter/version and a prepared manifest")
		}
		for _, output := range v.OutputPaths {
			if err = packaging.SafePath(output); err != nil {
				return manifest, "", err
			}
			if !strings.HasPrefix(output, "outputs/") && !strings.HasPrefix(output, "workspace/") {
				return manifest, "", errors.New("validator output_paths must refer to outputs/ or workspace/")
			}
		}
	}
	hash := sha256.Sum256(raw)
	return manifest, hex.EncodeToString(hash[:]), nil
}

func verifyInputs(inputs []LocalInput, base string) error {
	if len(inputs) > 10000 {
		return errors.New("local input file quota exceeded")
	}
	var total int64
	seen := map[string]bool{}
	dataset := false
	for _, input := range inputs {
		if err := packaging.SafePath(input.Path); err != nil {
			return err
		}
		if input.Kind == "project" && input.Path != "project.zip" {
			return errors.New("local project input must have path project.zip")
		}
		if input.Kind == "dataset" {
			if dataset {
				return errors.New("only one local /data binding is supported")
			}
			dataset = true
		}
		key := strings.ToLower(input.Path)
		if seen[key] {
			return errors.New("colliding local input paths")
		}
		seen[key] = true
		if !client.DigestPattern.MatchString(input.Digest) || input.SizeBytes < 0 || input.SizeBytes > (2<<30)-total {
			return errors.New("invalid local input descriptor or 2 GiB byte quota exceeded")
		}
		total += input.SizeBytes
		source := input.Source
		if !filepath.IsAbs(source) {
			source = filepath.Join(base, source)
		}
		if err := regularPath(source); err != nil {
			return err
		}
		digest, size, err := artifacts.HashFile(source)
		if err != nil {
			return err
		}
		if digest != input.Digest || size != input.SizeBytes {
			return fmt.Errorf("local input checksum/size mismatch: %s", input.Path)
		}
	}
	for name := range seen {
		for parent := filepath.ToSlash(filepath.Dir(filepath.FromSlash(name))); parent != "."; parent = filepath.ToSlash(filepath.Dir(filepath.FromSlash(parent))) {
			if seen[parent] {
				return errors.New("local input file/directory path collision")
			}
		}
	}
	return nil
}

func regularPath(path string) error {
	st, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0 {
		return errors.New("local input/output path must be a regular non-symlink file")
	}
	for parent := filepath.Dir(path); ; parent = filepath.Dir(parent) {
		st, err = os.Lstat(parent)
		if err != nil {
			return err
		}
		if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
			return errors.New("local path contains a symlink or non-directory parent")
		}
		if filepath.Dir(parent) == parent {
			return nil
		}
	}
}

func copyInput(input LocalInput, base, destination string) error {
	source := input.Source
	if !filepath.IsAbs(source) {
		source = filepath.Join(base, source)
	}
	if err := regularPath(source); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return err
	}
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	hash := sha256.New()
	n, err := io.Copy(io.MultiWriter(out, hash), io.LimitReader(in, input.SizeBytes+1))
	closeErr := out.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if n != input.SizeBytes || hex.EncodeToString(hash.Sum(nil)) != input.Digest {
		return errors.New("local input changed while snapshotting")
	}
	return nil
}
