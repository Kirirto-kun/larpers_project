package app

import (
	"campus/runner/internal/client"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVerifiedLocalImageRemainsAllowedForOfflineBaseline(t *testing.T) {
	a, _, _, _ := testApp(t)
	local := "sha256:" + strings.Repeat("a", 64)
	canonical := "campus/cpu@sha256:" + strings.Repeat("b", 64)
	profileFile := localOnlyProfiles(t, local)
	a.Discover = func(context.Context) (client.Capabilities, error) { return offlineCapabilities(), nil }
	verified := map[string]bool{}
	a.VerifyImage = func(_ context.Context, image, platform string) error {
		if platform != "linux/amd64" {
			t.Errorf("wrong platform %s", platform)
		}
		verified[image] = true
		return nil
	}
	probes := 0
	a.Probe = func(_ context.Context, image, platform string, gpu bool) error {
		probes++
		if image != local || !verified[local] || gpu {
			t.Errorf("local image not verified and probed: %s", image)
		}
		return nil
	}
	a.ResolveImage = func(context.Context, string) (string, error) { return canonical, nil }
	a.defaults()
	caps, allowed, err := a.preflight(context.Background(), profileFile, false)
	if err != nil || allowed[local] != "linux/amd64" || allowed[canonical] != "linux/amd64" || probes != 1 {
		t.Fatalf("approved local baseline image lost: allowed=%v probes=%d err=%v", allowed, probes, err)
	}
	if !verified[canonical] || len(caps.ImageDigests) != 1 || caps.ImageDigests[0] != canonical {
		t.Fatalf("server capabilities contain an unverified image or local ID: %#v verified=%v", caps.ImageDigests, verified)
	}
	_, baselineAllowed, err := readProfiles(profileFile)
	if err != nil || baselineAllowed[local] != "linux/amd64" {
		t.Fatalf("benchmark-local's profile allowlist lost the local image: %v %v", baselineAllowed, err)
	}
}

func TestDoctorAcceptsVerifiedOfflineImageWithoutRepositoryDigest(t *testing.T) {
	a, out, errout, _ := testApp(t)
	local := "sha256:" + strings.Repeat("a", 64)
	a.Discover = func(context.Context) (client.Capabilities, error) { return offlineCapabilities(), nil }
	a.VerifyImage = func(context.Context, string, string) error { return nil }
	probes := 0
	a.Probe = func(_ context.Context, image, platform string, gpu bool) error {
		probes++
		if image != local || gpu {
			t.Errorf("unexpected offline probe %s", image)
		}
		return nil
	}
	a.ResolveImage = func(context.Context, string) (string, error) { return "", errors.New("no cached canonical RepoDigest") }
	if code := a.Run(context.Background(), []string{"doctor", "--profiles", localOnlyProfiles(t, local)}); code != 0 || probes != 1 {
		t.Fatalf("offline doctor rejected a verified loaded image: code=%d probes=%d %s", code, probes, errout)
	}
	var caps client.Capabilities
	if err := json.Unmarshal(out.Bytes(), &caps); err != nil || len(caps.ImageDigests) != 0 || !strings.Contains(strings.Join(caps.Diagnostics, " "), "RepoDigest") {
		t.Fatalf("offline/canonical capability distinction missing: %s %v", out, err)
	}
}

func TestServeRequiresRepositoryDigestAfterOfflineVerification(t *testing.T) {
	a, _, errout, dir := testApp(t)
	local := "sha256:" + strings.Repeat("a", 64)
	a.Discover = func(context.Context) (client.Capabilities, error) { return offlineCapabilities(), nil }
	a.VerifyImage = func(context.Context, string, string) error { return nil }
	probes := 0
	a.Probe = func(_ context.Context, image, platform string, gpu bool) error {
		probes++
		if image != local {
			t.Errorf("unexpected probe %s", image)
		}
		return nil
	}
	a.ResolveImage = func(context.Context, string) (string, error) { return "", errors.New("no cached canonical RepoDigest") }
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		t.Errorf("participation registered without a canonical image: %s", r.URL)
		http.NotFound(w, r)
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if code := a.Run(context.Background(), []string{"serve", "--profiles", localOnlyProfiles(t, local)}); code != 1 || probes != 1 || !strings.Contains(errout.String(), "RepoDigest") {
		t.Fatalf("participation did not require canonical preparation: code=%d probes=%d %s", code, probes, errout)
	}
}

func offlineCapabilities() client.Capabilities {
	return client.Capabilities{OS: "windows", Arch: "amd64", CPULogical: 8, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 8 << 30, PowerState: "ac", PreflightRevision: "1", Runtime: client.Runtime{Type: "docker", Version: "29.2.1", Healthy: true}}
}

func localOnlyProfiles(t *testing.T, image string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "profiles.json")
	if err := os.WriteFile(path, []byte(fmt.Sprintf(`{"schema_version":"1","profiles":[{"id":"cpu","accelerator":"cpu","platform":"linux/amd64","image_digest":%q,"guard_version":"1"}]}`, image)), 0600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestResolveApprovedLocalImageUsesItsOwnRepositoryDigest(t *testing.T) {
	local := "sha256:" + strings.Repeat("a", 64)
	canonical := "campus/cpu@sha256:" + strings.Repeat("b", 64)
	for _, tc := range []struct{ name, inspect, want string }{
		{"canonical", "", canonical},
		{"local", fmt.Sprintf(`[{"Id":%q,"RepoDigests":[%q]}]`, local, canonical), canonical},
		{"no_repository_digest", fmt.Sprintf(`[{"Id":%q,"RepoDigests":[]}]`, local), ""},
		{"different_image", fmt.Sprintf(`[{"Id":%q,"RepoDigests":[%q]}]`, "sha256:"+strings.Repeat("c", 64), canonical), ""},
		{"mutable_repository", fmt.Sprintf(`[{"Id":%q,"RepoDigests":["campus/cpu:latest"]}]`, local), ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			input := local
			if tc.name == "canonical" {
				input = canonical
			}
			calls := 0
			got, err := resolveImageReference(context.Background(), input, func(_ context.Context, name string, args ...string) ([]byte, error) {
				calls++
				if name != "docker" || strings.Join(args, " ") != "image inspect "+local {
					t.Fatalf("unexpected command %s %q", name, args)
				}
				return []byte(tc.inspect), nil
			})
			if tc.want == "" {
				if err == nil {
					t.Fatalf("invalid image accepted: %s", got)
				}
				return
			}
			if err != nil || got != tc.want {
				t.Fatalf("reference=%s want=%s err=%v", got, tc.want, err)
			}
			if tc.name == "canonical" && calls != 0 {
				t.Fatal("canonical reference needed no resolution")
			}
		})
	}
}
