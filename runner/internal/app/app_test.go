package app

import (
	"archive/zip"
	"bytes"
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/session"
	"campus/runner/internal/worker"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testApp(t *testing.T) (*App, *bytes.Buffer, *bytes.Buffer, string) {
	t.Helper()
	dir := t.TempDir()
	out, errout := &bytes.Buffer{}, &bytes.Buffer{}
	a := &App{Stdout: out, Stderr: errout, Getenv: func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		return ""
	}}
	return a, out, errout, dir
}

func protocol(w http.ResponseWriter) {
	fmt.Fprint(w, `{"api_version":1,"min_worker_version":1,"max_worker_version":1,"lease_ms":60000,"heartbeat_interval_ms":10000,"long_poll_ms":25000}`)
}

func saveIdentity(t *testing.T, dir, endpoint string) {
	t.Helper()
	if err := session.Save(dir, session.Identity{InstallationID: "install-1", Endpoint: endpoint, NodeID: "node-1", PoolID: "pool-1", CredentialID: "cred-1", Credential: "device-secret"}); err != nil {
		t.Fatal(err)
	}
}

func TestHelpVersionAndUsageHaveDistinctStatuses(t *testing.T) {
	for _, tc := range []struct {
		args []string
		code int
		text string
	}{
		{[]string{"--help"}, 0, "benchmark-local"},
		{[]string{"--version"}, 0, "0.1.0"},
		{[]string{"unknown"}, 2, "unknown command"},
		{[]string{"fetch", "job-1"}, 2, "--to"},
		{[]string{"leave"}, 2, "--drain"},
		{[]string{"run", "--gpu"}, 2, "--"},
	} {
		t.Run(strings.Join(tc.args, " "), func(t *testing.T) {
			a, out, errout, _ := testApp(t)
			if got := a.Run(context.Background(), tc.args); got != tc.code {
				t.Fatalf("code %d, want %d: %s", got, tc.code, errout)
			}
			if !strings.Contains(out.String()+errout.String(), tc.text) {
				t.Fatalf("missing %q in %s%s", tc.text, out, errout)
			}
		})
	}
}

