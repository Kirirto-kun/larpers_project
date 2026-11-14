package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"campus/runner/internal/generated"
	"campus/runner/internal/session"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type fixtureRuntime struct {
	paths   containers.Paths
	stale   bool
	removed atomic.Bool
	stopped atomic.Bool
}

func (f *fixtureRuntime) VerifyImage(context.Context, string, string) error { return nil }
func (f *fixtureRuntime) Create(_ context.Context, _ client.Assignment, p containers.Paths) (string, error) {
	f.paths = p
	return "fixture", nil
}
func (f *fixtureRuntime) Start(context.Context, string) error {
	return session.AtomicJSON(filepath.Join(f.paths.State, "challenge.json"), map[string]any{"challenge": "fresh-random-challenge", "accepted_sequence": 1})
}
func (f *fixtureRuntime) Wait(ctx context.Context, _ string) error {
	if f.stale {
		<-ctx.Done()
		return ctx.Err()
	}
	data := []byte("actual transferred model fixture")
	h := sha256.Sum256(data)
	dir := filepath.Join(f.paths.Export, "results", "workspace")
	os.MkdirAll(dir, 0700)
	os.WriteFile(filepath.Join(dir, "model.pt"), data, 0600)
	return session.AtomicJSON(filepath.Join(f.paths.State, "result.json"), map[string]any{"exit_code": 0, "result_manifest": map[string]any{"files": []any{map[string]any{"path": "workspace/model.pt", "digest": hex.EncodeToString(h[:]), "size_bytes": len(data)}}}})
}
func (f *fixtureRuntime) Stop(context.Context, string) error   { f.stopped.Store(true); return nil }
func (f *fixtureRuntime) Remove(context.Context, string) error { f.removed.Store(true); return nil }
func (f *fixtureRuntime) Logs(ctx context.Context, _ string, w io.Writer) error {
	w.Write([]byte("node-credential lease-secret\n"))
	<-ctx.Done()
	return nil
}
func (f *fixtureRuntime) Stats(context.Context, string) (map[string]any, error) {
	return map[string]any{"cpu_percent": 1.0}, nil
}
func fixtureAssignment() client.Assignment {
	return client.Assignment{Fence: client.Fence{AttemptID: "attempt", Generation: 2, LeaseToken: "lease-secret"}, JobID: "job", StageID: "stage", TaskID: "task", VariantID: "cpu", LeaseRemainingMS: 3000, HeartbeatIntervalMS: 100, TaskRemainingMS: 10000, ManifestDigest: strings.Repeat("a", 64), Adapter: client.Adapter{ID: "generic.project", Version: "1"}, ImageDigest: "image@sha256:" + strings.Repeat("b", 64), Platform: "linux/amd64", Argv: []string{"python", "train.py"}, WorkingDir: "/workspace", NetworkPolicy: "none", ResourceLimits: client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 128 << 20, PIDs: 32, WorkspaceBytes: 32 << 20, OutputBytes: 32 << 20, CheckpointBytes: 16 << 20, TmpBytes: 16 << 20}}
}
func TestRunAssignmentTransportsOutputAndReconcilesReceipt(t *testing.T) {
	data := []byte("actual transferred model fixture")
	h := sha256.Sum256(data)
	digest := hex.EncodeToString(h[:])
	var completed atomic.Int32
	var uploaded atomic.Bool
	var fenced atomic.Bool
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Header.Get("X-Node-Session") != "session" {
			t.Error("missing session fencing")
		}
		switch req.URL.Path {
		case "/api/v1/uploads":
			fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
		case "/api/v1/uploads/upload":
			fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
		case "/api/v1/uploads/upload/parts/0":
			b, _ := io.ReadAll(req.Body)
			if string(b) != string(data) {
				t.Error("model bytes lost")
			}
			uploaded.Store(true)
			json.NewEncoder(w).Encode(map[string]any{"number": 0, "sha256": digest, "size_bytes": len(data)})
		case "/api/v1/uploads/upload/finalize":
			json.NewEncoder(w).Encode(client.Artifact{ArtifactID: "artifact", Path: "model.pt", MediaType: "application/octet-stream", Digest: digest, SizeBytes: int64(len(data))})
		case "/api/v1/attempts/attempt/complete":
			var body generated.Complete
			decoder := json.NewDecoder(req.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&body); err != nil || body.Generation != 2 || body.LeaseToken != "lease-secret" || len(body.Manifest.Outputs) != 1 || body.Manifest.Outputs[0].Path != "workspace/model.pt" || body.Manifest.Outputs[0].ArtifactID != "artifact" || body.Manifest.ComputeMs == nil {
				t.Errorf("incorrect completion %+v: %v", body, err)
			}
			fenced.Store(true)
			completed.Add(1)
			conn, _, _ := w.(http.Hijacker).Hijack()
			conn.Close()
		case "/api/v1/attempts/attempt":
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"verifying","receipt":{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-14T00:00:00Z"}}`)
		case "/api/v1/attempts/attempt/logs":
			var body generated.LogWrite
			decoder := json.NewDecoder(req.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&body); err != nil || body.Generation != 2 || body.LeaseToken != "lease-secret" || body.Sequence < 1 || len(body.Text) > 8192 {
				t.Errorf("logs do not match v1: %+v, %v", body, err)
			}
			if strings.Contains(body.Text, "node-credential") || strings.Contains(body.Text, "lease-secret") {
				t.Error("credential logged")
			}
			w.WriteHeader(204)
		case "/api/v1/attempts/attempt/renew":
			fmt.Fprint(w, `{"lease_remaining_ms":3000,"task_remaining_ms":9000}`)
		default:
			t.Error(req.URL.Path)
			w.WriteHeader(404)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node-credential", "session", s.Client())
	c.Backoff = time.Millisecond
	runtime := &fixtureRuntime{}
	a := fixtureAssignment()
	r := Runner{Client: c, Runtime: runtime, StateDir: t.TempDir(), AllowedImages: map[string]string{a.ImageDigest: a.Platform}}
	result, e := r.RunAssignment(context.Background(), a)
	if e != nil {
		t.Fatal(e)
	}
	if len(result.Files) != 1 || !uploaded.Load() || !fenced.Load() || completed.Load() != 1 || !runtime.removed.Load() {
		t.Fatal("incomplete lifecycle", result, completed.Load())
	}
}
func TestStaleRenewStopsRuntimeAndNeverCompletes(t *testing.T) {
	var completed atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if strings.HasSuffix(req.URL.Path, "/complete") {
			completed.Add(1)
		}
		w.WriteHeader(409)
		fmt.Fprint(w, `{"code":"STALE_ATTEMPT","message":"replaced","retryable":false}`)
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node-credential", "session", s.Client())
	runtime := &fixtureRuntime{stale: true}
	a := fixtureAssignment()
	r := Runner{Client: c, Runtime: runtime, StateDir: t.TempDir(), AllowedImages: map[string]string{a.ImageDigest: a.Platform}}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_, e := r.RunAssignment(ctx, a)
	if !client.Terminal(e) || completed.Load() != 0 || !runtime.stopped.Load() || !runtime.removed.Load() {
		t.Fatal("stale attempt published or kept running", e)
	}
}

func TestOwnerStopInterruptsHungRenewBeforeLeaseExpires(t *testing.T) {
	entered := make(chan struct{}, 1)
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if strings.HasSuffix(req.URL.Path, "/renew") {
			io.Copy(io.Discard, req.Body)
			select {
			case entered <- struct{}{}:
			default:
			}
			<-req.Context().Done()
			return
		}
		w.WriteHeader(204)
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node-credential", "session", s.Client())
	runtime := &fixtureRuntime{stale: true}
	a := fixtureAssignment()
	dir := t.TempDir()
	r := Runner{Client: c, Runtime: runtime, StateDir: dir, AllowedImages: map[string]string{a.ImageDigest: a.Platform}}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { _, e := r.RunAssignment(ctx, a); done <- e }()
	select {
	case <-entered:
	case <-ctx.Done():
		t.Fatal("renew not sent")
	}
	stop := time.Now()
	if e := session.Control(dir, "stop"); e != nil {
		t.Fatal(e)
	}
	select {
	case e := <-done:
		if e == nil || time.Since(stop) > time.Second || !runtime.stopped.Load() {
			t.Fatal("owner stop waited on network", e)
		}
	case <-time.After(time.Second):
		cancel()
		<-done
		t.Fatal("owner stop blocked behind renew HTTP")
	}
}

func TestStaleFinalLogFlushNeverCompletes(t *testing.T) {
	data := []byte("actual transferred model fixture")
	h := sha256.Sum256(data)
	digest := hex.EncodeToString(h[:])
	var completes atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		switch req.URL.Path {
		case "/api/v1/uploads", "/api/v1/uploads/upload":
			fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
		case "/api/v1/uploads/upload/parts/0":
			json.NewEncoder(w).Encode(generated.UploadPart{Number: 0, SHA256: digest, SizeBytes: int64(len(data))})
		case "/api/v1/uploads/upload/finalize":
			json.NewEncoder(w).Encode(generated.Artifact{ID: "artifact", Name: "model.pt", MediaType: "application/octet-stream", SHA256: digest, SizeBytes: int64(len(data))})
		case "/api/v1/attempts/attempt/logs":
			w.WriteHeader(http.StatusConflict)
			fmt.Fprint(w, `{"code":"STALE_ATTEMPT","message":"replaced","retryable":false}`)
		case "/api/v1/attempts/attempt/complete":
			completes.Add(1)
			w.WriteHeader(http.StatusAccepted)
		default:
			w.WriteHeader(http.StatusNoContent)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	a := fixtureAssignment()
	r := Runner{Client: c, Runtime: &fixtureRuntime{}, StateDir: t.TempDir(), AllowedImages: map[string]string{a.ImageDigest: a.Platform}}
	_, err := r.RunAssignment(context.Background(), a)
	if !client.Terminal(err) || completes.Load() != 0 {
		t.Fatalf("stale final logs allowed completion: err=%v completes=%d", err, completes.Load())
	}
}
