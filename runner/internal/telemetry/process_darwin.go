//go:build darwin

package telemetry

import (
	"errors"
	"os"
	"syscall"
)

func ReadProcessStats(pid int) (ProcessUsage, error) {
	var usage ProcessUsage
	if pid != os.Getpid() {
		return usage, errors.New("external macOS PID CPU/RSS measurement pending native implementation")
	}
	var r syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &r); err != nil {
		return usage, err
	}
	cpuNS := (r.Utime.Sec+r.Stime.Sec)*1e9 + int64(r.Utime.Usec+r.Stime.Usec)*1000
	usage.CPUTimeNS = &cpuNS
	// ru_maxrss is a lifetime peak, not current RSS. Do not label it as RSS.
	return usage, nil
}