func TestJoinHandshakesBeforeRedeemAndPersistsPrivateIdentity(t *testing.T) {
	a, out, errout, dir := testApp(t)
	var order []string
	var installation string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		order = append(order, r.Method+" "+r.URL.Path)
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/invitations/redeem":
			var body struct {
				Token           string `json:"token"`
				InstallationID  string `json:"installation_id"`
				ProtocolVersion int    `json:"protocol_version"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if body.Token != "invite secret" || body.InstallationID == "" || body.ProtocolVersion != 1 {
				t.Errorf("bad redeem body: %#v", body)
			}
			installation = body.InstallationID
			if r.Header.Get("Authorization") != "" {
				t.Error("invitation was treated as member credential")
			}
			fmt.Fprint(w, `{"id":"cred-1","token":"device-secret","scope":"device","node_id":"node-1","expires_at":null}`)
		default:
			t.Errorf("unexpected %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	code := a.Run(context.Background(), []string{"join", s.URL + "/join?token=invite%20secret", "--no-serve"})
	if code != 0 {
		t.Fatalf("code %d: %s", code, errout)
	}
	if strings.Join(order, ",") != "GET /api/v1/protocol,POST /api/v1/invitations/redeem" {
		t.Fatalf("order %#v", order)
	}
	identity, err := session.LoadOrCreate(dir)
	if err != nil || identity.InstallationID != installation || identity.Credential != "device-secret" || identity.MemberCredential != "" {
		t.Fatalf("bad saved identity %#v: %v", identity, err)
	}
	if strings.Contains(out.String(), "device-secret") {
		t.Fatal("credential printed")
	}
}

func TestDuplicateJoinTakesLockBeforeCreatingIdentityOrRedeeming(t *testing.T) {
	a, _, errout, dir := testApp(t)
	unlock, err := session.Lock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer unlock()
	if code := a.Run(context.Background(), []string{"join", "token", "--endpoint", "http://127.0.0.1:1", "--no-serve"}); code == 0 {
		t.Fatal("duplicate join accepted")
	}
	if _, err := os.Stat(filepath.Join(dir, "identity.json")); !os.IsNotExist(err) {
		t.Fatalf("identity created before lock: %v, %s", err, errout)
	}
}

func TestJoinRejectsIncompatibleProtocolWithoutRedeem(t *testing.T) {
	a, _, _, _ := testApp(t)
	redeemed := false
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			fmt.Fprint(w, `{"api_version":"2"}`)
			return
		}
		redeemed = true
	}))
	defer s.Close()
	if a.Run(context.Background(), []string{"join", "token", "--endpoint", s.URL, "--no-serve"}) == 0 || redeemed {
		t.Fatal("redeemed against incompatible coordinator")
	}
}

func TestVerifiedTLSAndOptionalCA(t *testing.T) {
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		fmt.Fprint(w, `{"id":"cred-1","token":"secret","scope":"device","node_id":"node-1","expires_at":null}`)
	}))
	defer s.Close()
	a, _, _, _ := testApp(t)
	if a.Run(context.Background(), []string{"join", "token", "--endpoint", s.URL, "--no-serve"}) == 0 {
		t.Fatal("untrusted TLS certificate accepted")
	}
	a, _, errout, _ := testApp(t)
	ca := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: s.Certificate().Raw}), 0600); err != nil {
		t.Fatal(err)
	}
	if got := a.Run(context.Background(), []string{"join", "token", "--endpoint", s.URL, "--ca-file", ca, "--no-serve"}); got != 0 {
		t.Fatalf("custom CA failed: %s", errout)
	}
}

func TestLeaveWritesDistinctLocalControlsWithoutNetwork(t *testing.T) {
	for _, action := range []string{"drain", "stop"} {
		a, _, errout, dir := testApp(t)
		if code := a.Run(context.Background(), []string{"leave", "--" + action}); code != 0 {
			t.Fatalf("%d: %s", code, errout)
		}
		got, err := session.ReadControl(dir)
		if err != nil || got != action {
			t.Fatalf("control %q: %v", got, err)
		}
	}
}

func preparedProfiles(t *testing.T) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "profiles.json")
	if err := os.WriteFile(p, []byte(`{"schema_version":"1","profiles":[{"id":"cpu","accelerator":"cpu","platform":"linux/amd64","image_digest":"campus/cpu@sha256:`+strings.Repeat("a", 64)+`","guard_version":"1"},{"id":"gpu","accelerator":"cuda","platform":"linux/amd64","image_digest":"campus/cuda@sha256:`+strings.Repeat("b", 64)+`","guard_version":"1"}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestServeRegistersProbedCapabilitiesAndHoldsSessionLock(t *testing.T) {
	a, _, errout, dir := testApp(t)
	var registered, updated bool
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/node-sessions":
			var body struct {
				ProtocolVersion int                    `json:"protocol_version"`
				Capabilities    generated.Capabilities `json:"capabilities"`
			}
			decoder := json.NewDecoder(r.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&body); err != nil {
				t.Error(err)
			}
			if body.ProtocolVersion != 1 {
				t.Error("missing worker protocol version")
			}
			if body.Capabilities.OfferedBudget.VRAMBytes == nil || *body.Capabilities.OfferedBudget.VRAMBytes != 0 || body.Capabilities.OfferedBudget.GPUSlots == nil || *body.Capabilities.OfferedBudget.GPUSlots != 0 {
				t.Error("GPU offered without opt in")
			}
			if len(body.Capabilities.ImageDigests) != 1 || body.Capabilities.ImageDigests[0] != "campus/cpu@sha256:"+strings.Repeat("a", 64) || body.Capabilities.PreflightRevision != 1 {
				t.Error("unverified image advertised or revision is missing")
			}
			registered = true
			fmt.Fprint(w, `{"id":"session-1","node_id":"node-1","state":"ready"}`)
		case "/api/v1/node-sessions/session-1/capabilities":
			if r.Method != "PUT" || r.Header.Get("X-Node-Session") != "session-1" {
				t.Errorf("bad update request %s", r.Method)
			}
			updated = true
			w.WriteHeader(204)
		default:
			t.Errorf("unexpected URL %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	a.Discover = func(context.Context) (client.Capabilities, error) {
		return client.Capabilities{OS: "windows", Arch: "amd64", CPULogical: 8, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 8 << 30, Runtime: client.Runtime{Type: "docker", Version: "29.2.1", Healthy: true}, PowerState: "unknown", PreflightRevision: "1", ImageDigests: []string{"unverified@sha256:" + strings.Repeat("c", 64)}, GPUs: []client.GPU{{ID: "0", Vendor: "nvidia", Driver: "test-driver", VRAMBytes: 8 << 30}}}, nil
	}
	var probes int
	a.Probe = func(_ context.Context, image, platform string, gpu bool) error {
		probes++
		if gpu {
			t.Error("GPU probe without optin")
		}
		return nil
	}
	a.VerifyImage = func(context.Context, string, string) error { return nil }
	a.Serve = func(_ context.Context, r *worker.Runner, c client.Capabilities, gpu bool) error {
		if r.Client.SessionID != "session-1" || !registered || !updated {
			t.Error("session was not registered")
		}
		if len(r.AllowedImages) != 1 || len(c.ImageDigests) != 1 {
			t.Error("unprobed profiles entered the worker allowlist")
		}
		if unlock, err := session.Lock(dir); err == nil {
			unlock()
			t.Error("serve session lock not held")
		}
		return nil
	}
	if code := a.Run(context.Background(), []string{"serve", "--profiles", preparedProfiles(t)}); code != 0 {
		t.Fatalf("%d: %s", code, errout)
	}
	if probes != 1 {
		t.Fatalf("CPU probes %d", probes)
	}
}

func TestDoctorRejectsUnpreparedProfilesWithoutRunningImage(t *testing.T) {
	a, out, errout, _ := testApp(t)
	p := filepath.Join(t.TempDir(), "profiles.json")
	os.WriteFile(p, []byte(`{"schema_version":"1","profiles":[{"id":"cpu","accelerator":"cpu","platform":"linux/amd64","image_digest":null,"guard_version":"1"}]}`), 0600)
	a.Discover = func(context.Context) (client.Capabilities, error) {
		return client.Capabilities{Arch: "amd64", Runtime: client.Runtime{Healthy: true}}, nil
	}
	a.Probe = func(context.Context, string, string, bool) error { t.Fatal("unapproved image probed"); return nil }
	if code := a.Run(context.Background(), []string{"doctor", "--profiles", p}); code == 0 {
		t.Fatal("unprepared profile passed doctor")
	}
	if !strings.Contains(out.String()+errout.String(), "prepare") {
		t.Fatalf("no actionable preparation error: %s%s", out, errout)
	}
}

func TestRunRequiresMemberScopeBeforeUpload(t *testing.T) {
	a, _, errout, dir := testApp(t)
	saveIdentity(t, dir, "http://127.0.0.1:1")
	if code := a.Run(context.Background(), []string{"run", "--", "python", "train.py"}); code == 0 || !strings.Contains(errout.String(), "CAMPUS_TOKEN") {
		t.Fatalf("device credential accepted for member submit: %d %s", code, errout)
	}
}

func TestRunPackagesSecretsDataAndReusesSubmissionKeyAfterLostACK(t *testing.T) {
	a, out, errout, dir := testApp(t)
	project, data := t.TempDir(), t.TempDir()
	os.WriteFile(filepath.Join(project, "train.py"), []byte("print('你好')"), 0600)
	os.WriteFile(filepath.Join(project, ".env"), []byte("TOP_SECRET"), 0600)
	os.WriteFile(filepath.Join(data, "samples.txt"), []byte("dataset"), 0600)
	a.Cwd = func() (string, error) { return project, nil }
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member-secret"
		}
		return ""
	}
	type upload struct {
		digest   string
		name     string
		size     int64
		contents []byte
	}
	uploads := map[string]*upload{}
	var submitKeys []string
	var preview map[string]any
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer member-secret" {
			t.Errorf("wrong member auth: %q", r.Header.Get("Authorization"))
		}
		route := strings.TrimPrefix(r.URL.Path, "/api/v1")
		switch {
		case route == "/protocol":
			protocol(w)
		case route == "/uploads":
			var body struct {
				Digest string `json:"sha256"`
				Name   string `json:"name"`
				Size   int64  `json:"size_bytes"`
			}
			json.NewDecoder(r.Body).Decode(&body)
			id := fmt.Sprintf("00000000-0000-4000-8000-%012d", len(uploads)+1)
			uploads[id] = &upload{digest: body.Digest, name: body.Name, size: body.Size}
			fmt.Fprintf(w, `{"id":%q,"state":"pending","part_count":1,"parts":[]}`, id)
		case strings.HasPrefix(route, "/uploads/"):
			parts := strings.Split(strings.TrimPrefix(route, "/uploads/"), "/")
			u := uploads[parts[0]]
			if u == nil {
				t.Errorf("unknown upload %s", route)
				http.NotFound(w, r)
				return
			}
			if len(parts) == 1 {
				fmt.Fprintf(w, `{"id":%q,"state":"pending","part_count":1,"parts":[]}`, parts[0])
				return
			}
			if parts[1] == "parts" {
				u.contents, _ = io.ReadAll(r.Body)
				fmt.Fprintf(w, `{"number":0,"sha256":%q,"size_bytes":%d}`, u.digest, u.size)
				return
			}
			fmt.Fprintf(w, `{"id":%q,"sha256":%q,"size_bytes":%d,"name":%q,"media_type":"application/zip"}`, parts[0], u.digest, u.size, u.name)
		case route == "/job-previews" && r.Method == "POST":
			raw, _ := io.ReadAll(r.Body)
			var typed generated.PreviewRequest
			decoder := json.NewDecoder(bytes.NewReader(raw))
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&typed); err != nil {
				t.Error(err)
			}
			json.Unmarshal(raw, &preview)
			fmt.Fprint(w, `{"id":"preview-1","state":"inspecting","plan_revision":1}`)
		case route == "/job-previews/preview-1":
			var intent map[string]any
			prompt, _ := preview["prompt"].(string)
			_, encoded, _ := strings.Cut(prompt, "Command intent: ")
			if err := json.Unmarshal([]byte(encoded), &intent); err != nil {
				t.Error(err)
			}
			json.NewEncoder(w).Encode(map[string]any{"id": "preview-1", "state": "ready", "plan_revision": 2, "plan": map[string]any{"schema_version": "1", "plan_revision": 2, "input_manifest_digest": strings.Repeat("a", 64), "execution_kind": "single", "job_deadline_at": "2026-11-14T23:00:00Z", "retry_policy": map[string]any{}, "stages": []any{map[string]any{"stage_id": "run", "adapter": map[string]any{"id": "generic.project", "version": "1"}, "variants": []any{map[string]any{"variant_id": "cuda", "image_digest": "campus/cuda@sha256:" + strings.Repeat("a", 64), "platform": "linux/amd64", "accelerator": "cuda", "resource_requirements": map[string]any{"cpu_millis": 1000, "memory_bytes": 536870912, "disk_bytes": 1073741824}}}, "argv": []string{"python", "train.py", "path with spaces/你好"}, "working_dir": "/workspace", "bindings": intent["bindings"], "outputs": []any{map[string]any{"path": "model.pt", "required": true, "max_bytes": 1048576}}, "network_policy": "none", "task_deadline_ms": 30000, "validation_policy": "unverified", "resume_policy": "restart"}}}})
		case route == "/jobs":
			submitKeys = append(submitKeys, r.Header.Get("Idempotency-Key"))
			var body map[string]any
			json.NewDecoder(r.Body).Decode(&body)
			if body["preview_id"] != "preview-1" || body["plan_revision"] != float64(2) {
				t.Errorf("bad jobs payload: %#v", body)
			}
			if len(submitKeys) == 1 {
				conn, _, err := w.(http.Hijacker).Hijack()
				if err != nil {
					t.Error(err)
					return
				}
				conn.Close()
				return
			}
			fmt.Fprint(w, `{"id":"job-1","state":"queued"}`)
		default:
			t.Errorf("unexpected %s %s", r.Method, route)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if code := a.Run(context.Background(), []string{"run", "--gpu", "--data", data + ":/data", "--output", "model.pt", "--", "python", "train.py", "path with spaces/你好"}); code != 0 {
		t.Fatalf("%d: %s", code, errout)
	}
	if len(submitKeys) != 2 || submitKeys[0] == "" || submitKeys[0] != submitKeys[1] {
		t.Fatalf("unstable submission keys %#v", submitKeys)
	}
	var request map[string]any
	prompt, _ := preview["prompt"].(string)
	_, encoded, _ := strings.Cut(prompt, "Command intent: ")
	if err := json.Unmarshal([]byte(encoded), &request); err != nil {
		t.Fatal(err)
	}
	if preview["accelerator"] != "cuda" || request["requires_gpu"] != true || len(request["argv"].([]any)) != 3 {
		t.Fatalf("argv/GPU corrupted: %#v", request)
	}
	if preview["project_artifact_id"] == "" || preview["project_artifact_id"] == nil {
		t.Fatalf("missing manifest: %#v", preview)
	}
	if len(preview["inputs"].([]any)) != 1 {
		t.Fatalf("dataset not separate: %#v", preview)
	}
	var names []string
	for _, u := range uploads {
		z, err := zip.NewReader(bytes.NewReader(u.contents), int64(len(u.contents)))
		if err != nil {
			t.Fatal(err)
		}
		for _, f := range z.File {
			names = append(names, f.Name)
		}
	}
	if strings.Contains(strings.Join(names, ","), ".env") || !strings.Contains(strings.Join(names, ","), "samples.txt") {
		t.Fatalf("unsafe snapshot: %#v", names)
	}
	if !strings.Contains(out.String(), "job-1") {
		t.Fatalf("missing job ID: %s", out)
	}
}

func TestNeedsInputNeverSubmitsAJob(t *testing.T) {
	a, out, errout, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	// Resume a persisted preview: no repackaging or replacement guesses.
	if err := os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"key-1","plan_revision":0}`), 0600); err != nil {
		t.Fatal(err)
	}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/job-previews/preview-1":
			fmt.Fprint(w, `{"id":"preview-1","state":"needs_input","plan_revision":1,"question":{"question_id":"dataset","prompt":"Choose the labeled dataset"}}`)
		default:
			t.Errorf("unexpected submission %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 3 {
		t.Fatalf("%d: %s", code, errout)
	}
	if !strings.Contains(out.String(), "preview-1") || !strings.Contains(out.String(), "Choose the labeled dataset") {
		t.Fatalf("missing unresolved input details %s", out)
	}
}

func TestFetchPublishesOnlyCompleteChecksumVerifiedDirectory(t *testing.T) {
	for _, bad := range []bool{false, true} {
		t.Run(fmt.Sprint(bad), func(t *testing.T) {
			a, _, errout, dir := testApp(t)
			dest := filepath.Join(t.TempDir(), "download")
			a.Getenv = func(k string) string {
				if k == "CAMPUS_STATE_DIR" {
					return dir
				}
				if k == "CAMPUS_TOKEN" {
					return "member"
				}
				return ""
			}
			sum := sha256.Sum256([]byte("model"))
			digest := hex.EncodeToString(sum[:])
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/api/v1/protocol":
					protocol(w)
				case "/api/v1/jobs/job-1":
					fmt.Fprintf(w, `{"id":"job-1","state":"succeeded","artifacts":[{"id":"file-1","sha256":%q,"size_bytes":5,"name":"workspace/model.pt","media_type":"application/octet-stream"}]}`, digest)
				case "/api/v1/artifacts/file-1/content":
					if bad {
						fmt.Fprint(w, "wrong")
					} else {
						fmt.Fprint(w, "model")
					}
				default:
					t.Errorf("unexpected route %s", r.URL)
					http.NotFound(w, r)
				}
			}))
			defer s.Close()
			saveIdentity(t, dir, s.URL)
			code := a.Run(context.Background(), []string{"fetch", "job-1", "--to", dest})
			if bad {
				if code == 0 {
					t.Fatal("bad checksum accepted")
				}
				if _, err := os.Stat(dest); !os.IsNotExist(err) {
					t.Fatal("partial destination exposed")
				}
				return
			}
			if code != 0 {
				t.Fatalf("%d: %s", code, errout)
			}
			b, err := os.ReadFile(filepath.Join(dest, "workspace", "model.pt"))
			if err != nil || string(b) != "model" {
				t.Fatalf("bad file %q %v", b, err)
			}
		})
	}
}

func TestFetchRejectsTraversalAndCaseCollisionsBeforeDownloading(t *testing.T) {
	for _, paths := range [][]string{{"../outside"}, {"A/model.pt", "a/model.pt"}, {"x", "x/y"}, {"x.part", "x"}, {"x", "x.part/y"}, {"x/y", "x/y.part/z"}} {
		a, _, _, dir := testApp(t)
		a.Getenv = func(k string) string {
			if k == "CAMPUS_STATE_DIR" {
				return dir
			}
			if k == "CAMPUS_TOKEN" {
				return "member"
			}
			return ""
		}
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/api/v1/protocol" {
				protocol(w)
				return
			}
			if r.URL.Path == "/api/v1/jobs/job-1" {
				var files []client.Artifact
				for i, p := range paths {
					files = append(files, client.Artifact{ArtifactID: fmt.Sprintf("f-%d", i), Digest: strings.Repeat("a", 64), SizeBytes: 1, Path: p})
				}
				json.NewEncoder(w).Encode(map[string]any{"id": "job-1", "state": "succeeded", "artifacts": files})
				return
			}
			t.Errorf("unsafe manifest caused content request %s", r.URL)
			http.NotFound(w, r)
		}))
		saveIdentity(t, dir, s.URL)
		if a.Run(context.Background(), []string{"fetch", "job-1", "--to", filepath.Join(t.TempDir(), "dest")}) == 0 {
			t.Fatalf("accepted paths %#v", paths)
		}
		s.Close()
	}
}

func TestLogsPaginationAndCancelUseMemberAPI(t *testing.T) {
	a, out, errout, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	var cancelled bool
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/jobs/job-1/logs":
			if r.URL.Query().Get("cursor") == "" {
				fmt.Fprint(w, `{"entries":[{"cursor":1,"attempt_id":"attempt-1","sequence":0,"occurred_at":"2026-11-14T06:00:00Z","text":"first","gap":false}],"cursor":1,"truncated":true}`)
			} else if r.URL.Query().Get("cursor") == "1" {
				fmt.Fprint(w, `{"entries":[{"cursor":2,"attempt_id":"attempt-1","sequence":1,"occurred_at":"2026-11-14T06:00:01Z","text":"second","gap":false}],"cursor":2,"truncated":false}`)
			} else {
				fmt.Fprint(w, `{"entries":[],"cursor":2,"truncated":false}`)
			}
		case "/api/v1/jobs/job-1/cancel":
			if r.Method != "POST" {
				t.Error("cancel not POST")
			}
			cancelled = true
			fmt.Fprint(w, `{"id":"job-1","state":"cancelled"}`)
		default:
			t.Errorf("unexpected URL %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if a.Run(context.Background(), []string{"logs", "job-1"}) != 0 || out.String() != "first\nsecond\n" {
		t.Fatalf("bad logs %s: %s", out, errout)
	}
	if a.Run(context.Background(), []string{"cancel", "job-1"}) != 0 || !cancelled {
		t.Fatalf("cancel failed %s", errout)
	}
}

func TestPollingRespectsContext(t *testing.T) {
	a, _, _, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"key-1"}`), 0600)
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
		} else {
			fmt.Fprint(w, `{"id":"preview-1","state":"inspecting"}`)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel()
	start := time.Now()
	if a.Run(ctx, []string{"run", "--resume"}) == 0 || time.Since(start) > time.Second {
		t.Fatal("preview polling ignored cancellation")
	}
}

