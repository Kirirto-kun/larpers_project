package artifacts

import (
	"campus/runner/internal/client"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReuseOwnedVerifiesAuthenticatedLookupAndLocalBytes(t *testing.T) {
	file := filepath.Join(t.TempDir(), "shard.json")
	os.WriteFile(file, []byte("payload"), 0600)
	digest, size, _ := HashFile(file)
	var requests int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if r.URL.Path != "/api/v1/artifacts/lookup" || r.URL.Query().Get("sha256") != digest || r.URL.Query().Get("size_bytes") != "7" || r.URL.Query().Get("kind") != "input" || r.Header.Get("Authorization") != "Bearer member-a" {
			t.Errorf("incorrect ownership lookup %s", r.URL)
		}
		fmt.Fprintf(w, `{"id":"owned-1","sha256":%q,"size_bytes":%d,"name":"old-name.json","media_type":"application/octet-stream"}`, digest, size)
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "member-a", "", s.Client())
	a, reused, err := ReuseOwned(ctxBackground(), c, file, "input")
	if err != nil || !reused || a.ArtifactID != "owned-1" || a.Digest != digest || requests != 1 {
		t.Fatalf("reuse=%v %#v requests=%d: %v", reused, a, requests, err)
	}
}

func ctxBackground() context.Context { return context.Background() }

func TestReuseOwnedNeverTrustsMismatchedLookupDescriptor(t *testing.T) {
	file := filepath.Join(t.TempDir(), "shard.json")
	os.WriteFile(file, []byte("payload"), 0600)
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, `{"id":"owned-1","sha256":%q,"size_bytes":7,"name":"shard.json","media_type":"application/octet-stream"}`, strings.Repeat("a", 64))
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "member-a", "", s.Client())
	if _, _, err := ReuseOwned(context.Background(), c, file, "input"); err == nil {
		t.Fatal("mismatched content cache accepted")
	}
}

func TestReuseOwnedDetectsMutationWhileOwnershipLookupIsInFlight(t *testing.T) {
	file := filepath.Join(t.TempDir(), "shard.json")
	os.WriteFile(file, []byte("payload"), 0600)
	digest, size, _ := HashFile(file)
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		os.WriteFile(file, []byte("changed"), 0600)
		fmt.Fprintf(w, `{"id":"owned-1","sha256":%q,"size_bytes":%d,"name":"shard.json","media_type":"application/octet-stream"}`, digest, size)
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "member-a", "", s.Client())
	if _, _, err := ReuseOwned(context.Background(), c, file, "input"); err == nil {
		t.Fatal("artifact mutation during lookup accepted")
	}
}
