//go:build linux

package discovery

import (
	"fmt"
	"os"
	"strings"
	"syscall"
)

func hostResources() (int64, int64, string) {
	var memory int64
	b, _ := os.ReadFile("/proc/meminfo")
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "MemAvailable:") {
			fmt.Sscanf(l, "MemAvailable: %d kB", &memory)
			memory *= 1024
		}
	}
	var s syscall.Statfs_t
	syscall.Statfs(".", &s)
	state := "unknown"
	supplies, _ := os.ReadDir("/sys/class/power_supply")
	battery := false
	ac := false
	for _, d := range supplies {
		p := "/sys/class/power_supply/" + d.Name() + "/"
		typ, _ := os.ReadFile(p + "type")
		if strings.TrimSpace(string(typ)) == "Battery" {
			battery = true
			status, _ := os.ReadFile(p + "status")
			if strings.TrimSpace(string(status)) != "Discharging" {
				ac = true
			}
		}
		if online, e := os.ReadFile(p + "online"); e == nil && strings.TrimSpace(string(online)) == "1" {
			ac = true
		}
	}
	if ac {
		state = "ac"
	} else if battery {
		state = "battery"
	}
	return memory, int64(s.Bavail) * s.Bsize, state
}