func TestResumeUnknownAcknowledgementPostsFrozenRevisionWithoutReplanning(t *testing.T) {
	a, out, errout, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	var posts int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/jobs":
			posts++
			var body map[string]any
			json.NewDecoder(r.Body).Decode(&body)
			if body["preview_id"] != "preview-1" || body["plan_revision"] != float64(7) || r.Header.Get("Idempotency-Key") != "saved-key" {
				t.Errorf("changed submission: %#v key=%q", body, r.Header.Get("Idempotency-Key"))
			}
			fmt.Fprint(w, `{"id":"job-1","state":"queued"}`)
		default:
			t.Errorf("a committed preview must not be polled/replanned during acknowledgement recovery: %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if err := os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"saved-key","plan_revision":7,"submission_started":true}`), 0600); err != nil {
		t.Fatal(err)
	}
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 0 || posts != 1 || !strings.Contains(out.String(), "job-1") {
		t.Fatalf("recovery failed: code=%d posts=%d out=%s err=%s", code, posts, out, errout)
	}
}

func TestDoctorGPUOptInRequiresSuccessfulCUDAComputation(t *testing.T) {
	a, _, errout, _ := testApp(t)
	a.Discover = func(context.Context) (client.Capabilities, error) {
		return client.Capabilities{Arch: "amd64", CPULogical: 8, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 8 << 30, Runtime: client.Runtime{Healthy: true}, GPUs: []client.GPU{{ID: "0", VRAMBytes: 8 << 30}}}, nil
	}
	a.VerifyImage = func(context.Context, string, string) error { return nil }
	a.Probe = func(_ context.Context, image, platform string, gpu bool) error {
		if gpu {
			return fmt.Errorf("CUDA computation failed")
		}
		return nil
	}
	if code := a.Run(context.Background(), []string{"doctor", "--gpu", "--profiles", preparedProfiles(t)}); code == 0 || !strings.Contains(errout.String(), "CUDA computation") {
		t.Fatalf("failed GPU probe was accepted: %d %s", code, errout)
	}
}

