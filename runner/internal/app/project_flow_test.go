package app

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
)

type projectUpload struct {
	id, name, kind, digest, owner string
	size                          int64
	body                          []byte
	ready                         bool
}
type projectAPI struct {
	t                   *testing.T
	mu                  sync.Mutex
	uploads             []*projectUpload
	previews            []map[string]any
	jobs                []map[string]any
	keys                []string
	lookupOwners        []string
	plan                map[string]any
	mutate              func(map[string]any)
	failJob             bool
	jobStates           []string
	jobReads            int
	question            bool
	answerLost          bool
	answerUncommitted   bool
	answerPosts         []map[string]any
	previewTerminal     string
	previewErrorCode    string
	previewErrorMessage string
}

func newProjectAPI(t *testing.T) (*projectAPI, *httptest.Server) {
	t.Helper()
	api := &projectAPI{t: t}
	s := httptest.NewServer(api)
	t.Cleanup(s.Close)
	return api, s
}

func fixtureProjectStage() map[string]any {
	return map[string]any{"id": "map", "kind": "map", "inputs": []string{"dataset/*.json"}, "depends_on": []string{}, "command": []string{"python", "worker.py", "{input}", "{accelerator}"}, "outputs": []any{map[string]any{"path": "result.json", "max_bytes": 1024, "required": true}}, "accelerator": "cpu", "resources": map[string]any{"cpu_millis": 1000, "memory_bytes": 536870912, "disk_bytes": 268435456, "vram_bytes": 1073741824}, "timeout_seconds": 300}
}

func (api *projectAPI) readyPlan(body map[string]any) map[string]any {
	id, _ := body["project_artifact_id"].(string)
	digest := ""
	for _, u := range api.uploads {
		if u.id == id {
			digest = u.digest
		}
	}
	bindings := []any{map[string]any{"artifact_id": id, "mount_path": "/workspace", "archive": true}}
	inputs, _ := body["inputs"].([]any)
	bindings = append(bindings, inputs...)
	return map[string]any{
		"schema_version": "1", "plan_revision": 1, "project_digest": digest,
		"input_manifest_digest": strings.Repeat("a", 64), "execution_kind": "map",
		"job_deadline_at": "2026-11-17T01:00:00Z", "retry_policy": map[string]any{},
		"stages": []any{map[string]any{
			"stage_id": "map", "adapter": map[string]any{"id": "campus.project-map", "version": "1"},
			"depends_on": []string{}, "argv": []string{"python", "worker.py", "{input}", "{accelerator}"},
			"working_dir": "/workspace", "bindings": bindings,
			"outputs":        []any{map[string]any{"path": "result.json", "max_bytes": 1024, "required": true}},
			"network_policy": "none", "task_deadline_ms": 300000, "resume_policy": "restart", "validation_policy": "structural",
			"parameters": map[string]any{"project_stage": fixtureProjectStage()},
			"variants": []any{map[string]any{
				"variant_id": "cpu", "accelerator": "cpu", "image_digest": "campus/cpu@sha256:" + strings.Repeat("a", 64), "platform": "linux/amd64",
				"resource_requirements": map[string]any{"cpu_millis": 1000, "memory_bytes": 536870912, "disk_bytes": 268435456, "gpu_slots": 0, "vram_bytes": 0},
			}},
		}},
	}
}

