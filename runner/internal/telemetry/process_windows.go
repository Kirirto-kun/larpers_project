//go:build windows

package telemetry

import (
	"errors"
	"fmt"
	"syscall"
	"unsafe"
)

var processKernel = syscall.NewLazyDLL("kernel32.dll")
var processTimes = processKernel.NewProc("GetProcessTimes")
var processMemory = processKernel.NewProc("K32GetProcessMemoryInfo")

func ReadProcessStats(pid int) (ProcessUsage, error) {
	var usage ProcessUsage
	if pid <= 0 || uint64(pid) > 1<<32-1 {
		return usage, errors.New("invalid process PID")
	}
	const queryInformation = 0x0400
	const vmRead = 0x0010
	handle, err := syscall.OpenProcess(queryInformation|vmRead, false, uint32(pid))
	if err != nil {
		return usage, err
	}
	defer syscall.CloseHandle(handle)
	var created, exit, kernel, user syscall.Filetime
	r, _, cpuErr := processTimes.Call(uintptr(handle), uintptr(unsafe.Pointer(&created)), uintptr(unsafe.Pointer(&exit)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user)))
	if r != 0 {
		cpuNS := int64((uint64(kernel.HighDateTime)<<32|uint64(kernel.LowDateTime))+(uint64(user.HighDateTime)<<32|uint64(user.LowDateTime))) * 100
		usage.CPUTimeNS = &cpuNS
	} else {
		err = fmt.Errorf("GetProcessTimes: %w", cpuErr)
	}
	var m struct {
		Size, Faults                                                                            uint32
		PeakRSS, RSS, PeakPaged, Paged, PeakNonPaged, NonPaged, Pagefile, PeakPagefile, Private uintptr
	}
	m.Size = uint32(unsafe.Sizeof(m))
	r, _, memoryErr := processMemory.Call(uintptr(handle), uintptr(unsafe.Pointer(&m)), uintptr(m.Size))
	if r != 0 {
		rss := int64(m.RSS)
		usage.RSSBytes = &rss
	} else {
		err = errors.Join(err, fmt.Errorf("K32GetProcessMemoryInfo: %w", memoryErr))
	}
	return usage, err
}