func TestDoctorOffersOnlyTheGPUActuallyAddressedByCUDAProbe(t *testing.T) {
	a, out, errout, _ := testApp(t)
	a.Discover = func(context.Context) (client.Capabilities, error) {
		return client.Capabilities{Arch: "amd64", CPULogical: 8, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 8 << 30, Runtime: client.Runtime{Healthy: true}, GPUs: []client.GPU{{ID: "0", VRAMBytes: 8 << 30}, {ID: "1", VRAMBytes: 16 << 30}}}, nil
	}
	a.VerifyImage = func(context.Context, string, string) error { return nil }
	a.Probe = func(context.Context, string, string, bool) error { return nil }
	if code := a.Run(context.Background(), []string{"doctor", "--gpu", "--profiles", preparedProfiles(t)}); code != 0 {
		t.Fatalf("%d: %s", code, errout)
	}
	var caps client.Capabilities
	if err := json.Unmarshal(out.Bytes(), &caps); err != nil {
		t.Fatal(err)
	}
	if len(caps.OfferedBudget.GPUIDs) != 1 || caps.OfferedBudget.GPUIDs[0] != "0" || caps.OfferedBudget.VRAMBytes != 8<<30 {
		t.Fatalf("unprobed CUDA devices offered: %#v", caps.OfferedBudget)
	}
}

