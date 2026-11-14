//go:build linux || darwin

package session

import (
	"os"
	"syscall"
)

func openSharedRead(path string) (*os.File, error) {
	handle, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_CLOEXEC|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, &os.PathError{Op: "open", Path: path, Err: err}
	}
	return os.NewFile(uintptr(handle), path), nil
}
