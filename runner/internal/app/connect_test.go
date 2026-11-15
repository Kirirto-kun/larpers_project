package app

import (
	"bytes"
	"campus/runner/internal/client"
	"campus/runner/internal/onboarding"
	"campus/runner/internal/session"
	"campus/runner/internal/worker"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

type connectionFixture struct {
	app                                      App
	root, hash, state, endpoint              string
	output, errors                           bytes.Buffer
	redeems, sessions, serves, loads, probes int
	gpu                                      bool
	token                                    string
	server                                   *httptest.Server
}

func connectionKit(t *testing.T, nvidia bool) *connectionFixture {
	t.Helper()
	f := &connectionFixture{token: strings.Repeat("s", 64)}
	f.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			fmt.Fprint(w, `{"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
		case "/api/v1/invitations/redeem":
			f.redeems++
			if f.probes == 0 {
				t.Error("invite consumed before runtime probe")
			}
			fmt.Fprint(w, `{"id":"00000000-0000-4000-8000-000000000001","node_id":"00000000-0000-4000-8000-000000000002","token":"device-secret","scope":"device"}`)
		case "/api/v1/node-sessions":
			f.sessions++
			fmt.Fprint(w, `{"id":"00000000-0000-4000-8000-000000000003","node_id":"00000000-0000-4000-8000-000000000002","state":"ready"}`)
		case "/api/v1/node-sessions/00000000-0000-4000-8000-000000000003/capabilities":
			w.WriteHeader(204)
		default:
			t.Error("unexpected connect request", r.URL.Path)
			w.WriteHeader(404)
		}
	}))
	t.Cleanup(f.server.Close)
	f.endpoint = f.server.URL
	f.root = filepath.Join(t.TempDir(), "Пакет 空格")
	os.Mkdir(f.root, 0700)
	f.state = filepath.Join(t.TempDir(), "state")
	makeFile := func(name, contents string) onboarding.File {
		path := filepath.Join(f.root, filepath.FromSlash(name))
		os.MkdirAll(filepath.Dir(path), 0700)
		os.WriteFile(path, []byte(contents), 0600)
		hash := sha256.Sum256([]byte(contents))
		return onboarding.File{Path: name, SHA256: hex.EncodeToString(hash[:]), SizeBytes: int64(len(contents))}
	}
	cpu := "campus/cpu@sha256:" + strings.Repeat("a", 64)
	cuda := "campus/cuda@sha256:" + strings.Repeat("b", 64)
	p := profiles{SchemaVersion: "1", Profiles: []profile{{ID: "cpu", Accelerator: "cpu", Platform: "linux/" + runtime.GOARCH, ImageDigest: &cpu, GuardVersion: "1"}}}
	m := onboarding.Manifest{SchemaVersion: "1", OS: runtime.GOOS, Arch: runtime.GOARCH, Runner: makeFile("campus", "binary"), Endpoint: f.endpoint, Images: []onboarding.Image{{File: makeFile("images/cpu.tar.gz", "CPU"), ImageDigest: cpu, Accelerator: "cpu", Platform: "linux/" + runtime.GOARCH}}}
	if nvidia {
		if runtime.GOARCH != "amd64" || runtime.GOOS == "darwin" {
			t.Skip("CUDA kit targets amd64 Linux runtime")
		}
		p.Profiles = append(p.Profiles, profile{ID: "cuda", Accelerator: "cuda", Platform: "linux/amd64", ImageDigest: &cuda, GuardVersion: "1"})
		m.Images = append(m.Images, onboarding.Image{File: makeFile("images/cuda.tar.gz", "CUDA"), ImageDigest: cuda, Accelerator: "cuda", Platform: "linux/amd64"})
	}
	raw, _ := json.Marshal(p)
	m.Profiles = makeFile("profiles.json", string(raw))
	inv := makeFile("invitation.txt", f.endpoint+"/join#token="+f.token+"\n")
	m.InviteFile = &inv
	raw, _ = json.Marshal(m)
	os.WriteFile(filepath.Join(f.root, "connection.json"), raw, 0600)
	sum := sha256.Sum256(raw)
	f.hash = hex.EncodeToString(sum[:])
	caps := client.Capabilities{OS: runtime.GOOS, Arch: runtime.GOARCH, CPULogical: 8, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 20 << 30, PowerState: "ac", PreflightRevision: "1", Runtime: client.Runtime{Type: "docker", Version: "29.2.1", Healthy: true}, GPUs: []client.GPU{}}
	if nvidia {
		caps.GPUs = []client.GPU{{ID: "0", Vendor: "nvidia", VRAMBytes: 8 << 30, Driver: "576.88"}}
	}
	f.app = App{Stdout: &f.output, Stderr: &f.errors, Stdin: strings.NewReader(""), Getenv: func(string) string { return "" }, Discover: func(context.Context) (client.Capabilities, error) { return caps, nil }, LocalDocker: func(context.Context) (string, error) { return "unix:///var/run/docker.sock", nil }, PinDocker: func(string) (func(), error) { return func() {}, nil }, LoadImage: func(context.Context, string) error { f.loads++; return nil }, VerifyImage: func(context.Context, string, string) error { return nil }, ResolveImage: func(ctx context.Context, s string) (string, error) { return s, nil }, Probe: func(context.Context, string, string, bool) error { f.probes++; return nil }, Serve: func(ctx context.Context, r *worker.Runner, c client.Capabilities, gpu bool) error {
		f.serves++
		f.gpu = gpu
		return nil
	}}
	return f
}
func (f *connectionFixture) run(args ...string) int {
	base := []string{"--state-dir", f.state, "connect", "--bundle", f.root, "--manifest-sha256", f.hash}
	return f.app.Run(context.Background(), append(base, args...))
}

func TestConnectFirstJoinThenReconnectWithoutInviteReplay(t *testing.T) {
	f := connectionKit(t, false)
	if code := f.run(); code != 0 {
		t.Fatal(code, f.errors.String())
	}
	if code := f.run(); code != 0 {
		t.Fatal(code, f.errors.String())
	}
	if f.redeems != 1 || f.sessions != 2 || f.serves != 2 || f.loads != 0 {
		t.Fatalf("unexpected connection lifecycle: %+v", f)
	}
	if strings.Contains(f.output.String()+f.errors.String(), f.token) || strings.Contains(f.output.String()+f.errors.String(), "device-secret") {
		t.Fatal("credential entered terminal output")
	}
}
func TestConnectCheckOnlyNeverEnrolls(t *testing.T) {
	f := connectionKit(t, false)
	if code := f.run("--check-only"); code != 0 {
		t.Fatal(code, f.errors.String())
	}
	if f.redeems != 0 || f.serves != 0 {
		t.Fatal("check consumed invitation")
	}
	if _, err := os.Stat(filepath.Join(f.state, "identity.json")); !os.IsNotExist(err) {
		t.Fatal("check created identity")
	}
}
func TestConnectRemoteOrCorruptKitHasNoRuntimeOrNetworkSideEffects(t *testing.T) {
	for _, remote := range []bool{false, true} {
		t.Run(fmt.Sprint(remote), func(t *testing.T) {
			f := connectionKit(t, false)
			if remote {
				f.app.LocalDocker = func(context.Context) (string, error) { return "", fmt.Errorf("remote engine refused") }
			} else {
				os.WriteFile(filepath.Join(f.root, "campus"), []byte("bad"), 0600)
			}
			if f.run() == 0 || f.redeems != 0 || f.loads != 0 || f.probes != 0 {
				t.Fatal("unsafe connection performed side effects")
			}
		})
	}
}
func TestConnectGPURequiresConsentAndActualProbe(t *testing.T) {
	f := connectionKit(t, true)
	f.app.Stdin = strings.NewReader("yes\n")
	if code := f.run(); code != 0 || !f.gpu {
		t.Fatal(code, f.errors.String(), f.gpu)
	}
	g := connectionKit(t, true)
	if code := g.run("--gpu=false"); code != 0 || g.gpu {
		t.Fatal(code, g.errors.String(), g.gpu)
	}
}
func TestConnectEOFDoesNotAssumeGPUConsent(t *testing.T) {
	f := connectionKit(t, true)
	if f.run() == 0 || f.redeems != 0 {
		t.Fatal("EOF granted GPU consent or consumed invite")
	}
}
func TestConnectStopDoesNotReadOrLoadBundle(t *testing.T) {
	f := connectionKit(t, false)
	os.Remove(filepath.Join(f.root, "connection.json"))
	if f.run("--stop") != 0 {
		t.Fatal(f.errors.String())
	}
	action, err := session.ReadControl(f.state)
	if err != nil || action != "stop" || f.redeems != 0 {
		t.Fatal(action, err)
	}
}

func TestConnectOpaqueInvitationCannotBecomeAFlagOrEnterErrors(t *testing.T) {
	for _, token := range []string{"--help", "-private-device-invitation-secret"} {
		t.Run(token[:2], func(t *testing.T) {
			f := connectionKit(t, false)
			raw, err := os.ReadFile(filepath.Join(f.root, "connection.json"))
			if err != nil {
				t.Fatal(err)
			}
			var m onboarding.Manifest
			if err = json.Unmarshal(raw, &m); err != nil {
				t.Fatal(err)
			}
			m.InviteFile = nil
			raw, _ = json.Marshal(m)
			os.WriteFile(filepath.Join(f.root, "connection.json"), raw, 0600)
			sum := sha256.Sum256(raw)
			f.hash = hex.EncodeToString(sum[:])
			f.token = token
			f.app.Stdin = strings.NewReader(token + "\n")
			if code := f.run(); code != 0 || f.redeems != 1 || f.serves != 1 {
				t.Fatalf("opaque invitation parsed as flags: status=%d redeem=%d serve=%d", code, f.redeems, f.serves)
			}
			if strings.Contains(f.output.String()+f.errors.String(), token) {
				t.Fatal("private invitation leaked into output")
			}
		})
	}
}

func TestConnectUsesVerifiedProfileSnapshotDuringPreparation(t *testing.T) {
	f := connectionKit(t, false)
	changed := false
	f.app.VerifyImage = func(context.Context, string, string) error {
		if !changed {
			path := filepath.Join(f.root, "profiles.json")
			raw, _ := os.ReadFile(path)
			raw = bytes.ReplaceAll(raw, []byte(strings.Repeat("a", 64)), []byte(strings.Repeat("c", 64)))
			os.WriteFile(path, raw, 0600)
			changed = true
		}
		return nil
	}
	f.app.Serve = func(ctx context.Context, r *worker.Runner, c client.Capabilities, gpu bool) error {
		f.serves++
		if _, ok := r.AllowedImages["campus/cpu@sha256:"+strings.Repeat("c", 64)]; ok {
			t.Error("mutated unpinned profile entered worker")
		}
		if _, ok := r.AllowedImages["campus/cpu@sha256:"+strings.Repeat("a", 64)]; !ok {
			t.Error("verified profile was lost")
		}
		return nil
	}
	if code := f.run(); code != 0 {
		t.Fatal(code, f.errors.String())
	}
	if f.serves != 1 {
		t.Fatal("verified original profile never served")
	}
}
