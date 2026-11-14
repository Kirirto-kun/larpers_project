//go:build windows

package session

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSharedReadWindowsPreservesExtendedUnicodeAndUNCPaths(t *testing.T) {
	for _, tc := range []struct{ input, want string }{
		{`C:\状态\campus\challenge.json`, `\\?\C:\状态\campus\challenge.json`},
		{`\\server\share\状态\challenge.json`, `\\?\UNC\server\share\状态\challenge.json`},
		{`\\?\C:\状态\challenge.json`, `\\?\C:\状态\challenge.json`},
		{`\\?\UNC\server\share\状态\challenge.json`, `\\?\UNC\server\share\状态\challenge.json`},
	} {
		got, err := sharedReadWindowsPath(tc.input)
		if err != nil || got != tc.want {
			t.Errorf("path %q = %q, %v; want %q", tc.input, got, err, tc.want)
		}
	}
}

func TestSharedReadWindowsReadsLongUnicodePathReadOnly(t *testing.T) {
	dir := t.TempDir()
	for len(dir) < 300 {
		dir = filepath.Join(dir, "状态-"+strings.Repeat("long-path-", 6))
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "challenge.json")
	const expected = `{"challenge":"unicode-long-path"}`
	if err := os.WriteFile(path, []byte(expected), 0600); err != nil {
		t.Fatal(err)
	}
	file, err := OpenSharedRead(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	raw, err := io.ReadAll(file)
	if err != nil || string(raw) != expected {
		t.Fatalf("long Unicode state read = %q, %v", raw, err)
	}
	if _, err := file.Write([]byte("must not write")); err == nil {
		t.Fatal("shared state reader granted write access")
	}
}

func TestSharedReadWindowsRejectsDirectoryAndFinalSymlink(t *testing.T) {
	dir := t.TempDir()
	if file, err := OpenSharedRead(dir); err == nil {
		file.Close()
		t.Fatal("state reader accepted a directory")
	}
	target := filepath.Join(dir, "target.json")
	if err := os.WriteFile(target, []byte(`{}`), 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "challenge.json")
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("Windows symlink privilege unavailable: %v", err)
	}
	if file, err := OpenSharedRead(link); err == nil {
		file.Close()
		t.Fatal("state reader followed a final symlink")
	}
}
