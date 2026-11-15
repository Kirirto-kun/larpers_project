//go:build linux

package telemetry

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
)

var clockTickOnce sync.Once
var clockTicks int64
var clockTickError error

func ReadProcessStats(pid int) (ProcessUsage, error) {
	var usage ProcessUsage
	if pid <= 0 {
		return usage, errors.New("invalid process PID")
	}
	var err error
	if pid == os.Getpid() {
		var r syscall.Rusage
		if err = syscall.Getrusage(syscall.RUSAGE_SELF, &r); err == nil {
			cpuNS := (r.Utime.Sec+r.Stime.Sec)*1e9 + (r.Utime.Usec+r.Stime.Usec)*1000
			usage.CPUTimeNS = &cpuNS
		}
	} else {
		// /proc/PID/stat includes all process threads and preserves cumulative
		// CPU when a thread exits. Query CLK_TCK rather than assuming USER_HZ.
		clockTickOnce.Do(func() {
			var data []byte
			data, clockTickError = exec.Command("getconf", "CLK_TCK").Output()
			if clockTickError == nil {
				clockTicks, clockTickError = strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
			}
			if clockTickError == nil && clockTicks <= 0 {
				clockTickError = errors.New("invalid CLK_TCK")
			}
		})
		data, readErr := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
		if readErr != nil {
			err = readErr
		} else if clockTickError != nil {
			err = clockTickError
		} else {
			// comm can contain spaces and parentheses. Fields after its final
			// ')' begin with state (field 3); utime and stime are fields 14/15.
			end := strings.LastIndexByte(string(data), ')')
			if end >= 0 {
				fields := strings.Fields(string(data[end+1:]))
				if len(fields) >= 13 {
					user, userErr := strconv.ParseInt(fields[11], 10, 64)
					kernel, kernelErr := strconv.ParseInt(fields[12], 10, 64)
					if userErr == nil && kernelErr == nil && user >= 0 && kernel >= 0 {
						cpuNS := (user + kernel) * 1e9 / clockTicks
						usage.CPUTimeNS = &cpuNS
					}
				}
			}
			if usage.CPUTimeNS == nil {
				err = errors.New("invalid process CPU counters")
			}
		}
	}
	data, readErr := os.ReadFile(fmt.Sprintf("/proc/%d/statm", pid))
	if readErr != nil {
		err = errors.Join(err, readErr)
	} else {
		var size, resident int64
		if _, readErr = fmt.Sscanf(string(data), "%d %d", &size, &resident); readErr == nil && resident >= 0 {
			rss := resident * int64(os.Getpagesize())
			usage.RSSBytes = &rss
		} else {
			err = errors.Join(err, errors.New("invalid process RSS counters"))
		}
	}
	return usage, err
}
