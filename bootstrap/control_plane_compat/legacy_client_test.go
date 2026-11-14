// Injected into runner/internal/client only through go test -overlay.
package client

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func TestDiagnosticRichServerFixtureAcceptedByExistingClient(t *testing.T) {
	fixture, err := os.ReadFile(os.Getenv("CAMPUS_DIAGNOSTIC_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(fixture)
	}))
	defer server.Close()
	c, err := New(server.URL, "fixture-device", "fixture-session", server.Client())
	if err != nil {
		t.Fatal(err)
	}
	a, err := c.ClaimWork(context.Background(), 0)
	if err != nil {
		t.Fatal(err)
	}
	if a == nil {
		t.Fatal("missing assignment")
	}
	if err := a.Validate(); err != nil {
		t.Fatal(err)
	}
	wantMemory := int64(268435456)
	if a.Adapter.ID == "campus.diagnostic.cuda" {
		wantMemory = 1073741824
		if a.Platform != "linux/amd64" || a.ResourceLimits.VRAMBytes != 1073741824 || len(a.ResourceLimits.GPUIDs) != 1 || a.ResourceLimits.GPUIDs[0] != "0" {
			t.Fatal("wrong bounded CUDA assignment")
		}
	}
	if a.ResourceLimits.MemoryBytes != wantMemory || a.ResourceLimits.CPUMillis != 1000 || len(a.Outputs) != 1 || a.Outputs[0] != "model.json" {
		t.Fatal("wrong bounded rich assignment")
	}
}
