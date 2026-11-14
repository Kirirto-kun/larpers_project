//go:build linux

package telemetry

import (
	"fmt"
	"os"
	"syscall"
)

func ProcessStats() (cpuNS int64, rssBytes int64) {
	var r syscall.Rusage
	if syscall.Getrusage(syscall.RUSAGE_SELF, &r) == nil {
		cpuNS = (r.Utime.Sec+r.Stime.Sec)*1e9 + (r.Utime.Usec+r.Stime.Usec)*1000
	}
	b, e := os.ReadFile("/proc/self/statm")
	if e == nil {
		var size, resident int64
		fmt.Sscanf(string(b), "%d %d", &size, &resident)
		rssBytes = resident * int64(os.Getpagesize())
	}
	return
}
