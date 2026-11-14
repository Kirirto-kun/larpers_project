package tests

import (
	"bytes"
	"campus/runner/internal/benchmark"
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"campus/runner/internal/leaseguard"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

type trainingFixture struct {
	Root         string
	Manifest     json.RawMessage
	ManifestPath string
	Inputs       []benchmark.LocalInput
}

// Generate disjoint, hash-distinct train/validation PNGs with Go only. The fixed
// red/blue fixture matches the registered recipe's small deterministic test.
func syntheticTraining(t *testing.T, profile preparedCPU) trainingFixture {
	t.Helper()
	root := t.TempDir()
	dataset := filepath.Join(root, "dataset")
	if err := os.Mkdir(dataset, 0700); err != nil {
		t.Fatal(err)
	}
	for _, split := range []struct {
		Name          string
		Count, Offset int
	}{{"train", 8, 0}, {"validation", 4, 30}} {
		var rows bytes.Buffer
		for i := 0; i < split.Count; i++ {
			label, base := "red", color.NRGBA{R: 230, A: 255}
			if i%2 == 1 {
				label = "blue"
				base = color.NRGBA{B: 230, A: 255}
			}
			pixels := image.NewNRGBA(image.Rect(0, 0, 16, 16))
			for y := 0; y < 16; y++ {
				for x := 0; x < 16; x++ {
					pixels.SetNRGBA(x, y, base)
				}
			}
			pixels.SetNRGBA(0, 0, color.NRGBA{R: uint8(i + split.Offset), G: 17, B: 93, A: 255})
			name := fmt.Sprintf("%s-%d.png", split.Name, i)
			path := filepath.Join(dataset, name)
			file, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
			if err != nil {
				t.Fatal(err)
			}
			err = png.Encode(file, pixels)
			closeErr := file.Close()
			if err != nil || closeErr != nil {
				t.Fatalf("encode actual PNG: %v %v", err, closeErr)
			}
			json.NewEncoder(&rows).Encode(map[string]string{"path": name, "label": label})
		}
		if err := os.WriteFile(filepath.Join(dataset, split.Name+".jsonl"), rows.Bytes(), 0600); err != nil {
			t.Fatal(err)
		}
	}
	manifest := map[string]any{"schema_version": "1", "kind": "training", "environment_digest": strings.TrimPrefix(profile.Image, "sha256:"), "dataset": map[string]any{"root": "/data", "train": "train.jsonl", "validation": "validation.jsonl", "classes": []string{"red", "blue"}}, "trainer": map[string]any{"seed": 23, "epochs": 3, "batch_size": 2, "learning_rate": 0.02, "image_size": 16, "checkpoint_every": 2}}
	raw, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, "workload.json")
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	var archive bytes.Buffer
	if _, err = packaging.PackageProject(dataset, packaging.Rules{MaxFiles: 32, MaxBytes: 1 << 20}, &archive); err != nil {
		t.Fatal(err)
	}
	zipPath := filepath.Join(root, "dataset.zip")
	if err = os.WriteFile(zipPath, archive.Bytes(), 0600); err != nil {
		t.Fatal(err)
	}
	return trainingFixture{Root: dataset, Manifest: raw, ManifestPath: path, Inputs: []benchmark.LocalInput{{Artifact: client.Artifact{ArtifactID: "training-data", Digest: digest(archive.Bytes()), SizeBytes: int64(archive.Len()), Kind: "dataset", Path: "data.zip"}, Source: zipPath}, {Artifact: client.Artifact{ArtifactID: "training-manifest", Digest: digest(raw), SizeBytes: int64(len(raw)), Kind: "input", Path: "workload.json"}, Source: path}}}
}

func readonlyMount(source, target string) string {
	var value strings.Builder
	writer := csv.NewWriter(&value)
	writer.Write([]string{"type=bind", "src=" + source, "dst=" + target, "readonly"})
	writer.Flush()
	return strings.TrimSuffix(value.String(), "\n")
}

