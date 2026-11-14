//go:build linux || darwin

package session

import (
	"os"
	"path/filepath"
	"sync"
	"syscall"
)

func replaceFile(source, destination string) error { return os.Rename(source, destination) }
func readFile(path string) ([]byte, error)         { return os.ReadFile(path) }

func protect(path string) error {
	st, e := os.Stat(path)
	if e != nil {
		return e
	}
	mode := os.FileMode(0600)
	if st.IsDir() {
		mode = 0700
	}
	return os.Chmod(path, mode)
}
func Lock(dir string) (func(), error) {
	if e := os.MkdirAll(dir, 0700); e != nil {
		return nil, e
	}
	f, e := os.OpenFile(filepath.Join(dir, "runner.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		f.Close()
		return nil, e
	}
	var once sync.Once
	return func() { once.Do(func() { syscall.Flock(int(f.Fd()), syscall.LOCK_UN); f.Close() }) }, nil
}
