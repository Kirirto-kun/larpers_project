package app

import (
	"context"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoginPasswordStdinSavesMemberCredentialWithoutPrintingSecrets(t *testing.T) {
	a, out, errout, dir := testApp(t)
	a.Stdin = strings.NewReader("very private password\n")
	var posts int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/auth/cli-login":
			posts++
			var body map[string]string
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if body["email"] != "person@example.test" || body["password"] != "very private password" || r.Header.Get("Authorization") != "" {
				t.Error("login did not preserve the password or used an existing credential")
			}
			fmt.Fprint(w, `{"token":"cli-private-token","user_id":"00000000-0000-4000-8000-000000000001","pool_id":"00000000-0000-4000-8000-000000000002","name":"Person","role":"member","expires_at":null}`)
		case "/api/v1/jobs/job-1/logs":
			if r.Header.Get("Authorization") != "Bearer cli-private-token" {
				t.Error("saved member credential was not used")
			}
			fmt.Fprint(w, `{"entries":[],"cursor":0,"truncated":false}`)
		default:
			t.Errorf("unexpected %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	if code := a.Run(context.Background(), []string{"login", "--endpoint", s.URL, "--email", "person@example.test", "--password-stdin"}); code != 0 {
		t.Fatalf("login code=%d: %s", code, errout)
	}
	if posts != 1 {
		t.Fatalf("login posts=%d", posts)
	}
	if code := a.Run(context.Background(), []string{"logs", "job-1"}); code != 0 {
		t.Fatalf("saved credential could not read logs: %s", errout)
	}
	if strings.Contains(out.String()+errout.String(), "cli-private-token") || strings.Contains(out.String()+errout.String(), "very private password") {
		t.Fatal("login printed a secret")
	}
	if _, err := os.Stat(filepath.Join(dir, "identity.json")); !os.IsNotExist(err) {
		t.Fatal("member login created or replaced a device identity")
	}
}

func TestLoginRejectsPasswordInArgvAndRequiresHiddenTerminalOrStdinFlag(t *testing.T) {
	for _, args := range [][]string{
		{"login", "--email", "person@example.test", "--password", "secret"},
		{"login", "--email", "person@example.test"},
	} {
		a, _, errout, _ := testApp(t)
		a.Stdin = strings.NewReader("secret\n")
		if code := a.Run(context.Background(), args); code != 2 {
			t.Errorf("code=%d want usage error: %s", code, errout)
		}
		if strings.Contains(errout.String(), "secret") {
			t.Error("rejected password was printed")
		}
	}
}

func TestLoginFailureDoesNotEchoServerSuppliedPasswordOrToken(t *testing.T) {
	a, out, errout, _ := testApp(t)
	a.Stdin = strings.NewReader("password-from-stdin\n")
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		w.WriteHeader(401)
		fmt.Fprint(w, `{"code":"BAD_CREDENTIALS","message":"password-from-stdin token-from-server"}`)
	}))
	defer s.Close()
	if code := a.Run(context.Background(), []string{"login", "--endpoint", s.URL, "--email", "person@example.test", "--password-stdin"}); code != 1 {
		t.Fatalf("code=%d: %s", code, errout)
	}
	if strings.Contains(out.String()+errout.String(), "password-from-stdin") || strings.Contains(out.String()+errout.String(), "token-from-server") {
		t.Fatal("authentication error echoed untrusted secret-bearing response")
	}
}

func TestSavedLoginCannotSendCredentialToDifferentEndpoint(t *testing.T) {
	a, _, errout, dir := testApp(t)
	a.Stdin = strings.NewReader("password\n")
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		fmt.Fprint(w, `{"token":"cli-private-token","user_id":"user-1","pool_id":"pool-1","name":"Person","role":"member","expires_at":null}`)
	}))
	defer s.Close()
	if code := a.Run(context.Background(), []string{"login", "--endpoint", s.URL, "--email", "person@example.test", "--password-stdin"}); code != 0 {
		t.Fatalf("login failed: %s", errout)
	}
	var requests int
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { requests++; protocol(w) }))
	defer other.Close()
	if code := a.Run(context.Background(), []string{"--state-dir", dir, "--endpoint", other.URL, "logs", "job-1"}); code != 1 || requests != 0 {
		t.Fatalf("saved token crossed coordinators: code=%d requests=%d %s", code, requests, errout)
	}
}

