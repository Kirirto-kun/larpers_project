package worker

import (
	"campus/runner/internal/client"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type drainingRuntime struct {
	fixtureRuntime
	entered, finish chan struct{}
}

func (r *drainingRuntime) Wait(ctx context.Context, name string) error {
	close(r.entered)
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-r.finish:
		return r.fixtureRuntime.Wait(ctx, name)
	}
}

func TestManagedDrainFinishesActiveAttemptBeforeStopping(t *testing.T) {
	var claims, started, completed atomic.Int32
	assignment := fixtureAssignment()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/v1/protocol":
			fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
		case r.URL.Path == "/api/v1/work/next":
			if claims.Add(1) == 1 {
				json.NewEncoder(w).Encode(assignment)
			} else {
				fmt.Fprint(w, "null")
			}
		case strings.HasSuffix(r.URL.Path, "/started") || strings.HasSuffix(r.URL.Path, "/renew"):
			if strings.HasSuffix(r.URL.Path, "/started") {
				started.Add(1)
			}
			fmt.Fprint(w, `{"attempt_id":"attempt","state":"running","lease_expires_at":"2026-11-15T00:00:00Z","lease_remaining_ms":3000}`)
		case strings.HasSuffix(r.URL.Path, "/complete"):
			completed.Add(1)
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-15T00:00:00Z"}`)
		case strings.HasSuffix(r.URL.Path, "/logs"), strings.HasSuffix(r.URL.Path, "/telemetry"):
			w.WriteHeader(204)
		default:
			t.Errorf("unexpected drain request %s", r.URL.Path)
			w.WriteHeader(404)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node-credential", "session", s.Client())
	c.Managed = true
	runtime := &drainingRuntime{entered: make(chan struct{}), finish: make(chan struct{})}
	control := &ManagedControl{Mode: "medium"}
	r := &Runner{Client: c, Runtime: runtime, StateDir: t.TempDir(), ManagedControl: control, AllowedImages: map[string]string{assignment.ImageDigest: assignment.Platform}}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- r.Serve(ctx, client.Capabilities{OfferedBudget: assignment.ResourceLimits}, false) }()
	select {
	case <-runtime.entered:
	case <-ctx.Done():
		t.Fatal("active attempt never started")
	}
	control.Drain()
	select {
	case e := <-done:
		t.Fatal("drain interrupted active attempt", e)
	case <-time.After(150 * time.Millisecond):
	}
	close(runtime.finish)
	select {
	case e := <-done:
		if e != nil || completed.Load() != 1 || started.Load() != 1 {
			t.Fatal(e, completed.Load(), started.Load())
		}
	case <-ctx.Done():
		t.Fatal("completed drain did not stop")
	}
	if !runtime.stopped.Load() || !runtime.removed.Load() {
		t.Fatal("drain returned before runtime teardown")
	}
}
