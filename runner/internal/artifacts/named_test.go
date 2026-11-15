package artifacts

import (
	"campus/runner/internal/client"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestUploadNamedPreservesDeclaredNestedPath(t *testing.T) {
	local := filepath.Join(t.TempDir(), "temporary-output")
	if err := os.WriteFile(local, nil, 0600); err != nil {
		t.Fatal(err)
	}
	const name = "models/result.json"
	const digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
	requests := 0
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		w.Header().Set("Content-Type", "application/json")
		if r.Method == "POST" && r.URL.Path == "/api/v1/uploads" {
			var body map[string]any
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if body["name"] != name || body["kind"] != "output" || body["sha256"] != digest {
				t.Errorf("wrong declared upload: %#v", body)
			}
			w.WriteHeader(201)
			json.NewEncoder(w).Encode(map[string]any{"id": "upload", "state": "pending", "part_count": 0, "parts": []any{}})
			return
		}
		if r.Method != "GET" || r.URL.Path != "/api/v1/uploads/upload" {
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
		json.NewEncoder(w).Encode(map[string]any{"id": "upload", "state": "ready", "part_count": 0, "parts": []any{}, "artifact": map[string]any{"id": "artifact", "name": name, "sha256": digest, "size_bytes": 0, "media_type": "application/octet-stream"}})
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "credential", "session", s.Client())
	result, err := UploadNamed(context.Background(), c, local, client.Fence{AttemptID: "attempt", Generation: 1, LeaseToken: "lease"}, name)
	if err != nil || result.Path != name || requests != 2 {
		t.Fatalf("result=%+v requests=%d err=%v", result, requests, err)
	}
}

func TestUploadNamedRejectsUnsafeNamesBeforeNetwork(t *testing.T) {
	for _, name := range []string{"", "../result", "/result", "a/../../b", "a\\b", "x\x00y"} {
		if _, err := UploadNamed(context.Background(), nil, "unused", client.Fence{}, name); err == nil {
			t.Errorf("accepted unsafe name %q", name)
		}
	}
}
