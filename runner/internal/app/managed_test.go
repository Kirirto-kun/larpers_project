package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"campus/runner/internal/worker"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// The injected Serve replaces Docker, while real CLI/session/settings code runs.
func TestManagedPauseResumeWaitsForTeardownAndReopensSession(t *testing.T) {
	a, _, errout, dir := testApp(t)
	var mu sync.Mutex
	desired := client.DeviceSettings{Revision: 1, Mode: "low", DesiredState: "paused"}
	acks := make(chan client.SettingsAck, 20)
	var sessions, discoveries atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Campus-Worker-Protocol") != "2" {
			t.Errorf("missing managed protocol at %s", r.URL.Path)
		}
		switch {
		case r.URL.Path == "/api/v1/protocol":
			protocol(w)
		case r.URL.Path == "/api/v1/device/settings":
			mu.Lock()
			v := desired
			mu.Unlock()
			json.NewEncoder(w).Encode(v)
		case r.URL.Path == "/api/v1/device/settings/ack":
			var ack client.SettingsAck
			json.NewDecoder(r.Body).Decode(&ack)
			acks <- ack
			w.WriteHeader(204)
		case r.URL.Path == "/api/v1/node-sessions":
			n := sessions.Add(1)
			fmt.Fprintf(w, `{"id":"session-%d","node_id":"node-1"}`, n)
		case strings.HasSuffix(r.URL.Path, "/capabilities"):
			w.WriteHeader(204)
		default:
			t.Errorf("unexpected %s", r.URL.Path)
			w.WriteHeader(404)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	a.ManagedPollInterval = 10 * time.Millisecond
	a.Discover = func(context.Context) (client.Capabilities, error) {
		discoveries.Add(1)
		return client.Capabilities{OS: "windows", Arch: "amd64", CPULogical: 8, CPUAvailableMillis: 4000, DockerCPUMillis: 8000, DockerMemoryBytes: 8 << 30, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 20 << 30, PowerState: "ac", PreflightRevision: "1", Runtime: client.Runtime{Type: "docker", Version: "test", Healthy: true}}, nil
	}
	a.VerifyImage = func(context.Context, string, string) error { return nil }
	a.Probe = func(context.Context, string, string, bool) error { return nil }
	a.ResolveImage = func(_ context.Context, image string) (string, error) { return image, nil }
	teardown := make(chan struct{})
	stopped := make(chan struct{}, 3)
	started := make(chan *worker.Runner, 3)
	a.Serve = func(ctx context.Context, r *worker.Runner, c client.Capabilities, gpu bool) error {
		wantCPU := int64(400)
		if r.ManagedControl.Mode == "medium" {
			wantCPU = 2000
		}
		if c.OfferedBudget.CPUMillis != wantCPU || gpu {
			t.Error("wrong managed budget/consent", c.OfferedBudget, gpu)
		}
		if _, ok := r.AllowedImages["campus/cpu@sha256:"+strings.Repeat("a", 64)]; !ok {
			t.Error("managed restart lost pinned profile")
		}
		if e := r.OnReady(ctx); e != nil {
			return e
		}
		started <- r
		<-ctx.Done()
		stopped <- struct{}{}
		if r.Client.SessionID == "session-1" {
			<-teardown
		}
		return ctx.Err()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	done := make(chan int, 1)
	profilePath := preparedProfiles(t)
	go func() { done <- a.Run(ctx, []string{"serve", "--managed", "--profiles", profilePath}) }()
	nextAck := func(rev int64, state string) {
		t.Helper()
		select {
		case ack := <-acks:
			if ack.Revision != rev || ack.ActualState != state {
				t.Fatalf("ACK %+v wanted %d/%s", ack, rev, state)
			}
		case <-ctx.Done():
			t.Fatal("ACK timed out", errout)
		}
	}
	nextAck(1, "paused")
	if discoveries.Load() != 0 || sessions.Load() != 0 {
		t.Fatal("paused startup ran preflight")
	}
	mu.Lock()
	desired = client.DeviceSettings{Revision: 2, Mode: "low", DesiredState: "running"}
	mu.Unlock()
	nextAck(2, "running")
	first := <-started
	if unlock, e := session.Lock(dir); e == nil {
		unlock()
		t.Fatal("supervisor lost state lock")
	}
	mu.Lock()
	desired = client.DeviceSettings{Revision: 3, Mode: "low", DesiredState: "paused"}
	mu.Unlock()
	<-stopped
	select {
	case ack := <-acks:
		t.Fatal("pause ACK before teardown", ack)
	case <-time.After(30 * time.Millisecond):
	}
	close(teardown)
	nextAck(3, "paused")
	// Restart uses the invocation snapshot, even if the source is edited.
	os.WriteFile(profilePath, []byte(`{"schema_version":"invalid"}`), 0600)
	action, e := session.ReadControl(dir)
	if e != nil || action != "resume" {
		t.Fatal("remote pause changed local controls", action, e)
	}
	mu.Lock()
	desired = client.DeviceSettings{Revision: 4, Mode: "low", DesiredState: "running"}
	mu.Unlock()
	nextAck(4, "running")
	second := <-started
	if first.Client.SessionID == second.Client.SessionID || sessions.Load() != 2 {
		t.Fatal("resume reused session")
	}
	saved, e := loadIdentity(dir)
	if e != nil || saved.SessionID != second.Client.SessionID {
		t.Fatal("fresh session not persisted", saved, e)
	}
	mu.Lock()
	desired = client.DeviceSettings{Revision: 5, Mode: "medium", DesiredState: "running"}
	mu.Unlock()
	<-stopped
	nextAck(5, "running")
	third := <-started
	if third.Client.SessionID == second.Client.SessionID || sessions.Load() != 3 {
		t.Fatal("mode change did not replace session")
	}
	mu.Lock()
	desired = client.DeviceSettings{Revision: 6, Mode: "low", DesiredState: "running"}
	mu.Unlock()
	<-stopped
	nextAck(6, "running")
	fourth := <-started
	if fourth.Client.SessionID == third.Client.SessionID || sessions.Load() != 4 {
		t.Fatal("explicit smaller web mode did not replace active session")
	}
	if e = session.Control(dir, "stop"); e != nil {
		t.Fatal(e)
	}
	select {
	case code := <-done:
		if code != 0 {
			t.Fatal(code, errout)
		}
	case <-ctx.Done():
		t.Fatal("local stop did not end supervisor")
	}
	action, _ = session.ReadControl(dir)
	if action != "stop" {
		t.Fatal("managed restart overrode local stop")
	}
}

func TestManagedUnavailableSettingsNeverStartsOrAcknowledges(t *testing.T) {
	for _, status := range []int{404, 503} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			a, _, _, dir := testApp(t)
			var calls atomic.Int32
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/v1/protocol" {
					protocol(w)
					return
				}
				if r.URL.Path == "/api/v1/device/settings" {
					calls.Add(1)
					w.WriteHeader(status)
					return
				}
				t.Errorf("unexpected mutation %s", r.URL.Path)
			}))
			defer s.Close()
			saveIdentity(t, dir, s.URL)
			a.ManagedPollInterval = 5 * time.Millisecond
			a.Discover = func(context.Context) (client.Capabilities, error) {
				t.Error("preflight without desired settings")
				return client.Capabilities{}, nil
			}
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			code := a.Run(ctx, []string{"serve", "--managed", "--profiles", preparedProfiles(t)})
			if code != 130 || calls.Load() < 1 {
				t.Fatal(code, calls.Load())
			}
		})
	}
}

func TestManagedSessionRejectsIncompatibleProtocolBeforePreflight(t *testing.T) {
	a, _, _, dir := testApp(t)
	var discoveries int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/protocol" {
			t.Error("incompatible protocol admitted mutation", r.URL.Path)
		}
		fmt.Fprint(w, `{"api_version":1,"min_worker_version":2,"max_worker_version":2,"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	identity, e := loadIdentity(dir)
	if e != nil {
		t.Fatal(e)
	}
	a.Discover = func(context.Context) (client.Capabilities, error) { discoveries++; return client.Capabilities{}, nil }
	a.defaults()
	c, _ := client.New(s.URL, "device", "", s.Client())
	c.Managed = true
	e = a.runManagedSession(context.Background(), config{StateDir: dir}, identity, preparedProfiles(t), false, c, &worker.ManagedControl{Mode: "low"})
	if e == nil || discoveries != 0 {
		t.Fatal("protocol checked after preflight", e, discoveries)
	}
}