func TestMalformedReadyPreviewNeverSubmits(t *testing.T) {
	for _, revision := range []string{`null`, `true`, `-1`, `{}`, `0`, `"revision-1"`} {
		t.Run(revision, func(t *testing.T) {
			a, _, _, dir := testApp(t)
			a.Getenv = func(k string) string {
				if k == "CAMPUS_STATE_DIR" {
					return dir
				}
				if k == "CAMPUS_TOKEN" {
					return "member"
				}
				return ""
			}
			os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"key-1"}`), 0600)
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/v1/protocol" {
					protocol(w)
					return
				}
				if r.URL.Path == "/api/v1/job-previews/preview-1" {
					fmt.Fprintf(w, `{"id":"preview-1","state":"ready","plan_revision":%s}`, revision)
					return
				}
				t.Errorf("malformed preview submitted %s", r.URL)
				http.NotFound(w, r)
			}))
			defer s.Close()
			saveIdentity(t, dir, s.URL)
			if a.Run(context.Background(), []string{"run", "--resume"}) == 0 {
				t.Fatalf("accepted revision %s", revision)
			}
		})
	}
}

func TestTerminalPreviewIsPersistedAndDoesNotBlockFreshRun(t *testing.T) {
	for _, terminal := range []string{"failed", "cancelled", "expired", "rejected"} {
		t.Run(terminal, func(t *testing.T) {
			a, _, errout, dir := testApp(t)
			project := t.TempDir()
			a.Cwd = func() (string, error) { return project, nil }
			a.Getenv = func(k string) string {
				if k == "CAMPUS_STATE_DIR" {
					return dir
				}
				if k == "CAMPUS_TOKEN" {
					return "member"
				}
				return ""
			}
			os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"saved-key"}`), 0600)
			var freshUploads int
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/api/v1/protocol":
					protocol(w)
				case "/api/v1/job-previews/preview-1":
					fmt.Fprintf(w, `{"id":"preview-1","state":%q}`, terminal)
				case "/api/v1/uploads":
					freshUploads++
					w.WriteHeader(400)
					fmt.Fprint(w, `{"code":"FIXTURE_UPLOAD_REACHED","message":"fresh upload reached"}`)
				default:
					t.Errorf("unexpected request %s", r.URL)
					http.NotFound(w, r)
				}
			}))
			defer s.Close()
			saveIdentity(t, dir, s.URL)
			if a.Run(context.Background(), []string{"run", "--resume"}) != 1 {
				t.Fatal("terminal preview not rejected")
			}
			var saved map[string]any
			raw, _ := os.ReadFile(filepath.Join(dir, "submission.json"))
			json.Unmarshal(raw, &saved)
			if saved["preview_terminal"] != true || saved["terminal_state"] != terminal {
				t.Fatalf("terminal state not durable: %s", raw)
			}
			errout.Reset()
			a.Run(context.Background(), []string{"run", "--", "python", "train.py"})
			if freshUploads != 1 || !strings.Contains(errout.String(), "fresh upload reached") {
				t.Fatalf("fresh run wedged: uploads=%d %s", freshUploads, errout)
			}
		})
	}
}

