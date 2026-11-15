package artifacts

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestDownloadResumesAndVerifiesContent(t *testing.T) {
	data := []byte("real model weights")
	h := sha256.Sum256(data)
	digest := hex.EncodeToString(h[:])
	dest := filepath.Join(t.TempDir(), "model.pt")
	os.WriteFile(dest+".part", data[:5], 0600)
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/artifacts/a/content" || r.Header.Get("Range") != "bytes=5-" {
			t.Error("download did not resume", r.URL.Path, r.Header)
		}
		w.Header().Set("Content-Range", fmt.Sprintf("bytes 5-%d/%d", len(data)-1, len(data)))
		w.WriteHeader(206)
		w.Write(data[5:])
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "token", "session", s.Client())
	if err := Download(context.Background(), c, client.Artifact{ArtifactID: "a", Digest: digest, SizeBytes: int64(len(data))}, dest); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(dest)
	if string(got) != string(data) {
		t.Fatal("download corrupt")
	}
}

func TestCorruptDownloadNeverPublishes(t *testing.T) {
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("bad")) }))
	defer s.Close()
	c, _ := client.New(s.URL, "token", "", s.Client())
	dest := filepath.Join(t.TempDir(), "artifact")
	if err := Download(context.Background(), c, client.Artifact{ArtifactID: "a", Digest: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SizeBytes: 3}, dest); err == nil {
		t.Fatal("bad digest accepted")
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatal("corrupt output published")
	}
}

func TestUploadResumesOnlyMissingParts(t *testing.T) {
	data := []byte("abcdef")
	f := filepath.Join(t.TempDir(), "payload")
	os.WriteFile(f, data, 0600)
	var parts []string
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/v1/uploads" && r.Method == "POST":
			var request generated.UploadCreate
			decoder := json.NewDecoder(r.Body)
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&request); err != nil {
				t.Error("not a published UploadCreate:", err)
				w.WriteHeader(422)
				return
			}
			if request.Name != "payload" || request.SHA256 != "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721" || request.SizeBytes != 6 || request.PartSizeBytes == nil || *request.PartSizeBytes != 3 || request.Kind == nil || *request.Kind != "output" || request.AttemptID == nil || *request.AttemptID != "33333333-3333-4333-8333-333333333333" || request.Proof == nil || request.Proof.Generation != 1 || request.Proof.LeaseToken != "lease" {
				t.Errorf("incorrect create request: %+v", request)
			}
			if r.Header.Get("Idempotency-Key") != "" {
				t.Error("upload creation has no documented idempotency guarantee")
			}
			w.WriteHeader(201)
			fmt.Fprint(w, `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":2,"parts":[]}`)
		case r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111" && r.Method == "GET":
			fmt.Fprint(w, `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":2,"parts":[{"number":0,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":3}]}`)
		case r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111/parts/1":
			parts = append(parts, r.URL.Path)
			body, _ := io.ReadAll(r.Body)
			if r.Method != "PUT" || string(body) != "def" || r.Header.Get("X-Content-SHA256") != "cb8379ac2098aa165029e3938a51da0bcecfc008fd6795f401178647f96c5b34" {
				t.Error("incorrect part bytes/checksum", r.Method, string(body), r.Header)
			}
			fmt.Fprint(w, `{"number":1,"sha256":"cb8379ac2098aa165029e3938a51da0bcecfc008fd6795f401178647f96c5b34","size_bytes":3}`)
		case r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111/finalize":
			body, _ := io.ReadAll(r.Body)
			if r.Method != "POST" || len(body) != 0 || r.Header.Get("Idempotency-Key") != "" {
				t.Error("finalize requires no body or undocumented idempotency", r.Method, string(body), r.Header)
			}
			fmt.Fprint(w, `{"id":"22222222-2222-4222-8222-222222222222","sha256":"bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721","size_bytes":6,"name":"payload","media_type":"application/octet-stream"}`)
		default:
			t.Error("unexpected request", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "token", "55555555-5555-4555-8555-555555555555", s.Client())
	c.UploadPartSize = 3
	a, err := Upload(context.Background(), c, f, client.Fence{AttemptID: "33333333-3333-4333-8333-333333333333", Generation: 1, LeaseToken: "lease"})
	if err != nil || a.ArtifactID != "22222222-2222-4222-8222-222222222222" || a.Path != "payload" || a.Digest != "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721" || a.SizeBytes != 6 || a.Kind != "output" || !reflect.DeepEqual(parts, []string{"/api/v1/uploads/11111111-1111-4111-8111-111111111111/parts/1"}) {
		t.Fatalf("%+v %v %v", a, err, parts)
	}
}

