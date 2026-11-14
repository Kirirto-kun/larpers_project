package installer

import (
	"campus/runner/internal/onboarding"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
)

func hash(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
func entry(path, data string) onboarding.File {
	return onboarding.File{Path: path, SHA256: hash([]byte(data)), SizeBytes: int64(len(data))}
}
func sampleCatalog() Catalog {
	return Catalog{SchemaVersion: "1", Endpoint: "https://coordinator.example:8443", Targets: []Target{{OS: "windows", Arch: "amd64", Runner: entry("windows/campus.exe", "runner"), Profiles: entry("profiles.json", "{}"), Images: []onboarding.Image{{File: entry("images/cpu.tar.gz", "cpu"), Accelerator: "cpu", Platform: "linux/amd64", ImageDigest: "sha256:" + strings.Repeat("a", 64)}, {File: entry("images/cuda.tar.gz", "cuda"), Accelerator: "cuda", Platform: "linux/amd64", ImageDigest: "sha256:" + strings.Repeat("b", 64)}}}}}
}
func encoded(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
func serveCatalog(t *testing.T, raw []byte, tls bool) (*httptest.Server, *http.Client) {
	t.Helper()
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(raw) })
	var server *httptest.Server
	if tls {
		server = httptest.NewTLSServer(h)
	} else {
		server = httptest.NewServer(h)
	}
	t.Cleanup(server.Close)
	return server, server.Client()
}

func TestFetchCatalogPinsTLSMetadataAndSelectsExactTarget(t *testing.T) {
	raw := encoded(t, sampleCatalog())
	server, h := serveCatalog(t, raw, true)
	catalog, err := FetchCatalog(context.Background(), h, server.URL+"/release/catalog.json", hash(raw))
	if err != nil {
		t.Fatal(err)
	}
	target, err := catalog.Select("windows", "amd64")
	if err != nil || target.Runner.Path != "windows/campus.exe" {
		t.Fatalf("wrong target: %+v %v", target, err)
	}
	if _, err = catalog.Select("darwin", "arm64"); err == nil {
		t.Fatal("unavailable target accepted")
	}
	if _, err = FetchCatalog(context.Background(), h, server.URL+"/release/catalog.json", strings.Repeat("0", 64)); err == nil {
		t.Fatal("unpinned catalog accepted")
	}
	if _, err = FetchCatalog(context.Background(), &http.Client{}, server.URL+"/release/catalog.json", hash(raw)); err == nil {
		t.Fatal("untrusted TLS certificate accepted")
	}
}

func TestFetchCatalogRejectsRedirectsEvenWithDefaultClient(t *testing.T) {
	raw := encoded(t, sampleCatalog())
	hits := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/catalog.json" {
			http.Redirect(w, r, "/redirected", http.StatusFound)
			return
		}
		hits++
		w.Write(raw)
	}))
	defer server.Close()
	if _, err := FetchCatalog(context.Background(), http.DefaultClient, server.URL+"/catalog.json", hash(raw)); err == nil {
		t.Fatal("redirect accepted")
	}
	if hits != 0 {
		t.Fatal("redirect target contacted")
	}
}

