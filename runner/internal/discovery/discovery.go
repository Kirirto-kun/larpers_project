package discovery

import (
	"campus/runner/internal/client"
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"
)

type Command func(context.Context, string, ...string) ([]byte, error)

func Exec(ctx context.Context, name string, args ...string) ([]byte, error) {
	bounded, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	return exec.CommandContext(bounded, name, args...).Output()
}

// ProbeExec leaves time for cold framework/GPU initialization while preserving
// an earlier caller deadline. Probe also imposes an independent container bound.
func ProbeExec(ctx context.Context, name string, args ...string) ([]byte, error) {
	bounded, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	output, err := exec.CommandContext(bounded, name, args...).Output()
	if bounded.Err() != nil {
		return output, bounded.Err()
	}
	return output, err
}
func Discover(ctx context.Context) (client.Capabilities, error) {
	caps, err := DiscoverWith(ctx, Exec)
	if err != nil {
		return caps, err
	}
	hardware := cachedHardware(ctx)
	caps.CPUModel = hardware.CPUModel
	caps.CPUVendor = hardware.CPUVendor
	caps.MemoryTotalBytes = hardware.MemoryTotalBytes
	caps.NVIDIADeviceNames = append([]string{}, hardware.NVIDIADeviceNames...)
	if len(caps.GPUs) == 0 {
		if len(caps.NVIDIADeviceNames) > 0 {
			caps.Diagnostics = append(caps.Diagnostics, "NVIDIA display hardware detected by Windows; CUDA is unavailable until the compute driver and explicit container probe succeed.")
		} else if !hardware.NVIDIAInventoryKnown {
			caps.Diagnostics = append(caps.Diagnostics, "OS NVIDIA display inventory unavailable; physical NVIDIA hardware presence is unknown.")
		}
	}
	return caps, nil
}
func DiscoverWith(ctx context.Context, run Command) (client.Capabilities, error) {
	c := client.Capabilities{OS: runtime.GOOS, Arch: runtime.GOARCH, CPULogical: runtime.NumCPU(), GPUs: []client.GPU{}, PowerState: "unknown", PreflightRevision: "1"}
	c.MemoryAvailableBytes, c.DiskAvailableBytes, c.PowerState = hostResources()
	b, e := run(ctx, "docker", "version", "--format", "{{json .Server}}")
	if e != nil {
		c.Diagnostics = append(c.Diagnostics, "Docker is unavailable. Start Docker Engine/Desktop; on Windows check WSL2.")
	} else {
		var s struct {
			Version string
			OS      string `json:"Os"`
			Arch    string
		}
		if e = json.Unmarshal(b, &s); e != nil || s.Version == "" || s.OS != "linux" {
			c.Diagnostics = append(c.Diagnostics, "A healthy Linux Docker runtime is required.")
		} else {
			c.Runtime = client.Runtime{Type: "docker", Version: s.Version, Healthy: true}
		}
	}
	b, e = run(ctx, "nvidia-smi", "--query-gpu=index,name,memory.total,driver_version", "--format=csv,noheader,nounits")
	if e == nil {
		rows, e := csv.NewReader(strings.NewReader(string(b))).ReadAll()
		if e == nil {
			for _, r := range rows {
				if len(r) != 4 {
					continue
				}
				n, e := strconv.ParseInt(strings.TrimSpace(r[2]), 10, 64)
				if e != nil || n <= 0 || n > (1<<63-1)>>20 {
					continue
				}
				c.GPUs = append(c.GPUs, client.GPU{ID: strings.TrimSpace(r[0]), Name: hardwareText(r[1]), Vendor: "nvidia", VRAMBytes: n << 20, Driver: strings.TrimSpace(r[3])})
			}
		}
	} else {
		c.Diagnostics = append(c.Diagnostics, "NVIDIA detection unavailable through the compute driver; this query cannot establish physical hardware presence or CUDA usability. Check the NVIDIA driver or nvidia-smi installation if a GPU is expected.")
	}
	if len(c.GPUs) > 0 {
		c.Diagnostics = append(c.Diagnostics, "NVIDIA detected; CUDA remains unavailable until the explicit GPU container probe passes.")
	}
	if c.MemoryAvailableBytes < 512<<20 {
		c.Diagnostics = append(c.Diagnostics, "Less than 512 MiB available memory; free memory before joining.")
	}
	c.OfferedBudget = Budget(c, false)
	return c, nil
}
func Budget(c client.Capabilities, gpu bool) client.ResourceLimits {
	b := client.ResourceLimits{CPUMillis: int64(max(1, c.CPULogical/4)) * 1000, MemoryBytes: min(c.MemoryAvailableBytes/4, 4<<30), PIDs: 256, WorkspaceBytes: 256 << 20, OutputBytes: 128 << 20, CheckpointBytes: 128 << 20, TmpBytes: 64 << 20, GPUIDs: []string{}}
	if c.PowerState == "battery" || c.MemoryAvailableBytes < 512<<20 || c.DiskAvailableBytes < 512<<20 {
		b.CPUMillis = 0
		b.MemoryBytes = 0
		return b
	}
	if gpu {
		for _, g := range c.GPUs {
			if g.CUDAVerified {
				b.GPUIDs = append(b.GPUIDs, g.ID)
				b.VRAMBytes += g.VRAMBytes
			}
		}
	}
	return b
}
func Probe(ctx context.Context, image, platform string, gpu bool, run Command) error {
	if !client.ImagePattern.MatchString(image) {
		return fmt.Errorf("probe requires a pinned image@sha256 digest")
	}
	code := "assert sum(i*i for i in range(1000)) == 332833500; print('cpu-ok')"
	args := []string{"run", "--rm", "--pull=never", "--platform", platform, "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--user=65532:65532", "--cpus=1", "--memory=512m", "--pids-limit=32", "--entrypoint=/opt/campus/.venv/bin/python"}
	if gpu {
		args = append(args, "--gpus=all")
		code = "import torch; assert torch.cuda.is_available(); a=torch.tensor([1.,2.,3.],device='cuda'); assert (a*a).sum().item()==14; torch.cuda.synchronize(); print('cuda-ok')"
	}
	args = append(args, image, "-c", boundedProbeCode(code, 45))
	out, e := run(ctx, "docker", args...)
	if e != nil {
		return fmt.Errorf("offline container probe failed; verify cached image, architecture and driver: %w", e)
	}
	marker := "cpu-ok"
	if gpu {
		marker = "cuda-ok"
	}
	if !strings.Contains(string(out), marker) {
		return fmt.Errorf("probe did not confirm %s", marker)
	}
	return nil
}

// The container's Python parent reaps/kills the calculation on timeout even if
// the host Docker CLI is interrupted or disappears. No host Python is required.
func boundedProbeCode(code string, seconds float64) string {
	return fmt.Sprintf("import subprocess, sys\nsubprocess.run([sys.executable, '-c', %s], check=True, timeout=%g)\n", strconv.Quote(code), seconds)
}
