package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type failedStartupRuntime struct{ fixtureRuntime }

func (f *failedStartupRuntime) Start(context.Context, string) error { return nil }
func (f *failedStartupRuntime) Logs(_ context.Context, _ string, out io.Writer) error {
	// A guard may exit before publishing its first challenge. Include a final
	// unterminated line and more than one protocol chunk in its diagnostics.
	_, err := fmt.Fprint(out, "guard startup exception: node-credential lease-secret\n"+strings.Repeat("startup detail\n", 1000)+"final guard traceback")
	return err
}

func TestStartupFailurePublishesRedactedLogsWithoutGrant(t *testing.T) {
	var mu sync.Mutex
	var received []generated.LogWrite
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.URL.Path != "/api/v1/attempts/attempt/logs" {
			t.Errorf("unexpected startup mutation: %s", req.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var body generated.LogWrite
		decoder := json.NewDecoder(req.Body)
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&body); err != nil {
			t.Error(err)
		}
		if req.Header.Get("X-Node-Session") != "session" || body.Generation != 2 || body.LeaseToken != "lease-secret" || len(body.Text) > 8192 {
			t.Error("startup logs lost fencing or exceeded the protocol chunk limit")
		}
		mu.Lock()
		received = append(received, body)
		mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	api, err := client.New(server.URL, "node-credential", "session", server.Client())
	if err != nil {
		t.Fatal(err)
	}
	runtime := &failedStartupRuntime{}
	assignment := fixtureAssignment()
	assignment.HeartbeatIntervalMS = 1000
	runner := Runner{Client: api, Runtime: runtime, StateDir: t.TempDir(), AllowedImages: map[string]string{assignment.ImageDigest: assignment.Platform}}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	if _, err = runner.RunAssignment(ctx, assignment); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected the ungranted startup to reach its canceled wait, got %v", err)
	}
	mu.Lock()
	defer mu.Unlock()
	var combined strings.Builder
	for i, body := range received {
		if body.Sequence != int64(i+1) {
			t.Error("startup log sequence is not monotonic")
		}
		combined.WriteString(body.Text)
	}
	text := combined.String()
	if !strings.Contains(text, "guard startup exception:") || !strings.Contains(text, "final guard traceback") {
		t.Fatalf("pre-grant diagnostics were discarded: received %d chunks", len(received))
	}
	if strings.Contains(text, "node-credential") || strings.Contains(text, "lease-secret") {
		t.Fatal("startup diagnostics exposed a credential")
	}
	if !runtime.stopped.Load() || !runtime.removed.Load() {
		t.Fatal("collecting startup logs skipped protected runtime cleanup")
	}
}
