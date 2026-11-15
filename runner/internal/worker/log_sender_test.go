package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestAttemptLogsChunkUTF8AndKeepSequenceAcrossFinalFlush(t *testing.T) {
	var received []generated.LogWrite
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body generated.LogWrite
		decoder := json.NewDecoder(r.Body)
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&body); err != nil {
			t.Error(err)
		}
		received = append(received, body)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	sender := newAttemptLogSender(c, fixtureAssignment().Fence)
	text := strings.Repeat("汉", 6000) + "\n"
	if err := sender.Send(context.Background(), []byte(text)); err != nil {
		t.Fatal(err)
	}
	if err := sender.Send(context.Background(), []byte("final tail")); err != nil {
		t.Fatal(err)
	}
	var joined strings.Builder
	for i, body := range received {
		if body.Generation != 2 || body.LeaseToken != "lease-secret" || body.Sequence != int64(i+1) || len(body.Text) > 8192 || !utf8.ValidString(body.Text) {
			t.Errorf("invalid log chunk: %+v", body)
		}
		joined.WriteString(body.Text)
	}
	if len(received) != 4 || joined.String() != text+"final tail" {
		t.Fatalf("logs lost/truncated across chunks/final flush: chunks=%d", len(received))
	}
}
