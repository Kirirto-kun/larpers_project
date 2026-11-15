package session

import (
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

type Identity struct {
	InstallationID   string `json:"installation_id"`
	Endpoint         string `json:"endpoint"`
	PoolID           string `json:"pool_id"`
	NodeID           string `json:"node_id"`
	SessionID        string `json:"session_id"`
	CredentialID     string `json:"credential_id"`
	Credential       string `json:"credential"`
	MemberCredential string `json:"member_credential,omitempty"`
}

func UUID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[:4], b[4:6], b[6:8], b[8:10], b[10:])
}
func DefaultDir() (string, error) {
	d, e := os.UserConfigDir()
	if e != nil {
		return "", e
	}
	return filepath.Join(d, "campus"), nil
}

// PrivateTempDir creates an owner-protected staging directory without creating
// an installation identity or changing an existing session.
func PrivateTempDir(parent, prefix string) (string, error) {
	dir, err := os.MkdirTemp(parent, prefix)
	if err != nil {
		return "", err
	}
	if err = protect(dir); err != nil {
		os.Remove(dir)
		return "", err
	}
	return dir, nil
}
func AtomicJSON(path string, value any) error {
	b, e := json.Marshal(value)
	if e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".campus-write-*")
	if e != nil {
		return e
	}
	name := f.Name()
	defer os.Remove(name)
	if e = f.Chmod(0600); e != nil {
		f.Close()
		return e
	}
	if _, e = f.Write(b); e != nil {
		f.Close()
		return e
	}
	if e = f.Sync(); e != nil {
		f.Close()
		return e
	}
	if e = f.Close(); e != nil {
		return e
	}
	return replaceFile(name, path)
}

// SavePrivateJSON applies the platform's owner-only protection to a credential
// directory and file, including Windows ACLs where Unix mode bits are insufficient.
func SavePrivateJSON(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	if err := protect(filepath.Dir(path)); err != nil {
		return err
	}
	if err := AtomicJSON(path, value); err != nil {
		return err
	}
	return protect(path)
}
func LoadOrCreate(dir string) (Identity, error) {
	var s Identity
	if e := os.MkdirAll(dir, 0700); e != nil {
		return s, e
	}
	if e := protect(dir); e != nil {
		return s, e
	}
	path := filepath.Join(dir, "identity.json")
	b, e := readFile(path)
	if e == nil {
		if len(b) > 65536 {
			return s, errors.New("identity file too large")
		}
		if e = json.Unmarshal(b, &s); e != nil {
			return s, e
		}
		if s.InstallationID == "" {
			return s, errors.New("identity lacks installation_id")
		}
		return s, nil
	}
	if !os.IsNotExist(e) {
		return s, e
	}
	s.InstallationID = UUID()
	return s, Save(dir, s)
}
func Save(dir string, s Identity) error {
	if e := os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	if e := protect(dir); e != nil {
		return e
	}
	path := filepath.Join(dir, "identity.json")
	if e := AtomicJSON(path, s); e != nil {
		return e
	}
	return protect(path)
}
func Control(dir, action string) error {
	if action != "drain" && action != "stop" && action != "resume" {
		return errors.New("control must be drain, stop or resume")
	}
	return AtomicJSON(filepath.Join(dir, "control.json"), map[string]string{"action": action})
}
func ReadControl(dir string) (string, error) {
	b, e := readFile(filepath.Join(dir, "control.json"))
	if os.IsNotExist(e) {
		return "", nil
	}
	if e != nil {
		return "", e
	}
	var m struct {
		Action string `json:"action"`
	}
	if e = json.Unmarshal(b, &m); e != nil {
		return "", e
	}
	if m.Action != "drain" && m.Action != "stop" && m.Action != "resume" {
		return "", errors.New("invalid control file")
	}
	return m.Action, nil
}