func TestDiscardIsLocalAndOnlyAllowsNeverSubmittedPreview(t *testing.T) {
	for _, submitted := range []bool{false, true} {
		a, _, errout, dir := testApp(t)
		body := fmt.Sprintf(`{"preview_id":"preview-1","idempotency_key":"saved-key","plan_revision":7,"submission_started":%t}`, submitted)
		os.WriteFile(filepath.Join(dir, "submission.json"), []byte(body), 0600)
		code := a.Run(context.Background(), []string{"run", "--discard"})
		var saved map[string]any
		raw, _ := os.ReadFile(filepath.Join(dir, "submission.json"))
		json.Unmarshal(raw, &saved)
		if submitted {
			if code == 0 || saved["preview_terminal"] == true {
				t.Fatalf("unknown job ACK discarded: %d %s %s", code, errout, raw)
			}
			continue
		}
		if code != 0 || saved["preview_terminal"] != true || saved["terminal_state"] != "discarded" {
			t.Fatalf("local preview discard failed: %d %s %s", code, errout, raw)
		}
	}
}

func TestResumeAnswersSamePreviewAndSubmitsItsNewRevision(t *testing.T) {
	a, out, errout, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"saved-key"}`), 0600)
	file := filepath.Join(t.TempDir(), "answers.json")
	os.WriteFile(file, []byte(`{"question_id":"batch","answer":"4"}`), 0600)
	var gets, answers, jobs int
	var key string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/job-previews/preview-1":
			gets++
			if answers == 0 {
				fmt.Fprint(w, `{"id":"preview-1","state":"needs_input","plan_revision":3,"question":{"question_id":"batch","prompt":"Choose a batch size"}}`)
			} else {
				fmt.Fprint(w, `{"id":"preview-1","state":"ready","plan_revision":4}`)
			}
		case "/api/v1/job-previews/preview-1/answers":
			answers++
			var body struct {
				Revision   int    `json:"plan_revision"`
				QuestionID string `json:"question_id"`
				Answer     string `json:"answer"`
			}
			decoder := json.NewDecoder(r.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&body); err != nil {
				t.Error(err)
			}
			if body.Revision != 3 || body.QuestionID != "batch" || body.Answer != "4" {
				t.Errorf("answer body changed: %#v", body)
			}
			if key == "" {
				key = r.Header.Get("Idempotency-Key")
			}
			if key == "" || key != r.Header.Get("Idempotency-Key") {
				t.Error("answer key not stable")
			}
			if answers == 1 {
				conn, _, err := w.(http.Hijacker).Hijack()
				if err != nil {
					t.Error(err)
					return
				}
				conn.Close()
				return
			}
			fmt.Fprint(w, `{"id":"preview-1"}`)
		case "/api/v1/jobs":
			jobs++
			var body map[string]any
			json.NewDecoder(r.Body).Decode(&body)
			if body["preview_id"] != "preview-1" || body["plan_revision"] != float64(4) || r.Header.Get("Idempotency-Key") != "saved-key" {
				t.Errorf("wrong run after answers: %#v", body)
			}
			fmt.Fprint(w, `{"id":"job-1","state":"queued"}`)
		default:
			t.Errorf("answer flow replaced preview or submitted unexpected endpoint %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if code := a.Run(context.Background(), []string{"run", "--resume", "--answers", file}); code != 1 || answers != 1 || jobs != 0 {
		t.Fatalf("unknown answer was retried: code=%d answers=%d jobs=%d %s", code, answers, jobs, errout)
	}
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 0 || gets != 2 || answers != 1 || jobs != 1 || !strings.Contains(out.String(), "job-1") {
		t.Fatalf("answer roundtrip failed code=%d gets=%d answers=%d jobs=%d %s", code, gets, answers, jobs, errout)
	}
}

func TestUnknownJobAcknowledgementCannotBeChangedByAnswersOrTerminalFlag(t *testing.T) {
	a, _, _, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"saved-key","plan_revision":7,"submission_started":true,"preview_terminal":true,"terminal_state":"expired"}`), 0600)
	var jobPosts int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		if r.URL.Path == "/api/v1/jobs" {
			jobPosts++
			fmt.Fprint(w, `{"id":"job-1"}`)
			return
		}
		t.Errorf("frozen job mutation %s", r.URL)
		http.NotFound(w, r)
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if a.Run(context.Background(), []string{"run", "--resume", "--answer", "batch=8"}) == 0 || jobPosts != 0 {
		t.Fatal("answers changed an unknown submitted job")
	}
	if a.Run(context.Background(), []string{"run", "--", "python", "train.py"}) == 0 || jobPosts != 0 {
		t.Fatal("terminal flag hid an unknown job acknowledgement")
	}
	if a.Run(context.Background(), []string{"run", "--resume"}) != 0 || jobPosts != 1 {
		t.Fatal("frozen job request could not be reconciled")
	}
}

