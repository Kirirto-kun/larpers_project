package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"runtime"
	"testing"
	"time"
)

func TestNodeMetricsPostsFoundationDTOWithNativeAvailability(t *testing.T) {
	got := make(chan generated.Telemetry, 1)
	errors := make(chan error, 1)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body generated.Telemetry
		decoder := json.NewDecoder(r.Body)
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&body); err != nil {
			errors <- err
			w.WriteHeader(422)
			return
		}
		got <- body
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	c, err := client.New(server.URL, "credential", "session", server.Client())
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { defer close(done); (&Runner{Client: c}).nodeMetrics(ctx) }()
	select {
	case err := <-errors:
		t.Fatal("foundation rejected node telemetry", err)
	case body := <-got:
		if body.Sequence != 1 || body.Phase != "idle" || body.CPUPercent == nil || *body.CPUPercent < 0 || *body.CPUPercent > 100 || body.GPUPercent != nil {
			t.Fatalf("incorrect native node telemetry: %+v", body)
		}
		if runtime.GOOS == "darwin" {
			if body.MemoryBytes != nil {
				t.Fatal("macOS has no current native RSS measurement")
			}
		} else if body.MemoryBytes == nil || *body.MemoryBytes <= 0 {
			t.Fatal("lost measured native RSS")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("no node telemetry")
	}
	cancel()
	<-done
}
