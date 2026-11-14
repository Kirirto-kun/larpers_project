//go:build windows

package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

type heldStateJSON struct {
	opened  chan struct{}
	release chan struct{}
	value   int
}

func (h *heldStateJSON) UnmarshalJSON(data []byte) error {
	var value struct {
		Sequence int `json:"sequence"`
	}
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	h.value = value.Sequence
	close(h.opened)
	<-h.release // Keep the actual production reader's file handle open.
	return nil
}

func TestStateJSONReaderAllowsGuardAtomicReplacementWhileOpen(t *testing.T) {
	image := os.Getenv("CAMPUS_STATE_RENAME_TEST_IMAGE")
	if image == "" {
		t.Skip("set CAMPUS_STATE_RENAME_TEST_IMAGE to an existing protected CPU image for the real Linux bind-mount rename regression")
	}
	if !client.ImagePattern.MatchString(image) {
		t.Fatal("the guard rename regression requires an immutable image")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "challenge.json")
	replacement := filepath.Join(dir, "challenge.tmp")
	if err := os.WriteFile(path, []byte(`{"sequence":1}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(replacement, []byte(`{"sequence":2}`), 0600); err != nil {
		t.Fatal(err)
	}
	held := &heldStateJSON{opened: make(chan struct{}), release: make(chan struct{})}
	done := make(chan error, 1)
	go func() { done <- readBoundedJSON(path, held) }()
	defer func() {
		close(held.release)
		if err := <-done; err != nil {
			t.Errorf("state read failed: %v", err)
		}
	}()
	select {
	case <-held.opened:
	case <-time.After(2 * time.Second):
		t.Fatal("production state reader did not open the fixture")
	}
	// The actual producer is Python on the Linux side of Docker Desktop's bind
	// mount. Go os.Rename uses MoveFileEx, which has different open-target rules.
	name := "campus-state-read-" + session.UUID()
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = exec.CommandContext(ctx, "docker", "rm", "--force", name).Run()
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "docker", "run", "--rm", "--name", name,
		"--network=none", "--read-only", "--cap-drop=ALL", "--pull=never",
		"--mount", "type=bind,src="+dir+",dst=/state", "--entrypoint=/opt/campus/.venv/bin/python",
		image, "-c", "import os; os.replace('/state/challenge.tmp', '/state/challenge.json')")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("held native reader blocked the real Linux guard replacement: %v\n%s", err, output)
	}
	if held.value != 1 {
		t.Fatal("atomic replacement changed the reader's immutable snapshot")
	}
	var latest struct {
		Sequence int `json:"sequence"`
	}
	if err := readBoundedJSON(path, &latest); err != nil || latest.Sequence != 2 {
		t.Fatalf("replacement was not fully published: sequence=%d err=%v", latest.Sequence, err)
	}
}
