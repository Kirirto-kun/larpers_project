package artifacts

import (
	"campus/runner/internal/client"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
)

func TestVerifiedBlobCacheReusesAndRechecksCorruptContent(t *testing.T) {
	data := []byte("shared model weights")
	sum := sha256.Sum256(data)
	ref := client.Artifact{ArtifactID: "input", Digest: hex.EncodeToString(sum[:]), SizeBytes: int64(len(data))}
	var requests atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { requests.Add(1); w.Write(data) }))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	cache := NewCache(t.TempDir(), 1024)
	dest := t.TempDir()
	for _, name := range []string{"attempt-1", "attempt-2"} {
		if e := cache.Materialize(context.Background(), c, ref, filepath.Join(dest, name)); e != nil {
			t.Fatal(e)
		}
	}
	if requests.Load() != 1 {
		t.Fatal("shared input retransmitted", requests.Load())
	}
	os.WriteFile(filepath.Join(cache.Root, "blobs", ref.Digest), []byte("corrupt"), 0600)
	if e := cache.Materialize(context.Background(), c, ref, filepath.Join(dest, "attempt-3")); e != nil {
		t.Fatal(e)
	}
	if requests.Load() != 2 {
		t.Fatal("corrupt cache trusted")
	}
	got, _ := os.ReadFile(filepath.Join(dest, "attempt-3"))
	if string(got) != string(data) {
		t.Fatal("corrupt cache exposed")
	}
}
func TestCacheBoundsStorageAndSharesConcurrentTransfer(t *testing.T) {
	data := []byte("abc")
	sum := sha256.Sum256(data)
	ref := client.Artifact{ArtifactID: "input", Digest: hex.EncodeToString(sum[:]), SizeBytes: 3}
	var requests atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { requests.Add(1); w.Write(data) }))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	cache := NewCache(t.TempDir(), 3)
	dest := t.TempDir()
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for _, name := range []string{"a", "b"} {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			errs <- cache.Materialize(context.Background(), c, ref, filepath.Join(dest, name))
		}(name)
	}
	wg.Wait()
	close(errs)
	for e := range errs {
		if e != nil {
			t.Fatal(e)
		}
	}
	if requests.Load() != 1 {
		t.Fatal("concurrent model transfer duplicated")
	}
	data = []byte("def")
	sum = sha256.Sum256(data)
	ref.Digest = hex.EncodeToString(sum[:])
	if e := cache.Materialize(context.Background(), c, ref, filepath.Join(dest, "c")); e != nil {
		t.Fatal(e)
	}
	entries, _ := os.ReadDir(filepath.Join(cache.Root, "blobs"))
	var size int64
	for _, entry := range entries {
		info, _ := entry.Info()
		size += info.Size()
	}
	if size > 3 {
		t.Fatal("cache byte quota exceeded", size)
	}
}
