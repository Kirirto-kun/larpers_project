//go:build windows

package session

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestAtomicJSONSurvivesShortWindowsReaderWithoutPartialPublication(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lease.json")
	if e := AtomicJSON(path, map[string]int{"sequence": 1}); e != nil {
		t.Fatal(e)
	}
	name, _ := syscall.UTF16PtrFromString(path)
	handle, e := syscall.CreateFile(name, syscall.GENERIC_READ, syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE, nil, syscall.OPEN_EXISTING, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if e != nil {
		t.Fatal(e)
	}
	released := make(chan struct{})
	go func() { time.Sleep(60 * time.Millisecond); syscall.CloseHandle(handle); close(released) }()
	e = AtomicJSON(path, map[string]int{"sequence": 2})
	<-released
	if e != nil {
		t.Fatalf("short reader made a healthy renewal fail: %v", e)
	}
	raw, e := os.ReadFile(path)
	if e != nil || string(raw) != "{\"sequence\":2}" {
		t.Fatal("partial or stale JSON", string(raw), e)
	}
}

func TestReadControlSurvivesShortWindowsSharingLock(t *testing.T) {
	dir := t.TempDir()
	if err := Control(dir, "stop"); err != nil {
		t.Fatal(err)
	}
	name, _ := syscall.UTF16PtrFromString(filepath.Join(dir, "control.json"))
	handle, err := syscall.CreateFile(name, syscall.GENERIC_READ|syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_EXISTING, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		t.Fatal(err)
	}
	released := make(chan struct{})
	go func() {
		time.Sleep(60 * time.Millisecond)
		syscall.CloseHandle(handle)
		close(released)
	}()
	action, err := ReadControl(dir)
	<-released
	if err != nil || action != "stop" {
		t.Fatalf("short sharing lock hid owner stop: action=%q err=%v", action, err)
	}
}

func TestReadControlDoesNotIgnorePersistentWindowsSharingLock(t *testing.T) {
	dir := t.TempDir()
	if err := Control(dir, "stop"); err != nil {
		t.Fatal(err)
	}
	name, _ := syscall.UTF16PtrFromString(filepath.Join(dir, "control.json"))
	handle, err := syscall.CreateFile(name, syscall.GENERIC_READ|syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_EXISTING, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer syscall.CloseHandle(handle)
	started := time.Now()
	action, err := ReadControl(dir)
	if err == nil || action != "" || time.Since(started) > time.Second {
		t.Fatalf("persistent lock was ignored or unbounded: action=%q err=%v", action, err)
	}
}
