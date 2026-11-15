package onboarding

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func fixture(t *testing.T) (string, Manifest, string) {
	t.Helper()
	root := filepath.Join(t.TempDir(), "Campus 空格")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	file := func(name, data string) File {
		path := filepath.Join(root, filepath.FromSlash(name))
		os.MkdirAll(filepath.Dir(path), 0700)
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256([]byte(data))
		return File{Path: name, SHA256: hex.EncodeToString(sum[:]), SizeBytes: int64(len(data))}
	}
	m := Manifest{SchemaVersion: "1", OS: "darwin", Arch: "arm64", Runner: file("campus", "native-binary"), Profiles: file("profiles.json", "{}"), Images: []Image{{File: file("images/cpu.tar.gz", "image archive"), ImageDigest: "sha256:" + strings.Repeat("a", 64), Accelerator: "cpu", Platform: "linux/arm64"}}}
	return root, m, publish(t, root, m)
}
func publish(t *testing.T, root string, m Manifest) string {
	t.Helper()
	raw, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(root, "connection.json"), raw, 0600)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func TestVerifiedBundleHandlesUnicodeAndSelectedImages(t *testing.T) {
	root, m, digest := fixture(t)
	b, err := Load(context.Background(), root, digest, "darwin", "arm64")
	if err != nil {
		t.Fatal(err)
	}
	if err = b.VerifySelected(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if b.Path(m.Runner) != filepath.Join(root, "campus") {
		t.Fatal("wrong native path")
	}
	if err = b.VerifySelected(context.Background(), true); err == nil {
		t.Fatal("missing GPU runtime accepted")
	}
}

func TestBundleRejectsCorruptionAndUnsafeMetadata(t *testing.T) {
	for _, bad := range []string{"wrong-hash", "wrong-target", "traversal", "duplicate", "wrong-platform", "no-cpu", "unbounded", "unknown-field"} {
		t.Run(bad, func(t *testing.T) {
			root, m, digest := fixture(t)
			switch bad {
			case "wrong-hash":
				digest = strings.Repeat("0", 64)
			case "wrong-target":
				m.Arch = "amd64"
			case "traversal":
				m.Runner.Path = "../outside"
			case "duplicate":
				m.Profiles = m.Runner
			case "wrong-platform":
				m.Images[0].Platform = "linux/amd64"
			case "no-cpu":
				m.Images[0].Accelerator = "cuda"
			case "unbounded":
				m.Images[0].SizeBytes = 9 << 30
			case "unknown-field":
				raw := []byte(`{"schema_version":"1","unexpected":true}`)
				os.WriteFile(filepath.Join(root, "connection.json"), raw, 0600)
				sum := sha256.Sum256(raw)
				digest = hex.EncodeToString(sum[:])
			}
			if bad != "wrong-hash" && bad != "unknown-field" {
				digest = publish(t, root, m)
			}
			if _, err := Load(context.Background(), root, digest, "darwin", "arm64"); err == nil {
				t.Fatal("bad bundle accepted")
			}
		})
	}
}

func TestCorruptPayloadNeverVerifies(t *testing.T) {
	root, m, digest := fixture(t)
	os.WriteFile(filepath.Join(root, "campus"), []byte("changed"), 0600)
	if _, err := Load(context.Background(), root, digest, "darwin", "arm64"); err == nil {
		t.Fatal("changed binary accepted")
	}
	os.WriteFile(filepath.Join(root, "campus"), []byte("native-binary"), 0600)
	b, err := Load(context.Background(), root, digest, "darwin", "arm64")
	if err != nil {
		t.Fatal(err)
	}
	os.WriteFile(b.Path(m.Images[0].File), []byte("wrong archive"), 0600)
	if b.VerifySelected(context.Background(), false) == nil {
		t.Fatal("changed image accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if b.VerifySelected(ctx, false) == nil {
		t.Fatal("cancelled verification continued")
	}
}
