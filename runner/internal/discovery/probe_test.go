package discovery

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestProbeParentIndependentlyTerminatesSlowCalculation(t *testing.T) {
	python := probeTestPython(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	started := time.Now()
	output, err := exec.CommandContext(ctx, python, "-c", boundedProbeCode("import time; time.sleep(3)", 0.05)).CombinedOutput()
	if err == nil || time.Since(started) > 2*time.Second || !strings.Contains(string(output), "TimeoutExpired") {
		t.Fatalf("container parent did not enforce its own deadline: elapsed=%v output=%s err=%v", time.Since(started), output, err)
	}
}

func TestCPUProbeRunsRealCalculationAndRetainsSandbox(t *testing.T) {
	python := probeTestPython(t)
	image := "sha256:" + strings.Repeat("a", 64)
	err := Probe(context.Background(), image, "linux/amd64", false, func(ctx context.Context, name string, args ...string) ([]byte, error) {
		for _, required := range []string{"--pull=never", "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--user=65532:65532", "--cpus=1", "--memory=512m", "--pids-limit=32"} {
			if !hasProbeArgument(args, required) {
				return nil, fmt.Errorf("sandbox option missing: %s", required)
			}
		}
		if name != "docker" || hasProbeArgument(args, "--gpus=all") || len(args) < 3 || args[len(args)-3] != image || args[len(args)-2] != "-c" {
			return nil, errors.New("incorrect CPU probe argv")
		}
		return exec.CommandContext(ctx, python, args[len(args)-2:]...).Output()
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestGPUProbeDoesNotAcceptAvailabilityWithoutComputedResult(t *testing.T) {
	err := Probe(context.Background(), "sha256:"+strings.Repeat("a", 64), "linux/amd64", true, func(_ context.Context, name string, args ...string) ([]byte, error) {
		if !hasProbeArgument(args, "--gpus=all") {
			t.Error("GPU opt-in probe did not request the GPU")
		}
		return []byte("CUDA available: True\n"), nil
	})
	if err == nil {
		t.Fatal("availability-only output was accepted as a successful computation")
	}
}

func TestProbeExecHonorsEarlierCallerCancellation(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	started := time.Now()
	_, err := ProbeExec(ctx, os.Args[0], "-test.run=TestProbeExecProcessHelper", "--", "campus-probe-child", "60000")
	if !errors.Is(err, context.DeadlineExceeded) || time.Since(started) > 3*time.Second {
		t.Fatalf("probe process escaped caller cancellation: elapsed=%v err=%v", time.Since(started), err)
	}
}

func TestProbeExecAllowsInitializationPastDiscoveryTimeout(t *testing.T) {
	output, err := ProbeExec(context.Background(), os.Args[0], "-test.run=TestProbeExecProcessHelper", "--", "campus-probe-child", "10200")
	if err != nil || !strings.Contains(string(output), "child-finished") {
		t.Fatalf("probe still uses discovery's short deadline: %s %v", output, err)
	}
}

func TestProbeExecProcessHelper(t *testing.T) {
	if len(os.Args) < 3 || os.Args[len(os.Args)-2] != "campus-probe-child" {
		return
	}
	duration, err := time.ParseDuration(os.Args[len(os.Args)-1] + "ms")
	if err != nil {
		os.Exit(2)
	}
	time.Sleep(duration)
	fmt.Println("child-finished")
	os.Exit(0)
}

func probeTestPython(t *testing.T) string {
	t.Helper()
	for _, candidate := range []string{os.Getenv("CAMPUS_TEST_PYTHON"), filepath.Join("..", "..", "..", "workloads", ".venv", "Scripts", "python.exe"), "python3", "python"} {
		if candidate == "" {
			continue
		}
		path, err := exec.LookPath(candidate)
		if err != nil {
			continue
		}
		absolute, err := filepath.Abs(path)
		if err == nil {
			return absolute
		}
	}
	t.Skip("Python unavailable for real independent probe-parent verification")
	return ""
}

func hasProbeArgument(args []string, value string) bool {
	for _, arg := range args {
		if arg == value {
			return true
		}
	}
	return false
}
