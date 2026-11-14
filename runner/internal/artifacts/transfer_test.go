package artifacts

import (
	"campus/runner/internal/client"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestDownloadResumesAndVerifiesContent(t *testing.T) {
	data := []byte("real model weights")
	h := sha256.Sum256(data)
	digest := hex.EncodeToString(h[:])
	dest := filepath.Join(t.TempDir(), "model.pt")
	os.WriteFile(dest+".part", data[:5], 0600)
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/artifacts/a/content" || r.Header.Get("Range") != "bytes=5-" {
			t.Error("download did not resume", r.URL.Path, r.Header)
		}
		w.Header().Set("Content-Range", fmt.Sprintf("bytes 5-%d/%d", len(data)-1, len(data)))
		w.WriteHeader(206)
		w.Write(data[5:])
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "token", "session", s.Client())
	if err := Download(context.Background(), c, client.Artifact{ArtifactID: "a", Digest: digest, SizeBytes: int64(len(data))}, dest); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(dest)
	if string(got) != string(data) {
		t.Fatal("download corrupt")
	}
}

func TestCorruptDownloadNeverPublishes(t *testing.T) {
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("bad")) }))
	defer s.Close()
	c, _ := client.New(s.URL, "token", "", s.Client())
	dest := filepath.Join(t.TempDir(), "artifact")
	if err := Download(context.Background(), c, client.Artifact{ArtifactID: "a", Digest: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SizeBytes: 3}, dest); err == nil {
		t.Fatal("bad digest accepted")
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatal("corrupt output published")
	}
}

func TestUploadResumesOnlyMissingParts(t *testing.T) {
	data := []byte("abcdef")
	f := filepath.Join(t.TempDir(), "payload")
	os.WriteFile(f, data, 0600)
	var parts []string
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/v1/uploads" && r.Method == "POST":
			fmt.Fprint(w, `{"upload_id":"u","part_size_bytes":3}`)
		case r.URL.Path == "/api/v1/uploads/u" && r.Method == "GET":
			fmt.Fprint(w, `{"upload_id":"u","part_size_bytes":3,"completed_parts":[0]}`)
		case r.URL.Path == "/api/v1/uploads/u/parts/1":
			parts = append(parts, r.URL.Path)
			if r.Header.Get("X-Content-SHA256") == "" {
				t.Error("missing part checksum")
			}
			w.WriteHeader(204)
		case r.URL.Path == "/api/v1/uploads/u/finalize":
			h := sha256.Sum256(data)
			json.NewEncoder(w).Encode(client.Artifact{ArtifactID: "artifact", Digest: hex.EncodeToString(h[:]), SizeBytes: 6})
		default:
			t.Error("unexpected request", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "token", "s", s.Client())
	a, err := Upload(context.Background(), c, f, client.Fence{AttemptID: "attempt", Generation: 1, LeaseToken: "lease"})
	if err != nil || a.ArtifactID != "artifact" || len(parts) != 1 {
		t.Fatalf("%+v %v %v", a, err, parts)
	}
}
