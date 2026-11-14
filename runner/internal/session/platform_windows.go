//go:build windows

package session

import (
	"errors"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

func replaceFile(source, destination string) error {
	deadline := time.Now().Add(250 * time.Millisecond)
	for {
		err := os.Rename(source, destination)
		if err == nil {
			return nil
		}
		if (!errors.Is(err, syscall.ERROR_ACCESS_DENIED) && !errors.Is(err, syscall.Errno(32)) && !errors.Is(err, syscall.Errno(33))) || !time.Now().Before(deadline) {
			return err
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// A filesystem filter or an overlapping atomic replacement can briefly deny a
// read. Keep owner control responsive without treating that short lock as fatal.
func readFile(path string) ([]byte, error) {
	deadline := time.Now().Add(250 * time.Millisecond)
	for {
		data, err := os.ReadFile(path)
		if err == nil {
			return data, nil
		}
		if (!errors.Is(err, syscall.ERROR_ACCESS_DENIED) && !errors.Is(err, syscall.Errno(32)) && !errors.Is(err, syscall.Errno(33))) || !time.Now().Before(deadline) {
			return nil, err
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func protect(path string) error {
	u, e := user.Current()
	if e != nil {
		return e
	}
	grant := "*" + u.Uid + ":(F)"
	if st, err := os.Stat(path); err != nil {
		return err
	} else if st.IsDir() {
		grant = "*" + u.Uid + ":(OI)(CI)(F)"
	}
	cmd := exec.Command("icacls.exe", path, "/inheritance:r", "/grant:r", grant)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return cmd.Run()
}
func Lock(dir string) (func(), error) {
	if e := os.MkdirAll(dir, 0700); e != nil {
		return nil, e
	}
	f, e := os.OpenFile(filepath.Join(dir, "runner.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	dll := syscall.NewLazyDLL("kernel32.dll")
	lock := dll.NewProc("LockFileEx")
	unlock := dll.NewProc("UnlockFileEx")
	var ov syscall.Overlapped
	r, _, e := lock.Call(f.Fd(), 3, 0, 1, 0, uintptr(unsafe.Pointer(&ov)))
	if r == 0 {
		f.Close()
		return nil, e
	}
	var once sync.Once
	return func() { once.Do(func() { unlock.Call(f.Fd(), 0, 1, 0, uintptr(unsafe.Pointer(&ov))); f.Close() }) }, nil
}