func TestLoginKeepsCredentialStorageOutsideCurrentProject(t *testing.T) {
	a, _, errout, _ := testApp(t)
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "campus.json"), []byte(projectFixture), 0600)
	a.Cwd = func() (string, error) { return root, nil }
	a.Stdin = strings.NewReader("password\n")
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return filepath.Join(root, "state")
		}
		return ""
	}
	var calls int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++; protocol(w) }))
	defer s.Close()
	if code := a.Run(context.Background(), []string{"login", "--endpoint", s.URL, "--email", "person@example.test", "--password-stdin"}); code != 2 || calls != 0 || !strings.Contains(errout.String(), "outside") {
		t.Fatalf("project-local credential storage accepted: %d requests=%d %s", code, calls, errout)
	}
}

func TestLoginCanUseUserConfigurationBelowHomeOutsideAnyProject(t *testing.T) {
	a, _, errout, _ := testApp(t)
	home := t.TempDir()
	a.Cwd = func() (string, error) { return home, nil }
	a.Stdin = strings.NewReader("password\n")
	a.Getenv = func(k string) string {
		if k == "CAMPUS_STATE_DIR" {
			return filepath.Join(home, "config", "campus")
		}
		return ""
	}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/protocol" {
			protocol(w)
			return
		}
		fmt.Fprint(w, `{"token":"cli-private-token","user_id":"user-1","pool_id":"pool-1","name":"Person","role":"member","expires_at":null}`)
	}))
	defer s.Close()
	if code := a.Run(context.Background(), []string{"login", "--endpoint", s.URL, "--email", "person@example.test", "--password-stdin"}); code != 0 {
		t.Fatalf("ordinary user config below home refused: %d %s", code, errout)
	}
}

func TestLoginPersistsEndpointPinnedCATrustForRunAndLogout(t *testing.T) {
	a, _, errout, _ := testApp(t)
	a.Stdin = strings.NewReader("private-password\n")
	var logins, logs, logouts int
	s := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/protocol":
			protocol(w)
		case "/api/v1/auth/cli-login":
			logins++
			fmt.Fprint(w, `{"token":"cli-private-token","user_id":"user-1","pool_id":"pool-1","name":"Person","role":"member","expires_at":null}`)
		case "/api/v1/jobs/job-1/logs":
			logs++
			fmt.Fprint(w, `{"entries":[],"cursor":0,"truncated":false}`)
		case "/api/v1/auth/cli-logout":
			logouts++
			if r.Header.Get("Authorization") != "Bearer cli-private-token" {
				t.Error("logout did not revoke saved credential")
			}
			w.WriteHeader(204)
		default:
			t.Errorf("unexpected %s", r.URL)
			http.NotFound(w, r)
		}
	}))
	defer s.Close()
	ca := filepath.Join(t.TempDir(), "approved.pem")
	os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: s.Certificate().Raw}), 0600)
	if code := a.Run(context.Background(), []string{"login", "--endpoint", s.URL, "--ca-file", ca, "--email", "person@example.test", "--password-stdin"}); code != 0 {
		t.Fatalf("trusted login=%d %s", code, errout)
	}
	os.Remove(ca)
	if code := a.Run(context.Background(), []string{"logs", "job-1"}); code != 0 {
		t.Fatalf("saved trusted CA missing on later command: %d %s", code, errout)
	}
	if code := a.Run(context.Background(), []string{"logout"}); code != 0 {
		t.Fatalf("saved trusted CA missing on logout: %d %s", code, errout)
	}
	if logins != 1 || logs != 1 || logouts != 1 {
		t.Fatalf("wrong API calls %d/%d/%d", logins, logs, logouts)
	}
	if code := a.Run(context.Background(), []string{"logs", "job-1"}); code != 1 {
		t.Fatalf("logout retained credential: %d", code)
	}
}
