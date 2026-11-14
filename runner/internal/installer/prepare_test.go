package installer

import (
	"campus/runner/internal/onboarding"
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

type downloadsFixture struct {
	catalog       Catalog
	raw           []byte
	url           string
	server        *httptest.Server
	client        *http.Client
	binary, cache string
	mu            sync.Mutex
	hits          map[string]int
	bodies        map[string][]byte
	interrupted   bool
	rangeHeader   string
	badResponse   string
}

func downloads(t *testing.T) *downloadsFixture {
	t.Helper()
	f := &downloadsFixture{catalog: sampleCatalog(), hits: map[string]int{}, bodies: map[string][]byte{}}
	profiles := []byte(`{"schema_version":"1","extra":"preserve-root","profiles":[{"id":"cpu-v1","accelerator":"cpu","platform":"linux/amd64","image_digest":"sha256:` + strings.Repeat("a", 64) + `","guard_version":"1","dependencies":{"python":"3.11"}},{"id":"cuda-v1","accelerator":"cuda","platform":"linux/amd64","image_digest":"sha256:` + strings.Repeat("b", 64) + `","guard_version":"1","dependencies":{"cuda":"12.8"}}]}`)
	f.catalog.Targets[0].Profiles = entry("profiles.json", string(profiles))
	f.catalog.CAFile = ptr(entry("ca.pem", "test CA"))
	f.bodies["/release/profiles.json"] = profiles
	f.bodies["/release/ca.pem"] = []byte("test CA")
	f.bodies["/release/images/cpu.tar.gz"] = []byte("cpu")
	f.bodies["/release/images/cuda.tar.gz"] = []byte("cuda")
	f.raw = encoded(t, f.catalog)
	f.server = httptest.NewTLSServer(http.HandlerFunc(f.respond))
	t.Cleanup(f.server.Close)
	f.client = f.server.Client()
	f.url = f.server.URL + "/release/catalog.json"
	f.binary = filepath.Join(t.TempDir(), "runner")
	if err := os.WriteFile(f.binary, []byte("runner"), 0700); err != nil {
		t.Fatal(err)
	}
	f.cache = filepath.Join(t.TempDir(), "Campus 缓存")
	return f
}
func ptr(file onboarding.File) *onboarding.File { return &file }
func (f *downloadsFixture) respond(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.hits[r.URL.Path]++
	if r.URL.Path == "/release/catalog.json" {
		w.Write(f.raw)
		return
	}
	data, ok := f.bodies[r.URL.Path]
	if !ok {
		http.NotFound(w, r)
		return
	}
	if r.URL.Path == "/release/images/cpu.tar.gz" {
		if f.badResponse == "redirect" {
			http.Redirect(w, r, "/elsewhere", http.StatusFound)
			return
		}
		if f.badResponse == "wrong-body" {
			data = []byte("bad")
		}
		if f.badResponse == "oversize" {
			data = []byte("oversize")
		}
		if f.badResponse == "unknown-length" {
			w.(http.Flusher).Flush()
			w.Write(data)
			return
		}
		if f.badResponse == "encoding" {
			w.Header().Set("Content-Encoding", "gzip")
		}
		if f.interrupted {
			f.interrupted = false
			conn, buf, err := w.(http.Hijacker).Hijack()
			if err != nil {
				panic(err)
			}
			fmt.Fprintf(buf, "HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n", len(data))
			buf.Write(data[:1])
			buf.Flush()
			conn.Close()
			return
		}
		if value := r.Header.Get("Range"); value != "" {
			f.rangeHeader = value
			var offset int
			fmt.Sscanf(value, "bytes=%d-", &offset)
			if f.badResponse == "range" {
				w.Header().Set("Content-Range", "bytes 0-2/3")
			} else {
				w.Header().Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", offset, len(data)-1, len(data)))
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(data)-offset))
			w.WriteHeader(http.StatusPartialContent)
			w.Write(data[offset:])
			return
		}
	}
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.Write(data)
}
func (f *downloadsFixture) fetched(t *testing.T) (*Catalog, Target) {
	t.Helper()
	c, err := FetchCatalog(context.Background(), f.client, f.url, hash(f.raw))
	if err != nil {
		t.Fatal(err)
	}
	target, err := c.Select("windows", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	return c, target
}
func (f *downloadsFixture) prepare(t *testing.T, gpu bool) Prepared {
	t.Helper()
	c, target := f.fetched(t)
	p, err := Prepare(context.Background(), f.client, f.url, hash(f.raw), c, target, gpu, f.cache, f.binary)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func TestPreparePinsCPUOnlyBundleAndReusesVerifiedCache(t *testing.T) {
	f := downloads(t)
	p := f.prepare(t, false)
	b, err := onboarding.Load(context.Background(), p.Root, p.ManifestSHA256, "windows", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	if len(b.Manifest.Images) != 1 || b.Manifest.Images[0].Accelerator != "cpu" || b.Manifest.InviteFile != nil {
		t.Fatal("bundle is not CPU-only and invitation-free")
	}
	if err = b.VerifySelected(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	profiles, err := os.ReadFile(b.Path(b.Manifest.Profiles))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(profiles), "cuda") || !strings.Contains(string(profiles), `"dependencies"`) || !strings.Contains(string(profiles), `"extra"`) {
		t.Fatalf("selected profile fields not preserved: %s", profiles)
	}
	if f.hits["/release/images/cuda.tar.gz"] != 0 || f.hits["/release/windows/campus.exe"] != 0 {
		t.Fatal("unselected image or running binary downloaded")
	}
	p2 := f.prepare(t, false)
	if p != p2 {
		t.Fatalf("bundle root/hash changed across repeated preparation: %+v %+v", p, p2)
	}
	if f.hits["/release/images/cpu.tar.gz"] != 1 || f.hits["/release/profiles.json"] != 1 || f.hits["/release/ca.pem"] != 1 {
		t.Fatal("verified cache was not reused")
	}
	if runtime.GOOS != "windows" {
		info, _ := os.Stat(f.cache)
		if info.Mode().Perm()&0077 != 0 {
			t.Fatal("cache is not private")
		}
	}
	if _, err = os.Stat(filepath.Join(f.cache, "identity.json")); !os.IsNotExist(err) {
		t.Fatal("preparation created an identity")
	}
}

func TestPrepareGPUDownloadsBothImagesAndReusesCPUAcrossReleases(t *testing.T) {
	f := downloads(t)
	cpu := f.prepare(t, false)
	gpu := f.prepare(t, true)
	if cpu.Root == gpu.Root {
		t.Fatal("GPU selection did not change bundle root")
	}
	b, err := onboarding.Load(context.Background(), gpu.Root, gpu.ManifestSHA256, "windows", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	if err = b.VerifySelected(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	if len(b.Manifest.Images) != 2 || f.hits["/release/images/cpu.tar.gz"] != 1 || f.hits["/release/images/cuda.tar.gz"] != 1 {
		t.Fatal("image selection/cache reuse failed")
	}
	f.catalog.Endpoint = "https://next.example:8443"
	f.raw = encoded(t, f.catalog)
	next := f.prepare(t, false)
	if next.Root == cpu.Root || f.hits["/release/images/cpu.tar.gz"] != 1 {
		t.Fatal("content-addressed cache was not reused across catalog versions")
	}
}

func TestPrepareResumesInterruptedImageWithExactRange(t *testing.T) {
	f := downloads(t)
	f.interrupted = true
	c, target := f.fetched(t)
	if _, err := Prepare(context.Background(), f.client, f.url, hash(f.raw), c, target, false, f.cache, f.binary); err == nil {
		t.Fatal("interrupted image published")
	}
	p := f.prepare(t, false)
	if f.rangeHeader != "bytes=1-" || f.hits["/release/images/cpu.tar.gz"] != 2 {
		t.Fatalf("interrupted download was not resumed: %q", f.rangeHeader)
	}
	b, err := onboarding.Load(context.Background(), p.Root, p.ManifestSHA256, "windows", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	if err = b.VerifySelected(context.Background(), false); err != nil {
		t.Fatal(err)
	}
}

func TestPrepareRejectsTamperingBeforeDownloads(t *testing.T) {
	for _, name := range []string{"binary", "catalog", "target", "url", "hash", "unfetched", "cancel", "gpu-unavailable"} {
		t.Run(name, func(t *testing.T) {
			f := downloads(t)
			c, target := f.fetched(t)
			ctx := context.Background()
			location, digest := f.url, hash(f.raw)
			gpu := false
			switch name {
			case "binary":
				os.WriteFile(f.binary, []byte("edited"), 0700)
			case "catalog":
				c.Endpoint = "https://evil.example"
			case "target":
				target.Profiles.Path = "other.json"
			case "url":
				location = f.server.URL + "/different/catalog.json"
			case "hash":
				digest = strings.Repeat("0", 64)
			case "unfetched":
				copy := f.catalog
				c = &copy
			case "cancel":
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			case "gpu-unavailable":
				gpu = true
				c.Targets[0].Images = c.Targets[0].Images[:1]
			}
			if _, err := Prepare(ctx, f.client, location, digest, c, target, gpu, f.cache, f.binary); err == nil {
				t.Fatal("tampered inputs accepted")
			}
			if f.hits["/release/profiles.json"] != 0 || f.hits["/release/images/cpu.tar.gz"] != 0 {
				t.Fatal("download began before input verification")
			}
		})
	}
}

func TestPrepareRejectsInvalidDownloadResponses(t *testing.T) {
	for _, name := range []string{"redirect", "wrong-body", "oversize", "unknown-length", "encoding", "range"} {
		t.Run(name, func(t *testing.T) {
			f := downloads(t)
			c, target := f.fetched(t)
			if name == "range" {
				f.interrupted = true
				Prepare(context.Background(), f.client, f.url, hash(f.raw), c, target, false, f.cache, f.binary)
			}
			f.badResponse = name
			if _, err := Prepare(context.Background(), f.client, f.url, hash(f.raw), c, target, false, f.cache, f.binary); err == nil {
				t.Fatal("bad response published")
			}
			if f.hits["/elsewhere"] != 0 {
				t.Fatal("redirect was followed")
			}
			filepath.WalkDir(f.cache, func(path string, d os.DirEntry, err error) error {
				if err == nil && d.Name() == "connection.json" {
					t.Error("failed download published a bundle")
				}
				return err
			})
		})
	}
}

func TestPrepareRehashesCorruptObjectsBeforeReuse(t *testing.T) {
	f := downloads(t)
	first := f.prepare(t, false)
	object := filepath.Join(f.cache, "objects", hash([]byte("cpu")))
	if err := os.WriteFile(object, []byte("bad"), 0600); err != nil {
		t.Fatal(err)
	}
	p := f.prepare(t, false)
	if p.Root != first.Root || f.hits["/release/images/cpu.tar.gz"] != 2 {
		t.Fatal("corrupt cache was trusted or bundle root changed")
	}
	b, err := onboarding.Load(context.Background(), p.Root, p.ManifestSHA256, "windows", "amd64")
	if err != nil {
		t.Fatal(err)
	}
	if err = b.VerifySelected(context.Background(), false); err != nil {
		t.Fatal(err)
	}
}

func TestPrepareRejectsLinkedCacheAndBinary(t *testing.T) {
	for _, name := range []string{"cache", "binary", "object"} {
		t.Run(name, func(t *testing.T) {
			f := downloads(t)
			c, target := f.fetched(t)
			outside := t.TempDir()
			link := filepath.Join(t.TempDir(), "link")
			if name == "binary" {
				link = filepath.Join(t.TempDir(), "runner-link")
				outside = f.binary
			}
			if name == "object" {
				f.prepare(t, false)
				link = filepath.Join(f.cache, "objects", hash([]byte("cpu")))
				if err := os.Remove(link); err != nil {
					t.Fatal(err)
				}
				outside = filepath.Join(t.TempDir(), "cpu")
				os.WriteFile(outside, []byte("cpu"), 0600)
			}
			if err := os.Symlink(outside, link); err != nil {
				t.Skipf("symlink unavailable: %v", err)
			}
			if name == "binary" {
				f.binary = link
			} else if name == "cache" {
				f.cache = link
			}
			if _, err := Prepare(context.Background(), f.client, f.url, hash(f.raw), c, target, false, f.cache, f.binary); err == nil {
				t.Fatal("linked input accepted")
			}
		})
	}
}

func TestPrepareCancelsAnInflightDownloadAndExcludesConcurrentWriters(t *testing.T) {
	f := downloads(t)
	c, target := f.fetched(t)
	started := make(chan struct{})
	f.server.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/release/images/cpu.tar.gz" {
			f.respond(w, r)
			return
		}
		w.Header().Set("Content-Length", "3")
		w.Write([]byte("c"))
		w.(http.Flusher).Flush()
		close(started)
		<-r.Context().Done()
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan error, 1)
	go func() {
		_, err := Prepare(ctx, f.client, f.url, hash(f.raw), c, target, false, f.cache, f.binary)
		finished <- err
	}()
	select {
	case <-started:
	case <-time.After(10 * time.Second):
		t.Fatal("download did not start")
	}
	if _, err := Prepare(context.Background(), f.client, f.url, hash(f.raw), c, target, false, f.cache, f.binary); err == nil {
		t.Fatal("concurrent cache writer was admitted")
	}
	cancel()
	select {
	case err := <-finished:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancel did not stop preparation: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("cancelled preparation hung")
	}
	if _, err := os.Stat(filepath.Join(f.cache, "objects", hash([]byte("cpu")))); !os.IsNotExist(err) {
		t.Fatal("cancelled download published an object")
	}
	filepath.WalkDir(f.cache, func(path string, d os.DirEntry, err error) error {
		if err == nil && d.Name() == "connection.json" {
			t.Error("cancelled preparation published a bundle")
		}
		return err
	})
}

func TestPreparePreservesUnexpectedFilesInExistingBundle(t *testing.T) {
	f := downloads(t)
	p := f.prepare(t, false)
	unexpected := filepath.Join(p.Root, "my-notes.txt")
	if err := os.WriteFile(unexpected, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	c, target := f.fetched(t)
	if _, err := Prepare(context.Background(), f.client, f.url, hash(f.raw), c, target, false, f.cache, f.binary); err == nil {
		t.Fatal("unexpected bundle files accepted")
	}
	data, err := os.ReadFile(unexpected)
	if err != nil || string(data) != "keep" {
		t.Fatal("unrelated file was deleted")
	}
}

func TestPrepareRejectsMismatchedApprovedProfiles(t *testing.T) {
	f := downloads(t)
	data := []byte(strings.ReplaceAll(string(f.bodies["/release/profiles.json"]), strings.Repeat("a", 64), strings.Repeat("c", 64)))
	f.bodies["/release/profiles.json"] = data
	f.catalog.Targets[0].Profiles = entry("profiles.json", string(data))
	f.raw = encoded(t, f.catalog)
	c, target := f.fetched(t)
	if _, err := Prepare(context.Background(), f.client, f.url, hash(f.raw), c, target, false, f.cache, f.binary); err == nil {
		t.Fatal("profile not matching the catalog image was accepted")
	}
}

func TestPrepareRejectsEmptyCacheBeforeFilesystemMutation(t *testing.T) {
	f := downloads(t)
	catalog, target := f.fetched(t)
	working := t.TempDir()
	t.Chdir(working)
	if err := os.Chmod(working, 0755); err != nil {
		t.Fatal(err)
	}
	before := directorySecurity(t, working)
	_, err := Prepare(context.Background(), f.client, f.url, hash(f.raw), catalog, target, false, "", f.binary)
	if err == nil {
		t.Error("empty cache path accepted")
	}
	if after := directorySecurity(t, working); after != before {
		t.Error("empty cache path changed the working directory permissions")
	}
	entries, readErr := os.ReadDir(working)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if len(entries) != 0 {
		t.Error("empty cache path created files in the working directory")
	}
	if f.hits["/release/profiles.json"] != 0 || f.hits["/release/images/cpu.tar.gz"] != 0 {
		t.Error("empty cache path began artifact downloads")
	}
}

func TestProfilesVerifyTheExactBytesConsumedForSynthesis(t *testing.T) {
	f := downloads(t)
	data := f.bodies["/release/profiles.json"]
	name := filepath.Join(t.TempDir(), "profiles.json")
	if err := os.WriteFile(name, data, 0600); err != nil {
		t.Fatal(err)
	}
	changed := false
	ctx := &onReadContext{Context: context.Background(), callback: func() {
		changed = true
		if err := os.WriteFile(name, []byte(strings.ReplaceAll(string(data), "3.11", "9.99")), 0600); err != nil {
			t.Fatal(err)
		}
	}}
	images := f.catalog.Targets[0].Images
	if _, err := selectedProfiles(ctx, name, f.catalog.Targets[0].Profiles, images, images[:1]); err == nil {
		t.Fatal("metadata changed between verification and synthesis was accepted")
	}
	if !changed {
		t.Fatal("metadata race was not exercised")
	}
}

// The first two context checks accompany the verification read and EOF. The
// third accompanies consuming the reopened file, after verification completed.
type onReadContext struct {
	context.Context
	calls    int
	callback func()
}

func (c *onReadContext) Err() error {
	c.calls++
	if c.calls == 3 {
		c.callback()
	}
	return nil
}
