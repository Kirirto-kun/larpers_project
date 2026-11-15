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

func TestFailedReductionGatesClaimsUntilPublishOrLocalStop(t *testing.T) {
	for _, localStop := range []bool{false, true} {
		t.Run(fmt.Sprint(localStop), func(t *testing.T) {
			var publishes, pendingClaims, releases, started atomic.Int32
			var revision int64 = 1
			failed := make(chan struct{}, 1)
			allow := make(chan struct{})
			assignment := fixtureAssignment()
			assignment.ResourceLimits.CPUMillis = 100
			control := &ManagedControl{Mode: "low"}
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.URL.Path == "/api/v1/protocol":
					fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
				case strings.HasSuffix(r.URL.Path, "/capabilities"):
					var caps generated.Capabilities
					json.NewDecoder(r.Body).Decode(&caps)
					if caps.PreflightRevision <= revision {
						t.Error("nonmonotonic capability revision", caps.PreflightRevision, revision)
					}
					revision = caps.PreflightRevision
					if publishes.Add(1) == 1 {
						w.WriteHeader(503)
						fmt.Fprint(w, `{"code":"TEMPORARY","message":"pending"}`)
						failed <- struct{}{}
						return
					}
					select {
					case <-allow:
					case <-r.Context().Done():
						return
					}
					w.WriteHeader(204)
				case r.URL.Path == "/api/v1/work/next":
					if publishes.Load() == 1 {
						pendingClaims.Add(1)
					}
					if publishes.Load() >= 2 && started.Load() == 0 {
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
					}
					fmt.Fprint(w, `{"attempt_id":"attempt","state":"running","lease_expires_at":"2026-11-15T00:00:00Z","lease_remaining_ms":3000}`)
				case strings.HasSuffix(r.URL.Path, "/complete"):
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
			r := &Runner{Client: c, StateDir: t.TempDir(), Runtime: &fixtureRuntime{}, ManagedControl: control, RefreshInterval: 20 * time.Millisecond, AllowedImages: map[string]string{assignment.ImageDigest: assignment.Platform}, Discover: func(context.Context) (client.Capabilities, error) {
				next := initial
				next.CPUAvailableMillis = 2000
				return next, nil
			}}
			ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
			defer cancel()
			done := make(chan error, 1)
			go func() { done <- r.Serve(ctx, initial, false) }()
			select {
			case <-failed:
			case <-ctx.Done():
				t.Fatal("failed publication not exercised")
			}
			if localStop {
				if e := session.Control(r.StateDir, "stop"); e != nil {
					t.Fatal(e)
				}
			} else {
				time.Sleep(80 * time.Millisecond)
				close(allow)
			}
			select {
			case e := <-done:
				if e != nil {
					t.Fatal(e)
				}
			case <-ctx.Done():
				t.Fatal("publication gate blocked stop/recovery")
			}
			if pendingClaims.Load() != 0 || releases.Load() != 0 {
				t.Fatal("unacknowledged reduction consumed claims/attempts", pendingClaims.Load(), releases.Load())
			}
			if !localStop && (started.Load() != 1 || revision < 3) {
				t.Fatal("claims did not resume after acknowledged fresh revision", started.Load(), revision)
			}
		})
	}
}
