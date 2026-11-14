package client

import (
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
			json.NewEncoder(w).Encode(map[string]any{"api_version": "1", "min_runner_version": "0.1.0", "max_runner_version": "0.1.99"})
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
			completes.Add(1)
			hj := w.(http.Hijacker)
			conn, _, _ := hj.Hijack()
			conn.Close()
		case "/api/v1/attempts/attempt":
			json.NewEncoder(w).Encode(map[string]any{"attempt_id": "attempt", "state": "verifying", "receipt_id": "receipt"})
		default:
			t.Error(r.URL.Path)
		}
	}))
	defer s.Close()
	c, _ := New(s.URL, "token", "s", s.Client())
	c.Backoff = time.Millisecond
	receipt, err := c.Complete(context.Background(), Fence{AttemptID: "attempt", Generation: 2, LeaseToken: "lease"}, map[string]any{"files": []any{}})
	if err != nil || receipt.ReceiptID != "receipt" || completes.Load() != 1 {
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
