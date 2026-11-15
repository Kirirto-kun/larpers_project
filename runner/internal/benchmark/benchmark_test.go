package benchmark

import (
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func localManifest(t *testing.T) string {
	t.Helper()
	m := Manifest{SchemaVersion: "1", AdapterDigest: strings.Repeat("d", 64), Batch: 1, Concurrency: 1, Assignment: client.Assignment{ManifestDigest: strings.Repeat("c", 64), Adapter: client.Adapter{ID: "generic.project", Version: "1"}, ImageDigest: "sha256:" + strings.Repeat("a", 64), Platform: "linux/amd64", Argv: []string{"python", "train.py"}, WorkingDir: "/workspace", TaskRemainingMS: 10000, NetworkPolicy: "none", ResourceLimits: client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 128 << 20, PIDs: 64, WorkspaceBytes: 1 << 20, OutputBytes: 1 << 20, CheckpointBytes: 1 << 20, TmpBytes: 1 << 20}}}
	b, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "manifest.json")
	if err = os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

// The external runtime double simulates a fast protected guard at its filesystem
// boundary. These are wiring/timing tests, not evidence of real Docker speed or
// ML correctness; production records are never generated with this runtime.
type fastGuard struct {
	paths            containers.Paths
	assignment       client.Assignment
	provenance       map[string]any
	verifyProvenance map[string]any
	eligible         bool
	computeStarts    int
}

func (f *fastGuard) VerifyImage(context.Context, string, string) error { return nil }
func (f *fastGuard) Create(_ context.Context, a client.Assignment, p containers.Paths) (string, error) {
	f.paths = p
	f.assignment = a
	return "local-container", nil
}
func (f *fastGuard) Start(context.Context, string) error {
	return session.AtomicJSON(filepath.Join(f.paths.State, "challenge.json"), map[string]any{"challenge": "unit-test-nonce", "accepted_sequence": 0})
}
func (f *fastGuard) Wait(ctx context.Context, _ string) error {
	for {
		if _, err := os.Stat(filepath.Join(f.paths.Control, "lease.json")); err == nil {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Millisecond):
		}
	}
	if err := session.AtomicJSON(filepath.Join(f.paths.State, "challenge.json"), map[string]any{"challenge": "unit-test-nonce", "accepted_sequence": 1}); err != nil {
		return err
	}
	name, contents := "outputs/model.pt", []byte("real bytes")
	if len(f.assignment.Argv) > 1 && f.assignment.Argv[1] == "-I" {
		name = "outputs/campus-validation.json"
		var request struct {
			Operation string `json:"operation"`
		}
		raw, err := os.ReadFile(filepath.Join(f.paths.Inputs, "campus-verify-request.json"))
		if err != nil {
			return err
		}
		if err = json.Unmarshal(raw, &request); err != nil {
			return err
		}
		provenance := f.provenance
		if provenance == nil {
			provenance = map[string]any{"manifest_digest": strings.Repeat("c", 64), "adapter_digest": strings.Repeat("d", 64), "adapter_digest_kind": "trusted_source_lf_sha256", "model_digest": "", "model_kind": "none"}
		}
		if request.Operation == "verify" && f.verifyProvenance != nil {
			provenance = f.verifyProvenance
		}
		result := map[string]any{}
		for k, v := range provenance {
			result[k] = v
		}
		if request.Operation == "validate_input" {
			result["valid"] = true
		} else {
			result["accepted"] = true
			result["quality"] = "structural_only"
			result["benchmark_eligible"] = false
			result["checked_items"] = 0
			if f.eligible {
				result["quality"] = "verified"
				result["benchmark_eligible"] = true
				result["checked_items"] = 4
			}
		}
		contents, err = json.Marshal(map[string]any{"schema_version": "1", "result": result, "error": nil})
		if err != nil {
			return err
		}
	} else {
		f.computeStarts++
	}
	path := filepath.Join(f.paths.Export, "results", filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	if err := os.WriteFile(path, contents, 0600); err != nil {
		return err
	}
	sum := sha256.Sum256(contents)
	return session.AtomicJSON(filepath.Join(f.paths.State, "result.json"), map[string]any{"exit_code": 0, "reason": nil, "result_manifest": map[string]any{"files": []packaging.File{{Path: name, Digest: hex.EncodeToString(sum[:]), SizeBytes: int64(len(contents))}}}})
}

