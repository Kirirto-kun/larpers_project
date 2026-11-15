package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
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
	"strings"
	"sync/atomic"
	"testing"
)

func checkpointExport(t *testing.T, export, name string, binding map[string]string) {
	t.Helper()
	root := filepath.Join(export, "checkpoints", name)
	if err := os.MkdirAll(root, 0700); err != nil {
		t.Fatal(err)
	}
	data := []byte("checkpoint")
	fileHash := sha256.Sum256(data)
	manifest, _ := json.Marshal(map[string]any{"schema_version": "1", "binding": binding, "files": []any{map[string]any{"path": "state.bin", "size_bytes": len(data), "digest": hex.EncodeToString(fileHash[:])}}})
	manifestHash := sha256.Sum256(manifest)
	for name, body := range map[string][]byte{"state.bin": data, "manifest.json": manifest, "READY": []byte(hex.EncodeToString(manifestHash[:]))} {
		if err := os.WriteFile(filepath.Join(root, name), body, 0600); err != nil {
			t.Fatal(err)
		}
	}
}

func checkpointBinding() map[string]string {
	return map[string]string{"code_digest": strings.Repeat("a", 64), "input_digest": strings.Repeat("b", 64), "environment_digest": strings.Repeat("c", 64), "trainer_digest": strings.Repeat("d", 64)}
}

func TestCheckpointPublicationUsesV1BindingDigestAndSequence(t *testing.T) {
	export := t.TempDir()
	checkpointExport(t, export, "first", checkpointBinding())
	var upload generated.UploadCreate
	var published []generated.CheckpointPublish
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		switch req.URL.Path {
		case "/api/v1/uploads":
			if err := json.NewDecoder(req.Body).Decode(&upload); err != nil {
				t.Error(err)
			}
			if upload.Kind == nil || *upload.Kind != "checkpoint" || upload.Proof == nil || upload.Proof.Generation != 2 {
				t.Errorf("checkpoint upload lost kind/fence: %+v", upload)
			}
			fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
		case "/api/v1/uploads/upload":
			fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
		case "/api/v1/uploads/upload/parts/0":
			data, _ := io.ReadAll(req.Body)
			sum := sha256.Sum256(data)
			json.NewEncoder(w).Encode(generated.UploadPart{Number: 0, SizeBytes: int64(len(data)), SHA256: hex.EncodeToString(sum[:])})
		case "/api/v1/uploads/upload/finalize":
			json.NewEncoder(w).Encode(generated.Artifact{ID: "artifact", Name: upload.Name, SizeBytes: upload.SizeBytes, SHA256: upload.SHA256, MediaType: "application/octet-stream"})
		case "/api/v1/attempts/attempt/checkpoints":
			var body generated.CheckpointPublish
			decoder := json.NewDecoder(req.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&body); err != nil {
				t.Errorf("checkpoint does not match v1: %v", err)
			}
			published = append(published, body)
			json.NewEncoder(w).Encode(generated.Artifact{ID: "artifact", Name: upload.Name, SizeBytes: upload.SizeBytes, SHA256: upload.SHA256, MediaType: "application/octet-stream"})
		default:
			t.Error(req.URL.Path)
			w.WriteHeader(404)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	r := Runner{Client: c}
	seen := map[string]bool{}
	for _, name := range []string{"first", "second"} {
		if name == "second" {
			checkpointExport(t, export, name, checkpointBinding())
		}
		if err := r.publishCheckpoints(context.Background(), fixtureAssignment(), export, seen); err != nil {
			t.Fatal(err)
		}
	}
	if len(published) != 2 {
		t.Fatalf("checkpoint publications=%d", len(published))
	}
	for i, body := range published {
		if body.ArtifactID != "artifact" || body.Generation != 2 || body.LeaseToken != "lease-secret" || body.Sequence != int64(i+1) || body.CompatibilityDigest != "49b0dcd0393a3cbe70e7bde6184fed98baa6b09f65682336af04b355e2bff1c6" {
			t.Errorf("checkpoint binding/fence/sequence changed: %+v", body)
		}
	}
}

func TestCheckpointRejectsInvalidBindingBeforeUpload(t *testing.T) {
	for _, field := range []string{"missing", "extra", "invalid digest"} {
		t.Run(field, func(t *testing.T) {
			binding := checkpointBinding()
			switch field {
			case "missing":
				delete(binding, "trainer_digest")
			case "extra":
				binding["extra"] = strings.Repeat("e", 64)
			case "invalid digest":
				binding["trainer_digest"] = "unknown"
			}
			export := t.TempDir()
			checkpointExport(t, export, "bad", binding)
			var calls atomic.Int32
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) { calls.Add(1); w.WriteHeader(http.StatusBadRequest) }))
			defer s.Close()
			c, _ := client.New(s.URL, "node", "session", s.Client())
			r := Runner{Client: c}
			if err := r.publishCheckpoints(context.Background(), fixtureAssignment(), export, map[string]bool{}); err == nil || calls.Load() != 0 {
				t.Fatalf("invalid binding uploaded: err=%v calls=%d", err, calls.Load())
			}
		})
	}
}

