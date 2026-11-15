package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/session"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestReleaseUnstartedUsesV1ProofOnly(t *testing.T) {
	var called atomic.Bool
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		var body generated.AttemptProof
		decoder := json.NewDecoder(req.Body)
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&body); err != nil || body.Generation != 2 || body.LeaseToken != "lease-secret" || req.URL.Path != "/api/v1/attempts/attempt/release" {
			t.Errorf("release does not match v1 proof: %+v, %v", body, err)
		}
		called.Store(true)
		fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"retry_wait"}`)
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	r := Runner{Client: c}
	r.releaseUnstarted(context.Background(), fixtureAssignment(), "drain")
	if !called.Load() {
		t.Fatal("unstarted assignment was not released")
	}
}

type rejectedRuntime struct{ fixtureRuntime }

func (*rejectedRuntime) VerifyImage(context.Context, string, string) error {
	return errors.New("image unavailable")
}

func TestServeUsesV1StartedAndFailureBodies(t *testing.T) {
	var started, failed atomic.Bool
	var polls atomic.Int32
	dir := t.TempDir()
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		switch {
		case req.URL.Path == "/api/v1/protocol":
			fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
		case req.URL.Path == "/api/v1/work/next":
			if polls.Add(1) == 1 {
				json.NewEncoder(w).Encode(fixtureAssignment())
			} else {
				w.WriteHeader(http.StatusNoContent)
			}
		case strings.HasSuffix(req.URL.Path, "/started"):
			var body generated.AttemptProof
			decoder := json.NewDecoder(req.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&body); err != nil || body.Generation != 2 || body.LeaseToken != "lease-secret" {
				t.Errorf("started does not match v1 proof: %+v, %v", body, err)
			}
			started.Store(true)
			fmt.Fprint(w, `{"attempt_id":"attempt","state":"running","lease_expires_at":"2026-11-14T00:01:00Z","lease_remaining_ms":3000}`)
		case strings.HasSuffix(req.URL.Path, "/fail"):
			var body generated.Failure
			decoder := json.NewDecoder(req.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&body); err != nil || body.Generation != 2 || body.LeaseToken != "lease-secret" || body.Code != "EXECUTION_FAILED" || body.Retryable == nil || *body.Retryable {
				t.Errorf("failure does not match v1: %+v, %v", body, err)
			}
			failed.Store(true)
			session.Control(dir, "stop")
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"failed","error_code":"EXECUTION_FAILED"}`)
		default:
			w.WriteHeader(http.StatusNoContent)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	a := fixtureAssignment()
	r := Runner{Client: c, Runtime: &rejectedRuntime{}, StateDir: dir, AllowedImages: map[string]string{a.ImageDigest: a.Platform}}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := r.Serve(ctx, client.Capabilities{OfferedBudget: a.ResourceLimits}, false); err != nil || !started.Load() || !failed.Load() {
		t.Fatalf("started/fail lifecycle missing: started=%v failed=%v err=%v", started.Load(), failed.Load(), err)
	}
}

func TestV1RenewalCannotExtendKnownHardDeadline(t *testing.T) {
	var renewals atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if strings.HasSuffix(req.URL.Path, "/renew") {
			var proof generated.AttemptProof
			decoder := json.NewDecoder(req.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&proof); err != nil || proof.Generation != 2 || proof.LeaseToken != "lease-secret" {
				t.Errorf("renew does not match v1 proof: %+v, %v", proof, err)
			}
			renewals.Add(1)
			fmt.Fprint(w, `{"attempt_id":"attempt","state":"running","lease_expires_at":"2026-11-14T00:01:00Z","lease_remaining_ms":5000}`)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	a := fixtureAssignment()
	a.TaskRemainingMS = 1200
	runtime := &fixtureRuntime{stale: true}
	r := Runner{Client: c, Runtime: runtime, StateDir: t.TempDir(), AllowedImages: map[string]string{a.ImageDigest: a.Platform}}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	start := time.Now()
	_, err := r.RunAssignment(ctx, a)
	elapsed := time.Since(start)
	if err == nil || elapsed < 700*time.Millisecond || elapsed > 1800*time.Millisecond || renewals.Load() < 3 || !runtime.stopped.Load() {
		t.Fatalf("v1 renewal lost or extended local hard deadline: err=%v elapsed=%v renewals=%d", err, elapsed, renewals.Load())
	}
}
