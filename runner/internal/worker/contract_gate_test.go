package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"
)

type forbiddenCreate struct {
	fixtureRuntime
	calls atomic.Int32
}

func (r *forbiddenCreate) Create(context.Context, client.Assignment, containers.Paths) (string, error) {
	r.calls.Add(1)
	return "", errors.New("unsafe create reached")
}

func TestSchemaValidFoundationAssignmentStopsBeforeStartedOrContainer(t *testing.T) {
	raw, err := os.ReadFile("../client/testdata/foundation-assignment.json")
	if err != nil {
		t.Fatal(err)
	}
	var started, claims atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
		case "/api/v1/work/next":
			claims.Add(1)
			w.Write(raw)
		default:
			started.Add(1)
			w.WriteHeader(204)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	runtime := &forbiddenCreate{}
	r := Runner{Client: c, Runtime: runtime, StateDir: t.TempDir()}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	err = r.Serve(ctx, client.Capabilities{OfferedBudget: fixtureAssignment().ResourceLimits}, false)
	if !errors.Is(err, client.ErrWorkerContractIncomplete) || runtime.calls.Load() != 0 || started.Load() != 0 || claims.Load() != 1 {
		t.Fatalf("invalid admission: create=%d mutation=%d claim=%d err=%v", runtime.calls.Load(), started.Load(), claims.Load(), err)
	}
}
