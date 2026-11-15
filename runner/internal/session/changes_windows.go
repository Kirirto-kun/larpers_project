//go:build windows

package session

import (
	"context"
	"syscall"
	"time"
	"unsafe"
)

// Directory notifications avoid repeatedly opening the control file on Windows,
// where filesystem filter work is charged to the otherwise idle runner process.
func ControlChanges(ctx context.Context, dir string) <-chan struct{} {
	events := make(chan struct{}, 1)
	dll := syscall.NewLazyDLL("kernel32.dll")
	first := dll.NewProc("FindFirstChangeNotificationW")
	next := dll.NewProc("FindNextChangeNotification")
	closeChange := dll.NewProc("FindCloseChangeNotification")
	path, e := syscall.UTF16PtrFromString(dir)
	if e != nil {
		return controlTicks(ctx)
	}
	handle, _, _ := first.Call(uintptr(unsafe.Pointer(path)), 0, 0x11)
	if handle == ^uintptr(0) || handle == 0 {
		return controlTicks(ctx)
	}
	go func() {
		defer close(events)
		defer closeChange.Call(handle)
		for {
			select {
			case <-ctx.Done():
				return
			default:
			}
			status, e := syscall.WaitForSingleObject(syscall.Handle(handle), 250)
			if e != nil {
				return
			}
			if status == syscall.WAIT_OBJECT_0 {
				select {
				case events <- struct{}{}:
				default:
				}
				if result, _, _ := next.Call(handle); result == 0 {
					return
				}
			}
		}
	}()
	return events
}

func controlTicks(ctx context.Context) <-chan struct{} {
	events := make(chan struct{}, 1)
	go func() {
		defer close(events)
		ticker := time.NewTicker(500 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				select {
				case events <- struct{}{}:
				default:
				}
			}
		}
	}()
	return events
}
