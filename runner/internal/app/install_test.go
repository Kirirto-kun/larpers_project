package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/onboarding"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

type installationFixture struct {
	*connectionFixture
	catalogURL, catalogSHA, cache string
	hits                          map[string]int
}

func installationKit(t *testing.T, gpu bool) *installationFixture {
	t.Helper()
	f := &installationFixture{connectionFixture: connectionKit(t, gpu), cache: filepath.Join(t.TempDir(), "Cache 空格"), hits: map[string]int{}}
	var local onboarding.Manifest
	raw, err := os.ReadFile(filepath.Join(f.root, "connection.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, &local); err != nil {
		t.Fatal(err)
	}
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	runner, err := os.ReadFile(executable)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(runner)
	local.Runner = onboarding.File{Path: "bin/campus", SHA256: hex.EncodeToString(sum[:]), SizeBytes: int64(len(runner))}
	target := map[string]any{"os": runtime.GOOS, "arch": runtime.GOARCH, "runner": local.Runner, "profiles": local.Profiles, "images": local.Images}
	raw, err = json.Marshal(map[string]any{"schema_version": "1", "endpoint": f.endpoint, "targets": []any{target}})
	if err != nil {
		t.Fatal(err)
	}
	sum = sha256.Sum256(raw)
	f.catalogSHA = hex.EncodeToString(sum[:])
	f.catalogURL = f.endpoint + "/public/catalog.json"
	assets := map[string][]byte{"/public/catalog.json": raw}
	for _, file := range append([]onboarding.File{local.Profiles}, imageFiles(local.Images)...) {
		data, e := os.ReadFile(filepath.Join(f.root, filepath.FromSlash(file.Path)))
		if e != nil {
			t.Fatal(e)
		}
		assets["/public/"+file.Path] = data
	}
	api := f.server.Config.Handler
	f.server.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/public/") {
			f.hits[r.URL.Path]++
			if r.Header.Get("Authorization") != "" || r.Header.Get("X-Node-Session") != "" {
				t.Error("credential sent to public download")
			}
			data, exists := assets[r.URL.Path]
			if !exists {
				t.Errorf("unexpected public download %s", r.URL.Path)
				w.WriteHeader(404)
				return
			}
			w.Header().Set("Content-Length", fmt.Sprint(len(data)))
			w.Write(data)
			return
		}
		api.ServeHTTP(w, r)
	})
	return f
}

func imageFiles(images []onboarding.Image) []onboarding.File {
	var files []onboarding.File
	for _, image := range images {
		files = append(files, image.File)
	}
	return files
}

func (f *installationFixture) install(args ...string) int {
	base := []string{"--state-dir", f.state, "install", "--catalog-url", f.catalogURL, "--catalog-sha256", f.catalogSHA, "--cache-dir", f.cache}
	return f.app.Run(context.Background(), append(base, args...))
}

func TestInstallPublicCPUAndCachedReconnect(t *testing.T) {
	f := installationKit(t, false)
	f.app.Stdin = strings.NewReader(f.endpoint + "/join#token=" + f.token + "\n")
	if code := f.install(); code != 0 {
		t.Fatalf("install=%d: %s", code, &f.errors)
	}
	if f.redeems != 1 || f.serves != 1 || f.hits["/public/images/cpu.tar.gz"] != 1 {
		t.Fatalf("bad enrollment/download counts: redeems=%d serves=%d hits=%v", f.redeems, f.serves, f.hits)
	}
	f.app.Stdin = strings.NewReader("")
	if code := f.install(); code != 0 {
		t.Fatalf("reconnect=%d: %s", code, &f.errors)
	}
	if f.redeems != 1 || f.serves != 2 || f.hits["/public/images/cpu.tar.gz"] != 1 {
		t.Fatalf("cache/reconnect failed: %v", f.hits)
	}
	if strings.Contains(f.output.String()+f.errors.String(), f.token) {
		t.Fatal("invitation leaked")
	}
}

func TestInstallCheckOnlyDoesNotRedeem(t *testing.T) {
	f := installationKit(t, false)
	if code := f.install("--check-only"); code != 0 {
		t.Fatalf("check=%d: %s", code, &f.errors)
	}
	if f.redeems != 0 || f.serves != 0 || f.probes == 0 {
		t.Fatal("local check enrolled or missed computation probe")
	}
	if _, err := os.Stat(filepath.Join(f.state, "identity.json")); !os.IsNotExist(err) {
		t.Fatalf("check-only created identity: %v", err)
	}
}

func TestInstallGPUConsentPreservesFollowingInvitation(t *testing.T) {
	for _, consent := range []string{"y", "n"} {
		t.Run(consent, func(t *testing.T) {
			f := installationKit(t, true)
			f.app.Stdin = strings.NewReader(consent + "\n" + f.endpoint + "/join#token=" + f.token + "\n")
			if code := f.install(); code != 0 {
				t.Fatalf("install=%d: %s", code, &f.errors)
			}
			want := 0
			if consent == "y" {
				want = 1
			}
			if f.hits["/public/images/cuda.tar.gz"] != want || f.gpu != (consent == "y") || f.redeems != 1 {
				t.Fatalf("consent/download/enrollment mismatch: hits=%v gpu=%t redeems=%d", f.hits, f.gpu, f.redeems)
			}
			if strings.Contains(f.output.String()+f.errors.String(), f.token) {
				t.Fatal("invitation leaked")
			}
		})
	}
}

func TestInstallCancellationDuringDockerReadinessReturns130(t *testing.T) {
	f := installationKit(t, false)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	f.app.Discover = func(context.Context) (client.Capabilities, error) {
		return client.Capabilities{Runtime: client.Runtime{Healthy: false}}, nil
	}
	f.app.StartDocker = func(context.Context) error { cancel(); return nil }
	args := []string{"--state-dir", f.state, "install", "--catalog-url", f.catalogURL, "--catalog-sha256", f.catalogSHA, "--cache-dir", f.cache}
	if code := f.app.Run(ctx, args); code != 130 {
		t.Fatalf("cancelled install=%d, want 130: %s", code, &f.errors)
	}
	if f.hits["/public/images/cpu.tar.gz"] != 0 || f.redeems != 0 {
		t.Fatal("cancelled install downloaded an environment or consumed an invitation")
	}
}

func TestInstallEOFNeverAuthorizesGPUOrDownloadsAnEnvironment(t *testing.T) {
	f := installationKit(t, true)
	f.app.Stdin = strings.NewReader("")
	if code := f.install(); code != 1 {
		t.Fatalf("EOF=%d: %s", code, &f.errors)
	}
	if f.hits["/public/images/cpu.tar.gz"] != 0 || f.hits["/public/images/cuda.tar.gz"] != 0 || f.redeems != 0 {
		t.Fatal("missing consent downloaded an environment or consumed an invitation")
	}
}
