package client

import (
	"campus/runner/internal/generated"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync"
	"testing"
	"time"
)

func TestCompleteRejectsUnprovenStatusWithoutRetry(t *testing.T) {
	for name, status := range map[string]string{
		"other attempt":         `{"attempt_id":"other","task_id":"task","generation":2,"state":"verifying","receipt":{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-14T00:00:00Z"}}`,
		"other generation":      `{"attempt_id":"attempt","task_id":"task","generation":3,"state":"verifying","receipt":{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-14T00:00:00Z"}}`,
		"missing receipt":       `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"verifying"}`,
		"other receipt attempt": `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"succeeded","receipt":{"attempt_id":"other","task_id":"task","state":"verifying","submitted_at":"2026-11-14T00:00:00Z"}}`,
		"other receipt task":    `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"succeeded","receipt":{"attempt_id":"attempt","task_id":"other","state":"verifying","submitted_at":"2026-11-14T00:00:00Z"}}`,
		"incomplete receipt":    `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"succeeded","receipt":{"attempt_id":"attempt","task_id":"task","state":"verifying"}}`,
		"unknown state":         `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"unknown"}`,
		"failed status read":    "",
	} {
		t.Run(name, func(t *testing.T) {
			var mu sync.Mutex
			var calls []string
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				calls = append(calls, r.Method)
				mu.Unlock()
				if r.Method == http.MethodPost {
					conn, _, _ := w.(http.Hijacker).Hijack()
					conn.Close()
					return
				}
				if status == "" {
					w.WriteHeader(http.StatusServiceUnavailable)
					return
				}
				fmt.Fprint(w, status)
			}))
			defer s.Close()
			c, _ := New(s.URL, "token", "session", s.Client())
			c.Backoff = time.Millisecond
			_, err := c.Complete(context.Background(), Fence{AttemptID: "attempt", Generation: 2, LeaseToken: "lease"}, generated.ResultManifest{Outputs: []generated.OutputRef{{Path: "model.pt", ArtifactID: "artifact"}}})
			mu.Lock()
			defer mu.Unlock()
			posts := 0
			for _, call := range calls {
				if call == http.MethodPost {
					posts++
				}
			}
			if err == nil || posts != 1 {
				t.Fatalf("unproven status accepted/replayed: err=%v calls=%v", err, calls)
			}
		})
	}
}

func TestCompleteReconcilesBeforeRetryWithIdenticalBody(t *testing.T) {
	var mu sync.Mutex
	var calls, bodies, keys []string
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		calls = append(calls, r.Method)
		if r.Method == http.MethodPost {
			bodies = append(bodies, string(body))
			keys = append(keys, r.Header.Get("Idempotency-Key"))
		}
		first := len(bodies) == 1
		mu.Unlock()
		if r.Method == http.MethodGet {
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","generation":2,"state":"running"}`)
		} else if first {
			conn, _, _ := w.(http.Hijacker).Hijack()
			conn.Close()
		} else {
			w.WriteHeader(http.StatusAccepted)
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-14T00:00:00Z"}`)
		}
	}))
	defer s.Close()
	c, _ := New(s.URL, "token", "session", s.Client())
	c.Backoff = time.Millisecond
	ms := int64(123)
	_, err := c.Complete(context.Background(), Fence{AttemptID: "attempt", Generation: 2, LeaseToken: "lease"}, generated.ResultManifest{Outputs: []generated.OutputRef{{Path: "model.pt", ArtifactID: "artifact"}}, ComputeMs: &ms, ItemIDs: []string{"item"}})
	mu.Lock()
	defer mu.Unlock()
	wantBody := `{"generation":2,"lease_token":"lease","manifest":{"compute_ms":123,"item_ids":["item"],"outputs":[{"artifact_id":"artifact","path":"model.pt"}]}}`
	if err != nil || !reflect.DeepEqual(calls, []string{"POST", "GET", "POST"}) || len(bodies) != 2 || bodies[0] != wantBody || bodies[1] != wantBody || keys[0] == "" || keys[0] != keys[1] {
		t.Fatalf("completion retry changed payload/order: err=%v calls=%v bodies=%v keys=%v", err, calls, bodies, keys)
	}
}

func TestCompleteRejectsInvalidAcceptanceReceipt(t *testing.T) {
	for _, body := range []string{
		`{"attempt_id":"attempt","state":"verifying","receipt_id":"legacy"}`,
		`{"attempt_id":"other","task_id":"task","state":"verifying","submitted_at":"2026-11-14T00:00:00Z"}`,
		`{"attempt_id":"attempt","task_id":"task","state":"running","submitted_at":"2026-11-14T00:00:00Z"}`,
	} {
		s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusAccepted)
			fmt.Fprint(w, body)
		}))
		c, _ := New(s.URL, "token", "session", s.Client())
		_, err := c.Complete(context.Background(), Fence{AttemptID: "attempt", Generation: 2, LeaseToken: "lease"}, generated.ResultManifest{Outputs: []generated.OutputRef{{Path: "model.pt", ArtifactID: "artifact"}}})
		s.Close()
		if err == nil {
			t.Fatalf("invalid completion acceptance accepted: %s", body)
		}
	}
}
