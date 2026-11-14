package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/packaging"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestManagedOutputsSelectDeclaredPathsAndRejectAmbiguity(t *testing.T) {
	files := []packaging.File{{Path: "workspace/train.log"}, {Path: "workspace/models/result.json"}, {Path: "outputs/summary.json"}}
	selected, e := selectManagedOutputs(files, []string{"models/result.json", "summary.json"}, nil)
	if e != nil || len(selected) != 2 || selected[0].Path != "models/result.json" || selected[0].SourcePath != "workspace/models/result.json" || selected[1].Path != "summary.json" {
		t.Fatal(selected, e)
	}
	for _, files := range [][]packaging.File{{{Path: "workspace/result.json"}, {Path: "outputs/result.json"}}, {{Path: "workspace/result.json"}, {Path: "workspace/result.json"}}, {{Path: "../result.json"}}} {
		if _, e := selectManagedOutputs(files, []string{"result.json"}, nil); e == nil {
			t.Fatal("ambiguous or unsafe guard output accepted", files)
		}
	}
}

func TestManagedOptionalOutputsMayBeAbsentAndAreSelectedWhenProduced(t *testing.T) {
	required := []string{"result.json"}
	optional := []string{"debug/report.txt"}
	for _, present := range []bool{false, true} {
		t.Run(fmt.Sprint(present), func(t *testing.T) {
			files := []packaging.File{{Path: "workspace/result.json"}, {Path: "workspace/incidental.log"}}
			if present {
				files = append(files, packaging.File{Path: "workspace/debug/report.txt"})
			}
			selected, e := selectManagedOutputs(files, required, optional)
			want := 1
			if present {
				want = 2
			}
			if e != nil || len(selected) != want || selected[0].Path != "result.json" {
				t.Fatal(selected, e)
			}
			if present && selected[1].Path != "debug/report.txt" {
				t.Fatal("optional path lost", selected)
			}
		})
	}
	if _, e := selectManagedOutputs([]packaging.File{{Path: "workspace/debug/report.txt"}}, required, optional); e == nil {
		t.Fatal("optional output substituted for missing required result")
	}
}

func TestManagedRunAssignmentUploadsAndCompletesDeclaredOutputName(t *testing.T) {
	data := []byte("actual transferred model fixture")
	h := sha256.Sum256(data)
	digest := hex.EncodeToString(h[:])
	var uploads, completes int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/uploads":
			var body generated.UploadCreate
			json.NewDecoder(r.Body).Decode(&body)
			if body.Name != "model.pt" {
				t.Error("upload did not use declared name", body.Name)
			}
			uploads++
			fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
		case "/api/v1/uploads/upload":
			fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
		case "/api/v1/uploads/upload/parts/0":
			json.NewEncoder(w).Encode(generated.UploadPart{Number: 0, SHA256: digest, SizeBytes: int64(len(data))})
		case "/api/v1/uploads/upload/finalize":
			json.NewEncoder(w).Encode(generated.Artifact{ID: "artifact", Name: "model.pt", MediaType: "application/octet-stream", SHA256: digest, SizeBytes: int64(len(data))})
		case "/api/v1/attempts/attempt/complete":
			var body generated.Complete
			json.NewDecoder(r.Body).Decode(&body)
			if len(body.Manifest.Outputs) != 1 || body.Manifest.Outputs[0].Path != "model.pt" {
				t.Error("completion did not use declared path", body)
			}
			completes++
			fmt.Fprint(w, `{"attempt_id":"attempt","task_id":"task","state":"verifying","submitted_at":"2026-11-15T00:00:00Z"}`)
		case "/api/v1/attempts/attempt/logs":
			w.WriteHeader(204)
		case "/api/v1/attempts/attempt/renew":
			fmt.Fprint(w, `{"lease_remaining_ms":3000,"task_remaining_ms":9000}`)
		default:
			t.Errorf("unexpected route %s", r.URL.Path)
			w.WriteHeader(404)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node-credential", "session", s.Client())
	c.Managed = true
	assignment := fixtureAssignment()
	assignment.Outputs = []string{"model.pt"}
	r := Runner{Client: c, Runtime: &fixtureRuntime{}, StateDir: t.TempDir(), AllowedImages: map[string]string{assignment.ImageDigest: assignment.Platform}}
	result, e := r.RunAssignment(context.Background(), assignment)
	if e != nil || uploads != 1 || completes != 1 || len(result.Files) != 1 || result.Files[0].Path != "model.pt" {
		t.Fatal(result, e, uploads, completes)
	}
}
