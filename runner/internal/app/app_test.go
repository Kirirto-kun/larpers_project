package app

import (
	"archive/zip"
	"bytes"
	"campus/runner/internal/client"
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
	fmt.Fprint(w, `{"api_version":"1","min_runner_version":"0.1.0","max_runner_version":"0.1.0"}`)
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
			var body map[string]string
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if body["token"] != "invite secret" || body["installation_id"] == "" {
				t.Errorf("bad redeem body: %#v", body)
			}
			installation = body["installation_id"]
			if r.Header.Get("Authorization") != "" {
				t.Error("invitation was treated as member credential")
			}
			fmt.Fprint(w, `{"pool_id":"pool-1","node_id":"node-1","credential_id":"cred-1","credential":"device-secret"}`)
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
		fmt.Fprint(w, `{"pool_id":"pool-1","node_id":"node-1","credential_id":"cred-1","credential":"secret"}`)
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
	if err := os.WriteFile(p, []byte(`{"schema_version":"1","profiles":[{"id":"cpu","accelerator":"cpu","platform":"linux/amd64","image_digest":"sha256:`+strings.Repeat("a", 64)+`","guard_version":"1"},{"id":"gpu","accelerator":"cuda","platform":"linux/amd64","image_digest":"sha256:`+strings.Repeat("b", 64)+`","guard_version":"1"}]}`), 0600); err != nil {
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
				InstallationID, NodeID string
				Capabilities           client.Capabilities `json:"capabilities"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if body.Capabilities.OfferedBudget.VRAMBytes != 0 || len(body.Capabilities.OfferedBudget.GPUIDs) != 0 {
				t.Error("GPU offered without opt in")
			}
			registered = true
			fmt.Fprint(w, `{"node_session_id":"session-1"}`)
		case "/api/v1/node-sessions/session-1/capabilities":
			if r.Method != "PUT" || r.Header.Get("X-Node-Session-ID") != "session-1" {
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
		return client.Capabilities{OS: "windows", Arch: "amd64", CPULogical: 8, MemoryAvailableBytes: 8 << 30, DiskAvailableBytes: 8 << 30, Runtime: client.Runtime{Healthy: true}, GPUs: []client.GPU{{ID: "0", VRAMBytes: 8 << 30}}}, nil
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
				Digest string `json:"digest"`
				Size   int64  `json:"size_bytes"`
			}
			json.NewDecoder(r.Body).Decode(&body)
			id := fmt.Sprintf("upload-%d", len(uploads)+1)
			uploads[id] = &upload{digest: body.Digest, size: body.Size}
			fmt.Fprintf(w, `{"upload_id":%q}`, id)
		case strings.HasPrefix(route, "/uploads/"):
			parts := strings.Split(strings.TrimPrefix(route, "/uploads/"), "/")
			u := uploads[parts[0]]
			if u == nil {
				t.Errorf("unknown upload %s", route)
				http.NotFound(w, r)
				return
			}
			if len(parts) == 1 {
				fmt.Fprintf(w, `{"upload_id":%q,"part_size_bytes":16777216,"completed_parts":[]}`, parts[0])
				return
			}
			if parts[1] == "parts" {
				u.contents, _ = io.ReadAll(r.Body)
				w.WriteHeader(204)
				return
			}
			fmt.Fprintf(w, `{"artifact_id":%q,"digest":%q,"size_bytes":%d}`, "artifact-"+parts[0], u.digest, u.size)
		case route == "/job-previews" && r.Method == "POST":
			json.NewDecoder(r.Body).Decode(&preview)
			fmt.Fprint(w, `{"preview_id":"preview-1","state":"analyzing"}`)
		case route == "/job-previews/preview-1":
			fmt.Fprint(w, `{"preview_id":"preview-1","state":"ready","plan_revision":2,"plan":{"device":"cuda"}}`)
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
			fmt.Fprint(w, `{"job_id":"job-1","state":"queued"}`)
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
	request := preview["request"].(map[string]any)
	if request["requires_gpu"] != true || len(request["argv"].([]any)) != 3 {
		t.Fatalf("argv/GPU corrupted: %#v", request)
	}
	if preview["project"].(map[string]any)["manifest_digest"] == "" {
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
			fmt.Fprint(w, `{"preview_id":"preview-1","state":"needs_input","questions":[{"field":"dataset","message":"Choose the labeled dataset"}]}`)
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
					fmt.Fprintf(w, `{"job_id":"job-1","state":"succeeded","artifacts":[{"artifact_id":"file-1","digest":%q,"size_bytes":5,"path":"workspace/model.pt"}]}`, digest)
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
				json.NewEncoder(w).Encode(map[string]any{"job_id": "job-1", "state": "succeeded", "artifacts": files})
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
				fmt.Fprint(w, `{"records":[{"text":"first"}],"next_cursor":"cursor/2"}`)
			} else {
				fmt.Fprint(w, `{"records":[{"text":"second"}],"next_cursor":null}`)
			}
		case "/api/v1/jobs/job-1/cancel":
			if r.Method != "POST" {
				t.Error("cancel not POST")
			}
			cancelled = true
			fmt.Fprint(w, `{"job_id":"job-1","state":"cancelled"}`)
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
			fmt.Fprint(w, `{"preview_id":"preview-1","state":"analyzing"}`)
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
			fmt.Fprint(w, `{"job_id":"job-1","state":"queued"}`)
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
	for _, revision := range []string{`null`, `true`, `-1`, `{}`} {
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
					fmt.Fprintf(w, `{"preview_id":"preview-1","state":"ready","plan_revision":%s}`, revision)
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
					fmt.Fprintf(w, `{"preview_id":"preview-1","state":%q}`, terminal)
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
	os.WriteFile(file, []byte(`{"answers":[{"question_id":"batch","value":4},{"question_id":"note","value":"explicit answer"}]}`), 0600)
	var gets, answers, jobs int
	var key string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/job-previews/preview-1":
			gets++
			if gets == 1 {
				fmt.Fprint(w, `{"preview_id":"preview-1","state":"needs_input","plan_revision":3,"questions":[{"question_id":"batch"},{"question_id":"note"}]}`)
			} else {
				fmt.Fprint(w, `{"preview_id":"preview-1","state":"ready","plan_revision":4}`)
			}
		case "/api/v1/job-previews/preview-1/answers":
			answers++
			var body struct {
				Revision int `json:"plan_revision"`
				Answers  []struct {
					ID    string `json:"question_id"`
					Value any    `json:"value"`
				} `json:"answers"`
			}
			json.NewDecoder(r.Body).Decode(&body)
			if body.Revision != 3 || len(body.Answers) != 2 || body.Answers[0].ID != "batch" || body.Answers[0].Value != float64(4) || body.Answers[1].Value != "explicit answer" {
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
			fmt.Fprint(w, `{"preview_id":"preview-1"}`)
		case "/api/v1/jobs":
			jobs++
			var body map[string]any
			json.NewDecoder(r.Body).Decode(&body)
			if body["preview_id"] != "preview-1" || body["plan_revision"] != float64(4) || r.Header.Get("Idempotency-Key") != "saved-key" {
				t.Errorf("wrong run after answers: %#v", body)
			}
			fmt.Fprint(w, `{"job_id":"job-1","state":"queued"}`)
		default:
			t.Errorf("answer flow replaced preview or submitted unexpected endpoint %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if code := a.Run(context.Background(), []string{"run", "--resume", "--answers", file}); code != 0 || gets != 2 || answers != 2 || jobs != 1 || !strings.Contains(out.String(), "job-1") {
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
			fmt.Fprint(w, `{"job_id":"job-1"}`)
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

func TestLogsTextCanonicalAndMessageCompatibilityAlias(t *testing.T) {
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
		fmt.Fprint(w, `{"records":[{"text":"canonical","message":"ignored"},{"message":"legacy"},"string"],"next_cursor":null}`)
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if code := a.Run(context.Background(), []string{"logs", "job-1"}); code != 0 || out.String() != "canonical\nlegacy\nstring\n" {
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
				fmt.Fprint(w, `{"preview_id":"preview-1","state":"needs_input","plan_revision":3}`)
			} else {
				fmt.Fprint(w, `{"preview_id":"preview-1","state":"ready","plan_revision":4}`)
			}
		case "/api/v1/job-previews/preview-1/answers":
			answers++
			if answers == 1 {
				w.WriteHeader(422)
				fmt.Fprint(w, `{"code":"INVALID_ANSWER","message":"expected a different value"}`)
			} else {
				fmt.Fprint(w, `{"preview_id":"preview-1"}`)
			}
		case "/api/v1/jobs":
			jobs++
			fmt.Fprint(w, `{"job_id":"job-1"}`)
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
		`{"answers":[{"question_id":"q","value":"` + strings.Repeat("x", 65536) + `"}]}`,
	} {
		a, _, errout, _ := testApp(t)
		file := filepath.Join(t.TempDir(), "answers.json")
		os.WriteFile(file, []byte(body), 0600)
		if code := a.Run(context.Background(), []string{"run", "--resume", "--answers", file}); code != 2 {
			t.Fatalf("invalid answer was not a usage failure before auth: code=%d %s", code, errout)
		}
	}
}

func TestUnknownAnswerAcknowledgementReplaysSavedBatchBeforePolling(t *testing.T) {
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
	var answerKey string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/job-previews/preview-1":
			gets++
			if answerPosts < 4 {
				fmt.Fprint(w, `{"preview_id":"preview-1","state":"needs_input","plan_revision":3}`)
			} else {
				fmt.Fprint(w, `{"preview_id":"preview-1","state":"ready","plan_revision":4}`)
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
			if answerPosts < 4 {
				w.WriteHeader(503)
				fmt.Fprint(w, `{"code":"ACK_UNKNOWN","message":"unavailable"}`)
			} else {
				fmt.Fprint(w, `{}`)
			}
		case "/api/v1/jobs":
			jobs++
			fmt.Fprint(w, `{"job_id":"job-1"}`)
		default:
			t.Errorf("unexpected answer recovery URL %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	if a.Run(context.Background(), []string{"run", "--resume", "--answer", "choice=explicit"}) != 1 || answerPosts != 3 {
		t.Fatalf("expected unknown answer acknowledgement: posts=%d %s", answerPosts, errout)
	}
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 0 || answerPosts != 4 || gets != 2 || jobs != 1 {
		t.Fatalf("answer ACK recovery failed: code=%d posts=%d gets=%d jobs=%d %s", code, answerPosts, gets, jobs, errout)
	}
}
