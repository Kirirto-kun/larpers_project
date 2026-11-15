package packaging

import (
	"archive/zip"
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSnapshotProtectsSecretsAndUnicode(t *testing.T) {
	root := filepath.Join(t.TempDir(), "проект с пробелами")
	os.MkdirAll(filepath.Join(root, "dataset"), 0700)
	for name, content := range map[string]string{"train.py": "print('你好')", ".env": "TOKEN=secret", "id_rsa": "private", "dataset/x.txt": "data", ".campusignore": "dataset/\n"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	var out bytes.Buffer
	m, err := PackageProject(root, Rules{MaxFiles: 20, MaxBytes: 1024}, &out)
	if err != nil {
		t.Fatal(err)
	}
	if len(m.Files) != 2 {
		t.Fatalf("unexpected files: %+v", m.Files)
	}
	if len(m.Digest) != 64 {
		t.Fatal("missing manifest digest")
	}
	zr, err := zip.NewReader(bytes.NewReader(out.Bytes()), int64(out.Len()))
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range zr.File {
		if strings.Contains(f.Name, "env") || strings.Contains(f.Name, "rsa") || strings.HasPrefix(f.Name, "dataset/") {
			t.Fatalf("leaked %s", f.Name)
		}
	}
	var again bytes.Buffer
	m2, err := PackageProject(root, Rules{MaxFiles: 20, MaxBytes: 1024}, &again)
	if err != nil || m2.Digest != m.Digest || !bytes.Equal(again.Bytes(), out.Bytes()) {
		t.Fatal("snapshot is not deterministic", err)
	}
}

func TestSnapshotRejectsEscapesAndQuota(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "big"), []byte("12345"), 0600)
	if _, err := PackageProject(root, Rules{MaxFiles: 1, MaxBytes: 4}, &bytes.Buffer{}); err == nil {
		t.Fatal("quota not enforced")
	}
	outside := filepath.Join(t.TempDir(), "secret")
	os.WriteFile(outside, []byte("secret"), 0600)
	if err := os.Symlink(outside, filepath.Join(root, "escape")); err == nil {
		if _, err := PackageProject(root, Rules{MaxFiles: 10, MaxBytes: 100}, &bytes.Buffer{}); err == nil {
			t.Fatal("symlink accepted")
		}
	}
}

func TestExtractRejectsArchiveTraversal(t *testing.T) {
	for _, name := range []string{"../escape", "/absolute", "C:/Windows/file", "a\\..\\escape", "safe/../../evil"} {
		var b bytes.Buffer
		z := zip.NewWriter(&b)
		w, _ := z.Create(name)
		w.Write([]byte("bad"))
		z.Close()
		if err := Extract(bytes.NewReader(b.Bytes()), int64(b.Len()), t.TempDir(), Rules{MaxFiles: 10, MaxBytes: 100}); err == nil {
			t.Errorf("accepted %q", name)
		}
	}
}

func TestCollectIncludesChangedModelButNotUnchangedSource(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "train.py"), []byte("source"), 0600)
	before, err := Scan(root, Rules{MaxFiles: 10, MaxBytes: 100})
	if err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(root, "model.pt"), []byte("weights"), 0600)
	os.WriteFile(filepath.Join(root, ".env"), []byte("secret"), 0600)
	result, err := CollectOutputs(root, before, Rules{MaxFiles: 10, MaxBytes: 100}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Files) != 1 || result.Files[0].Path != "model.pt" {
		t.Fatalf("lost model or leaked source: %+v", result)
	}
}