func (api *projectAPI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	api.mu.Lock()
	defer api.mu.Unlock()
	route := strings.TrimPrefix(r.URL.Path, "/api/v1")
	switch {
	case route == "/protocol":
		protocol(w)
	case route == "/artifacts/lookup":
		api.lookupOwners = append(api.lookupOwners, r.Header.Get("Authorization"))
		for _, u := range api.uploads {
			if u.ready && u.owner == r.Header.Get("Authorization") && u.kind == r.URL.Query().Get("kind") && u.digest == r.URL.Query().Get("sha256") {
				json.NewEncoder(w).Encode(map[string]any{"id": u.id, "name": u.name, "sha256": u.digest, "size_bytes": u.size, "media_type": "application/octet-stream"})
				return
			}
		}
		http.NotFound(w, r)
	case route == "/uploads" && r.Method == "POST":
		var body struct {
			Name, Kind, SHA256 string
			Size               int64 `json:"size_bytes"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			api.t.Error(err)
		}
		u := &projectUpload{id: fmt.Sprintf("00000000-0000-4000-8000-%012d", len(api.uploads)+1), name: body.Name, kind: body.Kind, digest: body.SHA256, size: body.Size, owner: r.Header.Get("Authorization")}
		api.uploads = append(api.uploads, u)
		parts := int64(1)
		if u.size == 0 {
			parts = 0
		}
		json.NewEncoder(w).Encode(map[string]any{"id": u.id, "state": "pending", "part_count": parts, "parts": []any{}})
	case strings.HasPrefix(route, "/uploads/"):
		parts := strings.Split(strings.TrimPrefix(route, "/uploads/"), "/")
		var u *projectUpload
		for _, candidate := range api.uploads {
			if candidate.id == parts[0] {
				u = candidate
				break
			}
		}
		if u == nil {
			http.NotFound(w, r)
			return
		}
		if len(parts) == 1 {
			json.NewEncoder(w).Encode(map[string]any{"id": u.id, "state": "pending", "part_count": 1, "parts": []any{}})
			return
		}
		if parts[1] == "parts" {
			u.body, _ = io.ReadAll(r.Body)
			sum := sha256.Sum256(u.body)
			if hex.EncodeToString(sum[:]) != u.digest || int64(len(u.body)) != u.size || r.Header.Get("X-Content-SHA256") != u.digest {
				api.t.Error("uploaded bytes differ from checksummed inventory")
			}
			json.NewEncoder(w).Encode(map[string]any{"number": 0, "sha256": u.digest, "size_bytes": u.size})
			return
		}
		if parts[1] == "finalize" {
			u.ready = true
			json.NewEncoder(w).Encode(map[string]any{"id": u.id, "name": u.name, "sha256": u.digest, "size_bytes": u.size, "media_type": "application/octet-stream"})
			return
		}
		http.NotFound(w, r)
	case route == "/job-previews" && r.Method == "POST":
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			api.t.Error(err)
		}
		api.previews = append(api.previews, body)
		api.plan = api.readyPlan(body)
		if api.mutate != nil {
			api.mutate(api.plan)
		}
		if api.previewTerminal != "" {
			json.NewEncoder(w).Encode(map[string]any{"id": "preview-1", "state": api.previewTerminal, "plan_revision": 1, "error_code": api.previewErrorCode, "error_message": api.previewErrorMessage})
			return
		}
		if api.question {
			w.WriteHeader(202)
			json.NewEncoder(w).Encode(map[string]any{"id": "preview-1", "state": "needs_input", "plan_revision": 1, "question": map[string]any{"question_id": "intent", "text": "Confirm intent"}})
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"id": "preview-1", "state": "ready", "plan_revision": 1, "plan": api.plan})
	case route == "/job-previews/preview-1/answers":
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		api.answerPosts = append(api.answerPosts, body)
		if body["question_id"] != "intent" || body["plan_revision"] != float64(1) || body["answer"] != "Proceed" {
			api.t.Errorf("bad explicit answer: %#v", body)
		}
		if !api.answerUncommitted {
			api.question = false
			api.plan["plan_revision"] = 2
		}
		if api.answerLost {
			w.WriteHeader(503)
			fmt.Fprint(w, `{"code":"UNKNOWN_ACK"}`)
			return
		}
		w.WriteHeader(202)
		json.NewEncoder(w).Encode(map[string]any{"id": "preview-1", "state": "planning", "plan_revision": 2})
	case route == "/job-previews/preview-1":
		if api.question {
			json.NewEncoder(w).Encode(map[string]any{"id": "preview-1", "state": "needs_input", "plan_revision": 1, "question": map[string]any{"question_id": "intent", "text": "Confirm intent"}})
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"id": "preview-1", "state": "ready", "plan_revision": 2, "plan": api.plan})
	case route == "/jobs" && r.Method == "POST":
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		api.jobs = append(api.jobs, body)
		api.keys = append(api.keys, r.Header.Get("Idempotency-Key"))
		if api.failJob {
			w.WriteHeader(503)
			fmt.Fprint(w, `{"code":"UNAVAILABLE"}`)
			return
		}
		fmt.Fprint(w, `{"id":"job-1"}`)
	case route == "/jobs/job-1":
		state := "succeeded"
		if len(api.jobStates) > 0 {
			state = api.jobStates[min(api.jobReads, len(api.jobStates)-1)]
			api.jobReads++
		}
		accepted := 2
		if state == "waiting_for_capacity" {
			accepted = 0
		} else if state != "succeeded" {
			accepted = 1
		}
		sum := sha256.Sum256([]byte("result"))
		json.NewEncoder(w).Encode(map[string]any{"id": "job-1", "state": state, "accepted_units": accepted, "total_units": 2, "tasks": []any{map[string]any{"id": "task-1", "stage_id": "map", "state": "succeeded", "node_id": "node-1"}}, "artifacts": []any{map[string]any{"id": "result-1", "name": "stages/map/task-1/result.json", "sha256": hex.EncodeToString(sum[:]), "size_bytes": 6, "media_type": "application/json"}}})
	case route == "/artifacts/result-1/content":
		fmt.Fprint(w, "result")
	default:
		api.t.Errorf("unexpected project request %s %s", r.Method, r.URL)
		http.NotFound(w, r)
	}
}

func projectApp(t *testing.T, endpoint string) (*App, *bytes.Buffer, *bytes.Buffer, string, string) {
	t.Helper()
	a, out, errout, stateDir := testApp(t)
	root := writeProjectFixture(t)
	a.Cwd = func() (string, error) { return root, nil }
	a.Stdin = strings.NewReader("worker.py\nyes\n")
	a.Getenv = func(k string) string {
		switch k {
		case "CAMPUS_STATE_DIR":
			return stateDir
		case "CAMPUS_TOKEN":
			return "member-a"
		case "CAMPUS_ENDPOINT":
			return endpoint
		}
		return ""
	}
	return a, out, errout, stateDir, root
}

func TestProjectRunUploadsSeparateDataReviewsCodeAndReusesOnlyOwnedContent(t *testing.T) {
	api, s := newProjectAPI(t)
	a, out, errout, _, root := projectApp(t, s.URL)
	os.Mkdir(filepath.Join(root, "results"), 0700)
	os.WriteFile(filepath.Join(root, "results", "previous.json"), []byte("previous"), 0600)
	os.WriteFile(filepath.Join(root, ".env"), []byte("secret"), 0600)
	if code := a.Run(context.Background(), []string{"run"}); code != 0 {
		t.Fatalf("first run=%d: %s", code, errout)
	}
	if len(api.uploads) != 3 || len(api.previews) != 1 || len(api.jobs) != 1 {
		t.Fatalf("wrong upload/run counts %d/%d/%d", len(api.uploads), len(api.previews), len(api.jobs))
	}
	request := api.previews[0]
	if request["share_with_llm"] != true || !reflect.DeepEqual(request["selected_files"], []any{"worker.py"}) || request["project_config"] == nil {
		t.Fatalf("explicit sharing/config missing: %#v", request)
	}
	inputs := request["inputs"].([]any)
	if len(inputs) != 2 || inputs[0].(map[string]any)["mount_path"] != "/inputs/dataset/a.json" || inputs[0].(map[string]any)["archive"] != false {
		t.Fatalf("data bindings %#v", inputs)
	}
	for _, u := range api.uploads {
		if u.kind == "project" {
			z, err := zip.NewReader(bytes.NewReader(u.body), int64(len(u.body)))
			if err != nil {
				t.Fatal(err)
			}
			var names []string
			for _, f := range z.File {
				names = append(names, f.Name)
			}
			if !reflect.DeepEqual(names, []string{"campus.json", "worker.py"}) {
				t.Fatalf("data/secrets/results leaked into project ZIP: %q", names)
			}
		}
	}
	if !strings.Contains(out.String(), "dataset/a.json") || !strings.Contains(out.String(), "cpu_millis") || !strings.Contains(out.String(), "Plan:") {
		t.Fatalf("inventory/defaults/plan not reviewable: %s", out)
	}
	a.Stdin = strings.NewReader("")
	if code := a.Run(context.Background(), []string{"run", "--yes"}); code != 0 {
		t.Fatalf("reviewed CI run=%d: %s", code, errout)
	}
	if len(api.uploads) != 3 || len(api.previews) != 2 || len(api.jobs) != 2 || api.keys[0] == api.keys[1] {
		t.Fatalf("cache missed or fresh run reused submission key: uploads=%d previews=%d keys=%q", len(api.uploads), len(api.previews), api.keys)
	}
}

func TestProjectRunRejectsDataSharingAndChangedUnreviewedCode(t *testing.T) {
	a, _, errout, _, root := projectApp(t, "http://127.0.0.1:1")
	a.Stdin = strings.NewReader("dataset/a.json\nyes\n")
	if code := a.Run(context.Background(), []string{"run"}); code != 2 || !strings.Contains(errout.String(), "selected") {
		t.Fatalf("data-sharing selection reached network: %d %s", code, errout)
	}
	api, s := newProjectAPI(t)
	a, _, errout, _, root = projectApp(t, s.URL)
	if a.Run(context.Background(), []string{"run"}) != 0 {
		t.Fatal(errout)
	}
	os.WriteFile(filepath.Join(root, "worker.py"), []byte("changed code"), 0600)
	if code := a.Run(context.Background(), []string{"run", "--yes"}); code != 2 || len(api.previews) != 1 {
		t.Fatalf("changed code retained CI review: %d %s", code, errout)
	}
}

func TestProjectRunPlanDriftNeverSubmits(t *testing.T) {
	for _, kind := range []string{"argv", "adapter", "input", "outputs", "dependency", "accelerator", "resources", "parameters", "timeout", "digest"} {
		t.Run(kind, func(t *testing.T) {
			api, s := newProjectAPI(t)
			a, _, errout, _, _ := projectApp(t, s.URL)
			api.mutate = func(plan map[string]any) {
				stage := plan["stages"].([]any)[0].(map[string]any)
				switch kind {
				case "argv":
					stage["argv"] = []string{"sh", "-c", "different"}
				case "adapter":
					stage["adapter"] = map[string]any{"id": "generic.project", "version": "1"}
				case "input":
					stage["bindings"] = stage["bindings"].([]any)[:1]
				case "outputs":
					stage["outputs"] = []any{map[string]any{"path": "other.json", "max_bytes": 1024, "required": true}}
				case "dependency":
					stage["depends_on"] = []string{"injected"}
				case "accelerator":
					stage["variants"].([]any)[0].(map[string]any)["accelerator"] = "cuda"
				case "resources":
					stage["variants"].([]any)[0].(map[string]any)["resource_requirements"].(map[string]any)["cpu_millis"] = 4000
				case "parameters":
					stage["parameters"] = map[string]any{"project_stage": map[string]any{"id": "different"}}
				case "timeout":
					stage["task_deadline_ms"] = 600000
				case "digest":
					plan["project_digest"] = strings.Repeat("f", 64)
				}
			}
			if code := a.Run(context.Background(), []string{"run"}); code != 1 || len(api.jobs) != 0 {
				t.Fatalf("%s plan drift submitted: %d %s", kind, code, errout)
			}
		})
	}
}

func TestProjectResumeFreezesSubmissionAndWaitDownloadsVerifiedNamespacedResults(t *testing.T) {
	api, s := newProjectAPI(t)
	a, _, errout, _, root := projectApp(t, s.URL)
	api.failJob = true
	if code := a.Run(context.Background(), []string{"run"}); code != 1 || len(api.jobs) != 1 {
		t.Fatalf("unknown acknowledgement was not frozen: %d %s", code, errout)
	}
	api.failJob = false
	a.Stdin = strings.NewReader("")
	to := filepath.Join(root, "results")
	if code := a.Run(context.Background(), []string{"run", "--resume", "--wait", "--to", to}); code != 0 {
		t.Fatalf("resume=%d: %s", code, errout)
	}
	if len(api.previews) != 1 || len(api.uploads) != 3 || len(api.jobs) != 2 || api.keys[0] != api.keys[1] || !reflect.DeepEqual(api.jobs[0], api.jobs[1]) {
		t.Fatalf("resume replanned or changed frozen submission: %#v %q", api.jobs, api.keys)
	}
	value, err := os.ReadFile(filepath.Join(to, "stages", "map", "task-1", "result.json"))
	if err != nil || string(value) != "result" {
		t.Fatalf("result missing or changed: %q %v", value, err)
	}
}

func TestProjectResumeRefusesChangedAccount(t *testing.T) {
	api, s := newProjectAPI(t)
	a, _, errout, stateDir, _ := projectApp(t, s.URL)
	api.failJob = true
	a.Run(context.Background(), []string{"run"})
	a.Getenv = func(k string) string {
		switch k {
		case "CAMPUS_STATE_DIR":
			return stateDir
		case "CAMPUS_TOKEN":
			return "member-b"
		case "CAMPUS_ENDPOINT":
			return s.URL
		}
		return ""
	}
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 1 || len(api.jobs) != 1 {
		t.Fatalf("frozen submission crossed accounts: %d %s", code, errout)
	}
}

func TestProjectResumeUsesFrozenRequestEvenWhenLocalConfigWasRemoved(t *testing.T) {
	api, s := newProjectAPI(t)
	a, _, errout, _, root := projectApp(t, s.URL)
	api.failJob = true
	if code := a.Run(context.Background(), []string{"run"}); code != 1 {
		t.Fatal(errout)
	}
	os.Remove(filepath.Join(root, "campus.json"))
	api.failJob = false
	a.Stdin = strings.NewReader("")
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 0 || len(api.jobs) != 2 || api.keys[0] != api.keys[1] {
		t.Fatalf("resume did not preserve project state after config removal: %d %s", code, errout)
	}
}

func TestProjectNoSharingReviewNeverSendsSourceSelections(t *testing.T) {
	api, s := newProjectAPI(t)
	a, _, errout, _, _ := projectApp(t, s.URL)
	a.Stdin = strings.NewReader("none\nyes\n")
	if code := a.Run(context.Background(), []string{"run"}); code != 0 {
		t.Fatal(errout)
	}
	if api.previews[0]["share_with_llm"] != false || !reflect.DeepEqual(api.previews[0]["selected_files"], []any{}) {
		t.Fatalf("source shared without consent: %#v", api.previews[0])
	}
}

func TestProjectChangedDataRequiresNewReviewAndOnlyChangedFileUploads(t *testing.T) {
	api, s := newProjectAPI(t)
	a, _, errout, _, root := projectApp(t, s.URL)
	if a.Run(context.Background(), []string{"run"}) != 0 {
		t.Fatal(errout)
	}
	os.WriteFile(filepath.Join(root, "dataset", "a.json"), []byte("[9,10]"), 0600)
	if code := a.Run(context.Background(), []string{"run", "--yes"}); code != 2 {
		t.Fatalf("mutated data reused review: %d %s", code, errout)
	}
	a.Stdin = strings.NewReader("worker.py\nyes\n")
	if code := a.Run(context.Background(), []string{"run"}); code != 0 || len(api.uploads) != 4 {
		t.Fatalf("changed shard upload/cache wrong: %d uploads=%d %s", code, len(api.uploads), errout)
	}
}

func TestProjectWaitFollowsRealWaitingForCapacityStateToSuccess(t *testing.T) {
	api, s := newProjectAPI(t)
	a, out, errout, _, _ := projectApp(t, s.URL)
	api.jobStates = []string{"waiting_for_capacity", "running", "succeeded"}
	if code := a.Run(context.Background(), []string{"run", "--wait"}); code != 0 || api.jobReads != 3 {
		t.Fatalf("waiting job failed early: %d %s", code, errout)
	}
	for _, state := range []string{"waiting_for_capacity", "running", "succeeded"} {
		if !strings.Contains(out.String(), state) {
			t.Errorf("progress omitted %s", state)
		}
	}
}

func TestProjectWaitPartialDownloadsAcceptedResultsAndReportsIncompleteWork(t *testing.T) {
	api, s := newProjectAPI(t)
	a, _, errout, _, root := projectApp(t, s.URL)
	api.jobStates = []string{"partial"}
	to := filepath.Join(root, "results")
	if code := a.Run(context.Background(), []string{"run", "--wait", "--to", to}); code != 1 {
		t.Fatalf("partial job became successful: %d %s", code, errout)
	}
	if _, err := os.Stat(filepath.Join(to, "stages", "map", "task-1", "result.json")); err != nil {
		t.Fatalf("accepted partial outputs not downloaded: %v %s", err, errout)
	}
	for _, part := range []string{"partial", "accepted 1/2", "campus logs"} {
		if !strings.Contains(errout.String(), part) {
			t.Errorf("missing useful partial status %q: %s", part, errout)
		}
	}
}

func TestProjectQuestionResumesWithExplicitAnswerAndReviewsNewRevision(t *testing.T) {
	api, s := newProjectAPI(t)
	a, _, errout, _, _ := projectApp(t, s.URL)
	api.question = true
	a.Stdin = strings.NewReader("worker.py\n")
	if code := a.Run(context.Background(), []string{"run"}); code != 3 {
		t.Fatalf("needs_input=%d %s", code, errout)
	}
	a.Stdin = strings.NewReader("yes\n")
	if code := a.Run(context.Background(), []string{"run", "--resume", "--answer", "intent=Proceed"}); code != 0 {
		t.Fatalf("answer resume=%d %s", code, errout)
	}
	if len(api.answerPosts) != 1 || len(api.jobs) != 1 || api.jobs[0]["plan_revision"] != float64(2) || len(api.previews) != 1 {
		t.Fatalf("answer did not advance exact saved preview: answers=%#v jobs=%#v", api.answerPosts, api.jobs)
	}
}

func TestProjectUnknownAnswerAcknowledgementIsReconciledWithoutReplay(t *testing.T) {
	for _, uncommitted := range []bool{false, true} {
		t.Run(fmt.Sprint(uncommitted), func(t *testing.T) {
			api, s := newProjectAPI(t)
			a, _, errout, _, _ := projectApp(t, s.URL)
			api.question = true
			api.answerLost = true
			api.answerUncommitted = uncommitted
			a.Stdin = strings.NewReader("worker.py\n")
			if a.Run(context.Background(), []string{"run"}) != 3 {
				t.Fatal(errout)
			}
			if code := a.Run(context.Background(), []string{"run", "--resume", "--answer", "intent=Proceed"}); code != 1 || len(api.answerPosts) != 1 || len(api.jobs) != 0 {
				t.Fatalf("unknown answer mutated job: %d %s", code, errout)
			}
			a.Stdin = strings.NewReader("yes\n")
			code := a.Run(context.Background(), []string{"run", "--resume"})
			if len(api.answerPosts) != 1 {
				t.Fatal("unknown answer replayed")
			}
			if uncommitted {
				if code != 1 || len(api.jobs) != 0 || !strings.Contains(errout.String(), "remains unknown") {
					t.Fatalf("uncommitted answer guessed: %d %s", code, errout)
				}
			} else if code != 0 || len(api.jobs) != 1 {
				t.Fatalf("committed answer not reconciled: %d %s", code, errout)
			}
		})
	}
}

func TestExplicitCommandResumeRemainsAvailableInsideConfiguredProject(t *testing.T) {
	api, s := newProjectAPI(t)
	a, _, errout, stateDir, _ := projectApp(t, s.URL)
	os.WriteFile(filepath.Join(stateDir, "submission.json"), []byte(`{"preview_id":"preview-1","idempotency_key":"legacy-key","submission_started":true,"plan_revision":1}`), 0600)
	if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 0 || len(api.jobs) != 1 || api.keys[0] != "legacy-key" {
		t.Fatalf("legacy submission recovery was shadowed by campus.json: %d %s", code, errout)
	}
}

func TestProjectTerminalPreviewShowsBoundedSafeCoordinatorDiagnostic(t *testing.T) {
	for _, state := range []string{"failed", "cancelled"} {
		t.Run(state, func(t *testing.T) {
			api, s := newProjectAPI(t)
			a, _, errout, _, _ := projectApp(t, s.URL)
			api.previewTerminal = state
			api.previewErrorCode = "PLANNER_SCHEMA_INVALID"
			api.previewErrorMessage = "Project review schema rejected; credential member-a\n" + strings.Repeat("detail ", 1000)
			if code := a.Run(context.Background(), []string{"run"}); code != 1 {
				t.Fatalf("terminal preview=%d %s", code, errout)
			}
			for _, want := range []string{"preview-1", state, "PLANNER_SCHEMA_INVALID", "Project review schema rejected"} {
				if !strings.Contains(errout.String(), want) {
					t.Errorf("terminal diagnostic omitted %q: %s", want, errout)
				}
			}
			if strings.Contains(errout.String(), "member-a") || len(errout.String()) > 1800 {
				t.Fatalf("terminal diagnostic leaked credentials or was unbounded: %d bytes", errout.Len())
			}
			if len(api.jobs) != 0 {
				t.Fatal("terminal preview submitted a job")
			}
			errout.Reset()
			if code := a.Run(context.Background(), []string{"run", "--resume"}); code != 1 || !strings.Contains(errout.String(), "PLANNER_SCHEMA_INVALID") || strings.Contains(errout.String(), "member-a") {
				t.Fatalf("resume lost safe terminal reason: %d %s", code, errout)
			}
		})
	}
}
