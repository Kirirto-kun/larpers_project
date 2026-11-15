package packaging

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSelectFilesOnlyHashesDeclaredSafeDataAndRejectsMissingPatterns(t *testing.T) {
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "dataset", "nested"), 0700)
	for name, value := range map[string]string{"worker.py": "code", "dataset/a.json": "one", "dataset/nested/b.json": "two", "dataset/.env": "secret"} {
		if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(name)), []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
	}
	m, err := SelectFiles(root, []string{"dataset/**/*.json"}, Rules{MaxFiles: 3, MaxBytes: 6})
	if err != nil || len(m.Files) != 2 || m.TotalBytes != 6 || m.Files[0].Path != "dataset/a.json" || m.Files[1].Path != "dataset/nested/b.json" {
		t.Fatalf("wrong selection: %#v %v", m, err)
	}
	for _, patterns := range [][]string{{"../*.json"}, {"dataset/../../*"}, {"C:/dataset/*"}, {"dataset\\*"}, {"dataset/*.missing"}, {"dataset/.env"}} {
		if _, err := SelectFiles(root, patterns, Rules{}); err == nil {
			t.Errorf("unsafe or unmatched selection accepted: %q", patterns)
		}
	}
}

func TestSelectFilesRejectsQuotaAndUnsafeSymlinkData(t *testing.T) {
	root := t.TempDir()
	os.Mkdir(filepath.Join(root, "data"), 0700)
	os.WriteFile(filepath.Join(root, "data", "a"), []byte("four"), 0600)
	if _, err := SelectFiles(root, []string{"data/*"}, Rules{MaxBytes: 3}); err == nil {
		t.Fatal("oversize data accepted")
	}
	if err := os.Symlink(filepath.Join(root, "data", "a"), filepath.Join(root, "data", "link")); err != nil {
		t.Skip("symlink privilege unavailable")
	}
	if _, err := SelectFiles(root, []string{"data/*"}, Rules{}); err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("symlink data accepted: %v", err)
	}
}

func TestForcedExclusionsCannotBeReincludedByCampusIgnore(t *testing.T) {
	root := t.TempDir()
	os.Mkdir(filepath.Join(root, "dataset"), 0700)
	os.WriteFile(filepath.Join(root, "dataset", "a"), []byte("private data"), 0600)
	os.WriteFile(filepath.Join(root, ".campusignore"), []byte("!dataset/a\n"), 0600)
	os.WriteFile(filepath.Join(root, "worker.py"), []byte("code"), 0600)
	m, err := Scan(root, Rules{Exclude: []string{"dataset/"}})
	if err != nil {
		t.Fatal(err)
	}
	for _, file := range m.Files {
		if strings.HasPrefix(file.Path, "dataset/") {
			t.Fatal("forced data exclusion was overridden")
		}
	}
}

func TestProjectGlobsAndFilesRejectReservedOrUnboundedPortablePaths(t *testing.T) {
	for _, pattern := range []string{"stages/*", "project.zip", ".codex/*", "data/\t*.json", strings.Repeat("a/", 33) + "*.json", strings.Repeat("**/", 5) + "*.json", strings.Repeat("*", 65)} {
		if err := ValidateGlob(pattern); err == nil {
			t.Errorf("unsafe glob accepted: %q", pattern)
		}
	}
}

func TestProjectGlobCharacterClassesMatchServerFnmatchSemantics(t *testing.T) {
	for _, tc := range []struct {
		pattern, name string
		want          bool
	}{
		{"data/[ab].json", "data/a.json", true}, {"data/[ab].json", "data/c.json", false},
		{"data/[!a].json", "data/b.json", true}, {"data/[!a].json", "data/a.json", false},
		{"data/[^a].json", "data/^.json", true}, {"data/[^a].json", "data/a.json", true}, {"data/[^a].json", "data/b.json", false},
		{"data/[a-c].json", "data/b.json", true}, {"data/[!a-c].json", "data/d.json", true},
		{"data/**/*.json", "data/a.json", true}, {"data/**/*.json", "data/nested/a.json", true},
	} {
		if got := MatchGlob(tc.pattern, tc.name); got != tc.want {
			t.Errorf("%q matches %q = %t want %t", tc.pattern, tc.name, got, tc.want)
		}
	}
}

func TestProjectGlobClassEdgesMatchCoordinatorFnmatchcase(t *testing.T) {
	// Literal expectations were checked with Python's fnmatch.fnmatchcase, the
	// coordinator's matcher. No local Go helper computes the expected values.
	for _, tc := range []struct {
		pattern, name string
		want          bool
	}{
		{"[a[!b]", "!", true}, {"[a[!b]", "^", false}, {"[a[!b]", "a", true}, {"[a[!b]", "b", true},
		{"[a-]", "-", true}, {"[a-]", "a", true}, {"[a-]", "b", false},
		{"[-a]", "-", true}, {"[-a]", "a", true}, {"[-a]", "b", false},
		{"[a--b]", "b", true}, {"[a--b]", "a", false}, {"[a--b]", "-", false},
		{"[!a--b]", "a", true}, {"[!a--b]", "b", false},
		{"[!z-a]", "x", true}, {"[z-a]", "x", false},
		{"[z-a!b]", "!", true}, {"[z-a!b]", "^", true}, {"[z-a!b]", "b", false},
		{"[z-a^b]", "^", true}, {"[z-a^b]", "b", true}, {"[z-a^b]", "a", false},
		{"[]]", "]", true}, {"[!]]", "a", true}, {"[!]]", "]", false},
		{"[!]", "[!]", true}, {"[a-b-c]", "-", true}, {"[a-b-c]", "c", true}, {"[a-b-c]", "z", false},
		{"[α-γ]", "β", true}, {"[!α-γ]", "δ", true}, {"*[-a]?", "prefix-a", true},
	} {
		if err := ValidateGlob("data/" + tc.pattern + ".json"); err != nil {
			t.Errorf("coordinator accepts class %q: %v", tc.pattern, err)
		}
		if got := MatchGlob("data/"+tc.pattern+".json", "data/"+tc.name+".json"); got != tc.want {
			t.Errorf("%q matches %q = %t want %t", tc.pattern, tc.name, got, tc.want)
		}
	}
}

func TestProjectGlobNestedClassAndLiteralHyphenSelectExactInventory(t *testing.T) {
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "data"), 0700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"!", "^", "a", "b", "-"} {
		if err := os.WriteFile(filepath.Join(root, "data", name+".json"), []byte("x"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for _, tc := range []struct {
		pattern string
		want    []string
	}{
		{"data/[a[!b].json", []string{"data/!.json", "data/a.json", "data/b.json"}},
		{"data/[a-].json", []string{"data/-.json", "data/a.json"}},
	} {
		inventory, err := SelectFiles(root, []string{tc.pattern}, Rules{MaxFiles: 5, MaxBytes: 5})
		if err != nil {
			t.Errorf("inventory %q: %v", tc.pattern, err)
			continue
		}
		var names []string
		for _, file := range inventory.Files {
			names = append(names, file.Path)
		}
		if strings.Join(names, "\n") != strings.Join(tc.want, "\n") {
			t.Errorf("inventory %q = %q want %q", tc.pattern, names, tc.want)
		}
	}
}
