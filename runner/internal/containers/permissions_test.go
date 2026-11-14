package containers

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestOnlyApprovedInputTreesBecomeReadableAndRestoreForCleanup(t *testing.T) {
	root := t.TempDir()
	p := Paths{Inputs: filepath.Join(root, "inputs"), Control: filepath.Join(root, "control"), State: filepath.Join(root, "state"), Export: filepath.Join(root, "export")}
	for _, d := range []string{p.Inputs, p.Control, p.State, p.Export} {
		os.Mkdir(d, 0700)
		os.WriteFile(filepath.Join(d, "payload"), []byte("data"), 0600)
	}
	if e := PrepareInputs(p); e != nil {
		t.Fatal(e)
	}
	if runtime.GOOS != "windows" {
		st, _ := os.Stat(p.Inputs)
		if st.Mode().Perm() != 0555 {
			t.Fatal("input is not readable by workload")
		}
		st, _ = os.Stat(p.State)
		if st.Mode().Perm() != 0700 {
			t.Fatal("state privacy changed")
		}
		if os.Getuid() != 0 {
			if e := os.WriteFile(filepath.Join(p.Inputs, "payload"), []byte("mutated"), 0600); e == nil {
				t.Fatal("input writable")
			}
		}
	}
	if e := RestoreInputs(p); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(filepath.Join(p.Inputs, "payload"), []byte("restored"), 0600); e != nil {
		t.Fatal("cleanup owner cannot restore", e)
	}
}