func TestUploadRejectsUnverifiedRetainedParts(t *testing.T) {
	for _, tc := range []struct{ name, state string }{
		{"wrong checksum", `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":2,"parts":[{"number":0,"sha256":"cb8379ac2098aa165029e3938a51da0bcecfc008fd6795f401178647f96c5b34","size_bytes":3}]}`},
		{"wrong size", `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":2,"parts":[{"number":0,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":2}]}`},
		{"out of range", `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":2,"parts":[{"number":2,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":3}]}`},
		{"duplicate", `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":2,"parts":[{"number":0,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":3},{"number":0,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":3}]}`},
		{"wrong part count", `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":1,"parts":[]}`},
		{"wrong ID", `{"id":"44444444-4444-4444-8444-444444444444","state":"pending","part_count":2,"parts":[]}`},
		{"missing ID", `{"state":"pending","part_count":2,"parts":[]}`},
		{"missing state", `{"id":"11111111-1111-4111-8111-111111111111","part_count":2,"parts":[]}`},
		{"missing part count", `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","parts":[]}`},
		{"expired", `{"id":"11111111-1111-4111-8111-111111111111","state":"expired","part_count":2,"parts":[]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "payload")
			os.WriteFile(path, []byte("abcdef"), 0600)
			var mutations []string
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.Method == "POST" && r.URL.Path == "/api/v1/uploads":
					fmt.Fprint(w, `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":2,"parts":[]}`)
				case r.Method == "GET" && r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111":
					fmt.Fprint(w, tc.state)
				default:
					mutations = append(mutations, r.Method+" "+r.URL.Path)
					w.WriteHeader(400)
				}
			}))
			defer s.Close()
			c, _ := client.New(s.URL, "token", "55555555-5555-4555-8555-555555555555", s.Client())
			c.UploadPartSize = 3
			_, err := Upload(context.Background(), c, path, client.Fence{AttemptID: "33333333-3333-4333-8333-333333333333", Generation: 1, LeaseToken: "lease"})
			if err == nil || len(mutations) != 0 {
				t.Fatalf("unverified upload continued: err=%v requests=%v", err, mutations)
			}
		})
	}
}

func TestUploadCreateUnknownAcknowledgementIsNeverReplayed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "empty")
	os.WriteFile(path, nil, 0600)
	creates := 0
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		creates++
		w.WriteHeader(503)
		fmt.Fprint(w, `{"code":"UNAVAILABLE","message":"commit acknowledgement lost","request_id":"r"}`)
	}))
	defer s.Close()
	c, _ := client.New(s.URL, "token", "55555555-5555-4555-8555-555555555555", s.Client())
	c.Backoff = 0
	_, err := Upload(context.Background(), c, path, client.Fence{})
	if err == nil || creates != 1 {
		t.Fatalf("creation replayed without a documented key: requests=%d err=%v", creates, err)
	}
}