func TestFetchCatalogRejectsUnsafePublicMetadata(t *testing.T) {
	for _, name := range []string{"invite", "trailing", "schema", "endpoint-secret", "endpoint-http", "traversal", "absolute", "query", "fragment", "encoded", "backslash", "unicode", "collision", "wrong-platform", "no-cpu", "cuda-arm64", "duplicate-target", "runner-quota", "metadata-quota", "image-quota", "total-quota", "bad-digest", "zero-size", "catalog-quota"} {
		t.Run(name, func(t *testing.T) {
			c := sampleCatalog()
			switch name {
			case "schema":
				c.SchemaVersion = "2"
			case "endpoint-secret":
				c.Endpoint = "https://user:secret@coordinator.example"
			case "endpoint-http":
				c.Endpoint = "http://coordinator.example"
			case "traversal":
				c.Targets[0].Profiles.Path = "../profiles.json"
			case "absolute":
				c.Targets[0].Profiles.Path = "https://elsewhere.example/a"
			case "query":
				c.Targets[0].Profiles.Path = "profiles.json?token=secret"
			case "fragment":
				c.Targets[0].Profiles.Path = "profiles.json#x"
			case "encoded":
				c.Targets[0].Profiles.Path = "%2e%2e/profiles.json"
			case "backslash":
				c.Targets[0].Profiles.Path = `a\profiles.json`
			case "unicode":
				c.Targets[0].Profiles.Path = "资料.json"
			case "collision":
				c.Targets[0].Profiles.Path = "WINDOWS/CAMPUS.EXE"
			case "wrong-platform":
				c.Targets[0].Images[0].Platform = "linux/arm64"
			case "no-cpu":
				c.Targets[0].Images = c.Targets[0].Images[1:]
			case "cuda-arm64":
				c.Targets[0].Arch = "arm64"
				for i := range c.Targets[0].Images {
					c.Targets[0].Images[i].Platform = "linux/arm64"
				}
			case "duplicate-target":
				c.Targets = append(c.Targets, c.Targets[0])
			case "runner-quota":
				c.Targets[0].Runner.SizeBytes = (128 << 20) + 1
			case "metadata-quota":
				c.Targets[0].Profiles.SizeBytes = (2 << 20) + 1
			case "image-quota":
				c.Targets[0].Images[0].SizeBytes = (8 << 30) + 1
			case "total-quota":
				for i := range c.Targets[0].Images {
					c.Targets[0].Images[i].SizeBytes = 8 << 30
				}
			case "bad-digest":
				c.Targets[0].Runner.SHA256 = "not-a-digest"
			case "zero-size":
				c.Targets[0].Profiles.SizeBytes = 0
			}
			raw := encoded(t, c)
			if name == "invite" {
				raw = append(raw[:len(raw)-1], []byte(`,"invite_file":{"path":"invite.json"}}`)...)
			}
			if name == "trailing" {
				raw = append(raw, []byte(` {}`)...)
			}
			if name == "catalog-quota" {
				raw = append(raw, []byte(strings.Repeat(" ", 64<<10))...)
			}
			server, h := serveCatalog(t, raw, false)
			if _, err := FetchCatalog(context.Background(), h, server.URL+"/catalog.json", hash(raw)); err == nil {
				t.Fatal("unsafe catalog accepted")
			}
		})
	}
}

func TestCatalogURLRejectsCredentialsQueriesAndNonHTTPS(t *testing.T) {
	for _, value := range []string{"https://u:p@example.org/catalog.json", "https://example.org/catalog.json?secret=x", "https://example.org/catalog.json#x", "http://example.org/catalog.json", "file:///catalog.json"} {
		if _, err := FetchCatalog(context.Background(), nil, value, strings.Repeat("a", 64)); err == nil {
			t.Fatal("unsafe URL accepted")
		}
	}
}

func TestFetchCatalogPreservesCancellationDuringBodyRead(t *testing.T) {
	raw := encoded(t, sampleCatalog())
	started := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", strconv.Itoa(len(raw)))
		w.Write(raw[:1])
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer server.Close()
	h := server.Client()
	transport := h.Transport
	h.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		response, err := transport.RoundTrip(r)
		if err == nil {
			response.Body = &observedBody{ReadCloser: response.Body, started: started}
		}
		return response, err
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := FetchCatalog(ctx, h, server.URL+"/catalog.json", hash(raw)); done <- err }()
	<-started
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation was hidden: %v", err)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type observedBody struct {
	io.ReadCloser
	started chan struct{}
	once    sync.Once
}

func (b *observedBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	if n > 0 {
		b.once.Do(func() { close(b.started) })
	}
	return n, err
}
