package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/session"
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

func TestManagedAmbientRefreshKeepsActiveWorkAndConstrainsNewAdmission(t *testing.T) {
	testManagedAmbientRefresh(t, false, false)
}

func TestLocalStopCancelsActiveWorkAfterAmbientRefresh(t *testing.T) {
	testManagedAmbientRefresh(t, true, false)
}

func TestManagedOneMillicoreSamplingNoiseDoesNotCancelWork(t *testing.T) {
	testManagedAmbientRefresh(t, false, true)
}

func testManagedAmbientRefresh(t *testing.T, localStop, noise bool) {
	t.Helper()
	var claims, publishes, started, released, completed atomic.Int32
	activeAssignment := fixtureAssignment()
	blocked := activeAssignment
	blocked.AttemptID = "blocked"
	blocked.TaskID = "blocked-task"
	blocked.ResourceLimits.CPUMillis = 250
	wantCPU := int64(500)
	if noise {
		blocked.ResourceLimits.CPUMillis = 3000
		wantCPU = 3999
	}
	runtime := &drainingRuntime{entered: make(chan struct{}), finish: make(chan struct{})}
	control := &ManagedControl{Mode: "medium"}
	published := make(chan struct{}, 1)
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/v1/protocol":
			fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
		case r.URL.Path == "/api/v1/work/next":
			if claims.Add(1) == 1 {
				json.NewEncoder(w).Encode(activeAssignment)
			} else if publishes.Load() > 0 && released.Load() == 0 {
				json.NewEncoder(w).Encode(blocked)
			} else {
				fmt.Fprint(w, "null")
			}
		case strings.HasSuffix(r.URL.Path, "/capabilities"):
			var caps generated.Capabilities
			json.NewDecoder(r.Body).Decode(&caps)
			if caps.OfferedBudget.CPUMillis != wantCPU {
				t.Error("refresh lost managed medium mode", caps.OfferedBudget)
			}
			publishes.Add(1)
			w.WriteHeader(204)
			select {
			case published <- struct{}{}:
			default:
			}
		case strings.HasSuffix(r.URL.Path, "/started") || strings.HasSuffix(r.URL.Path, "/renew"):
			if strings.HasSuffix(r.URL.Path, "/started") {
				started.Add(1)
				if r.URL.Path != "/api/v1/attempts/attempt/started" {
					t.Error("ambient reduction admitted work above remaining budget")
				}
			}
			fmt.Fprint(w, `{"attempt_id":"attempt","state":"running","lease_expires_at":"2026-11-15T00:00:00Z","lease_remaining_ms":3000}`)
		case strings.HasSuffix(r.URL.Path, "/release"):
			released.Add(1)
			w.WriteHeader(204)
		case strings.HasSuffix(r.URL.Path, "/complete"):
			completed.Add(1)
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-15T00:00:00Z"}`)
		default:
			w.WriteHeader(204)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "device", "session", s.Client())
	c.Managed = true
	initial := refreshCapabilitiesFixture()
	initial.OfferedBudget.CPUMillis = 2000
	initial.OfferedBudget.MemoryBytes = 4 << 30
	if noise {
		initial.CPUAvailableMillis = 8000
		initial.OfferedBudget.CPUMillis = 4000
	}
	r := &Runner{Client: c, StateDir: t.TempDir(), Runtime: runtime, ManagedControl: control, AllowedImages: map[string]string{activeAssignment.ImageDigest: activeAssignment.Platform}, RefreshInterval: 10 * time.Millisecond, Discover: func(context.Context) (client.Capabilities, error) {
		next := initial
		next.CPUAvailableMillis = 1000
		next.MemoryAvailableBytes = 1 << 30
		if noise {
			next.CPUAvailableMillis = 7999
			next.MemoryAvailableBytes = 8 << 30
		}
		return next, nil
	}}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- r.Serve(ctx, initial, false) }()
	select {
	case <-runtime.entered:
	case <-ctx.Done():
		t.Fatal("attempt never started")
	}
	select {
	case <-published:
	case e := <-done:
		t.Fatal("ambient sample canceled active work", e)
	case <-ctx.Done():
		t.Fatal("refresh never published")
	}
	deadline := time.NewTimer(time.Second)
	defer deadline.Stop()
	wait := time.NewTicker(5 * time.Millisecond)
	defer wait.Stop()
	for released.Load() == 0 {
		select {
		case e := <-done:
			t.Fatal("active work interrupted before completion", e)
		case <-deadline.C:
			t.Fatal("no rejection while reservations exceed sampled capacity")
		case <-wait.C:
		}
	}
	if runtime.stopped.Load() || started.Load() != 1 {
		t.Fatal("normal self-use canceled/admitted work", started.Load())
	}
	if localStop {
		if e := session.Control(r.StateDir, "stop"); e != nil {
			t.Fatal(e)
		}
	} else {
		control.Drain()
		close(runtime.finish)
	}
	select {
	case e := <-done:
		wantCompleted := int32(1)
		if localStop {
			wantCompleted = 0
		}
		if e != nil || completed.Load() != wantCompleted || !runtime.stopped.Load() {
			t.Fatal("active work failed after ambient reduction", e, completed.Load())
		}
	case <-ctx.Done():
		t.Fatal("active work did not complete")
	}
}