func TestUploadFinalizeUnknownAcknowledgementIsReconciled(t *testing.T) {
	for _, tc := range []struct {
		name, ready string
		wantSuccess bool
	}{
		{"ready", `{"id":"11111111-1111-4111-8111-111111111111","state":"ready","part_count":0,"parts":[],"artifact":{"id":"22222222-2222-4222-8222-222222222222","sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","size_bytes":0,"name":"empty","media_type":"application/octet-stream"}}`, true},
		{"pending", `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":0,"parts":[]}`, false},
		{"corrupt ready artifact", `{"id":"11111111-1111-4111-8111-111111111111","state":"ready","part_count":0,"parts":[],"artifact":{"id":"22222222-2222-4222-8222-222222222222","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size_bytes":0,"name":"empty","media_type":"application/octet-stream"}}`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "empty")
			os.WriteFile(path, nil, 0600)
			finalizes, gets := 0, 0
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.URL.Path == "/api/v1/uploads":
					fmt.Fprint(w, `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":0,"parts":[]}`)
				case r.Method == "GET" && r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111":
					gets++
					if finalizes == 0 {
						fmt.Fprint(w, `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":0,"parts":[]}`)
					} else {
						fmt.Fprint(w, tc.ready)
					}
				case r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111/finalize":
					finalizes++
					w.WriteHeader(503)
					fmt.Fprint(w, `{"code":"UNAVAILABLE","message":"commit acknowledgement lost","request_id":"r"}`)
				default:
					t.Error("unexpected upload request", r.Method, r.URL.Path)
					w.WriteHeader(400)
				}
			}))
			defer s.Close()
			c, _ := client.New(s.URL, "token", "55555555-5555-4555-8555-555555555555", s.Client())
			c.Backoff = 0
			a, err := Upload(context.Background(), c, path, client.Fence{})
			if (err == nil) != tc.wantSuccess || finalizes != 1 || gets != 2 || tc.wantSuccess && a.ArtifactID != "22222222-2222-4222-8222-222222222222" {
				t.Fatalf("unreconciled finalization: artifact=%+v err=%v finalizes=%d gets=%d", a, err, finalizes, gets)
			}
			if !tc.wantSuccess && err != nil && !strings.Contains(err.Error(), "acknowledgement") && tc.name == "pending" {
				t.Errorf("unknown finalize outcome lost its cause: %v", err)
			}
		})
	}
}

func TestUploadAsUsesPublishedKindsAndOmitsEmptyAttemptProof(t *testing.T) {
	for _, kind := range []string{"input", "project", "output", "checkpoint"} {
		t.Run(kind, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "empty")
			os.WriteFile(path, nil, 0600)
			fence := client.Fence{}
			if kind == "output" || kind == "checkpoint" {
				fence = client.Fence{AttemptID: "33333333-3333-4333-8333-333333333333", Generation: 2, LeaseToken: "lease"}
			}
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.URL.Path == "/api/v1/uploads":
					var request map[string]json.RawMessage
					if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
						t.Error(err)
					}
					if string(request["kind"]) != `"`+kind+`"` || string(request["part_size_bytes"]) != "4194304" || string(request["name"]) != `"empty"` || string(request["media_type"]) != `"application/octet-stream"` {
						t.Errorf("incorrect upload metadata: %s", request)
					}
					_, proof := request["proof"]
					_, attempt := request["attempt_id"]
					if proof != (fence.AttemptID != "") || attempt != proof || request["generation"] != nil || request["lease_token"] != nil {
						t.Errorf("incorrect attempt proof envelope: %s", request)
					}
					fmt.Fprint(w, `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":0,"parts":[]}`)
				case r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111" && r.Method == "GET":
					fmt.Fprint(w, `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":0,"parts":[]}`)
				case r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111/finalize":
					fmt.Fprint(w, `{"id":"22222222-2222-4222-8222-222222222222","sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855","size_bytes":0,"name":"empty","media_type":"application/octet-stream"}`)
				default:
					t.Error("unexpected upload request", r.Method, r.URL.Path)
					w.WriteHeader(400)
				}
			}))
			defer s.Close()
			c, _ := client.New(s.URL, "token", "55555555-5555-4555-8555-555555555555", s.Client())
			artifact, err := UploadAs(context.Background(), c, path, fence, kind)
			if err != nil || artifact.Kind != kind || artifact.MediaType != "application/octet-stream" {
				t.Fatalf("%+v %v", artifact, err)
			}
		})
	}
}

