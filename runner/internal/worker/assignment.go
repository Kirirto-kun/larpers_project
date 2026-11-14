package worker

import (
	"campus/runner/internal/artifacts"
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"campus/runner/internal/generated"
	"campus/runner/internal/leaseguard"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"campus/runner/internal/telemetry"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

type Runtime interface {
	VerifyImage(context.Context, string, string) error
	Create(context.Context, client.Assignment, containers.Paths) (string, error)
	Start(context.Context, string) error
	Wait(context.Context, string) error
	Stop(context.Context, string) error
	Remove(context.Context, string) error
	Logs(context.Context, string, io.Writer) error
	Stats(context.Context, string) (map[string]any, error)
}

type ResultManifest struct {
	SchemaVersion      string            `json:"schema_version"`
	Files              []client.Artifact `json:"files"`
	Quality            string            `json:"quality"`
	BenchmarkEligible  bool              `json:"benchmark_eligible"`
	ComputeDurationMS  int64             `json:"compute_duration_ms"`
	OverheadDurationMS int64             `json:"overhead_duration_ms"`
}
type Runner struct {
	Client        *client.Client
	Docker        containers.Docker
	Runtime       Runtime
	StateDir      string
	AllowedImages map[string]string
	MaxInputBytes int64
	Identity      session.Identity
	cacheOnce     sync.Once
	inputCache    *artifacts.Cache
	controlAction atomic.Value
}

func (r *Runner) cache() *artifacts.Cache {
	r.cacheOnce.Do(func() { r.inputCache = artifacts.NewCache(filepath.Join(r.StateDir, "cache"), 2<<30) })
	return r.inputCache
}

func (r *Runner) runtime() Runtime {
	if r.Runtime != nil {
		return r.Runtime
	}
	return r.Docker
}

func readBoundedJSON(path string, dest any) error {
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	st, e := f.Stat()
	if e != nil {
		return e
	}
	if st.Size() > 8<<20 {
		return errors.New("state JSON exceeds quota")
	}
	return json.NewDecoder(f).Decode(dest)
}

func (r *Runner) RunAssignment(ctx context.Context, a client.Assignment) (ResultManifest, error) {
	var result ResultManifest
	start := time.Now()
	if !a.ReceivedAt.IsZero() {
		elapsed := start.Sub(a.ReceivedAt).Milliseconds()
		a.LeaseRemainingMS -= elapsed
		a.TaskRemainingMS -= elapsed
	}
	if e := a.Validate(); e != nil {
		return result, e
	}
	if platform, ok := r.AllowedImages[a.ImageDigest]; !ok || platform != a.Platform {
		return result, errors.New("assignment image is not in the local administrator-approved profiles")
	}
	if r.Client == nil {
		return result, errors.New("runner has no authenticated API client")
	}
	lease, e := leaseguard.New(a.LeaseRemainingMS, a.TaskRemainingMS, 0, start)
	if e != nil {
		return result, e
	}
	if e := r.runtime().VerifyImage(ctx, a.ImageDigest, a.Platform); e != nil {
		return result, e
	}
	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	failures := make(chan error, 1)
	fail := func(e error) {
		select {
		case failures <- e:
		default:
		}
		cancel()
	}
	attemptDir := filepath.Join(r.StateDir, "attempts", a.AttemptID+"-"+fmt.Sprint(a.Generation))
	if e = os.MkdirAll(filepath.Dir(attemptDir), 0700); e != nil {
		return result, e
	}
	if e = os.Mkdir(attemptDir, 0700); e != nil {
		return result, fmt.Errorf("attempt workspace already exists; start a fresh session for reconciliation: %w", e)
	}
	defer os.RemoveAll(attemptDir)
	p := containers.Paths{Inputs: filepath.Join(attemptDir, "inputs"), Control: filepath.Join(attemptDir, "control"), State: filepath.Join(attemptDir, "guard"), Export: filepath.Join(attemptDir, "export")}
	defer func() { containers.RestoreInputs(p) }()
	for _, dir := range []string{p.Inputs, p.Control, p.State, p.Export} {
		if e = os.Mkdir(dir, 0700); e != nil {
			return result, e
		}
	}
	var activity sync.WaitGroup
	activity.Add(2)
	go func() {
		defer activity.Done()
		heartbeat := time.NewTicker(time.Duration(a.HeartbeatIntervalMS) * time.Millisecond)
		defer heartbeat.Stop()
		for {
			select {
			case <-runCtx.Done():
				return
			case <-heartbeat.C:
				sent := time.Now()
				renewal, e := r.requestLease(runCtx, a, "renew")
				received := time.Now()
				if e != nil {
					if client.Terminal(e) {
						fail(e)
						return
					}
					continue
				}
				// v1 renews only the lease. Lease.Renew retains the earlier local
				// hard deadline; the original relative task bound cannot extend it.
				if e = lease.Renew(renewal.LeaseRemainingMs, a.TaskRemainingMS, received.Sub(sent), received); e != nil {
					fail(e)
					return
				}
				if _, e = os.Stat(filepath.Join(p.State, "challenge.json")); e == nil {
					if e = lease.Publish(p.State, p.Control, time.Now()); e != nil {
						fail(e)
						return
					}
				}
			}
		}
	}()
	go func() {
		defer activity.Done()
		watchdog := time.NewTicker(100 * time.Millisecond)
		defer watchdog.Stop()
		for {
			select {
			case <-runCtx.Done():
				return
			case <-watchdog.C:
				if lease.Remaining(time.Now()) <= 0 {
					fail(errors.New("confirmed lease expired locally"))
					return
				}
				action, err := session.ReadControl(r.StateDir)
				if err != nil {
					fail(err)
					return
				}
				if action == "stop" {
					fail(errors.New("owner stopped participation"))
					return
				}
			}
		}
	}()
	defer func() { cancel(); activity.Wait() }()
	maxInput := r.MaxInputBytes
	if maxInput <= 0 {
		maxInput = 10 << 30
	}
	var total int64
	dataset := false
	for _, in := range a.Inputs {
		if in.SizeBytes > maxInput-total {
			return result, errors.New("input byte quota exceeded")
		}
		total += in.SizeBytes
		if in.Kind == "project" && in.Path != "project.zip" {
			return result, errors.New("project archive must use the fixed project.zip binding")
		}
		if in.Kind == "dataset" {
			if dataset {
				return result, errors.New("only one /data binding is supported")
			}
			dataset = true
		}
		dest := filepath.Join(p.Inputs, filepath.FromSlash(in.Path))
		if e = r.cache().Materialize(runCtx, r.Client, in, dest); e != nil {
			return result, e
		}
		if in.Kind == "dataset" {
			p.Data = filepath.Join(attemptDir, "dataset")
			f, e := os.Open(dest)
			if e != nil {
				return result, e
			}
			e = packaging.Extract(f, in.SizeBytes, p.Data, packaging.Rules{MaxFiles: 100000, MaxBytes: maxInput - total + in.SizeBytes})
			f.Close()
			if e != nil {
				return result, e
			}
		}
	}
	var resumeBinding any
	if a.ResumeCheckpoint != nil {
		resume := *a.ResumeCheckpoint
		if resume.SizeBytes > a.ResourceLimits.CheckpointBytes {
			return result, errors.New("resume checkpoint exceeds quota")
		}
		dest := filepath.Join(attemptDir, "resume.zip")
		if e = r.cache().Materialize(runCtx, r.Client, resume, dest); e != nil {
			return result, e
		}
		p.Resume = filepath.Join(attemptDir, "resume")
		f, e := os.Open(dest)
		if e != nil {
			return result, e
		}
		e = packaging.Extract(f, resume.SizeBytes, p.Resume, packaging.Rules{MaxFiles: 10000, MaxBytes: a.ResourceLimits.CheckpointBytes})
		f.Close()
		if e != nil {
			return result, e
		}
		resumeBinding = a.ValidatorPolicy["resume_binding"]
		if resumeBinding == nil {
			return result, errors.New("resume requires explicit code/input/environment/trainer binding")
		}
	}
	config := map[string]any{"schema_version": "1", "argv": a.Argv, "working_dir": a.WorkingDir, "hard_runtime_ms": lease.HardRemaining(time.Now()).Milliseconds(), "workspace_bytes": a.ResourceLimits.WorkspaceBytes, "output_bytes": a.ResourceLimits.OutputBytes, "checkpoint_bytes": a.ResourceLimits.CheckpointBytes, "outputs": append([]string{}, a.Outputs...), "max_files": 10000, "threads": max(1, a.ResourceLimits.CPUMillis/1000), "resume_binding": resumeBinding}
	if e = session.AtomicJSON(filepath.Join(p.Control, "config.json"), config); e != nil {
		return result, e
	}
	if e = containers.PrepareInputs(p); e != nil {
		return result, e
	}
	name, e := r.runtime().Create(runCtx, a, p)
	if e != nil {
		return result, e
	}
	defer func() {
		cleanCtx, close := context.WithTimeout(context.Background(), 20*time.Second)
		defer close()
		r.runtime().Stop(cleanCtx, name)
		r.runtime().Remove(cleanCtx, name)
	}()
	computeStart := time.Now()
	if e = r.runtime().Start(runCtx, name); e != nil {
		return result, e
	}
	// The guard will not start user code until it receives a fresh nonce-bound grant.
	ready := time.NewTimer(min(30*time.Second, lease.Remaining(time.Now())))
	tick := time.NewTicker(25 * time.Millisecond)
	granted := false
	for !granted {
		select {
		case <-runCtx.Done():
			ready.Stop()
			tick.Stop()
			select {
			case e := <-failures:
				return result, e
			default:
				return result, runCtx.Err()
			}
		case <-ready.C:
			tick.Stop()
			return result, errors.New("guard did not issue a startup challenge")
		case <-tick.C:
			if e = lease.Publish(p.State, p.Control, time.Now()); e == nil {
				var acknowledgement struct {
					AcceptedSequence int64 `json:"accepted_sequence"`
				}
				if readBoundedJSON(filepath.Join(p.State, "challenge.json"), &acknowledgement) == nil && acknowledgement.AcceptedSequence >= 1 {
					granted = true
				}
			}
		}
	}
	ready.Stop()
	tick.Stop()
	logs := telemetry.NewLogBuffer(4<<20, []string{r.Client.Credential, a.LeaseToken})
	logSink := newAttemptLogSender(r.Client, a.Fence)
	observeCtx, stopObserve := context.WithCancel(runCtx)
	defer stopObserve()
	var observe sync.WaitGroup
	var metricSequence atomic.Int64
	metricSink := newAttemptTelemetrySender(r.Client, a.Fence)
	if len(a.ResourceLimits.GPUIDs) > 0 {
		observe.Add(1)
		go func() { defer observe.Done(); r.gpuMetrics(observeCtx, a, metricSink, fail) }()
	}
	observe.Add(1)
	go func() { defer observe.Done(); r.runtime().Logs(observeCtx, name, logs) }()
	published := map[string]bool{}
	observe.Add(1)
	go func() {
		defer observe.Done()
		sampleTicker := time.NewTicker(time.Second)
		defer sampleTicker.Stop()
		window := time.Now()
		for {
			select {
			case <-observeCtx.Done():
				return
			case <-sampleTicker.C:
				sequence := metricSequence.Add(1)
				end := time.Now()
				stats, e := r.runtime().Stats(observeCtx, name)
				if e == nil {
					s := telemetry.NewSample(sequence, "attempt/container", window, end, stats)
					if e = metricSink.Send(observeCtx, s, "compute", float64(a.ResourceLimits.CPUMillis)/1000); client.Terminal(e) {
						fail(e)
						return
					}
				}
				window = end
				if b := logs.Drain(); len(b) > 0 {
					if e = logSink.Send(observeCtx, b); client.Terminal(e) {
						fail(e)
						return
					}
				}
				if e = r.publishCheckpoints(observeCtx, a, p.Export, published); e != nil {
					fail(e)
					return
				}
			}
		}
	}()
	waitErr := r.runtime().Wait(runCtx, name)
	computeEnd := time.Now()
	stopObserve()
	observe.Wait()
	select {
	case e := <-failures:
		return result, e
	default:
	}
	if runCtx.Err() != nil {
		return result, runCtx.Err()
	}
	var guarded struct {
		ExitCode       int    `json:"exit_code"`
		Reason         string `json:"reason"`
		ResultManifest struct {
			Files []packaging.File `json:"files"`
		} `json:"result_manifest"`
	}
	if e = readBoundedJSON(filepath.Join(p.State, "result.json"), &guarded); e != nil {
		return result, fmt.Errorf("guard did not finalize outputs: %w", e)
	}
	if waitErr != nil || guarded.ExitCode != 0 || guarded.Reason != "" {
		return result, fmt.Errorf("workload failed (exit %d, %s): %v", guarded.ExitCode, guarded.Reason, waitErr)
	}
	if e = r.publishCheckpoints(runCtx, a, p.Export, published); e != nil {
		return result, e
	}
	result = ResultManifest{SchemaVersion: "1", Files: []client.Artifact{}, Quality: "unverified", BenchmarkEligible: false, ComputeDurationMS: computeEnd.Sub(computeStart).Milliseconds()}
	var outputBytes int64
	for _, entry := range guarded.ResultManifest.Files {
		if e = packaging.SafePath(entry.Path); e != nil {
			return result, e
		}
		outputBytes += entry.SizeBytes
		if entry.SizeBytes < 0 || outputBytes > a.ResourceLimits.OutputBytes || len(result.Files) >= 10000 {
			return result, errors.New("exported results exceed quota")
		}
		path := filepath.Join(p.Export, "results", filepath.FromSlash(entry.Path))
		sum, n, e := artifacts.HashFile(path)
		if e != nil || sum != entry.Digest || n != entry.SizeBytes {
			return result, errors.New("exported result checksum mismatch")
		}
		artifact, e := artifacts.Upload(runCtx, r.Client, path, a.Fence)
		if e != nil {
			return result, e
		}
		artifact.Path = entry.Path
		result.Files = append(result.Files, artifact)
	}
	if b := logs.Bytes(); len(b) > 0 {
		if e = logSink.Send(runCtx, b); client.Terminal(e) {
			return result, e
		}
	}
	result.OverheadDurationMS = time.Since(start).Milliseconds() - result.ComputeDurationMS
	wireResult := generated.ResultManifest{Outputs: make([]generated.OutputRef, 0, len(result.Files)), ComputeMs: &result.ComputeDurationMS, ItemIDs: append([]string{}, a.ItemIDs...)}
	for _, artifact := range result.Files {
		wireResult.Outputs = append(wireResult.Outputs, generated.OutputRef{Path: artifact.Path, ArtifactID: artifact.ArtifactID})
	}
	if _, e = r.Client.Complete(runCtx, a.Fence, wireResult); e != nil {
		return result, e
	}
	return result, nil
}

func (r *Runner) publishCheckpoints(ctx context.Context, a client.Assignment, export string, published map[string]bool) error {
	// Missing means unsent, true means acknowledged, and false means an unknown
	// acknowledgement. Never issue another mutation after that last state: v1
	// has no checkpoint pointer/status resource or idempotency guarantee.
	for _, acknowledged := range published {
		if !acknowledged {
			return errCheckpointPublicationUnknown
		}
	}
	entries, e := os.ReadDir(filepath.Join(export, "checkpoints"))
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return e
	}
	for _, entry := range entries {
		if !entry.IsDir() || entry.Type()&os.ModeSymlink != 0 || published[entry.Name()] {
			continue
		}
		if e = packaging.SafePath(entry.Name()); e != nil {
			return e
		}
		root := filepath.Join(export, "checkpoints", entry.Name())
		if _, e = os.Stat(filepath.Join(root, "READY")); e != nil {
			continue
		}
		compatibility, e := checkpointCompatibility(root)
		if e != nil {
			return e
		}
		zipPath := filepath.Join(export, "checkpoint-"+entry.Name()+".zip")
		f, e := os.OpenFile(zipPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
		if e != nil {
			return e
		}
		_, e = packaging.PackageProject(root, packaging.Rules{MaxFiles: 10000, MaxBytes: a.ResourceLimits.CheckpointBytes}, f)
		closeErr := f.Close()
		if e != nil {
			return e
		}
		if closeErr != nil {
			return closeErr
		}
		artifact, e := artifacts.UploadAs(ctx, r.Client, zipPath, a.Fence, "checkpoint")
		if e != nil {
			return e
		}
		body := generated.CheckpointPublish{Generation: a.Generation, LeaseToken: a.LeaseToken, ArtifactID: artifact.ArtifactID, CompatibilityDigest: compatibility, Sequence: int64(len(published)) + 1}
		published[entry.Name()] = false
		var acknowledged generated.Artifact
		if e = r.Client.DoOnce(ctx, "POST", "/attempts/"+a.AttemptID+"/checkpoints", body, &acknowledged, ""); e != nil {
			return errors.Join(errCheckpointPublicationUnknown, e)
		}
		if acknowledged.ID != artifact.ArtifactID || acknowledged.SHA256 != artifact.Digest || acknowledged.SizeBytes != artifact.SizeBytes || acknowledged.Name != artifact.Path || acknowledged.MediaType != artifact.MediaType {
			return errors.Join(errCheckpointPublicationUnknown, errors.New("checkpoint artifact acknowledgement mismatch"))
		}
		published[entry.Name()] = true
		os.Remove(zipPath)
		os.RemoveAll(root)
	}
	return nil
}