func refreshCapabilitiesFixture() client.Capabilities {
	return client.Capabilities{OS: "windows", Arch: "amd64", CPULogical: 8, CPUAvailableMillis: 4000, DockerCPUMillis: 8000, DockerMemoryBytes: 8 << 30, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 20 << 30, PowerState: "ac", PreflightRevision: "1", Runtime: client.Runtime{Type: "docker", Version: "fixture", Healthy: true}, OfferedBudget: client.ResourceLimits{CPUMillis: 400, MemoryBytes: 858993459}}
}

func TestRefreshAdvancesRevisionAndRejectsAdmissionUntilIncreaseAcknowledged(t *testing.T) {
	var publishes, releases, started, completed atomic.Int32
	var previousRevision int64 = 1
	control := &ManagedControl{Mode: "low"}
	assignment := fixtureAssignment()
	assignment.ResourceLimits.CPUMillis = 600
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/v1/protocol":
			fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
		case strings.HasSuffix(r.URL.Path, "/capabilities"):
			var caps generated.Capabilities
			json.NewDecoder(r.Body).Decode(&caps)
			n := publishes.Add(1)
			if caps.PreflightRevision <= previousRevision {
				w.WriteHeader(409)
				fmt.Fprint(w, `{"code":"STALE_PREFLIGHT","message":"changed capabilities reused revision"}`)
				return
			}
			previousRevision = caps.PreflightRevision
			if n == 1 {
				w.WriteHeader(503)
				fmt.Fprint(w, `{"code":"TEMPORARY","message":"not acknowledged"}`)
				return
			}
			w.WriteHeader(204)
		case r.URL.Path == "/api/v1/work/next":
			if publishes.Load() == 1 && releases.Load() == 0 {
				a := assignment
				a.AttemptID = "before-ack"
				json.NewEncoder(w).Encode(a)
			} else if publishes.Load() >= 2 && started.Load() == 0 {
				json.NewEncoder(w).Encode(assignment)
			} else {
				fmt.Fprint(w, "null")
			}
		case strings.HasSuffix(r.URL.Path, "/release"):
			releases.Add(1)
			w.WriteHeader(204)
		case strings.HasSuffix(r.URL.Path, "/started") || strings.HasSuffix(r.URL.Path, "/renew"):
			if strings.HasSuffix(r.URL.Path, "/started") {
				started.Add(1)
				if publishes.Load() < 2 || strings.Contains(r.URL.Path, "before-ack") {
					t.Error("unacknowledged capacity increase admitted work")
				}
			}
			fmt.Fprint(w, `{"attempt_id":"attempt","state":"running","lease_expires_at":"2026-11-15T00:00:00Z","lease_remaining_ms":3000}`)
		case strings.HasSuffix(r.URL.Path, "/complete"):
			completed.Add(1)
			control.Drain()
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-15T00:00:00Z"}`)
		default:
			w.WriteHeader(204)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node-credential", "session", s.Client())
	c.Managed = true
	c.Attempts = 1
	initial := refreshCapabilitiesFixture()
	r := &Runner{Client: c, StateDir: t.TempDir(), Runtime: &fixtureRuntime{}, ManagedControl: control, RefreshInterval: 10 * time.Millisecond, AllowedImages: map[string]string{assignment.ImageDigest: assignment.Platform}, Discover: func(context.Context) (client.Capabilities, error) {
		next := initial
		next.CPUAvailableMillis = 8000
		return next, nil
	}}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	e := r.Serve(ctx, initial, false)
	if e != nil || releases.Load() != 0 || started.Load() != 1 || completed.Load() != 1 || previousRevision < 3 {
		t.Fatal("capability acknowledgement/revision regression", e, publishes.Load(), releases.Load(), started.Load(), completed.Load(), previousRevision)
	}
}

func TestManagedRefreshKeepsGPUAndBatteryConsentLocal(t *testing.T) {
	before := client.Capabilities{AllowOnBattery: false, OwnerCPUMillis: 300, OwnerMemoryBytes: 1 << 30, ImageDigests: []string{"approved"}, GPUs: []client.GPU{{ID: "0", CUDAVerified: true}}}
	next := client.Capabilities{CPULogical: 8, CPUAvailableMillis: 8000, DockerCPUMillis: 8000, DockerMemoryBytes: 8 << 30, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 20 << 30, PowerState: "ac", GPUs: []client.GPU{{ID: "0", CUDAVerified: false, VRAMBytes: 8 << 30}, {ID: "1", CUDAVerified: true, VRAMBytes: 8 << 30}}}
	r := &Runner{ManagedControl: &ManagedControl{Mode: "max"}}
	updated, e := r.refreshCapabilities(before, next, false)
	if e != nil || updated.OfferedBudget.CPUMillis != 300 || updated.OfferedBudget.MemoryBytes != 1<<30 || len(updated.OfferedBudget.GPUIDs) != 0 || updated.GPUs[1].CUDAVerified {
		t.Fatal(updated, e)
	}
	next.PowerState = "battery"
	next.AllowOnBattery = true
	updated, e = r.refreshCapabilities(before, next, true)
	if e != nil || updated.OfferedBudget.CPUMillis != 0 || updated.AllowOnBattery {
		t.Fatal("refresh granted battery consent", updated, e)
	}
}
