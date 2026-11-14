//go:build windows

package telemetry

import (
	"syscall"
	"unsafe"
)

func ProcessStats() (cpuNS int64, rssBytes int64) {
	k := syscall.NewLazyDLL("kernel32.dll")
	handle, _, _ := k.NewProc("GetCurrentProcess").Call()
	var created, exit, kernel, user syscall.Filetime
	r, _, _ := k.NewProc("GetProcessTimes").Call(handle, uintptr(unsafe.Pointer(&created)), uintptr(unsafe.Pointer(&exit)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user)))
	if r != 0 {
		cpuNS = int64((uint64(kernel.HighDateTime)<<32|uint64(kernel.LowDateTime))+(uint64(user.HighDateTime)<<32|uint64(user.LowDateTime))) * 100
	}
	var m struct {
		Size, Faults                                                                            uint32
		PeakRSS, RSS, PeakPaged, Paged, PeakNonPaged, NonPaged, Pagefile, PeakPagefile, Private uintptr
	}
	m.Size = uint32(unsafe.Sizeof(m))
	r, _, _ = k.NewProc("K32GetProcessMemoryInfo").Call(handle, uintptr(unsafe.Pointer(&m)), uintptr(m.Size))
	if r != 0 {
		rssBytes = int64(m.RSS)
	}
	return
}