func formalManifest(t *testing.T, model string) string {
	t.Helper()
	path := localManifest(t)
	var manifest Manifest
	raw, _ := os.ReadFile(path)
	json.Unmarshal(raw, &manifest)
	manifest.Assignment.Adapter = client.Adapter{ID: "training.image-classifier", Version: "1"}
	manifest.ModelDigest = model
	manifest.Validator = &Validator{Adapter: manifest.Assignment.Adapter, Manifest: json.RawMessage(`{"schema_version":"1","kind":"training","recipe":"fixture"}`)}
	raw, _ = json.Marshal(manifest)
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func formalProvenance() map[string]any {
	return map[string]any{"manifest_digest": strings.Repeat("c", 64), "adapter_digest": strings.Repeat("d", 64), "adapter_digest_kind": "trusted_source_lf_sha256", "model_digest": strings.Repeat("e", 64), "model_kind": "registered_training_recipe"}
}

func TestEligibleBaselineRejectsStaleManifestModelAndAdapterBeforeCompute(t *testing.T) {
	for _, field := range []string{"manifest", "model", "adapter", "missing_manifest", "missing_model", "missing_adapter"} {
		t.Run(field, func(t *testing.T) {
			path := formalManifest(t, strings.Repeat("e", 64))
			var manifest Manifest
			raw, _ := os.ReadFile(path)
			json.Unmarshal(raw, &manifest)
			provenance := formalProvenance()
			switch field {
			case "manifest":
				manifest.Assignment.ManifestDigest = strings.Repeat("f", 64)
			case "model":
				manifest.ModelDigest = strings.Repeat("f", 64)
			case "adapter":
				manifest.AdapterDigest = strings.Repeat("f", 64)
			default:
				delete(provenance, strings.TrimPrefix(field, "missing_")+"_digest")
			}
			raw, _ = json.Marshal(manifest)
			os.WriteFile(path, raw, 0600)
			runtime := &fastGuard{provenance: provenance, eligible: true}
			record, err := Run(context.Background(), Options{ManifestPath: path, Repetitions: 1, StateDir: t.TempDir(), AllowedImages: map[string]string{"sha256:" + strings.Repeat("a", 64): "linux/amd64"}, Identity: session.Identity{InstallationID: "local-1"}, Runtime: runtime})
			if err == nil || len(record.Runs) != 0 || runtime.computeStarts != 0 {
				t.Fatalf("stale/missing %s provenance reached eligible compute: runs=%d computes=%d err=%v", field, len(record.Runs), runtime.computeStarts, err)
			}
		})
	}
}

func TestEligibleBaselineRejectsVerifierProvenanceMismatch(t *testing.T) {
	for _, field := range []string{"manifest_digest", "model_digest", "adapter_digest"} {
		t.Run(field, func(t *testing.T) {
			path := formalManifest(t, strings.Repeat("e", 64))
			altered := formalProvenance()
			altered[field] = strings.Repeat("f", 64)
			runtime := &fastGuard{provenance: formalProvenance(), verifyProvenance: altered, eligible: true}
			record, err := Run(context.Background(), Options{ManifestPath: path, Repetitions: 1, StateDir: t.TempDir(), AllowedImages: map[string]string{"sha256:" + strings.Repeat("a", 64): "linux/amd64"}, Identity: session.Identity{InstallationID: "local-1"}, Runtime: runtime})
			if err == nil || len(record.Runs) != 0 {
				t.Fatalf("verifier changed %s on eligible record: %#v %v", field, record, err)
			}
		})
	}
}

func TestOmittedTrainingModelDigestIsDerivedFromTrustedPreflight(t *testing.T) {
	path := formalManifest(t, "")
	runtime := &fastGuard{provenance: formalProvenance(), eligible: true}
	record, err := Run(context.Background(), Options{ManifestPath: path, Repetitions: 1, StateDir: t.TempDir(), AllowedImages: map[string]string{"sha256:" + strings.Repeat("a", 64): "linux/amd64"}, Identity: session.Identity{InstallationID: "local-1"}, Runtime: runtime})
	if err != nil || record.ModelDigest != strings.Repeat("e", 64) || len(record.Runs) != 1 || !record.Runs[0].BenchmarkEligible || runtime.computeStarts != 1 {
		t.Fatalf("training model provenance not derived/bound: %#v computes=%d err=%v", record, runtime.computeStarts, err)
	}
}
func (f *fastGuard) Stop(context.Context, string) error   { return nil }
func (f *fastGuard) Remove(context.Context, string) error { return nil }
func (f *fastGuard) Logs(ctx context.Context, _ string, _ io.Writer) error {
	<-ctx.Done()
	return ctx.Err()
}
func (f *fastGuard) Stats(context.Context, string) (map[string]any, error) {
	return map[string]any{}, nil
}

func TestFastWorkloadExitAfterAcceptedGrantIsACompletedMeasurement(t *testing.T) {
	path := localManifest(t)
	record, err := Run(context.Background(), Options{ManifestPath: path, Repetitions: 2, StateDir: t.TempDir(), AllowedImages: map[string]string{"sha256:" + strings.Repeat("a", 64): "linux/amd64"}, Identity: session.Identity{InstallationID: "local-1", NodeID: "node-1", CredentialID: "cred-1", Credential: "secret"}, Runtime: &fastGuard{}})
	if err != nil {
		t.Fatal(err)
	}
	if len(record.Runs) != 2 || !VerifyOrigin(record, "secret") {
		t.Fatalf("missing runs/origin: %#v", record)
	}
	for _, run := range record.Runs {
		if run.DurationMS <= 0 || run.Quality != "unverified" || run.BenchmarkEligible || len(run.Outputs) != 1 {
			t.Fatalf("invented quality or missing real result: %#v", run)
		}
	}
}

func TestStructuralValidatorVerdictCannotBecomeCorrectnessClaim(t *testing.T) {
	path := localManifest(t)
	var m Manifest
	raw, _ := os.ReadFile(path)
	json.Unmarshal(raw, &m)
	m.Validator = &Validator{Adapter: m.Assignment.Adapter, Manifest: json.RawMessage(`{"schema_version":"1","kind":"generic","command":["python","train.py"]}`)}
	raw, _ = json.Marshal(m)
	os.WriteFile(path, raw, 0600)
	record, err := Run(context.Background(), Options{ManifestPath: path, Repetitions: 1, StateDir: t.TempDir(), AllowedImages: map[string]string{"sha256:" + strings.Repeat("a", 64): "linux/amd64"}, Identity: session.Identity{InstallationID: "local-1"}, Runtime: &fastGuard{}})
	if err != nil {
		t.Fatal(err)
	}
	if record.Runs[0].Quality != "structural_only" || record.Runs[0].BenchmarkEligible || record.Runs[0].ValidationDurationMS <= 0 {
		t.Fatalf("structural checks were mislabeled: %#v", record.Runs[0])
	}
}

func TestUnapprovedImageCannotStartLocalBenchmark(t *testing.T) {
	path := localManifest(t)
	_, err := Run(context.Background(), Options{ManifestPath: path, Repetitions: 1, StateDir: t.TempDir(), Identity: session.Identity{InstallationID: "local-1"}})
	if err == nil || !strings.Contains(err.Error(), "approved") {
		t.Fatalf("unapproved image accepted: %v", err)
	}
}

func TestMissingDockerFailsWithoutInventingRecord(t *testing.T) {
	path := localManifest(t)
	t.Setenv("PATH", t.TempDir())
	record, err := Run(context.Background(), Options{ManifestPath: path, Repetitions: 1, StateDir: t.TempDir(), AllowedImages: map[string]string{"sha256:" + strings.Repeat("a", 64): "linux/amd64"}, Identity: session.Identity{InstallationID: "local-1"}})
	if err == nil || len(record.Runs) != 0 {
		t.Fatalf("missing runtime invented measurements: %#v %v", record, err)
	}
}

func TestOriginSignatureRejectsTamperedTimingAndWrongCredential(t *testing.T) {
	record := StandaloneRecord{SchemaVersion: "1", DeviceID: "node-1", Runs: []Measurement{{DurationMS: 123, Quality: "unverified"}}}
	identity := session.Identity{NodeID: "node-1", CredentialID: "cred-1", Credential: "registered-secret"}
	if err := sign(&record, identity); err != nil {
		t.Fatal(err)
	}
	if !VerifyOrigin(record, "registered-secret") || VerifyOrigin(record, "other-secret") {
		t.Fatal("origin signature does not bind credential")
	}
	record.Runs[0].DurationMS++
	if VerifyOrigin(record, "registered-secret") {
		t.Fatal("modified timing accepted")
	}
	record.Runs[0].DurationMS--
	record.Signature.CredentialID = "another-credential"
	if VerifyOrigin(record, "registered-secret") {
		t.Fatal("modified signer identity accepted")
	}
}

func TestLocalInputsRequireMatchingDigestsAndSafeBindings(t *testing.T) {
	for _, tc := range []struct{ name, path, digest string }{{"traversal", "../project.zip", strings.Repeat("a", 64)}, {"corrupt", "project.zip", strings.Repeat("a", 64)}} {
		t.Run(tc.name, func(t *testing.T) {
			file := localManifest(t)
			var m Manifest
			b, _ := os.ReadFile(file)
			json.Unmarshal(b, &m)
			source := filepath.Join(filepath.Dir(file), "prepared.zip")
			os.WriteFile(source, []byte("bytes"), 0600)
			m.Inputs = []LocalInput{{Artifact: client.Artifact{ArtifactID: "input-1", Path: tc.path, Digest: tc.digest, SizeBytes: 5, Kind: "project"}, Source: source}}
			b, _ = json.Marshal(m)
			os.WriteFile(file, b, 0600)
			_, err := Run(context.Background(), Options{ManifestPath: file, Repetitions: 1, StateDir: t.TempDir(), AllowedImages: map[string]string{"sha256:" + strings.Repeat("a", 64): "linux/amd64"}, Identity: session.Identity{InstallationID: "local-1"}})
			if err == nil || !(strings.Contains(err.Error(), "path") || strings.Contains(err.Error(), "checksum")) {
				t.Fatalf("unsafe/corrupt local input reached runtime: %v", err)
			}
		})
	}
}
