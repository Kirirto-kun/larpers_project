// These opt-in tests execute real Linux containers. Their TLS coordinator is a
// protocol fixture, not the production control plane or a simulated engine.
package tests

import (
	"bytes"
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"campus/runner/internal/leaseguard"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"campus/runner/internal/worker"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

type preparedCPU struct{ Image, Platform string }

func dockerCPU(t *testing.T) preparedCPU {
	t.Helper()
	path := os.Getenv("CAMPUS_DOCKER_PROFILE")
	if path == "" {
		t.Skip("set CAMPUS_DOCKER_PROFILE to an absolute prepared profiles JSON to run real Docker tests")
	}
	if !filepath.IsAbs(path) {
		t.Fatal("CAMPUS_DOCKER_PROFILE must be absolute")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("prepared Docker profile unavailable: %v", err)
	}
	var profiles struct {
		Schema   string `json:"schema_version"`
		Profiles []struct {
			Accelerator string  `json:"accelerator"`
			Image       *string `json:"image_digest"`
			Platform    string  `json:"platform"`
			Guard       string  `json:"guard_version"`
		} `json:"profiles"`
	}
	if err = json.Unmarshal(raw, &profiles); err != nil || profiles.Schema != "1" {
		t.Fatalf("invalid prepared profile: %v", err)
	}
	var selected preparedCPU
	for _, p := range profiles.Profiles {
		if p.Accelerator == "cpu" && p.Guard == "1" && p.Platform == "linux/amd64" && p.Image != nil && client.ImagePattern.MatchString(*p.Image) {
			selected = preparedCPU{*p.Image, p.Platform}
			break
		}
	}
	if selected.Image == "" {
		t.Skip("no prepared Linux/amd64 CPU guard profile")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
	defer cancel()
	if output, err := exec.CommandContext(ctx, "docker", "version", "--format", "{{.Server.Os}}").CombinedOutput(); err != nil || strings.TrimSpace(string(output)) != "linux" {
		t.Skipf("Linux Docker runtime unavailable: %v %s", err, output)
	}
	if err = (containers.Docker{}).VerifyImage(ctx, selected.Image, selected.Platform); err != nil {
		if strings.Contains(err.Error(), "not cached") {
			t.Skipf("prepared image unavailable: %v", err)
		}
		t.Fatalf("prepared image failed the real guard/platform contract: %v", err)
	}
	t.Logf("real Docker image %s (%s)", selected.Image, selected.Platform)
	return selected
}

func digest(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }

func assignment(profile preparedCPU) client.Assignment {
	return client.Assignment{Fence: client.Fence{AttemptID: "attempt-" + session.UUID(), Generation: 4, LeaseToken: "integration-lease-secret"}, JobID: "job-integration", StageID: "stage-integration", TaskID: "task-integration", VariantID: "variant-integration", LeaseRemainingMS: 30000, HeartbeatIntervalMS: 1000, TaskRemainingMS: 90000, ManifestDigest: strings.Repeat("e", 64), Adapter: client.Adapter{ID: "generic.project", Version: "1"}, ImageDigest: profile.Image, Platform: profile.Platform, WorkingDir: "/workspace", NetworkPolicy: "none", ResourceLimits: client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 1 << 30, PIDs: 256, WorkspaceBytes: 128 << 20, OutputBytes: 16 << 20, CheckpointBytes: 16 << 20, TmpBytes: 32 << 20}}
}

// Every operation delegates to containers.Docker. The wrapper only retains
// actual engine logs before the worker's automatic cleanup removes the container.
type observedDocker struct {
	containers.Docker
	t    *testing.T
	mu   sync.Mutex
	logs []byte
}

func (d *observedDocker) Create(ctx context.Context, a client.Assignment, paths containers.Paths) (string, error) {
	name, err := d.Docker.Create(ctx, a, paths)
	if err != nil {
		return name, err
	}
	output, err := exec.CommandContext(ctx, "docker", "inspect", name).CombinedOutput()
	if err != nil {
		d.Docker.Remove(ctx, name)
		return "", fmt.Errorf("inspect actual runtime restrictions: %w: %s", err, output)
	}
	var details []struct {
		HostConfig struct {
			NetworkMode                string
			ReadonlyRootfs, Privileged bool
			RestartPolicy              struct{ Name string }
			SecurityOpt                []string
		}
		Config struct{ Labels map[string]string }
		Mounts []struct {
			Destination string
			RW          bool
		}
	}
	if err = json.Unmarshal(output, &details); err != nil || len(details) != 1 {
		d.Docker.Remove(ctx, name)
		return "", fmt.Errorf("malformed actual container inspection: %v", err)
	}
	info := details[0]
	secure := info.HostConfig.NetworkMode == "none" && info.HostConfig.ReadonlyRootfs && !info.HostConfig.Privileged && info.HostConfig.RestartPolicy.Name == "no" && info.Config.Labels["io.campus.installation"] == d.InstallationID
	nnp := false
	for _, option := range info.HostConfig.SecurityOpt {
		if strings.HasPrefix(option, "no-new-privileges") {
			nnp = true
		}
	}
	readOnly := map[string]bool{}
	for _, mount := range info.Mounts {
		if mount.Destination == "/inputs" || mount.Destination == "/run/campus-lease" {
			readOnly[mount.Destination] = !mount.RW
		}
		if mount.Destination == "/var/run/docker.sock" {
			secure = false
		}
	}
	if !secure || !nnp || !readOnly["/inputs"] || !readOnly["/run/campus-lease"] {
		d.Docker.Remove(ctx, name)
		return "", fmt.Errorf("actual engine restrictions missing: %s", output)
	}
	d.t.Log("actual engine inspection: network=none, rootfs read-only, unprivileged container, restart=no, no-new-privileges, input/control read-only")
	return name, nil
}

func (d *observedDocker) Remove(ctx context.Context, name string) error {
	output, _ := exec.CommandContext(ctx, "docker", "logs", name).CombinedOutput()
	d.mu.Lock()
	d.logs = append([]byte(nil), output...)
	d.mu.Unlock()
	return d.Docker.Remove(ctx, name)
}
func (d *observedDocker) captured() string { d.mu.Lock(); defer d.mu.Unlock(); return string(d.logs) }

type upload struct {
	Artifact client.Artifact
	Parts    map[int][]byte
}
type coordinator struct {
	t                                          *testing.T
	a                                          client.Assignment
	inputs                                     map[string][]byte
	mu                                         sync.Mutex
	uploads                                    map[string]*upload
	stored                                     map[string][]byte
	complete                                   worker.ResultManifest
	completes, receipts, renews, logs, metrics int
	logText                                    string
}

func (f *coordinator) fenced(w http.ResponseWriter, r *http.Request) bool {
	var fence client.Fence
	raw, err := io.ReadAll(io.LimitReader(r.Body, 8<<20))
	if err != nil || json.Unmarshal(raw, &fence) != nil || fence != f.a.Fence {
		f.t.Errorf("wrong JSON attempt fence for %s: %s", r.URL.Path, raw)
		http.Error(w, "bad fence", 403)
		return false
	}
	r.Body = io.NopCloser(bytes.NewReader(raw))
	return true
}
func (f *coordinator) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != "Bearer integration-device" || r.Header.Get("X-Node-Session-ID") != "session-integration" {
		f.t.Errorf("missing authenticated node session: %s", r.URL)
		http.Error(w, "auth", 403)
		return
	}
	route := strings.TrimPrefix(r.URL.Path, "/api/v1")
	if route == "/protocol" {
		fmt.Fprint(w, `{"api_version":"1","min_runner_version":"0.1.0","max_runner_version":"0.1.0"}`)
		return
	}
	if route == "/work/next" && r.Method == "POST" {
		json.NewEncoder(w).Encode(f.a)
		return
	}
	if strings.HasPrefix(route, "/artifacts/") && strings.HasSuffix(route, "/content") {
		id := strings.TrimSuffix(strings.TrimPrefix(route, "/artifacts/"), "/content")
		if body, ok := f.inputs[id]; ok {
			w.Write(body)
			return
		}
		http.NotFound(w, r)
		return
	}
	if route == "/uploads" && r.Method == "POST" {
		if !f.fenced(w, r) {
			return
		}
		var body struct {
			Digest string `json:"digest"`
			Size   int64  `json:"size_bytes"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			f.t.Error(err)
			http.Error(w, "json", 400)
			return
		}
		f.mu.Lock()
		id := fmt.Sprintf("upload-%d", len(f.uploads)+1)
		f.uploads[id] = &upload{Artifact: client.Artifact{ArtifactID: "artifact-" + id, Digest: body.Digest, SizeBytes: body.Size}, Parts: map[int][]byte{}}
		f.mu.Unlock()
		fmt.Fprintf(w, `{"upload_id":%q}`, id)
		return
	}
	if strings.HasPrefix(route, "/uploads/") {
		parts := strings.Split(strings.TrimPrefix(route, "/uploads/"), "/")
		f.mu.Lock()
		defer f.mu.Unlock()
		u := f.uploads[parts[0]]
		if u == nil {
			f.t.Errorf("unknown upload %s", route)
			http.NotFound(w, r)
			return
		}
		if len(parts) == 1 && r.Method == "GET" {
			json.NewEncoder(w).Encode(map[string]any{"upload_id": parts[0], "part_size_bytes": 512, "completed_parts": []int{}})
			return
		}
		if len(parts) == 3 && parts[1] == "parts" && r.Method == "PUT" {
			if r.Header.Get("X-Attempt-ID") != f.a.AttemptID || r.Header.Get("X-Generation") != "4" || r.Header.Get("X-Lease-Token") != f.a.LeaseToken {
				f.t.Error("upload part lost fence")
				http.Error(w, "fence", 403)
				return
			}
			n, err := strconv.Atoi(parts[2])
			body, readErr := io.ReadAll(io.LimitReader(r.Body, 513))
			if err != nil || readErr != nil || len(body) > 512 || digest(body) != r.Header.Get("X-Content-SHA256") {
				f.t.Error("upload part digest/size rejected")
				http.Error(w, "part", 400)
				return
			}
			u.Parts[n] = body
			w.WriteHeader(204)
			return
		}
		if len(parts) == 2 && parts[1] == "finalize" && r.Method == "POST" {
			if !f.fenced(w, r) {
				return
			}
			var numbers []int
			for n := range u.Parts {
				numbers = append(numbers, n)
			}
			sort.Ints(numbers)
			var body []byte
			for i, n := range numbers {
				if n != i {
					f.t.Error("upload parts not contiguous")
				}
				body = append(body, u.Parts[n]...)
			}
			if int64(len(body)) != u.Artifact.SizeBytes || digest(body) != u.Artifact.Digest {
				f.t.Error("finalized upload checksum mismatch")
				http.Error(w, "checksum", 400)
				return
			}
			f.stored[u.Artifact.ArtifactID] = body
			json.NewEncoder(w).Encode(u.Artifact)
			return
		}
	}
	base := "/attempts/" + f.a.AttemptID
	if route == base && r.Method == "GET" {
		f.mu.Lock()
		f.receipts++
		count := f.completes
		f.mu.Unlock()
		if count != 1 {
			f.t.Error("receipt requested without one committed completion")
		}
		json.NewEncoder(w).Encode(client.Receipt{AttemptID: f.a.AttemptID, State: "verifying", ReceiptID: "receipt-integration"})
		return
	}
	if strings.HasPrefix(route, base+"/") {
		if !f.fenced(w, r) {
			return
		}
		switch strings.TrimPrefix(route, base+"/") {
		case "renew":
			f.mu.Lock()
			f.renews++
			f.mu.Unlock()
			json.NewEncoder(w).Encode(client.Renewal{LeaseRemainingMS: 30000, TaskRemainingMS: 90000})
			return
		case "logs":
			var body struct {
				Text string `json:"text"`
			}
			json.NewDecoder(r.Body).Decode(&body)
			f.mu.Lock()
			f.logs++
			f.logText += body.Text
			f.mu.Unlock()
			w.WriteHeader(204)
			return
		case "telemetry":
			var body struct {
				Sample struct {
					Kind        string         `json:"kind"`
					WindowStart string         `json:"window_start_at"`
					WindowEnd   string         `json:"window_end_at"`
					DurationMS  int64          `json:"duration_ms"`
					Values      map[string]any `json:"values"`
				} `json:"sample"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				f.t.Error(err)
			}
			start, e1 := time.Parse(time.RFC3339Nano, body.Sample.WindowStart)
			end, e2 := time.Parse(time.RFC3339Nano, body.Sample.WindowEnd)
			if body.Sample.Kind != "attempt/container" || body.Sample.DurationMS <= 0 || e1 != nil || e2 != nil || !end.After(start) || len(body.Sample.Values) == 0 {
				f.t.Errorf("missing real telemetry window/values: %#v", body.Sample)
			}
			f.mu.Lock()
			f.metrics++
			f.mu.Unlock()
			w.WriteHeader(204)
			return
		case "complete":
			var body struct {
				Result worker.ResultManifest `json:"result_manifest"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				f.t.Error(err)
			}
			f.mu.Lock()
			f.completes++
			f.complete = body.Result
			f.mu.Unlock()
			want := "complete:" + f.a.AttemptID + ":4"
			if r.Header.Get("Idempotency-Key") != want {
				f.t.Errorf("completion key %q want %q", r.Header.Get("Idempotency-Key"), want)
			}
			connection, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				f.t.Error(err)
				return
			}
			connection.Close()
			return
		}
	}
	f.t.Errorf("unexpected fixture request %s %s", r.Method, route)
	http.NotFound(w, r)
}

const proofProgram = `import hashlib,json,os,pathlib,signal,socket,time,torch
proof={'uid':os.getuid(),'gid':os.getgid()}
status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line}
proof.update(capeff=status['CapEff'],no_new_privs=status['NoNewPrivs'])
seed=pathlib.Path('/inputs/seed.txt').read_bytes()
proof['input_digest']=hashlib.sha256(seed).hexdigest()
def blocked(name,operation):
    try:
        operation()
    except OSError as exc:
        proof[name]={'blocked':True,'errno':exc.errno}
    else:
        proof[name]={'blocked':False}
blocked('input_readonly',lambda:pathlib.Path('/inputs/cannot-write').write_text('unsafe'))
blocked('lease_write',lambda:pathlib.Path('/run/campus-lease/lease.json').write_text('{}'))
blocked('state_read',lambda:pathlib.Path('/run/campus-state/challenge.json').read_text())
blocked('signal_pid1',lambda:os.kill(1,signal.SIGTERM))
blocked('root_readonly',lambda:pathlib.Path('/etc/campus-test-write').write_text('unsafe'))
connection=socket.socket();connection.settimeout(.5)
try:
    proof['network_errno']=connection.connect_ex(('1.1.1.1',443))
finally:
    connection.close()
proof['docker_socket_absent']=not pathlib.Path('/var/run/docker.sock').exists()
torch.save({'weights':torch.arange(4,dtype=torch.float32)},'model.pt')
proof['model_digest']=hashlib.sha256(pathlib.Path('model.pt').read_bytes()).hexdigest()
pathlib.Path('/outputs/proof.json').write_text(json.dumps(proof),encoding='utf-8')
print('campus-integration-proof',flush=True)
time.sleep(3.2)
`

func TestDockerWorkerExecutesFencedAssignmentAndReconcilesLostCompleteACK(t *testing.T) {
	profile := dockerCPU(t)
	project := t.TempDir()
	if err := os.WriteFile(filepath.Join(project, "train.py"), []byte(proofProgram), 0600); err != nil {
		t.Fatal(err)
	}
	var zip bytes.Buffer
	manifest, err := packaging.PackageProject(project, packaging.Rules{MaxFiles: 10, MaxBytes: 1 << 20}, &zip)
	if err != nil {
		t.Fatal(err)
	}
	seed := []byte("real input seed — 你好\n")
	a := assignment(profile)
	a.ManifestDigest = manifest.Digest
	a.Argv = []string{"python", "train.py"}
	// A normal user need not declare --output for a new workspace model file.
	a.Outputs = nil
	a.Inputs = []client.Artifact{{ArtifactID: "input-project", Digest: digest(zip.Bytes()), SizeBytes: int64(zip.Len()), Path: "project.zip", Kind: "project"}, {ArtifactID: "input-seed", Digest: digest(seed), SizeBytes: int64(len(seed)), Path: "seed.txt", Kind: "input"}}
	f := &coordinator{t: t, a: a, inputs: map[string][]byte{"input-project": zip.Bytes(), "input-seed": seed}, uploads: map[string]*upload{}, stored: map[string][]byte{}}
	server := httptest.NewTLSServer(f)
	defer server.Close()
	c, err := client.New(server.URL, "integration-device", "session-integration", server.Client())
	if err != nil {
		t.Fatal(err)
	}
	c.Backoff = 10 * time.Millisecond
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	if err = c.Handshake(ctx); err != nil {
		t.Fatal(err)
	}
	var assigned client.Assignment
	if err = c.Do(ctx, "POST", "/work/next", map[string]any{"node_session_id": "session-integration", "wait_ms": 0}, &assigned, ""); err != nil {
		t.Fatal(err)
	}
	installation := "integration-" + session.UUID()
	docker := containers.Docker{InstallationID: installation}
	observed := &observedDocker{Docker: docker, t: t}
	t.Cleanup(func() {
		cleanup, close := context.WithTimeout(context.Background(), 20*time.Second)
		defer close()
		if err := docker.Cleanup(cleanup); err != nil {
			t.Errorf("own installation cleanup: %v", err)
		}
	})
	runner := worker.Runner{Client: c, Docker: docker, Runtime: observed, StateDir: t.TempDir(), AllowedImages: map[string]string{profile.Image: profile.Platform}, Identity: session.Identity{InstallationID: installation}}
	result, err := runner.RunAssignment(ctx, assigned)
	if err != nil {
		t.Fatalf("real worker failed: %v\nactual container logs:\n%s", err, observed.captured())
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.completes != 1 || f.receipts != 1 {
		t.Fatalf("completion lost-ACK not reconciled exactly once: complete=%d receipt=%d", f.completes, f.receipts)
	}
	if f.renews < 1 || f.logs < 1 || f.metrics < 1 || !strings.Contains(f.logText, "campus-integration-proof") {
		t.Fatalf("missing real renewal/logs/telemetry: renew=%d logs=%d metrics=%d text=%q", f.renews, f.logs, f.metrics, f.logText)
	}
	if result.Quality != "unverified" || result.BenchmarkEligible {
		t.Fatal("generic execution was incorrectly promoted to correctness")
	}
	var proofRaw, model []byte
	for _, file := range f.complete.Files {
		body := f.stored[file.ArtifactID]
		if digest(body) != file.Digest || int64(len(body)) != file.SizeBytes {
			t.Fatalf("result checksum/size mismatch: %#v", file)
		}
		switch file.Path {
		case "outputs/proof.json":
			proofRaw = body
		case "workspace/model.pt":
			model = body
		}
	}
	if len(proofRaw) == 0 || len(model) == 0 {
		t.Fatalf("model/proof not captured: %#v\nlogs:%s", f.complete.Files, observed.captured())
	}
	var proof struct {
		UID                                                            int    `json:"uid"`
		CapEff                                                         string `json:"capeff"`
		NoNewPrivs                                                     string `json:"no_new_privs"`
		InputDigest                                                    string `json:"input_digest"`
		ModelDigest                                                    string `json:"model_digest"`
		NetworkErrno                                                   int    `json:"network_errno"`
		DockerSocketAbsent                                             bool   `json:"docker_socket_absent"`
		InputReadonly, LeaseWrite, StateRead, SignalPID1, RootReadonly json.RawMessage
	}
	if err = json.Unmarshal(proofRaw, &proof); err != nil {
		t.Fatal(err)
	}
	var all map[string]any
	json.Unmarshal(proofRaw, &all)
	if proof.UID == 0 || proof.CapEff != "0000000000000000" || proof.NoNewPrivs != "1" || proof.InputDigest != digest(seed) || proof.ModelDigest != digest(model) || proof.NetworkErrno == 0 || !proof.DockerSocketAbsent {
		t.Fatalf("actual isolation proof failed: %s", proofRaw)
	}
	for _, name := range []string{"input_readonly", "lease_write", "state_read", "signal_pid1", "root_readonly"} {
		value, ok := all[name].(map[string]any)
		if !ok || value["blocked"] != true {
			t.Fatalf("isolation %s failed: %s", name, proofRaw)
		}
	}
	t.Logf("actual isolation proof %s; uploaded model SHA256 %s (%d bytes); renew=%d logs=%d metrics=%d completion=1 receipt=1", proofRaw, digest(model), len(model), f.renews, f.logs, f.metrics)
}

const hangingProgram = `import signal,subprocess,sys,time
signal.signal(signal.SIGTERM,lambda *_:print('guard-term-seen',flush=True))
subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'],start_new_session=True)
print('guard-hang-started',flush=True)
while True: time.sleep(.05)
`

func TestDockerGuardExpiresWithoutFreshRunnerGrants(t *testing.T) {
	// Skip the parent test as well when opt-in/runtime prerequisites are absent.
	dockerCPU(t)
	for _, mode := range []string{"stale_replay", "hard_deadline"} {
		t.Run(mode, func(t *testing.T) {
			profile := dockerCPU(t)
			installation := "guardtest-" + session.UUID()
			docker := containers.Docker{InstallationID: installation}
			dir := t.TempDir()
			paths := containers.Paths{Inputs: filepath.Join(dir, "inputs"), Control: filepath.Join(dir, "control"), State: filepath.Join(dir, "guard"), Export: filepath.Join(dir, "export")}
			for _, path := range []string{paths.Inputs, paths.Control, paths.State, paths.Export} {
				if err := os.Mkdir(path, 0700); err != nil {
					t.Fatal(err)
				}
			}
			defer containers.RestoreInputs(paths)
			a := assignment(profile)
			a.Argv = []string{"python", "-c", hangingProgram}
			hardMS := int64(9000)
			ttlMS := int64(2800)
			if mode == "hard_deadline" {
				hardMS = 3400
				ttlMS = 6000
			}
			config := map[string]any{"schema_version": "1", "argv": a.Argv, "working_dir": "/workspace", "hard_runtime_ms": hardMS, "workspace_bytes": a.ResourceLimits.WorkspaceBytes, "output_bytes": a.ResourceLimits.OutputBytes, "checkpoint_bytes": a.ResourceLimits.CheckpointBytes, "outputs": []string{}, "max_files": 1000, "threads": 1}
			if err := session.AtomicJSON(filepath.Join(paths.Control, "config.json"), config); err != nil {
				t.Fatal(err)
			}
			if err := containers.PrepareInputs(paths); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			name, err := docker.Create(ctx, a, paths)
			if err != nil {
				t.Fatal(err)
			}
			defer func() {
				cleanup, close := context.WithTimeout(context.Background(), 20*time.Second)
				defer close()
				docker.Remove(cleanup, name)
			}()
			if err = docker.Start(ctx, name); err != nil {
				t.Fatal(err)
			}
			var challenge struct {
				Challenge string `json:"challenge"`
				Accepted  int64  `json:"accepted_sequence"`
			}
			startup := time.Now().Add(4 * time.Second)
			for time.Now().Before(startup) {
				raw, _ := os.ReadFile(filepath.Join(paths.State, "challenge.json"))
				if json.Unmarshal(raw, &challenge) == nil && len(challenge.Challenge) >= 8 {
					break
				}
				time.Sleep(20 * time.Millisecond)
			}
			if challenge.Challenge == "" {
				output, _ := exec.CommandContext(ctx, "docker", "logs", name).CombinedOutput()
				t.Fatalf("real guard never challenged: %s", output)
			}
			grantedAt := time.Now()
			lease, err := leaseguard.New(ttlMS, 15000, 0, grantedAt)
			if err != nil {
				t.Fatal(err)
			}
			if err = lease.Publish(paths.State, paths.Control, time.Now()); err != nil {
				t.Fatal(err)
			}
			for time.Now().Before(startup) {
				raw, _ := os.ReadFile(filepath.Join(paths.State, "challenge.json"))
				if json.Unmarshal(raw, &challenge) == nil && challenge.Accepted >= 1 {
					break
				}
				time.Sleep(20 * time.Millisecond)
			}
			if challenge.Accepted != 1 {
				t.Fatalf("guard did not accept confirmed grant: %#v", challenge)
			}
			original, err := os.ReadFile(filepath.Join(paths.Control, "lease.json"))
			if err != nil {
				t.Fatal(err)
			}
			waited := make(chan error, 1)
			go func() { waited <- docker.Wait(ctx, name) }()
			ticker := time.NewTicker(100 * time.Millisecond)
			defer ticker.Stop()
			done := false
			for !done {
				select {
				case err = <-waited:
					done = true
				case <-ctx.Done():
					t.Fatal("guard outlived its independent lease/hard runtime")
				case <-ticker.C:
					if mode == "hard_deadline" {
						if e := lease.Renew(6000, 15000, 0, time.Now()); e != nil {
							t.Fatal(e)
						}
						if e := lease.Publish(paths.State, paths.Control, time.Now()); e != nil {
							t.Fatalf("fresh confirmed grant publish: %v", e)
						}
					} else {
						var replay map[string]any
						json.Unmarshal(original, &replay)
						// A copied grant or enlarged TTL with the already accepted sequence
						// must never extend the independent deadline.
						if time.Since(grantedAt) > time.Second {
							raw, _ := os.ReadFile(filepath.Join(paths.State, "challenge.json"))
							json.Unmarshal(raw, &challenge)
							replay["challenge"] = challenge.Challenge
							replay["remaining_ms"] = int64(60000)
						}
						if e := session.AtomicJSON(filepath.Join(paths.Control, "lease.json"), replay); e != nil {
							t.Fatal(e)
						}
					}
				}
			}
			elapsed := time.Since(grantedAt)
			if err == nil || elapsed > 6*time.Second || elapsed < time.Second {
				t.Fatalf("wrong independent guard bound: mode=%s elapsed=%s wait=%v", mode, elapsed, err)
			}
			var result struct {
				Exit   int    `json:"exit_code"`
				Reason string `json:"reason"`
			}
			raw, readErr := os.ReadFile(filepath.Join(paths.State, "result.json"))
			if readErr != nil || json.Unmarshal(raw, &result) != nil || result.Reason != "lease_or_runtime_expired" || result.Exit != -9 {
				t.Fatalf("guard did not TERM then KILL hanging workload: result=%s error=%v wait=%v", raw, readErr, err)
			}
			challengeRaw, challengeErr := os.ReadFile(filepath.Join(paths.State, "challenge.json"))
			if challengeErr != nil || json.Unmarshal(challengeRaw, &challenge) != nil || (mode == "stale_replay" && challenge.Accepted != 1) || (mode == "hard_deadline" && challenge.Accepted < 2) {
				t.Fatalf("unexpected actual accepted lease sequence: mode=%s challenge=%s error=%v", mode, challengeRaw, challengeErr)
			}
			logs, _ := exec.CommandContext(ctx, "docker", "logs", name).CombinedOutput()
			if !bytes.Contains(logs, []byte("guard-hang-started")) || !bytes.Contains(logs, []byte("guard-term-seen")) {
				t.Fatalf("no actual TERM escalation evidence: %s", logs)
			}
			t.Logf("actual %s expiry after %s: %s; logs=%s", mode, elapsed, raw, logs)
		})
	}
}