// Ask the immutable image's trusted dispatch for metadata; never derive source
// identity from the host checkout or invoke host Python.
func trainingMetadata(t *testing.T, profile preparedCPU, fixture trainingFixture) map[string]any {
	t.Helper()
	dir := t.TempDir()
	request := map[string]any{"schema_version": "1", "adapter": client.Adapter{ID: "training.image-classifier", Version: "1"}, "operation": "validate_input", "payload": map[string]any{"manifest": fixture.Manifest}}
	if err := session.AtomicJSON(filepath.Join(dir, "request.json"), request); err != nil {
		t.Fatal(err)
	}
	paths := containers.Paths{Inputs: dir, Data: fixture.Root}
	defer containers.RestoreInputs(paths)
	if err := containers.PrepareInputs(paths); err != nil {
		t.Fatal(err)
	}
	installation := "metadata-" + session.UUID()
	name := "campus-" + installation
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	t.Cleanup(func() {
		cleanup, close := context.WithTimeout(context.Background(), 15*time.Second)
		defer close()
		(containers.Docker{InstallationID: installation}).Cleanup(cleanup)
	})
	code := `import json,pathlib,sys;sys.path.insert(0,'/opt/campus');from workloads.adapter import dispatch;print(json.dumps(dispatch(json.loads(pathlib.Path('/inputs/request.json').read_text())),allow_nan=False))`
	args := []string{"run", "--rm", "--name", name, "--label", "io.campus.installation=" + installation, "--pull=never", "--platform", profile.Platform, "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--user=65532:65532", "--cpus=1", "--memory=1g", "--pids-limit=128", "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m", "--mount", readonlyMount(dir, "/inputs"), "--mount", readonlyMount(fixture.Root, "/data"), "--entrypoint=/opt/campus/.venv/bin/python", profile.Image, "-I", "-c", code}
	output, err := exec.CommandContext(ctx, "docker", args...).CombinedOutput()
	if err != nil {
		t.Fatalf("actual trusted validate_input failed: %v\n%s", err, output)
	}
	var response struct {
		Schema string         `json:"schema_version"`
		Result map[string]any `json:"result"`
		Error  any            `json:"error"`
	}
	if err = json.Unmarshal(bytes.TrimSpace(output), &response); err != nil || response.Schema != "1" || response.Error != nil || response.Result["valid"] != true {
		t.Fatalf("trusted metadata failed: %v %s", err, output)
	}
	for _, field := range []string{"manifest_digest", "model_digest", "adapter_digest"} {
		if value, ok := response.Result[field].(string); !ok || !client.DigestPattern.MatchString(value) {
			t.Fatalf("actual trusted %s missing: %s", field, output)
		}
	}
	return response.Result
}

type trainingEvidence struct {
	Phase        string
	Paths        containers.Paths
	Assignment   client.Assignment
	Files        map[string][]byte
	Logs         string
	CaptureError error
}
type observedTrainingDocker struct {
	containers.Docker
	mu        sync.Mutex
	active    map[string]trainingEvidence
	completed []trainingEvidence
}

func (d *observedTrainingDocker) Create(ctx context.Context, a client.Assignment, p containers.Paths) (string, error) {
	name, err := d.Docker.Create(ctx, a, p)
	if err != nil {
		return name, err
	}
	phase := "compute"
	if raw, err := os.ReadFile(filepath.Join(p.Inputs, "campus-verify-request.json")); err == nil {
		var request struct {
			Operation string `json:"operation"`
		}
		json.Unmarshal(raw, &request)
		phase = request.Operation
	}
	d.mu.Lock()
	if d.active == nil {
		d.active = map[string]trainingEvidence{}
	}
	d.active[name] = trainingEvidence{Phase: phase, Paths: p, Assignment: a}
	d.mu.Unlock()
	return name, nil
}
func (d *observedTrainingDocker) Remove(ctx context.Context, name string) error {
	d.mu.Lock()
	evidence, ok := d.active[name]
	d.mu.Unlock()
	if ok {
		output, _ := exec.CommandContext(ctx, "docker", "logs", name).CombinedOutput()
		evidence.Logs = string(output)
		evidence.Files = map[string][]byte{}
		var total int
		evidence.CaptureError = filepath.WalkDir(evidence.Paths.Export, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.IsDir() {
				return nil
			}
			if !entry.Type().IsRegular() {
				return fmt.Errorf("unexpected export type %s", path)
			}
			st, err := entry.Info()
			if err != nil {
				return err
			}
			if st.Size() > 8<<20 || total > 32<<20 {
				return fmt.Errorf("test export evidence exceeds bound")
			}
			total += int(st.Size())
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			rel, err := filepath.Rel(evidence.Paths.Export, path)
			if err != nil {
				return err
			}
			evidence.Files[filepath.ToSlash(rel)] = data
			return nil
		})
		d.mu.Lock()
		d.completed = append(d.completed, evidence)
		delete(d.active, name)
		d.mu.Unlock()
	}
	return d.Docker.Remove(ctx, name)
}
func (d *observedTrainingDocker) evidence() []trainingEvidence {
	d.mu.Lock()
	defer d.mu.Unlock()
	return append([]trainingEvidence(nil), d.completed...)
}

