package discovery

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestRequireLocalDockerAcceptsOnlyLocalSocketTransports(t *testing.T) {
	for _, tt := range []struct {
		endpoint string
		ok       bool
	}{
		{"unix:///var/run/docker.sock", true},
		{"unix:///Users/test/.docker/run/docker.sock", true},
		{"npipe:////./pipe/dockerDesktopLinuxEngine", true},
		{"tcp://127.0.0.1:2375", false},
		{"tcp://remote.example:2376", false},
		{"ssh://user@remote.example", false},
		{"unix://remote.example/docker.sock", false},
		{"unix:relative.sock", false},
		{"unix:////remote.example/socket", false},
		{"npipe:////other-computer/pipe/docker_engine", false},
		{"npipe:////./pipe/", false},
		{"npipe:////./pipe/%5c%5cother-computer", false},
		{"unix:///var/run/docker%00.sock", false},
		{"unix:///var/run/docker.sock?target=elsewhere", false},
		{"", false},
	} {
		t.Run(tt.endpoint, func(t *testing.T) {
			raw, _ := json.Marshal(tt.endpoint)
			endpoint, err := RequireLocalDocker(context.Background(), func(_ context.Context, name string, args ...string) ([]byte, error) {
				if name != "docker" || strings.Join(args, " ") != "context inspect --format {{json .Endpoints.docker.Host}}" {
					return nil, errors.New("unexpected Docker operation")
				}
				return raw, nil
			}, func(string) string { return "" })
			if tt.ok && (err != nil || endpoint != tt.endpoint) || !tt.ok && (err == nil || endpoint != "") {
				t.Fatalf("local endpoint classification: got=%q err=%v", endpoint, err)
			}
		})
	}
}

func TestRequireLocalDockerRejectsUnknownAndCancelledSelection(t *testing.T) {
	for _, output := range []string{"null", `""`, `"unix:///a"` + "\n" + `"unix:///b"`, strings.Repeat("x", 16385)} {
		if endpoint, err := RequireLocalDocker(context.Background(), func(context.Context, string, ...string) ([]byte, error) {
			return []byte(output), nil
		}, func(string) string { return "" }); err == nil || endpoint != "" {
			t.Fatalf("ambiguous selection accepted: endpoint=%q err=%v", endpoint, err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	endpoint, err := RequireLocalDocker(ctx, func(ctx context.Context, _ string, _ ...string) ([]byte, error) {
		return nil, ctx.Err()
	}, func(string) string { return "" })
	if endpoint != "" || !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled selection accepted: %q %v", endpoint, err)
	}
}

func TestRequireLocalDockerPreservesCancellationDuringInspection(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	endpoint, err := RequireLocalDocker(ctx, func(context.Context, string, ...string) ([]byte, error) {
		cancel()
		return []byte(`"unix:///var/run/docker.sock"`), nil
	}, func(string) string { return "" })
	if endpoint != "" || !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled inspection returned a usable endpoint: %q %v", endpoint, err)
	}
}

func TestActualDockerCLISelectionPrecedenceWithoutDaemonConnection(t *testing.T) {
	docker, err := exec.LookPath("docker")
	if err != nil {
		t.Skip("Docker CLI unavailable")
	}
	for _, tt := range []struct {
		name, host, dockerContext string
		want                      string
	}{
		{"host bypasses configured context", "tcp://127.0.0.1:65535", "", ""},
		{"host and named context", "tcp://127.0.0.1:65535", "missing-context-never-contacted", ""},
		{"explicit default uses host", "tcp://127.0.0.1:65535", "default", ""},
		{"local host bypasses named context", "unix:///local-selection-test.sock", "missing-context-never-contacted", "unix:///local-selection-test.sock"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			env := append(withoutDockerSelection(os.Environ()), "DOCKER_HOST="+tt.host, "DOCKER_CONTEXT="+tt.dockerContext)
			getenv := func(key string) string {
				if key == "DOCKER_HOST" {
					return tt.host
				}
				if key == "DOCKER_CONTEXT" {
					return tt.dockerContext
				}
				return ""
			}
			endpoint, err := RequireLocalDocker(context.Background(), func(ctx context.Context, _ string, args ...string) ([]byte, error) {
				command := exec.CommandContext(ctx, docker, args...)
				command.Env = env
				return command.Output()
			}, getenv)
			if tt.want == "" && (err == nil || endpoint != "") || tt.want != "" && (err != nil || endpoint != tt.want) {
				t.Fatalf("effective environment selection mismatch: endpoint=%q err=%v", endpoint, err)
			}
		})
	}
}

func withoutDockerSelection(env []string) []string {
	result := make([]string, 0, len(env))
	for _, value := range env {
		upper := strings.ToUpper(value)
		if !strings.HasPrefix(upper, "DOCKER_HOST=") && !strings.HasPrefix(upper, "DOCKER_CONTEXT=") {
			result = append(result, value)
		}
	}
	return result
}
