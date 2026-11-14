package client

import (
	"bytes"
	"campus/runner/internal/generated"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func TestPublishedFoundationProtocolDefaultsAndSessionHeader(t *testing.T) {
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Node-Session") != "session" || r.Header.Get("X-Node-Session-ID") != "" {
			t.Error("session header differs from published OpenAPI")
		}
		fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
	}))
	defer s.Close()
	c, _ := New(s.URL, "device", "session", s.Client())
	if err := c.Handshake(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestPublishedWorkClaimDoesNotInventHardDeadline(t *testing.T) {
	fixture, err := os.ReadFile("testdata/foundation-assignment.json")
	if err != nil {
		t.Fatal(err)
	}
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request generated.WorkRequest
		d := json.NewDecoder(r.Body)
		d.DisallowUnknownFields()
		if err := d.Decode(&request); err != nil {
			t.Error(err)
		}
		// Published Assignment has task_deadline_at, but no task_remaining_ms.
		w.Write(fixture)
	}))
	defer s.Close()
	c, _ := New(s.URL, "device", "session", s.Client())
	a, err := c.ClaimWork(context.Background(), 25000)
	if a != nil || !errors.Is(err, ErrWorkerContractIncomplete) {
		t.Fatalf("unsafe work admitted: %+v %v", a, err)
	}
}

func TestPublishedFoundationProtocolRejectsInvalidValues(t *testing.T) {
	for _, raw := range []string{
		`{"api_version":2,"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`,
		`{"min_worker_version":2,"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`,
		`{"api_version":"1","lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`,
		`{"lease_ms":0,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`,
		`{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":-1}`,
	} {
		t.Run(raw, func(t *testing.T) {
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, raw) }))
			defer s.Close()
			c, _ := New(s.URL, "", "", s.Client())
			if c.Handshake(context.Background()) == nil {
				t.Fatal("incompatible protocol accepted")
			}
		})
	}
}

func TestCapabilitiesUseGeneratedFoundationBudget(t *testing.T) {
	c := Capabilities{OS: "windows", Arch: "amd64", CPULogical: 24, PreflightRevision: "2", PowerState: "ac", Runtime: Runtime{Type: "docker", Version: "29.2.1", Healthy: true}, ImageDigests: []string{"campus/cpu@sha256:" + strings.Repeat("a", 64)}, Diagnostics: []string{"local-only"}, OfferedBudget: ResourceLimits{CPUMillis: 1000, MemoryBytes: 1 << 30, WorkspaceBytes: 128, OutputBytes: 32, CheckpointBytes: 32, TmpBytes: 64, PIDs: 128}}
	wire, err := c.Contract()
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(wire)
	var decoded generated.Capabilities
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err = d.Decode(&decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.PreflightRevision != 2 || decoded.OfferedBudget.DiskBytes != 256 || bytes.Contains(raw, []byte("diagnostics")) || bytes.Contains(raw, []byte("pids")) {
		t.Fatalf("wrong wire DTO: %s", raw)
	}
	c.ImageDigests = []string{"sha256:" + strings.Repeat("a", 64)}
	if _, err = c.Contract(); err == nil {
		t.Fatal("local image ID advertised as contract registry digest")
	}
}

func TestArtifactUsesPublishedFields(t *testing.T) {
	const raw = `{"id":"00000000-0000-4000-8000-000000000001","name":"workspace/model.pt","size_bytes":3,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","media_type":"application/octet-stream"}`
	var a Artifact
	if err := json.Unmarshal([]byte(raw), &a); err != nil {
		t.Fatal(err)
	}
	if a.ArtifactID == "" || a.Path != "workspace/model.pt" || a.Digest == "" {
		t.Fatalf("lost artifact identity: %+v", a)
	}
	b, err := json.Marshal(a)
	if err != nil {
		t.Fatal(err)
	}
	var target generated.Artifact
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if err = d.Decode(&target); err != nil {
		t.Fatal(err)
	}
	if target.ID != a.ArtifactID || target.SHA256 != a.Digest {
		t.Fatal("artifact identity changed")
	}
}
