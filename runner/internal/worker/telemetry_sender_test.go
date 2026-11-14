package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/telemetry"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func TestAttemptTelemetrySerializesConcurrentCPUGPUWithFoundationProof(t *testing.T) {
	var mu sync.Mutex
	var received []generated.AttemptTelemetry
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/attempts/attempt/telemetry" {
			t.Error("wrong attempt path", r.URL.Path)
		}
		var body generated.AttemptTelemetry
		decoder := json.NewDecoder(r.Body)
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&body); err != nil {
			t.Error("foundation rejects telemetry", err)
		}
		// A slow first request exposes sequence allocation without send ordering.
		if body.Sequence == 1 {
			time.Sleep(30 * time.Millisecond)
		}
		mu.Lock()
		received = append(received, body)
		mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	c, err := client.New(server.URL, "credential", "session", server.Client())
	if err != nil {
		t.Fatal(err)
	}
	sender := newAttemptTelemetrySender(c, client.Fence{AttemptID: "attempt", Generation: 3, LeaseToken: "lease-token"})
	var workers sync.WaitGroup
	for i := range 12 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			values := map[string]any{"CPUPerc": "200%"}
			if i%2 == 0 {
				value := 25.0
				values = map[string]any{"gpus": []telemetry.GPUUsage{{ID: "0", UtilizationPercent: &value}}}
			}
			sample := telemetry.Sample{Sequence: 1, Values: values}
			if err := sender.Send(context.Background(), sample, "compute", 2); err != nil {
				t.Error(err)
			}
			if sample.Sequence != 1 {
				t.Error("sender mutated precise local sequence")
			}
		}()
	}
	workers.Wait()
	if len(received) != 12 {
		t.Fatal("missing telemetry", len(received))
	}
	for i, body := range received {
		if body.Sequence != int64(i+1) || body.Generation != 3 || body.LeaseToken != "lease-token" || body.Phase != "compute" {
			t.Fatalf("out of order or unfenced telemetry: %+v", received)
		}
		if (body.CPUPercent == nil) == (body.GPUPercent == nil) {
			t.Fatalf("lost sensor value: %+v", body)
		}
	}
}
