package discovery

import (
	"campus/runner/internal/client"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func TestWindowsHardwareInventoryReportsAMDAndTotalRAM(t *testing.T) {
	hardware := readStaticHardware(context.Background(), "windows", func(_ context.Context, name string, args ...string) ([]byte, error) {
		if name != "powershell.exe" || !strings.Contains(strings.Join(args, " "), "Get-CimInstance") {
			return nil, errors.New("unexpected Windows query")
		}
		return []byte(`{"cpu_model":"AMD Ryzen 7 8845HS w/ Radeon 780M Graphics","cpu_vendor":"AuthenticAMD","memory_total_bytes":17179869184}`), nil
	})
	if hardware.CPUModel != "AMD Ryzen 7 8845HS w/ Radeon 780M Graphics" || hardware.CPUVendor != "AuthenticAMD" || hardware.MemoryTotalBytes != 16<<30 {
		t.Fatalf("incorrect Windows inventory: %+v", hardware)
	}
}

func TestWindowsNVIDIAHardwareNamesRemainAvailableWithoutComputeDriver(t *testing.T) {
	hardware := readStaticHardware(context.Background(), "windows", func(_ context.Context, name string, args ...string) ([]byte, error) {
		if name != "powershell.exe" || !strings.Contains(strings.Join(args, " "), "Win32_PnPEntity") {
			return nil, errors.New("physical display hardware was not queried")
		}
		return []byte(`{"cpu_model":"AMD Ryzen fixture","cpu_vendor":"AuthenticAMD","memory_total_bytes":17179869184,"nvidia_device_names":["NVIDIA display adapter (model unavailable)"]}`), nil
	})
	raw, err := json.Marshal(hardware)
	if err != nil || !strings.Contains(string(raw), `"nvidia_device_names":["NVIDIA display adapter (model unavailable)"]`) {
		t.Fatalf("physical NVIDIA display inventory lost when compute driver is unavailable: %s %v", raw, err)
	}
}

func TestAppleSiliconHardwareSummaryDoesNotInventCUDAGPUs(t *testing.T) {
	hardware := readStaticHardware(context.Background(), "darwin", func(_ context.Context, name string, args ...string) ([]byte, error) {
		if name == "/usr/sbin/sysctl" && strings.Join(args, " ") == "-n machdep.cpu.brand_string" {
			return []byte("Apple M2 Pro\n"), nil
		}
		if name == "/usr/sbin/sysctl" && strings.Join(args, " ") == "-n hw.memsize" {
			return []byte("17179869184\n"), nil
		}
		return nil, errors.New("no NVIDIA device or Intel vendor")
	})
	caps := client.Capabilities{OS: "darwin", Arch: "arm64", CPUModel: hardware.CPUModel, CPUVendor: hardware.CPUVendor, MemoryTotalBytes: hardware.MemoryTotalBytes}
	if caps.CPUModel != "Apple M2 Pro" || caps.CPUVendor != "Apple" || caps.MemoryTotalBytes != 16<<30 || len(caps.GPUs) != 0 {
		t.Fatalf("incorrect native Mac summary: %+v", caps)
	}
}

func TestStaticHardwareReadFailureRemainsUnknown(t *testing.T) {
	for _, platform := range []string{"windows", "darwin"} {
		hardware := readStaticHardware(context.Background(), platform, func(context.Context, string, ...string) ([]byte, error) {
			return nil, errors.New("inventory denied")
		})
		if hardware.CPUModel != "" || hardware.CPUVendor != "" || hardware.MemoryTotalBytes != 0 {
			t.Fatalf("failed query invented hardware: %+v", hardware)
		}
	}
}

func TestLocalHardwareDetailsDoNotChangeServerCapabilities(t *testing.T) {
	caps := client.Capabilities{OS: "windows", Arch: "amd64", CPULogical: 8, CPUModel: "AMD Ryzen fixture", CPUVendor: "AuthenticAMD", MemoryTotalBytes: 16 << 30, NVIDIADeviceNames: []string{"NVIDIA display fixture"}, PreflightRevision: "1", GPUs: []client.GPU{{ID: "0", Name: "RTX 5050", Vendor: "nvidia", VRAMBytes: 8 << 30}}}
	contract, err := caps.Contract()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(contract)
	if err != nil || strings.Contains(string(raw), "AMD Ryzen") || strings.Contains(string(raw), "RTX 5050") || strings.Contains(string(raw), "cpu_model") || strings.Contains(string(raw), "memory_total_bytes") || strings.Contains(string(raw), "nvidia_device_names") {
		t.Fatalf("local display metadata leaked into server capabilities: %s %v", raw, err)
	}
}
