package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"campus/runner/internal/worker"
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestConnectManagedPropagatesOptInToJoinedSupervisor(t *testing.T) {
	f := connectionKit(t, false)
	// Existing enrollment avoids unrelated invitation checks in this fixture.
	if e := session.Save(f.state, session.Identity{InstallationID: "installation", NodeID: "00000000-0000-4000-8000-000000000002", Credential: "device-secret", Endpoint: f.endpoint}); e != nil {
		t.Fatal(e)
	}
	old := f.server.Config.Handler
	f.server.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/device/settings" {
			fmt.Fprint(w, `{"revision":1,"mode":"low","desired_state":"running"}`)
			return
		}
		old.ServeHTTP(w, r)
	})
	f.app.Serve = func(ctx context.Context, r *worker.Runner, c client.Capabilities, gpu bool) error {
		f.serves++
		if !r.Client.Managed || r.ManagedControl == nil {
			t.Error("connect lost managed opt in")
		}
		return session.Control(f.state, "stop")
	}
	original := f.app.Discover
	f.app.Discover = func(ctx context.Context) (client.Capabilities, error) {
		c, e := original(ctx)
		c.CPUAvailableMillis = 4000
		c.DockerCPUMillis = 8000
		c.DockerMemoryBytes = 8 << 30
		return c, e
	}
	if code := f.run("--managed"); code != 0 || f.serves != 1 {
		t.Fatal(code, f.serves, f.errors.String())
	}
}

func TestInstallManagedCheckOnlyAcceptsOptInWithoutEnrollment(t *testing.T) {
	a, _, errout, _ := testApp(t)
	// Argument validation should reach the catalog requirement, not reject managed.
	if code := a.Run(context.Background(), []string{"install", "--managed", "--check-only"}); code != 2 || errout.String() == "" {
		t.Fatal(code, errout)
	}
	if strings.Contains(errout.String(), "unknown flag") {
		t.Fatal("installer rejected managed opt in", errout)
	}
}
