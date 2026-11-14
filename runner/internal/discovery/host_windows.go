//go:build windows

package discovery

import (
	"os"
	"syscall"
	"unsafe"
)

func hostResources() (int64, int64, string) {
	k := syscall.NewLazyDLL("kernel32.dll")
	var m struct {
		Length                                                                           uint32
		Load                                                                             uint32
		TotalPhys, AvailPhys, TotalPage, AvailPage, TotalVirtual, AvailVirtual, Extended uint64
	}
	m.Length = uint32(unsafe.Sizeof(m))
	k.NewProc("GlobalMemoryStatusEx").Call(uintptr(unsafe.Pointer(&m)))
	cwd, _ := os.Getwd()
	p, _ := syscall.UTF16PtrFromString(cwd)
	var free, total, avail uint64
	k.NewProc("GetDiskFreeSpaceExW").Call(uintptr(unsafe.Pointer(p)), uintptr(unsafe.Pointer(&avail)), uintptr(unsafe.Pointer(&total)), uintptr(unsafe.Pointer(&free)))
	var power struct {
		AC, Battery, Percent, Flag byte
		Life, Full                 uint32
	}
	r, _, _ := k.NewProc("GetSystemPowerStatus").Call(uintptr(unsafe.Pointer(&power)))
	state := "unknown"
	if r != 0 {
		if power.AC == 0 {
			state = "battery"
		} else if power.AC == 1 {
			state = "ac"
		}
	}
	return int64(m.AvailPhys), int64(avail), state
}
