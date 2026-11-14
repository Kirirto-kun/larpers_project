package discovery

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"unicode"
)

type hardwareInventory struct {
	CPUModel             string   `json:"cpu_model"`
	CPUVendor            string   `json:"cpu_vendor"`
	MemoryTotalBytes     int64    `json:"memory_total_bytes"`
	NVIDIADeviceNames    []string `json:"nvidia_device_names,omitempty"`
	NVIDIAInventoryKnown bool     `json:"nvidia_inventory_known"`
}

var staticInventory struct {
	sync.Mutex
	value *hardwareInventory
}

// Only the real Discover path uses this cache. Injected DiscoverWith calls do
// not run host CIM/sysctl commands or share machine inventory between fixtures.
func cachedHardware(ctx context.Context) hardwareInventory {
	staticInventory.Lock()
	defer staticInventory.Unlock()
	if staticInventory.value != nil {
		return *staticInventory.value
	}
	value := readStaticHardware(ctx, runtime.GOOS, Exec)
	if ctx.Err() == nil {
		staticInventory.value = &value
	}
	return value
}

func readStaticHardware(ctx context.Context, platform string, run Command) hardwareInventory {
	var value hardwareInventory
	switch platform {
	case "windows":
		const query = `$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $c=Get-CimInstance Win32_Processor | Select-Object -First 1; $m=Get-CimInstance Win32_ComputerSystem; $v=@(); $checked=$false; try { $v=@(Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -like 'PCI\VEN_10DE*' -and ($_.PNPClass -eq 'Display' -or $_.HardwareID -match '^PCI\\VEN_10DE.*&CC_03') } | ForEach-Object { if ($_.Name -match 'NVIDIA') { [string]$_.Name } else { 'NVIDIA display adapter (model unavailable)' } } | Sort-Object -Unique); $checked=$true } catch { }; @{cpu_model=[string]$c.Name;cpu_vendor=[string]$c.Manufacturer;memory_total_bytes=[long]$m.TotalPhysicalMemory;nvidia_device_names=@($v);nvidia_inventory_known=$checked} | ConvertTo-Json -Compress`
		if raw, err := run(ctx, "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", query); err == nil && len(raw) <= 16<<10 {
			if json.Unmarshal([]byte(strings.TrimPrefix(string(raw), "\ufeff")), &value) != nil {
				return hardwareInventory{}
			}
		}
	case "darwin":
		if raw, err := run(ctx, "/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"); err == nil {
			value.CPUModel = string(raw)
		}
		if strings.HasPrefix(strings.TrimSpace(value.CPUModel), "Apple ") {
			value.CPUVendor = "Apple"
		} else if raw, err := run(ctx, "/usr/sbin/sysctl", "-n", "machdep.cpu.vendor"); err == nil {
			value.CPUVendor = string(raw)
		}
		if raw, err := run(ctx, "/usr/sbin/sysctl", "-n", "hw.memsize"); err == nil {
			value.MemoryTotalBytes, _ = strconv.ParseInt(strings.TrimSpace(string(raw)), 10, 64)
		}
	case "linux":
		cpu, _ := os.ReadFile("/proc/cpuinfo")
		memory, _ := os.ReadFile("/proc/meminfo")
		value = parseLinuxHardware(string(cpu), string(memory))
	}
	value.CPUModel = hardwareText(value.CPUModel)
	value.CPUVendor = hardwareText(value.CPUVendor)
	names := make([]string, 0, min(len(value.NVIDIADeviceNames), 64))
	seen := map[string]bool{}
	for _, name := range value.NVIDIADeviceNames {
		name = hardwareText(name)
		if name != "" && !seen[name] && len(names) < 64 {
			names = append(names, name)
			seen[name] = true
		}
	}
	value.NVIDIADeviceNames = names
	if value.MemoryTotalBytes < 0 {
		value.MemoryTotalBytes = 0
	}
	return value
}

func parseLinuxHardware(cpu, memory string) hardwareInventory {
	var value hardwareInventory
	for _, line := range strings.Split(cpu, "\n") {
		key, field, found := strings.Cut(line, ":")
		if !found {
			continue
		}
		switch strings.TrimSpace(key) {
		case "model name", "Hardware":
			if value.CPUModel == "" {
				value.CPUModel = strings.TrimSpace(field)
			}
		case "vendor_id":
			if value.CPUVendor == "" {
				value.CPUVendor = strings.TrimSpace(field)
			}
		}
	}
	for _, line := range strings.Split(memory, "\n") {
		if strings.HasPrefix(line, "MemTotal:") {
			var kibibytes int64
			if n, _ := fmt.Sscanf(line, "MemTotal: %d kB", &kibibytes); n == 1 && kibibytes > 0 && kibibytes <= (1<<63-1)/1024 {
				value.MemoryTotalBytes = kibibytes * 1024
			}
		}
	}
	return value
}

func hardwareText(value string) string {
	runes := []rune(strings.Map(func(r rune) rune {
		if unicode.IsControl(r) {
			return ' '
		}
		return r
	}, value))
	if len(runes) > 256 {
		runes = runes[:256]
	}
	return strings.TrimSpace(string(runes))
}
