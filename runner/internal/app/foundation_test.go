package app

import (
	"campus/runner/internal/generated"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLostDeviceRedemptionAcknowledgementNeverAutomaticallyRepeats(t *testing.T) {
	a, _, errout, dir := testApp(t)
	var posts int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		if r.URL.Path == "/api/v1/invitations/redeem" {
			posts++
			conn, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			conn.Close()
			return
		}
		t.Errorf("unexpected request %s", r.URL)
		http.NotFound(w, r)
	}))
	defer s.Close()
	if code := a.Run(context.Background(), []string{"join", strings.Repeat("t", 32), "--endpoint", s.URL, "--no-serve"}); code != 1 || posts != 1 {
		t.Fatalf("redemption repeated: code=%d posts=%d %s", code, posts, errout)
	}
	identity, err := loadIdentity(dir)
	if err != nil || identity.InstallationID == "" || identity.Credential != "" {
		t.Fatalf("installation identity lost after ambiguous redemption: %#v %v", identity, err)
	}
}

func TestLogsRejectMalformedFoundationPageInsteadOfDroppingOutput(t *testing.T) {
	for _, body := range []string{`{"records":[{"text":"lost"}],"next_cursor":null}`, `{"entries":null,"cursor":0,"truncated":false}`, `{"entries":[],"cursor":0}`, `{"entries":[{"cursor":1}],"cursor":1,"truncated":false}`} {
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
			fmt.Fprint(w, body)
		}))
		saveIdentity(t, dir, s.URL)
		if code := a.Run(context.Background(), []string{"logs", "job-1"}); code != 1 || out.Len() != 0 {
			t.Errorf("malformed page treated as complete logs: code=%d body=%s out=%q %s", code, body, out.String(), errout)
		}
		s.Close()
	}
}

func TestCommandPlanDriftNeverSubmits(t *testing.T) {
	for _, mutation := range []string{"exact", "argv", "gpu", "binding", "output", "network", "extra_stage"} {
		t.Run(mutation, func(t *testing.T) {
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
			state := `{"preview_id":"preview-1","idempotency_key":"key-1","command_intent":{"argv":["python","train.py"],"requires_gpu":true,"working_dir":"/workspace","outputs":["model.pt"],"bindings":[{"artifact_id":"00000000-0000-4000-8000-000000000001","mount_path":"/workspace","archive":true}],"network_policy":"none"}}`
			if err := os.WriteFile(filepath.Join(dir, "submission.json"), []byte(state), 0600); err != nil {
				t.Fatal(err)
			}
			none, archive, required := "none", true, true
			plan := generated.JobPlan{
				PlanRevision: 1, InputManifestDigest: strings.Repeat("a", 64), ExecutionKind: "single", JobDeadlineAt: "2026-11-14T23:00:00Z", RetryPolicy: generated.RetryPolicy{},
				Stages: []generated.Stage{{
					StageID: "run", Adapter: generated.AdapterRef{ID: "generic.project", Version: "1"}, Argv: []string{"python", "train.py"}, WorkingDir: "/workspace", NetworkPolicy: &none,
					Bindings: []generated.InputBinding{{ArtifactID: "00000000-0000-4000-8000-000000000001", MountPath: "/workspace", Archive: &archive}},
					Variants: []generated.Variant{{VariantID: "cuda", ImageDigest: "campus/cuda@sha256:" + strings.Repeat("a", 64), Platform: "linux/amd64", Accelerator: "cuda", ResourceRequirements: generated.Resources{CPUMillis: 1000, MemoryBytes: 536870912, DiskBytes: 1073741824}}},
					Outputs:  []generated.Output{{Path: "model.pt", Required: &required, MaxBytes: 1024}}, TaskDeadlineMs: 30000, ValidationPolicy: "unverified", ResumePolicy: "restart",
				}},
			}
			stage := &plan.Stages[0]
			switch mutation {
			case "argv":
				stage.Argv = []string{"sh", "-c", "python train.py"}
			case "gpu":
				stage.Variants[0].Accelerator = "cpu"
			case "binding":
				stage.Bindings = []generated.InputBinding{}
			case "output":
				stage.Outputs[0].Path = "another.pt"
			case "network":
				internet := "internet"
				stage.NetworkPolicy = &internet
			}
			if mutation == "extra_stage" {
				plan.Stages = append(plan.Stages, *stage)
			}
			var jobs int
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/api/v1/protocol":
					protocol(w)
				case "/api/v1/job-previews/preview-1":
					json.NewEncoder(w).Encode(map[string]any{"id": "preview-1", "state": "ready", "plan_revision": 1, "plan": plan})
				case "/api/v1/jobs":
					jobs++
					fmt.Fprint(w, `{"id":"job-1"}`)
				default:
					t.Errorf("unexpected request %s", r.URL)
					http.NotFound(w, r)
				}
			}))
			defer s.Close()
			saveIdentity(t, dir, s.URL)
			code := a.Run(context.Background(), []string{"run", "--resume"})
			if mutation == "exact" {
				if code != 0 || jobs != 1 {
					t.Fatalf("original command rejected: code=%d jobs=%d %s", code, jobs, errout)
				}
			} else if code == 0 || jobs != 0 {
				t.Fatalf("changed command submitted: code=%d jobs=%d %s", code, jobs, errout)
			}
		})
	}
}

func TestUnknownPreviewCreationNeverRepeatsTheMutation(t *testing.T) {
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
	if err := os.WriteFile(filepath.Join(dir, "submission.json"), []byte(`{"idempotency_key":"job-key","preview_key":"preview-key","preview_request":{"prompt":"Run the explicit command"}}`), 0600); err != nil {
		t.Fatal(err)
	}
	var posts int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		if r.URL.Path == "/api/v1/job-previews" && r.Method == "POST" {
			posts++
			conn, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			conn.Close()
			return
		}
		t.Errorf("unexpected request %s", r.URL)
		http.NotFound(w, r)
	}))
	defer s.Close()
	saveIdentity(t, dir, s.URL)
	for i := 0; i < 2; i++ {
		if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 1 || posts != 1 {
			t.Fatalf("unknown creation repeated: run=%d code=%d posts=%d %s", i, code, posts, errout)
		}
	}
	var state submission
	if err := readJSON(filepath.Join(dir, "submission.json"), 8<<20, &state); err != nil || state.SubmissionStarted || state.PreviewID != "" {
		t.Fatalf("creation uncertainty was not retained: %#v %v", state, err)
	}
	if code := a.Run(context.Background(), []string{"run", "--discard"}); code != 0 {
		t.Fatalf("never-submitted unknown preview could not be discarded: %s", errout)
	}
}