func TestDockerRegisteredTrainingStandaloneBenchmark(t *testing.T) {
	profile := dockerCPU(t)
	fixture := syntheticTraining(t, profile)
	metadata := trainingMetadata(t, profile, fixture)
	a := assignment(profile)
	a.Adapter = client.Adapter{ID: "training.image-classifier", Version: "1"}
	a.ManifestDigest = metadata["manifest_digest"].(string)
	a.TaskRemainingMS = 120000
	a.Argv = []string{"python", "-m", "workloads.training.run", "--manifest", "/inputs/workload.json", "--device", "cpu"}
	a.Outputs = []string{}
	manifest := benchmark.Manifest{SchemaVersion: "1", Assignment: a, Inputs: fixture.Inputs, AdapterDigest: metadata["adapter_digest"].(string), Batch: 2, Concurrency: 1, Validator: &benchmark.Validator{Adapter: a.Adapter, Manifest: fixture.Manifest, Policy: map[string]any{"minimum_accuracy": 0.75, "maximum_loss": 0.8}, OutputPaths: []string{"outputs/result.json"}}}
	// Omitted model_digest must derive from trusted preflight before compute.
	file := filepath.Join(t.TempDir(), "local-training.json")
	if err := session.AtomicJSON(file, manifest); err != nil {
		t.Fatal(err)
	}
	installation := "trainingtest-" + session.UUID()
	observed := &observedTrainingDocker{Docker: containers.Docker{InstallationID: installation}}
	t.Cleanup(func() {
		cleanup, close := context.WithTimeout(context.Background(), 20*time.Second)
		defer close()
		observed.Docker.Cleanup(cleanup)
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	record, err := benchmark.Run(ctx, benchmark.Options{ManifestPath: file, Repetitions: 1, StateDir: t.TempDir(), AllowedImages: map[string]string{profile.Image: profile.Platform}, Identity: session.Identity{InstallationID: installation}, Runtime: observed})
	if err != nil {
		var logs strings.Builder
		for _, e := range observed.evidence() {
			fmt.Fprintf(&logs, "phase=%s capture=%v\n%s\n", e.Phase, e.CaptureError, e.Logs)
		}
		t.Fatalf("actual registered training benchmark failed: %v\n%s", err, &logs)
	}
	if record.ManifestDigest != metadata["manifest_digest"] || record.ModelDigest != metadata["model_digest"] || record.AdapterDigest != metadata["adapter_digest"] || record.ProvenanceDurationMS <= 0 || len(record.Runs) != 1 {
		t.Fatalf("real benchmark lost trusted provenance: %#v", record)
	}
	run := record.Runs[0]
	if run.Quality != "verified" || !run.BenchmarkEligible || run.Units != 4 || run.DurationMS <= 0 || run.ValidationDurationMS <= 0 {
		t.Fatalf("real independent training verifier failed: %#v", run)
	}
	metrics, ok := run.Validation["metrics"].(map[string]any)
	if !ok || metrics["accuracy"].(float64) < 0.75 || metrics["loss"].(float64) > 0.8 {
		t.Fatalf("real validation quality outside fixed policy: %#v", run.Validation)
	}
	var compute *trainingEvidence
	phases := map[string]int{}
	for _, evidence := range observed.evidence() {
		phases[evidence.Phase]++
		if evidence.CaptureError != nil {
			t.Fatalf("actual export capture failed: %v", evidence.CaptureError)
		}
		if evidence.Phase == "compute" {
			copy := evidence
			compute = &copy
		}
	}
	if phases["validate_input"] != 1 || phases["verify"] != 1 || phases["compute"] != 1 || compute == nil {
		t.Fatalf("independent real containers missing: %#v", phases)
	}
	weights := compute.Files["results/outputs/model.safetensors"]
	resultRaw := compute.Files["results/outputs/result.json"]
	var result struct {
		Status        string            `json:"status"`
		Step          int               `json:"step"`
		WeightsDigest string            `json:"weights_digest"`
		Metrics       map[string]any    `json:"metrics"`
		Binding       map[string]string `json:"binding"`
	}
	if err = json.Unmarshal(resultRaw, &result); err != nil || result.Status != "completed" || result.Step != 12 || len(weights) == 0 || digest(weights) != result.WeightsDigest {
		t.Fatalf("actual safe training weights/result missing: %v %s weights=%d", err, resultRaw, len(weights))
	}
	for _, field := range []string{"accuracy", "loss", "num_examples", "validation_digest"} {
		if result.Metrics[field] != metrics[field] {
			t.Fatalf("independent metric %s differs: claimed=%#v actual=%#v", field, result.Metrics[field], metrics[field])
		}
	}
	checkpoints := 0
	for name, ready := range compute.Files {
		if strings.HasPrefix(name, "checkpoints/") && strings.HasSuffix(name, "/READY") {
			checkpoints++
			base := strings.TrimSuffix(name, "READY")
			raw := compute.Files[base+"manifest.json"]
			if strings.TrimSpace(string(ready)) != digest(raw) || len(compute.Files[base+"state.json"]) == 0 || len(compute.Files[base+"tensors.safetensors"]) == 0 {
				t.Fatalf("actual checkpoint envelope/payload missing: %s", name)
			}
		}
	}
	if checkpoints < 1 {
		t.Fatal("real registered trainer exported no complete checkpoint")
	}
	t.Logf("actual verified training: weights=%s bytes=%d accuracy=%v loss=%v validated=%v checkpoints=%d manifest=%s model=%s adapter=%s provenance_ms=%.3f compute_ms=%.3f independent_validation_ms=%.3f", result.WeightsDigest, len(weights), metrics["accuracy"], metrics["loss"], metrics["num_examples"], checkpoints, record.ManifestDigest, record.ModelDigest, record.AdapterDigest, record.ProvenanceDurationMS, run.DurationMS, run.ValidationDurationMS)
}

type actualTrainingResult struct {
	Status         string            `json:"status"`
	Step           int               `json:"step"`
	WeightsDigest  string            `json:"weights_digest"`
	CheckpointPath string            `json:"checkpoint_path"`
	Metrics        map[string]any    `json:"metrics"`
	Binding        map[string]string `json:"binding"`
}

// Direct guarded execution exercises the trainer's real complete checkpoint
// envelope across fresh containers without scheduler or network involvement.
func guardedTraining(t *testing.T, profile preparedCPU, fixture trainingFixture, observed *observedTrainingDocker, stopAt int, resume map[string][]byte, binding map[string]string) (trainingEvidence, actualTrainingResult) {
	t.Helper()
	var result actualTrainingResult
	dir := t.TempDir()
	p := containers.Paths{Inputs: filepath.Join(dir, "inputs"), Control: filepath.Join(dir, "control"), State: filepath.Join(dir, "guard"), Export: filepath.Join(dir, "export"), Data: fixture.Root}
	for _, path := range []string{p.Inputs, p.Control, p.State, p.Export} {
		if err := os.Mkdir(path, 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(p.Inputs, "workload.json"), fixture.Manifest, 0600); err != nil {
		t.Fatal(err)
	}
	if len(resume) > 0 {
		p.Resume = filepath.Join(dir, "resume")
		if err := os.Mkdir(p.Resume, 0700); err != nil {
			t.Fatal(err)
		}
		for name, data := range resume {
			if err := packaging.SafePath(name); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(p.Resume, filepath.FromSlash(name))
			if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
		}
	}
	a := assignment(profile)
	a.Adapter = client.Adapter{ID: "training.image-classifier", Version: "1"}
	a.Argv = []string{"python", "-m", "workloads.training.run", "--manifest", "/inputs/workload.json", "--device", "cpu"}
	if stopAt > 0 {
		a.Argv = append(a.Argv, "--stop-after-steps", fmt.Sprint(stopAt))
	}
	config := map[string]any{"schema_version": "1", "argv": a.Argv, "working_dir": "/workspace", "hard_runtime_ms": 120000, "workspace_bytes": a.ResourceLimits.WorkspaceBytes, "output_bytes": a.ResourceLimits.OutputBytes, "checkpoint_bytes": a.ResourceLimits.CheckpointBytes, "outputs": []string{}, "max_files": 1000, "threads": 1, "resume_binding": binding}
	if err := session.AtomicJSON(filepath.Join(p.Control, "config.json"), config); err != nil {
		t.Fatal(err)
	}
	defer containers.RestoreInputs(p)
	if err := containers.PrepareInputs(p); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	name, err := observed.Create(ctx, a, p)
	if err != nil {
		t.Fatal(err)
	}
	removed := false
	defer func() {
		if !removed {
			cleanup, close := context.WithTimeout(context.Background(), 20*time.Second)
			defer close()
			observed.Remove(cleanup, name)
		}
	}()
	if err = observed.Start(ctx, name); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(8 * time.Second)
	var acknowledgement struct {
		Accepted int64 `json:"accepted_sequence"`
	}
	lease, err := leaseguard.New(90000, 120000, 0, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	for time.Now().Before(deadline) {
		if lease.Publish(p.State, p.Control, time.Now()) == nil {
			raw, _ := os.ReadFile(filepath.Join(p.State, "challenge.json"))
			json.Unmarshal(raw, &acknowledgement)
			if acknowledgement.Accepted >= 1 {
				break
			}
		}
		time.Sleep(25 * time.Millisecond)
	}
	if acknowledgement.Accepted < 1 {
		logs, _ := exec.CommandContext(ctx, "docker", "logs", name).CombinedOutput()
		t.Fatalf("registered training guard never accepted initial grant: %s", logs)
	}
	waitErr := observed.Wait(ctx, name)
	removeErr := observed.Remove(ctx, name)
	removed = true
	evidence := observed.evidence()
	if len(evidence) == 0 {
		t.Fatal("real training evidence missing")
	}
	actual := evidence[len(evidence)-1]
	if waitErr != nil || removeErr != nil || actual.CaptureError != nil {
		t.Fatalf("real guarded training failed: wait=%v remove=%v capture=%v\n%s", waitErr, removeErr, actual.CaptureError, actual.Logs)
	}
	raw := actual.Files["results/outputs/result.json"]
	if err = json.Unmarshal(raw, &result); err != nil || result.WeightsDigest == "" || digest(actual.Files["results/outputs/model.safetensors"]) != result.WeightsDigest {
		t.Fatalf("real registered result/safeweights missing: %v %s", err, raw)
	}
	return actual, result
}

func TestDockerRegisteredTrainingCheckpointResume(t *testing.T) {
	profile := dockerCPU(t)
	fixture := syntheticTraining(t, profile)
	installation := "resumetest-" + session.UUID()
	observed := &observedTrainingDocker{Docker: containers.Docker{InstallationID: installation}}
	t.Cleanup(func() {
		cleanup, close := context.WithTimeout(context.Background(), 20*time.Second)
		defer close()
		observed.Docker.Cleanup(cleanup)
	})
	_, full := guardedTraining(t, profile, fixture, observed, 0, nil, nil)
	partialEvidence, partial := guardedTraining(t, profile, fixture, observed, 3, nil, nil)
	if partial.Status != "interrupted" || partial.Step != 3 || full.Status != "completed" || full.Step != 12 {
		t.Fatalf("wrong actual interruption boundary: full=%#v partial=%#v", full, partial)
	}
	name := strings.TrimPrefix(partial.CheckpointPath, "/checkpoints/")
	if name == partial.CheckpointPath || strings.Contains(name, "/") || packaging.SafePath(name) != nil {
		t.Fatalf("unsafe real checkpoint path %q", partial.CheckpointPath)
	}
	prefix := "checkpoints/" + name + "/"
	files := map[string][]byte{}
	for path, data := range partialEvidence.Files {
		if strings.HasPrefix(path, prefix) {
			files[strings.TrimPrefix(path, prefix)] = data
		}
	}
	if len(files["state.json"]) == 0 || len(files["tensors.safetensors"]) == 0 || strings.TrimSpace(string(files["READY"])) != digest(files["manifest.json"]) {
		t.Fatalf("intermediate actual checkpoint missing verified envelope: %s", name)
	}
	var envelope struct {
		Binding map[string]string `json:"binding"`
		Files   []packaging.File  `json:"files"`
	}
	if err := json.Unmarshal(files["manifest.json"], &envelope); err != nil {
		t.Fatal(err)
	}
	for _, file := range envelope.Files {
		if digest(files[file.Path]) != file.Digest || int64(len(files[file.Path])) != file.SizeBytes {
			t.Fatalf("real checkpoint payload digest mismatch: %s", file.Path)
		}
	}
	for key, value := range partial.Binding {
		if envelope.Binding[key] != value {
			t.Fatalf("checkpoint binding mismatch: %s", key)
		}
	}
	_, resumed := guardedTraining(t, profile, fixture, observed, 0, files, partial.Binding)
	if resumed.Status != "completed" || resumed.Step != 12 || resumed.WeightsDigest != full.WeightsDigest {
		t.Fatalf("actual fresh-container resume differs from uninterrupted weights: full=%#v resumed=%#v", full, resumed)
	}
	for _, field := range []string{"accuracy", "loss", "num_examples", "validation_digest"} {
		if resumed.Metrics[field] != full.Metrics[field] {
			t.Fatalf("resumed metric %s differs: full=%v resumed=%v", field, full.Metrics[field], resumed.Metrics[field])
		}
	}
	t.Logf("actual new-container resume step3->12 matched uninterrupted safeweights %s; checkpoint=%s payloads=%d accuracy=%v loss=%v", resumed.WeightsDigest, name, len(envelope.Files), resumed.Metrics["accuracy"], resumed.Metrics["loss"])
}
