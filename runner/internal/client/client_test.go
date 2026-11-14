package client

import (
	"campus/runner/internal/generated"
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

func TestClientHandshakeAndAuthNeverFollowRedirect(t *testing.T) {
	var leaked atomic.Bool
	other := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaked.Store(true) }))
	defer other.Close()
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			json.NewEncoder(w).Encode(map[string]any{"api_version": 1, "min_worker_version": 1, "max_worker_version": 1, "lease_ms": 60000, "heartbeat_interval_ms": 10000, "long_poll_ms": 25000})
			return
		}
		if r.Header.Get("Authorization") != "Bearer secret" {
			t.Error("missing auth")
		}
		http.Redirect(w, r, other.URL, http.StatusFound)
	}))
	defer server.Close()
	c, err := New(server.URL, "secret", "session", server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if err = c.Handshake(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err = c.Do(context.Background(), "POST", "/work/next", nil, nil, ""); err == nil {
		t.Fatal("redirect accepted")
	}
	if leaked.Load() {
		t.Fatal("credential sent to another origin")
	}
}

func TestForbiddenAndStaleAreNotRetried(t *testing.T) {
	for _, code := range []string{"FORBIDDEN", "STALE_ATTEMPT", "SESSION_REPLACED"} {
		t.Run(code, func(t *testing.T) {
			var calls atomic.Int32
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				w.WriteHeader(403)
				fmt.Fprintf(w, `{"code":%q,"message":"denied","retryable":true}`, code)
			}))
			defer s.Close()
			c, _ := New(s.URL, "token", "s", s.Client())
			c.Backoff = time.Millisecond
			err := c.Do(context.Background(), "POST", "/work/next", nil, nil, "")
			if err == nil || calls.Load() != 1 {
				t.Fatalf("terminal request retried %d: %v", calls.Load(), err)
			}
		})
	}
}

func TestCompleteRecoversReceiptAfterLostACK(t *testing.T) {
	var completes atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/attempts/attempt/complete":
			var body generated.Complete
			decoder := json.NewDecoder(r.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&body); err != nil || body.Generation != 2 || body.LeaseToken != "lease" || len(body.Manifest.Outputs) != 1 || body.Manifest.Outputs[0].Path != "model.pt" {
				t.Errorf("completion does not match v1: %+v, %v", body, err)
			}
			completes.Add(1)
			hj := w.(http.Hijacker)
			conn, _, _ := hj.Hijack()
			conn.Close()
		case "/api/v1/attempts/attempt":
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"verifying","receipt":{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-14T00:00:00Z"}}`)
		default:
			t.Error(r.URL.Path)
		}
	}))
	defer s.Close()
	c, _ := New(s.URL, "token", "s", s.Client())
	c.Backoff = time.Millisecond
	receipt, err := c.Complete(context.Background(), Fence{AttemptID: "attempt", Generation: 2, LeaseToken: "lease"}, generated.ResultManifest{Outputs: []generated.OutputRef{{Path: "model.pt", ArtifactID: "artifact"}}})
	raw, _ := json.Marshal(receipt)
	var fields map[string]any
	json.Unmarshal(raw, &fields)
	if err != nil || fields["task_id"] != "task" || fields["submitted_at"] != "2026-11-14T00:00:00Z" || completes.Load() != 1 {
		t.Fatalf("lost ACK was not reconciled: %+v, %v, calls=%d", receipt, err, completes.Load())
	}
}

func TestEndpointAndVersionAreValidated(t *testing.T) {
	for _, endpoint := range []string{"http://example.com", "https://user:pass@example.com", "https://example.com?token=x", "ftp://example.com", "https://example.com/other"} {
		if _, err := New(endpoint, "", "", nil); err == nil {
			t.Errorf("unsafe endpoint accepted: %s", endpoint)
		}
	}
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `{"api_version":"2","min_runner_version":"0.1.0","max_runner_version":"0.1.99"}`)
	}))
	defer s.Close()
	c, _ := New(s.URL, "", "", s.Client())
	if err := c.Handshake(context.Background()); err == nil || !strings.Contains(err.Error(), "version") {
		t.Fatal("incompatible server accepted")
	}
}

func TestAttemptFencingDoesNotRevokeHealthySession(t *testing.T) {
	for _, code := range []string{"STALE_ATTEMPT", "LEASE_EXPIRED"} {
		e := &APIError{Status: 409, Code: code}
		if !Terminal(e) || SessionFatal(e) {
			t.Fatal("attempt fence became a node failure", code)
		}
	}
	for _, e := range []*APIError{{Status: 401, Code: "UNAUTHORIZED"}, {Status: 403, Code: "FORBIDDEN"}, {Status: 409, Code: "SESSION_REPLACED"}} {
		if !SessionFatal(e) {
			t.Fatal("revoked session kept serving", e)
		}
	}
}