func TestUploadRejectsInvalidOptionsBeforeCreatingAnUpload(t *testing.T) {
	for _, tc := range []struct {
		name, kind string
		partSize   int64
		fence      client.Fence
	}{
		{name: "unknown kind", kind: "dataset"},
		{name: "oversized part", kind: "input", partSize: 8388609},
		{name: "negative part", kind: "input", partSize: -1},
		{name: "partial proof", kind: "output", fence: client.Fence{AttemptID: "33333333-3333-4333-8333-333333333333"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "empty")
			os.WriteFile(path, nil, 0600)
			requests := 0
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { requests++; w.WriteHeader(400) }))
			defer s.Close()
			c, _ := client.New(s.URL, "token", "55555555-5555-4555-8555-555555555555", s.Client())
			c.UploadPartSize = tc.partSize
			if _, err := UploadAs(context.Background(), c, path, tc.fence, tc.kind); err == nil || requests != 0 {
				t.Fatalf("invalid upload reached server: requests=%d err=%v", requests, err)
			}
		})
	}
}

func TestUploadPartRetryAndFailureNeverFinalizeUnverifiedBytes(t *testing.T) {
	for _, tc := range []struct {
		name                    string
		status                  int
		body                    string
		mutate                  bool
		wantParts, wantFinalize int
		wantCode                string
	}{
		{name: "transient part failure", status: 503, body: `{"code":"UNAVAILABLE","message":"try again","request_id":"r"}`, wantParts: 2, wantFinalize: 1},
		{name: "revoked session", status: 403, body: `{"code":"SESSION_REPLACED","message":"revoked","request_id":"r"}`, wantParts: 1, wantCode: "SESSION_REPLACED"},
		{name: "stale attempt", status: 409, body: `{"code":"STALE_ATTEMPT","message":"stale","request_id":"r"}`, wantParts: 1, wantCode: "STALE_ATTEMPT"},
		{name: "incorrect part acknowledgement", status: 200, body: `{"number":1,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":3}`, wantParts: 1},
		{name: "source mutation", status: 200, body: `{"number":0,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":3}`, mutate: true, wantParts: 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "payload")
			os.WriteFile(path, []byte("abc"), 0600)
			parts, finalized := 0, 0
			s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.URL.Path == "/api/v1/uploads" || r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111":
					fmt.Fprint(w, `{"id":"11111111-1111-4111-8111-111111111111","state":"pending","part_count":1,"parts":[]}`)
				case r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111/parts/0":
					parts++
					body, _ := io.ReadAll(r.Body)
					if string(body) != "abc" || r.Header.Get("X-Content-SHA256") != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" {
						t.Error("part bytes/checksum changed during retry")
					}
					if parts == 1 {
						if tc.mutate {
							os.WriteFile(path, []byte("def"), 0600)
						}
						w.WriteHeader(tc.status)
						fmt.Fprint(w, tc.body)
					} else {
						fmt.Fprint(w, `{"number":0,"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":3}`)
					}
				case r.URL.Path == "/api/v1/uploads/11111111-1111-4111-8111-111111111111/finalize":
					finalized++
					fmt.Fprint(w, `{"id":"22222222-2222-4222-8222-222222222222","sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size_bytes":3,"name":"payload","media_type":"application/octet-stream"}`)
				default:
					t.Error("unexpected upload request", r.Method, r.URL.Path)
					w.WriteHeader(400)
				}
			}))
			defer s.Close()
			c, _ := client.New(s.URL, "token", "55555555-5555-4555-8555-555555555555", s.Client())
			c.Backoff = 0
			_, err := Upload(context.Background(), c, path, client.Fence{})
			if (err == nil) != (tc.wantFinalize == 1) || parts != tc.wantParts || finalized != tc.wantFinalize {
				t.Fatalf("err=%v parts=%d finalize=%d", err, parts, finalized)
			}
			if tc.wantCode != "" {
				var api *client.APIError
				if !errors.As(err, &api) || api.Code != tc.wantCode || api.Status != tc.status {
					t.Fatalf("API error lost: %v", err)
				}
			}
		})
	}
}
