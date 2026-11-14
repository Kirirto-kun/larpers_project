package containers

import (
	"campus/runner/internal/client"
	"strings"
	"testing"
)

func TestSandboxArgumentsEnforceIsolationAndQuotas(t *testing.T) {
	a := client.Assignment{ImageDigest: "demo@sha256:" + strings.Repeat("a", 64), Platform: "linux/amd64", NetworkPolicy: "none", ResourceLimits: client.ResourceLimits{CPUMillis: 1000, MemoryBytes: 512 << 20, PIDs: 64, WorkspaceBytes: 128 << 20, OutputBytes: 64 << 20, CheckpointBytes: 64 << 20, TmpBytes: 32 << 20}}
	args, e := BuildArgs(a, Paths{Inputs: t.TempDir(), Control: t.TempDir(), State: t.TempDir(), Export: t.TempDir()}, "installation", "attempt")
	if e != nil {
		t.Fatal(e)
	}
	joined := strings.Join(args, " ")
	for _, need := range []string{"--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--pull=never", "--restart=no", "--pids-limit=64", "--memory=536870912", "dst=/inputs,readonly", "dst=/run/campus-lease,readonly", "/workspace:rw", "/outputs:rw", "/checkpoints:rw"} {
		if !strings.Contains(joined, need) {
			t.Errorf("missing %s in %v", need, args)
		}
	}
	for _, bad := range []string{"--privileged", "docker.sock", "--network=host"} {
		if strings.Contains(joined, bad) {
			t.Fatal("unsafe sandbox", joined)
		}
	}
	a.NetworkPolicy = "host"
	if _, e := BuildArgs(a, Paths{}, "i", "a"); e == nil {
		t.Fatal("network escalation allowed")
	}
}