func TestCheckpointRejectsChangedManifestBeforeUpload(t *testing.T) {
	export := t.TempDir()
	checkpointExport(t, export, "bad", checkpointBinding())
	if err := os.WriteFile(filepath.Join(export, "checkpoints", "bad", "READY"), []byte(strings.Repeat("0", 64)), 0600); err != nil {
		t.Fatal(err)
	}
	var calls atomic.Int32
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) { calls.Add(1); w.WriteHeader(http.StatusBadRequest) }))
	defer s.Close()
	c, _ := client.New(s.URL, "node", "session", s.Client())
	r := Runner{Client: c}
	if err := r.publishCheckpoints(context.Background(), fixtureAssignment(), export, map[string]bool{}); err == nil || calls.Load() != 0 {
		t.Fatalf("changed checkpoint uploaded: err=%v calls=%d", err, calls.Load())
	}
}

func TestLostCheckpointACKNeverReplaysUnknownPublication(t *testing.T) {
	for _, mode := range []string{"lost ACK", "server failure", "invalid artifact receipt"} {
		t.Run(mode, func(t *testing.T) {
			export := t.TempDir()
			checkpointExport(t, export, "snapshot", checkpointBinding())
			var upload generated.UploadCreate
			var publishes atomic.Int32
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				switch req.URL.Path {
				case "/api/v1/uploads":
					json.NewDecoder(req.Body).Decode(&upload)
					fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
				case "/api/v1/uploads/upload":
					fmt.Fprint(w, `{"id":"upload","state":"pending","part_count":1,"parts":[]}`)
				case "/api/v1/uploads/upload/parts/0":
					data, _ := io.ReadAll(req.Body)
					sum := sha256.Sum256(data)
					json.NewEncoder(w).Encode(generated.UploadPart{Number: 0, SizeBytes: int64(len(data)), SHA256: hex.EncodeToString(sum[:])})
				case "/api/v1/uploads/upload/finalize":
					json.NewEncoder(w).Encode(generated.Artifact{ID: "artifact", Name: upload.Name, SizeBytes: upload.SizeBytes, SHA256: upload.SHA256, MediaType: "application/octet-stream"})
				case "/api/v1/attempts/attempt/checkpoints":
					publishes.Add(1)
					switch mode {
					case "lost ACK":
						conn, _, _ := w.(http.Hijacker).Hijack()
						conn.Close()
					case "server failure":
						w.WriteHeader(http.StatusServiceUnavailable)
					default:
						fmt.Fprint(w, `{"id":"other"}`)
					}
				default:
					t.Error(req.URL.Path)
					w.WriteHeader(404)
				}
			}))
			defer s.Close()
			c, _ := client.New(s.URL, "node", "session", s.Client())
			r := Runner{Client: c}
			state := map[string]bool{}
			for i := 0; i < 2; i++ {
				if err := r.publishCheckpoints(context.Background(), fixtureAssignment(), export, state); err == nil {
					t.Fatal("lost checkpoint ACK reported success")
				}
			}
			if publishes.Load() != 1 || state["snapshot"] {
				t.Fatalf("unknown checkpoint replayed or marked published: calls=%d state=%v", publishes.Load(), state)
			}
			if _, err := os.Stat(filepath.Join(export, "checkpoint-snapshot.zip")); err != nil {
				t.Fatalf("unknown checkpoint archive removed: %v", err)
			}
		})
	}
}
