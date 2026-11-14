//go:build darwin

package telemetry

import (
	"syscall"
)

func ProcessStats() (cpuNS int64, rssBytes int64) {
	var r syscall.Rusage
	if syscall.Getrusage(syscall.RUSAGE_SELF, &r) == nil {
		cpuNS = (r.Utime.Sec+r.Stime.Sec)*1e9 + int64(r.Utime.Usec+r.Stime.Usec)*1000
	}
	return cpuNS, 0
}
