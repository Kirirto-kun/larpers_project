package session

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIdentityPersistsAndDuplicateRunnerCannotLock(t *testing.T) {
	d := t.TempDir()
	s, e := LoadOrCreate(d)
	if e != nil {
		t.Fatal(e)
	}
	again, e := LoadOrCreate(d)
	if e != nil || again.InstallationID != s.InstallationID {
		t.Fatal("installation identity changed", e)
	}
	unlock, e := Lock(d)
	if e != nil {
		t.Fatal(e)
	}
	if other, e := Lock(d); e == nil {
		other()
		t.Fatal("double resource promise allowed")
	}
	unlock()
	unlock, e = Lock(d)
	if e != nil {
		t.Fatal(e)
	}
	unlock()
}
func TestStateIsPrivateAndControlIsValidated(t *testing.T) {
	d := t.TempDir()
	s, _ := LoadOrCreate(d)
	s.Credential = "private-token"
	if e := Save(d, s); e != nil {
		t.Fatal(e)
	}
	st, _ := os.Stat(filepath.Join(d, "identity.json"))
	if st.Mode().Perm()&0077 != 0 && os.PathSeparator != '\\' {
		t.Fatal("credential readable by other users")
	}
	if e := Control(d, "invalid"); e == nil {
		t.Fatal("invalid control accepted")
	}
	if e := Control(d, "drain"); e != nil {
		t.Fatal(e)
	}
	if action, _ := ReadControl(d); action != "drain" {
		t.Fatal("missing drain")
	}
}
