//go:build darwin

package discovery

import (
	"context"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

func hostResources() (int64, int64, string) {
	var s syscall.Statfs_t
	syscall.Statfs(".", &s)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	b, _ := exec.CommandContext(ctx, "/usr/bin/vm_stat").Output()
	power, _ := exec.CommandContext(ctx, "/usr/bin/pmset", "-g", "batt").Output()
	state := "unknown"
	if strings.Contains(string(power), "Battery Power") {
		state = "battery"
	} else if strings.Contains(string(power), "AC Power") {
		state = "ac"
	}
	return parseVMStat(string(b)), int64(s.Bavail) * int64(s.Bsize), state
}
