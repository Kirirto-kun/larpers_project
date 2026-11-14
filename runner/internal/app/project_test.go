package app

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const projectFixture = `{"schema_version":"1","name":"batch-demo","data":["dataset/*.json"],"stages":[{"id":"map","kind":"map","inputs":["dataset/*.json"],"command":["python","worker.py","{input}","{accelerator}"],"outputs":[{"path":"result.json","max_bytes":1024}],"accelerator":"cpu"}]}`

func writeProjectFixture(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	if err := os.Mkdir(filepath.Join(root, "dataset"), 0700); err != nil {
		t.Fatal(err)
	}
	for name, value := range map[string]string{"campus.json": projectFixture, "worker.py": "print('safe code')\n", "dataset/a.json": "[1,2]", "dataset/b.json": "[3,4]"} {
		if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(name)), []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestProjectRunWithoutConfigReportsCandidatesWithoutNetworkOrGuessing(t *testing.T) {
	a, out, errout, _ := testApp(t)
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "train.py"), []byte("print('example')"), 0600)
	os.WriteFile(filepath.Join(root, "samples.csv"), []byte("a,b\n1,2"), 0600)
	a.Cwd = func() (string, error) { return root, nil }
	a.Stdin = strings.NewReader("")
	if code := a.Run(context.Background(), []string{"run"}); code != 2 {
		t.Fatalf("code=%d want actionable usage: %s", code, errout)
	}
	text := out.String() + errout.String()
	for _, want := range []string{"campus.json", "train.py", "samples.csv", "map", "reduce"} {
		if !strings.Contains(text, want) {
			t.Errorf("discovery omitted %q: %s", want, text)
		}
	}
}

func TestProjectRunYesNeedsSavedExplicitSharingReviewBeforeNetwork(t *testing.T) {
	a, _, errout, _ := testApp(t)
	root := writeProjectFixture(t)
	a.Cwd = func() (string, error) { return root, nil }
	a.Stdin = strings.NewReader("")
	if code := a.Run(context.Background(), []string{"run", "--yes"}); code != 2 || !strings.Contains(errout.String(), "review") {
		t.Fatalf("unreviewed noninteractive run accepted or unclear: code=%d %s", code, errout)
	}
}

func TestProjectRunRejectsUnsafeConfigBeforeNetwork(t *testing.T) {
	for _, mutation := range []struct{ from, to string }{
		{`dataset/*.json`, `../*.json`},
		{`"{input}"`, `"{unknown}"`},
		{`"kind":"map"`, `"kind":"reduce"`},
		{`"path":"result.json"`, `"path":"../result.json"`},
		{`"max_bytes":1024`, `"max_bytes":0`},
		{`"schema_version":"1"`, `"schema_version":"2"`},
	} {
		t.Run(mutation.to, func(t *testing.T) {
			a, _, errout, _ := testApp(t)
			root := writeProjectFixture(t)
			os.WriteFile(filepath.Join(root, "campus.json"), []byte(strings.ReplaceAll(projectFixture, mutation.from, mutation.to)), 0600)
			a.Cwd = func() (string, error) { return root, nil }
			a.Stdin = strings.NewReader("none\nyes\n")
			if code := a.Run(context.Background(), []string{"run"}); code != 2 || !strings.Contains(errout.String(), "invalid project config") {
				t.Fatalf("unsafe config reached authentication: code=%d %s", code, errout)
			}
		})
	}
}

func TestProjectConfigUsesServerContractBoundsAndExactDependencyNames(t *testing.T) {
	for _, tc := range []struct {
		name      string
		edit      func(*projectConfig)
		wantError bool
	}{
		{"portable-digit-id", func(c *projectConfig) { c.Stages[0].ID = "1.fit-v1" }, false},
		{"name256", func(c *projectConfig) { c.Name = strings.Repeat("a", 256) }, false},
		{"minimum-memory", func(c *projectConfig) { c.Stages[0].Resources.MemoryBytes = 1 }, true},
		{"minimum-disk", func(c *projectConfig) { c.Stages[0].Resources.DiskBytes = 1 }, true},
		{"executable-template", func(c *projectConfig) { c.Stages[0].Command[0] = "{accelerator}" }, true},
		{"output-disk-budget", func(c *projectConfig) { c.Stages[0].Outputs[0].MaxBytes = 268435456 }, true},
		{"reserved-stages-output", func(c *projectConfig) { c.Stages[0].Outputs[0].Path = "stages" }, true},
		{"reserved-stages-directory", func(c *projectConfig) { c.Stages[0].Outputs[0].Path = "Stages/x" }, true},
		{"ordinary-stages-json", func(c *projectConfig) { c.Stages[0].Outputs[0].Path = "stages.json" }, false},
		{"wrong-case-dependency", func(c *projectConfig) {
			next := c.Stages[0]
			next.ID = "next"
			next.DependsOn = []string{"MAP"}
			c.Stages = append(c.Stages, next)
		}, true},
		{"case-glob-alias", func(c *projectConfig) { c.Data = append(c.Data, "DATASET/*.json") }, true},
		{"reduce-declared-data", func(c *projectConfig) {
			c.Stages[0].Kind = "reduce"
			c.Stages[0].Command = []string{"python", "worker.py"}
		}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := writeProjectFixture(t)
			c, err := loadProject(root)
			if err != nil {
				t.Fatal(err)
			}
			tc.edit(&c)
			err = validateProjectConfig(c)
			if (err != nil) != tc.wantError {
				t.Fatalf("wantError=%t got %v", tc.wantError, err)
			}
		})
	}
}