func TestLogsPreservesTextAndReportsRetentionGaps(t *testing.T) {
	a, out, errout, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		if r.URL.Query().Get("cursor") == "" {
			fmt.Fprint(w, `{"entries":[{"cursor":4,"attempt_id":"attempt-1","sequence":3,"occurred_at":"2026-11-14T06:00:01Z","text":"canonical","gap":true}],"cursor":4,"truncated":false}`)
		} else {
			fmt.Fprint(w, `{"entries":[],"cursor":4,"truncated":false}`)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if code := a.Run(context.Background(), []string{"logs", "job-1"}); code != 0 || out.String() != "canonical\n" || !strings.Contains(errout.String(), "gap") {
		t.Fatalf("log DTO mismatch %d %s %s", code, out, errout)
	}
}

func TestRejectedAnswersCanBeCorrectedOnTheSamePreview(t *testing.T) {
	a, _, errout, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"saved-key"}`), 0600)
	var answers, jobs int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/job-previews/preview-1":
			if answers < 2 {
				fmt.Fprint(w, `{"id":"preview-1","state":"needs_input","plan_revision":3,"question":{"question_id":"choice","prompt":"Choose an option"}}`)
			} else {
				fmt.Fprint(w, `{"id":"preview-1","state":"ready","plan_revision":4}`)
			}
		case "/api/v1/job-previews/preview-1/answers":
			answers++
			if answers == 1 {
				w.WriteHeader(422)
				fmt.Fprint(w, `{"code":"INVALID_ANSWER","message":"expected a different value"}`)
			} else {
				fmt.Fprint(w, `{"id":"preview-1"}`)
			}
		case "/api/v1/jobs":
			jobs++
			fmt.Fprint(w, `{"id":"job-1"}`)
		default:
			t.Errorf("replacement preview created %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if a.Run(context.Background(), []string{"run", "--resume", "--answer", "choice=bad"}) != 1 {
		t.Fatal("invalid answer not rejected")
	}
	if code := a.Run(context.Background(), []string{"run", "--resume", "--answer", "choice=good"}); code != 0 || answers != 2 || jobs != 1 {
		t.Fatalf("rejected answers wedged correction: code=%d answers=%d jobs=%d %s", code, answers, jobs, errout)
	}
}

func TestAnswerLimitsRejectInvalidValuesBeforeNetwork(t *testing.T) {
	for _, body := range []string{
		`{"answers":[]}`,
		`{"answers":[{"question_id":"q","value":1},{"question_id":"q","value":2}]}`,
		`{"answers":[{"question_id":"bad/id","value":1}]}`,
		`{"answers":[{"question_id":"q"}]}`,
		`{"answers":[{"question_id":"q","value":1e1000}]}`,
		`{"answers":[{"question_id":"q","value":` + strings.Repeat("[", 20) + `0` + strings.Repeat("]", 20) + `}]}`,
		`{"question_id":"q","answer":"` + strings.Repeat("x", 2001) + `"}`,
	} {
		a, _, errout, _ := testApp(t)
		file := filepath.Join(t.TempDir(), "answers.json")
		os.WriteFile(file, []byte(body), 0600)
		if code := a.Run(context.Background(), []string{"run", "--resume", "--answers", file}); code != 2 {
			t.Fatalf("invalid answer was not a usage failure before auth: code=%d %s", code, errout)
		}
	}
}

func TestUnknownAnswerAcknowledgementReadsWithoutRepeatingMutation(t *testing.T) {
	a, _, errout, dir := testApp(t)
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return dir
		}
		if k == "CAMPUS_TOKEN" {
			return "member"
		}
		return ""
	}
	os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"job-key"}`), 0600)
	var answerPosts, gets, jobs int
	advanced := false
	var answerKey string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/job-previews/preview-1":
			gets++
			if !advanced {
				fmt.Fprint(w, `{"id":"preview-1","state":"needs_input","plan_revision":3,"question":{"question_id":"choice","prompt":"Choose an option"}}`)
			} else {
				fmt.Fprint(w, `{"id":"preview-1","state":"ready","plan_revision":4}`)
			}
		case "/api/v1/job-previews/preview-1/answers":
			answerPosts++
			if answerKey == "" {
				answerKey = r.Header.Get("Idempotency-Key")
			}
			if answerKey == "" || answerKey != r.Header.Get("Idempotency-Key") {
				t.Error("unknown answer ACK lost stable key")
			}
			var body map[string]any
			json.NewDecoder(r.Body).Decode(&body)
			if body["plan_revision"] != float64(3) {
				t.Errorf("answer replay replanned: %#v", body)
			}
			w.WriteHeader(503)
			fmt.Fprint(w, `{"code":"ACK_UNKNOWN","message":"unavailable"}`)
		case "/api/v1/jobs":
			jobs++
			fmt.Fprint(w, `{"id":"job-1"}`)
		default:
			t.Errorf("unexpected answer recovery URL %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if a.Run(context.Background(), []string{"run", "--resume", "--answer", "choice=explicit"}) != 1 || answerPosts != 1 {
		t.Fatalf("answer mutation was automatically repeated: posts=%d %s", answerPosts, errout)
	}
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 1 || answerPosts != 1 || gets != 2 || jobs != 0 {
		t.Fatalf("unresolved answer was repeated: code=%d posts=%d gets=%d jobs=%d %s", code, answerPosts, gets, jobs, errout)
	}
	advanced = true
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 0 || answerPosts != 1 || gets != 3 || jobs != 1 {
		t.Fatalf("new revision failed to resolve answer acknowledgement: code=%d posts=%d gets=%d jobs=%d %s", code, answerPosts, gets, jobs, errout)
	}
}
