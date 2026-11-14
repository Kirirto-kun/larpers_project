package tests

import (
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"campus/runner/internal/telemetry"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
	"testing"
	"time"
)

// This launches the real binary. The fixture coordinator's CPU/memory is in the
// parent process and is deliberately not included in the child's native counters.
func TestIdleRunnerNativeProcess(t *testing.T) {
	seconds, e := strconv.Atoi(os.Getenv("CAMPUS_IDLE_SECONDS"))
	if e != nil || seconds < 1 {
		t.Skip("set CAMPUS_IDLE_SECONDS=300 for the real five-minute idle measurement")
	}
	if seconds < 300 {
		t.Fatal("acceptance measurement requires at least 300 seconds")
	}
	profile := os.Getenv("CAMPUS_DOCKER_PROFILE")
	if !filepath.IsAbs(profile) {
		t.Fatal("set absolute CAMPUS_DOCKER_PROFILE")
	}
	cwd, e := os.Getwd()
	if e != nil {
		t.Fatal(e)
	}
	root := filepath.Dir(cwd)
	temp := t.TempDir()
	binary := filepath.Join(temp, "campus")
	goName := "go"
	if runtime.GOOS == "windows" {
		binary += ".exe"
		goName += ".exe"
	}
	build := exec.Command(filepath.Join(runtime.GOROOT(), "bin", goName), "build", "-trimpath", "-o", binary, "./cmd/campus")
	build.Dir = root
	if out, e := build.CombinedOutput(); e != nil {
		t.Fatalf("build %v: %s", e, out)
	}
	credential := "idle-fixture-" + session.UUID()
	sessionID := session.UUID()
	node := session.UUID()
	var mu sync.Mutex
	var samples []telemetry.Sample
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer r.Body.Close()
		if r.URL.Path == "/api/v1/protocol" {
			fmt.Fprint(w, `{"api_version":"1","min_runner_version":"0.1.0","max_runner_version":"0.1.99"}`)
			return
		}
		if r.Header.Get("Authorization") != "Bearer "+credential {
			w.WriteHeader(401)
			return
		}
		switch r.URL.Path {
		case "/api/v1/node-sessions":
			io.Copy(io.Discard, r.Body)
			fmt.Fprintf(w, `{"node_session_id":%q}`, sessionID)
		case "/api/v1/node-sessions/" + sessionID + "/capabilities":
			io.Copy(io.Discard, r.Body)
			w.WriteHeader(204)
		case "/api/v1/node-sessions/" + sessionID + "/telemetry":
			var s telemetry.Sample
			if e := json.NewDecoder(r.Body).Decode(&s); e == nil {
				mu.Lock()
				samples = append(samples, s)
				mu.Unlock()
			}
			w.WriteHeader(204)
		case "/api/v1/work/next":
			io.Copy(io.Discard, r.Body)
			select {
			case <-r.Context().Done():
			case <-time.After(25 * time.Second):
				w.WriteHeader(204)
			}
		default:
			w.WriteHeader(404)
		}
	}))
	defer server.Close()
	ca := filepath.Join(temp, "ca.pem")
	if e := os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0600); e != nil {
		t.Fatal(e)
	}
	state := filepath.Join(temp, "state")
	identity, e := session.LoadOrCreate(state)
	if e != nil {
		t.Fatal(e)
	}
	identity.Endpoint = server.URL
	identity.NodeID = node
	identity.Credential = credential
	identity.CredentialID = session.UUID()
	if e = session.Save(state, identity); e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(seconds+120)*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, binary, "--state-dir", state, "--ca-file", ca, "serve", "--profiles", profile)
	output := telemetry.NewLogBuffer(64<<10, []string{credential})
	command.Stdout = output
	command.Stderr = output
	if e = command.Start(); e != nil {
		t.Fatal(e)
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	defer func() {
		if command.Process != nil {
			command.Process.Kill()
		}
	}()
	startup := time.Now()
	deadline := time.NewTimer(40 * time.Second)
	ticker := time.NewTicker(100 * time.Millisecond)
	started := false
	for !started {
		select {
		case e := <-done:
			t.Fatalf("serve exited %v: %s", e, output.Bytes())
		case <-deadline.C:
			t.Fatalf("no native telemetry: %s", output.Bytes())
		case <-ticker.C:
			mu.Lock()
			started = len(samples) > 0
			mu.Unlock()
		}
	}
	deadline.Stop()
	ticker.Stop()
	firstSampleAfterMS := time.Since(startup).Milliseconds()
	measurement := time.NewTimer(time.Duration(seconds) * time.Second)
	select {
	case e := <-done:
		t.Fatalf("idle runner stopped early %v: %s", e, output.Bytes())
	case <-measurement.C:
	}
	if e = session.Control(state, "stop"); e != nil {
		t.Fatal(e)
	}
	select {
	case e := <-done:
		if e != nil {
			t.Fatalf("owner stop failed %v: %s", e, output.Bytes())
		}
	case <-time.After(10 * time.Second):
		t.Fatal("idle process did not honor owner stop")
	}
	mu.Lock()
	captured := append([]telemetry.Sample(nil), samples...)
	mu.Unlock()
	if len(captured) < seconds-5 {
		t.Fatalf("missing idle measurements: %d", len(captured))
	}
	var weighted, totalWindow float64
	var peakRSS float64
	var last int64
	for _, s := range captured {
		cpu, ok := s.Values["runner_cpu_percent_one_core"].(float64)
		rss, okRSS := s.Values["runner_rss_bytes"].(float64)
		if !ok || !okRSS || rss <= 0 || s.DurationMS <= 0 || s.Sequence <= last {
			t.Fatal("invalid native measurement", s)
		}
		last = s.Sequence
		weighted += cpu * float64(s.DurationMS)
		totalWindow += float64(s.DurationMS)
		peakRSS = max(peakRSS, rss)
	}
	average := weighted / totalWindow
	rawBinary, _ := os.ReadFile(binary)
	digest := sha256.Sum256(rawBinary)
	record := map[string]any{"schema_version": "1", "duration_ms": totalWindow, "samples": captured, "average_cpu_percent_one_core": average, "peak_rss_bytes": peakRSS, "startup_to_first_sample_ms": firstSampleAfterMS, "binary_digest": hex.EncodeToString(digest[:]), "runner_version": client.Version, "os": runtime.GOOS, "arch": runtime.GOARCH, "scope": "actual standalone Go runner PID; fixture API is a separate process; external docker CLI/WSL/model overhead not included", "background_environment": "other preexisting containers and image preparation may be active; no workload assigned to measured runner"}
	payload, _ := json.MarshalIndent(record, "", "  ")
	out := filepath.Join(root, "test-results", "idle-runner.json")
	os.MkdirAll(filepath.Dir(out), 0700)
	os.WriteFile(out, payload, 0600)
	t.Logf("%d samples, %.4f%% one logical CPU, peak RSS %.2f MiB; %s", len(captured), average, peakRSS/(1<<20), out)
	if average >= .5 || peakRSS >= 50<<20 {
		t.Fatalf("idle engineering target exceeded: CPU%.4f%% RSS%.2fMiB", average, peakRSS/(1<<20))
	}
}
