package benchmark

import (
	"campus/runner/internal/artifacts"
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"campus/runner/internal/leaseguard"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"campus/runner/internal/telemetry"
	"campus/runner/internal/worker"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type Options struct {
	ManifestPath  string
	Repetitions   int
	StateDir      string
	AllowedImages map[string]string
	Identity      session.Identity
	GPUOptIn      bool
	Runtime       worker.Runtime
}
type execution struct {
	Paths    containers.Paths
	Files    []packaging.File
	Duration time.Duration
	Samples  []telemetry.Sample
}

// Run never calls the coordinator and never downloads or installs dependencies.
// Every repetition starts a fresh bounded container from an approved cached image.
func Run(ctx context.Context, options Options) (StandaloneRecord, error) {
	var record StandaloneRecord
	if options.Repetitions < 1 || options.Repetitions > 100 {
		return record, errors.New("repetitions must be between 1 and 100")
	}
	manifestPath, err := filepath.Abs(options.ManifestPath)
	if err != nil {
		return record, err
	}
	manifest, configDigest, err := loadManifest(manifestPath)
	if err != nil {
		return record, err
	}
	a := manifest.Assignment
	if platform, ok := options.AllowedImages[a.ImageDigest]; !ok || platform != a.Platform {
		return record, errors.New("benchmark image is not in local administrator-approved profiles")
	}
	if !client.IDPattern.MatchString(options.Identity.InstallationID) {
		return record, errors.New("local installation identity is required")
	}
	if len(a.ResourceLimits.GPUIDs) > 0 && !options.GPUOptIn {
		return record, errors.New("benchmark GPU use requires explicit --gpu opt in")
	}
	base := filepath.Dir(manifestPath)
	if err = verifyInputs(combinedInputs(manifest), base); err != nil {
		return record, err
	}
	runtime := options.Runtime
	if runtime == nil {
		runtime = containers.Docker{InstallationID: options.Identity.InstallationID}
	}
	if err = runtime.VerifyImage(ctx, a.ImageDigest, a.Platform); err != nil {
		return record, fmt.Errorf("local runtime gate: %w", err)
	}
	if err = os.MkdirAll(options.StateDir, 0700); err != nil {
		return record, err
	}
	var provenance map[string]any
	var provenanceDuration time.Duration
	if manifest.Validator != nil {
		started := time.Now()
		provenance, err = inspectProvenance(ctx, runtime, options.StateDir, &manifest, base)
		provenanceDuration = time.Since(started)
		if err != nil {
			return record, fmt.Errorf("local provenance preflight: %w", err)
		}
	}
	device := options.Identity.NodeID
	if device == "" {
		device = options.Identity.InstallationID
	}
	record = StandaloneRecord{SchemaVersion: "1", DeviceID: device, InstallationID: options.Identity.InstallationID, ConfigDigest: configDigest, ManifestDigest: a.ManifestDigest, ModelDigest: manifest.ModelDigest, ImageDigest: a.ImageDigest, Adapter: a.Adapter, AdapterDigest: manifest.AdapterDigest, ResourceLimits: a.ResourceLimits, Batch: manifest.Batch, Concurrency: manifest.Concurrency, TimingScope: "monotonic_container_start_to_exit_including_guard_and_output_capture", CachePolicy: "fresh_process_each_repetition; host/model/image caches are not flushed or asserted cold", Runs: []Measurement{}}
	record.Provenance = provenance
	record.ProvenanceDurationMS = float64(provenanceDuration) / float64(time.Millisecond)
	for repetition := 1; repetition <= options.Repetitions; repetition++ {
		if err = ctx.Err(); err != nil {
			return record, err
		}
		measurement, err := measure(ctx, runtime, options.StateDir, manifest, base, repetition)
		if err != nil {
			return record, fmt.Errorf("local repetition %d failed; no successful record was exported: %w", repetition, err)
		}
		record.Runs = append(record.Runs, measurement)
	}
	if err = sign(&record, options.Identity); err != nil {
		return record, err
	}
	return record, nil
}

func measure(ctx context.Context, runtime worker.Runtime, stateDir string, manifest Manifest, base string, repetition int) (Measurement, error) {
	start := time.Now()
	measurement := Measurement{Repetition: repetition, ColdWarm: "cold_container_first_host_cache_uncontrolled", StartedAt: start.UTC().Format(time.RFC3339Nano), Quality: "unverified", Utilization: []telemetry.Sample{}, Outputs: []LocalOutput{}}
	if repetition > 1 {
		measurement.ColdWarm = "cold_container_subsequent_host_cache_uncontrolled"
	}
	temp, err := os.MkdirTemp(stateDir, "baseline-")
	if err != nil {
		return measurement, err
	}
	defer os.RemoveAll(temp)
	a := manifest.Assignment
	a.AttemptID = "local-" + session.UUID()
	paths, err := prepare(filepath.Join(temp, "compute"), manifest.Inputs, base, a.ResourceLimits.WorkspaceBytes)
	if err != nil {
		return measurement, err
	}
	computed, err := execute(ctx, runtime, a, paths)
	if err != nil {
		return measurement, err
	}
	measurement.DurationMS = float64(computed.Duration) / float64(time.Millisecond)
	measurement.Utilization = computed.Samples
	for _, file := range computed.Files {
		measurement.Outputs = append(measurement.Outputs, LocalOutput{Digest: file.Digest, SizeBytes: file.SizeBytes, Path: file.Path, Kind: "local_output"})
	}
	if manifest.Validator != nil {
		validationStart := time.Now()
		result, err := validate(ctx, runtime, filepath.Join(temp, "validate"), manifest, base, computed)
		measurement.ValidationDurationMS = float64(time.Since(validationStart)) / float64(time.Millisecond)
		if err != nil {
			return measurement, err
		}
		measurement.Validation = result
		if err = bindProvenance(&manifest, result, false); err != nil {
			return measurement, err
		}
		quality, ok := result["quality"].(string)
		if !ok || quality == "" {
			return measurement, errors.New("registered validator returned no quality verdict")
		}
		accepted, ok := result["accepted"].(bool)
		if !ok {
			return measurement, errors.New("registered validator returned no accepted verdict")
		}
		eligible, ok := result["benchmark_eligible"].(bool)
		if !ok {
			return measurement, errors.New("registered validator returned no benchmark_eligible verdict")
		}
		if eligible && (!accepted || quality != "verified") {
			return measurement, errors.New("registered validator returned inconsistent benchmark eligibility")
		}
		if eligible && manifest.Assignment.Adapter.ID == "generic.project" {
			return measurement, errors.New("generic execution has no registered correctness benchmark")
		}
		measurement.Quality = quality
		measurement.BenchmarkEligible = accepted && eligible && quality == "verified"
		if units, ok := result["checked_items"].(float64); ok && units >= 0 && units <= 1e12 {
			measurement.Units = int64(units)
		}
	}
	end := time.Now()
	measurement.FinishedAt = end.UTC().Format(time.RFC3339Nano)
	measurement.OverheadDurationMS = max(0, float64(end.Sub(start))/float64(time.Millisecond)-measurement.DurationMS-measurement.ValidationDurationMS)
	return measurement, nil
}

func prepare(dir string, inputs []LocalInput, base string, unpackedLimit int64) (containers.Paths, error) {
	p := containers.Paths{Inputs: filepath.Join(dir, "inputs"), Control: filepath.Join(dir, "control"), State: filepath.Join(dir, "guard"), Export: filepath.Join(dir, "export")}
	for _, folder := range []string{p.Inputs, p.Control, p.State, p.Export} {
		if err := os.MkdirAll(folder, 0700); err != nil {
			return p, err
		}
	}
	for _, input := range inputs {
		destination := filepath.Join(p.Inputs, filepath.FromSlash(input.Path))
		if err := copyInput(input, base, destination); err != nil {
			return p, err
		}
		if input.Kind == "dataset" {
			p.Data = filepath.Join(dir, "dataset")
			f, err := os.Open(destination)
			if err != nil {
				return p, err
			}
			err = packaging.Extract(f, input.SizeBytes, p.Data, packaging.Rules{MaxFiles: 10000, MaxBytes: min(2<<30, max(unpackedLimit, 32<<20))})
			f.Close()
			if err != nil {
				return p, err
			}
		}
	}
	return p, nil
}

func execute(ctx context.Context, runtime worker.Runtime, a client.Assignment, paths containers.Paths) (execution, error) {
	result := execution{Paths: paths, Samples: []telemetry.Sample{}}
	start := time.Now()
	lease, err := leaseguard.New(a.TaskRemainingMS, a.TaskRemainingMS, 0, start)
	if err != nil {
		return result, err
	}
	config := map[string]any{"schema_version": "1", "argv": a.Argv, "working_dir": a.WorkingDir, "hard_runtime_ms": lease.HardRemaining(time.Now()).Milliseconds(), "workspace_bytes": a.ResourceLimits.WorkspaceBytes, "output_bytes": a.ResourceLimits.OutputBytes, "checkpoint_bytes": a.ResourceLimits.CheckpointBytes, "outputs": append([]string{}, a.Outputs...), "max_files": 10000, "threads": max(1, a.ResourceLimits.CPUMillis/1000)}
	if err = session.AtomicJSON(filepath.Join(paths.Control, "config.json"), config); err != nil {
		return result, err
	}
	defer containers.RestoreInputs(paths)
	if err = containers.PrepareInputs(paths); err != nil {
		return result, fmt.Errorf("prepare read-only local inputs: %w", err)
	}
	runCtx, cancel := context.WithTimeout(ctx, time.Duration(a.TaskRemainingMS)*time.Millisecond)
	defer cancel()
	name, err := runtime.Create(runCtx, a, paths)
	if err != nil {
		return result, err
	}
	defer func() {
		cleanup, close := context.WithTimeout(context.Background(), 20*time.Second)
		defer close()
		runtime.Stop(cleanup, name)
		runtime.Remove(cleanup, name)
	}()
	containerStart := time.Now()
	if err = runtime.Start(runCtx, name); err != nil {
		return result, err
	}
	waited := make(chan error, 1)
	go func() { waited <- runtime.Wait(runCtx, name) }()
	startup := time.NewTimer(min(30*time.Second, lease.Remaining(time.Now())))
	ticks := time.NewTicker(25 * time.Millisecond)
	accepted := false
	finishedEarly := false
	for !accepted {
		select {
		case <-runCtx.Done():
			startup.Stop()
			ticks.Stop()
			return result, runCtx.Err()
		case err = <-waited:
			var challenge struct {
				AcceptedSequence int64 `json:"accepted_sequence"`
			}
			if err == nil && readBounded(filepath.Join(paths.State, "challenge.json"), 4096, &challenge) == nil && challenge.AcceptedSequence >= 1 {
				accepted = true
				finishedEarly = true
			} else {
				startup.Stop()
				ticks.Stop()
				return result, fmt.Errorf("guard exited before accepting its startup grant: %v", err)
			}
		case <-startup.C:
			ticks.Stop()
			return result, errors.New("guard did not accept its startup grant before the local lease expired")
		case <-ticks.C:
			if err = lease.Publish(paths.State, paths.Control, time.Now()); err == nil {
				var challenge struct {
					AcceptedSequence int64 `json:"accepted_sequence"`
				}
				if readBounded(filepath.Join(paths.State, "challenge.json"), 4096, &challenge) == nil && challenge.AcceptedSequence >= 1 {
					accepted = true
				}
			}
		}
	}
	startup.Stop()
	ticks.Stop()
	observeCtx, stopObserve := context.WithCancel(runCtx)
	logs := telemetry.NewLogBuffer(4<<20, nil)
	var observers sync.WaitGroup
	observers.Add(2)
	go func() { defer observers.Done(); runtime.Logs(observeCtx, name, logs) }()
	go func() {
		defer observers.Done()
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		window := time.Now()
		var sequence int64
		for {
			select {
			case <-observeCtx.Done():
				return
			case <-ticker.C:
				end := time.Now()
				values, err := runtime.Stats(observeCtx, name)
				sequence++
				if err == nil {
					result.Samples = append(result.Samples, telemetry.NewSample(sequence, "local/container", window, end, values))
				}
				window = end
			}
		}
	}()
	if !finishedEarly {
		select {
		case err = <-waited:
		case <-runCtx.Done():
			err = runCtx.Err()
		}
	}
	containerEnd := time.Now()
	stopObserve()
	observers.Wait()
	result.Duration = containerEnd.Sub(containerStart)
	if err != nil {
		return result, fmt.Errorf("local guarded workload: %w", err)
	}
	var guarded struct {
		ExitCode       int    `json:"exit_code"`
		Reason         string `json:"reason"`
		ResultManifest struct {
			Files []packaging.File `json:"files"`
		} `json:"result_manifest"`
	}
	if err = readBounded(filepath.Join(paths.State, "result.json"), 8<<20, &guarded); err != nil {
		return result, fmt.Errorf("local guard did not finalize its result: %w", err)
	}
	if guarded.ExitCode != 0 || guarded.Reason != "" {
		return result, fmt.Errorf("local workload failed (exit %d, %s)", guarded.ExitCode, guarded.Reason)
	}
	if len(guarded.ResultManifest.Files) > 10000 {
		return result, errors.New("local results exceed file quota")
	}
	var total int64
	seen := map[string]bool{}
	for _, file := range guarded.ResultManifest.Files {
		if err = packaging.SafePath(file.Path); err != nil {
			return result, err
		}
		if !strings.HasPrefix(file.Path, "outputs/") && !strings.HasPrefix(file.Path, "workspace/") {
			return result, errors.New("local result has an unexpected output root")
		}
		if file.SizeBytes < 0 || file.SizeBytes > a.ResourceLimits.OutputBytes-total {
			return result, errors.New("local results exceed output byte quota")
		}
		total += file.SizeBytes
		key := strings.ToLower(file.Path)
		if seen[key] {
			return result, errors.New("colliding local output paths")
		}
		seen[key] = true
		source := filepath.Join(paths.Export, "results", filepath.FromSlash(file.Path))
		if err = regularPath(source); err != nil {
			return result, err
		}
		digest, size, err := artifacts.HashFile(source)
		if err != nil || digest != file.Digest || size != file.SizeBytes {
			return result, errors.New("local output checksum/size mismatch")
		}
	}
	result.Files = guarded.ResultManifest.Files
	return result, nil
}

// Isolated Python ignores the project directory and PYTHONPATH before importing
// the immutable registered adapter from /opt/campus. The request is JSON only.
const validatorProgram = `import json, pathlib, shutil, sys
sys.path.insert(0, '/opt/campus')
from workloads.adapter import dispatch
root = pathlib.Path('/inputs/benchmark-results')
for name in ('outputs', 'workspace'):
    source = root / name
    if source.exists():
        destination = pathlib.Path('/') / name
        shutil.copytree(source, destination, dirs_exist_ok=True)
        # copytree copies the read-only input directory's metadata too. Restore
        # write access to this workload-owned tmpfs root for validator results.
        destination.chmod(0o700)
request = json.loads(pathlib.Path('/inputs/campus-verify-request.json').read_text(encoding='utf-8'))
response = dispatch(request)
pathlib.Path('/outputs/campus-validation.json').write_text(json.dumps(response, allow_nan=False), encoding='utf-8')
`

func validate(ctx context.Context, runtime worker.Runtime, dir string, manifest Manifest, base string, computed execution) (map[string]any, error) {
	v := manifest.Validator
	p, err := trustedAdapterPaths(dir, manifest, base)
	if err != nil {
		return nil, err
	}
	outputNames := map[string]bool{}
	for _, file := range computed.Files {
		if file.Path == "outputs/campus-validation.json" {
			return nil, errors.New("workload output uses a reserved validator result path")
		}
		source := filepath.Join(computed.Paths.Export, "results", filepath.FromSlash(file.Path))
		input := LocalInput{Artifact: client.Artifact{Path: file.Path, Digest: file.Digest, SizeBytes: file.SizeBytes}, Source: source}
		if err = copyInput(input, "", filepath.Join(p.Inputs, "benchmark-results", filepath.FromSlash(file.Path))); err != nil {
			return nil, err
		}
		outputNames[file.Path] = true
	}
	outputPaths := []string{}
	for _, path := range v.OutputPaths {
		if !outputNames[path] {
			return nil, fmt.Errorf("validator declared missing output %s", path)
		}
		outputPaths = append(outputPaths, "/"+path)
	}
	payload := map[string]any{"manifest": v.Manifest, "policy": v.Policy}
	if len(outputPaths) > 0 {
		payload["output_paths"] = outputPaths
	} else {
		payload["outputs"] = []any{}
	}
	return invokeAdapter(ctx, runtime, manifest, p, "verify", payload)
}

func invokeAdapter(ctx context.Context, runtime worker.Runtime, manifest Manifest, p containers.Paths, operation string, payload map[string]any) (map[string]any, error) {
	request := map[string]any{"schema_version": "1", "adapter": manifest.Validator.Adapter, "operation": operation, "payload": payload}
	if err := session.AtomicJSON(filepath.Join(p.Inputs, "campus-verify-request.json"), request); err != nil {
		return nil, err
	}
	a := manifest.Assignment
	a.AttemptID = "validator-" + session.UUID()
	a.Inputs = nil
	a.WorkingDir = "/workspace"
	a.Outputs = nil
	a.Argv = []string{"/opt/campus/.venv/bin/python", "-I", "-c", validatorProgram}
	validated, err := execute(ctx, runtime, a, p)
	if err != nil {
		return nil, fmt.Errorf("registered validator runtime: %w", err)
	}
	found := false
	for _, file := range validated.Files {
		if file.Path == "outputs/campus-validation.json" {
			found = true
		}
	}
	if !found {
		return nil, errors.New("registered validator produced no bounded JSON response")
	}
	var response struct {
		SchemaVersion string                          `json:"schema_version"`
		Result        map[string]any                  `json:"result"`
		Error         *struct{ Code, Message string } `json:"error"`
	}
	if err = readBounded(filepath.Join(p.Export, "results", "outputs", "campus-validation.json"), 8<<20, &response); err != nil {
		return nil, err
	}
	if response.SchemaVersion != "1" || response.Error != nil || response.Result == nil {
		return nil, errors.New("registered validator rejected its JSON request; inspect the prepared manifest/policy/reference inputs")
	}
	return response.Result, nil
}

func readBounded(path string, limit int64, value any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil {
		return err
	}
	if int64(len(data)) > limit {
		return errors.New("local JSON exceeds quota")
	}
	return json.Unmarshal(data, value)
}
