//go:build windows

package discovery

import (
	"context"
	"errors"
	"runtime"
	"syscall"
	"time"
	"unsafe"
)

func availableCPU(ctx context.Context) (int64, error) {
	return sampleIdleCPU(ctx, runtime.NumCPU(), 200*time.Millisecond, func() (uint64, uint64, error) {
		var idle, kernel, user syscall.Filetime
		ok, _, _ := syscall.NewLazyDLL("kernel32.dll").NewProc("GetSystemTimes").Call(uintptr(unsafe.Pointer(&idle)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user)))
		if ok == 0 {
			return 0, 0, errors.New("GetSystemTimes unavailable")
		}
		ticks := func(f syscall.Filetime) uint64 { return uint64(f.HighDateTime)<<32 | uint64(f.LowDateTime) }
		return ticks(idle), ticks(kernel) + ticks(user), nil
	})
}
