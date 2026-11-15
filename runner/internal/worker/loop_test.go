package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func (f *fixtureRuntime) Cleanup(context.Context) error { return nil }
func TestDrainDuringHeldPollCannotStartNewWork(t *testing.T) {
	entered := make(chan struct{}, 1)
	var started atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		io.Copy(io.Discard, req.Body)
		switch {
		case req.URL.Path == "/api/v1/protocol":
			fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
		case req.URL.Path == "/api/v1/work/next":
			entered <- struct{}{}
			select {
			case <-req.Context().Done():
				return
			case <-time.After(300 * time.Millisecond):
				json.NewEncoder(w).Encode(fixtureAssignment())
			}
		case strings.HasSuffix(req.URL.Path, "/started"):
			started.Add(1)
			w.WriteHeader(204)
		default:
			w.WriteHeader(204)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	a := fixtureAssignment()
	dir := t.TempDir()
	r := Runner{Client: c, Runtime: &fixtureRuntime{}, StateDir: dir, AllowedImages: map[string]string{a.ImageDigest: a.Platform}}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- r.Serve(ctx, client.Capabilities{OfferedBudget: a.ResourceLimits}, false) }()
	select {
	case <-entered:
	case <-ctx.Done():
		t.Fatal("poll never started")
	}
	if e := session.Control(dir, "drain"); e != nil {
		t.Fatal(e)
	}
	select {
	case e := <-done:
		if e != nil || started.Load() != 0 {
			t.Fatal("drain admitted new work", started.Load(), e)
		}
	case <-time.After(time.Second):
		cancel()
		<-done
		t.Fatal("drain blocked on long poll")
	}
}

func TestManagedRemoteDrainCancelsPollWithoutChangingLocalControl(t *testing.T) {
	entered := make(chan struct{}, 1)
	var started atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		switch {
		case req.URL.Path == "/api/v1/protocol":
			fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
		case req.URL.Path == "/api/v1/work/next":
			select {
			case entered <- struct{}{}:
			default:
			}
			select {
			case <-req.Context().Done():
			case <-time.After(300 * time.Millisecond):
				json.NewEncoder(w).Encode(fixtureAssignment())
			}
		case strings.HasSuffix(req.URL.Path, "/started"):
			started.Add(1)
			w.WriteHeader(204)
		default:
			w.WriteHeader(204)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "device", "session", s.Client())
	c.Managed = true
	dir := t.TempDir()
	session.Control(dir, "resume")
	control := &ManagedControl{Mode: "low"}
	r := &Runner{Client: c, StateDir: dir, Runtime: &fixtureRuntime{}, ManagedControl: control}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- r.Serve(ctx, client.Capabilities{OfferedBudget: fixtureAssignment().ResourceLimits}, false)
	}()
	<-entered
	control.Drain()
	select {
	case e := <-done:
		if e != nil || started.Load() != 0 {
			t.Fatal("remote drain admitted work", e, started.Load())
		}
	case <-ctx.Done():
		t.Fatal("remote drain did not interrupt long poll")
	}
	action, e := session.ReadControl(dir)
	if e != nil || action != "resume" {
		t.Fatal("remote drain changed local control", action, e)
	}
}

func TestManagedChildNeverResetsLocalLeaveOrAcknowledgesReadiness(t *testing.T) {
	for _, action := range []string{"stop", "drain"} {
		t.Run(action, func(t *testing.T) {
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/v1/protocol" {
					fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
					return
				}
				t.Errorf("local leave admitted request %s", r.URL.Path)
			}))
			defer s.Close()
			c, _ := client.New(s.URL, "device", "session", s.Client())
			c.Managed = true
			dir := t.TempDir()
			session.Control(dir, action)
			r := &Runner{Client: c, StateDir: dir, Runtime: &fixtureRuntime{}, ManagedControl: &ManagedControl{Mode: "low"}, OnReady: func(context.Context) error { t.Error("stopped child acknowledged running"); return nil }}
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			if e := r.Serve(ctx, client.Capabilities{}, false); e != nil {
				t.Fatal(e)
			}
			got, _ := session.ReadControl(dir)
			if got != action {
				t.Fatal("child reset owner command", got)
			}
		})
	}
}
