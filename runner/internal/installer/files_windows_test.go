//go:build windows

package installer

import (
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"unsafe"
)

// An existing cache can carry explicit permissions inherited from an older
// installer. Restricting inheritance alone must not leave those grants intact.
func TestPrivateCacheReplacesExistingExplicitACLGrants(t *testing.T) {
	root := filepath.Join(t.TempDir(), "cache")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	setTestACL(t, root, "D:P(A;OICI;FA;;;WD)")
	if err := privateDir(root); err != nil {
		t.Fatal(err)
	}
	sddl := directorySecurity(t, root)
	u, err := user.Current()
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(sddl, ";;;WD)") || strings.Count(sddl, "(A;") != 1 || !strings.Contains(sddl, ";;;"+u.Uid+")") || !strings.HasPrefix(sddl, "D:P") {
		t.Fatalf("cache ACL is not owner-only: %s", sddl)
	}
}

func directorySecurity(t *testing.T, root string) string {
	t.Helper()
	advapi := syscall.NewLazyDLL("advapi32.dll")
	get := advapi.NewProc("GetFileSecurityW")
	convert := advapi.NewProc("ConvertSecurityDescriptorToStringSecurityDescriptorW")
	name, _ := syscall.UTF16PtrFromString(root)
	var needed uint32
	get.Call(uintptr(unsafe.Pointer(name)), 4, 0, 0, uintptr(unsafe.Pointer(&needed)))
	data := make([]byte, needed)
	r, _, err := get.Call(uintptr(unsafe.Pointer(name)), 4, uintptr(unsafe.Pointer(&data[0])), uintptr(needed), uintptr(unsafe.Pointer(&needed)))
	if r == 0 {
		t.Fatal(err)
	}
	var result *uint16
	r, _, err = convert.Call(uintptr(unsafe.Pointer(&data[0])), 1, 4, uintptr(unsafe.Pointer(&result)), 0)
	if r == 0 {
		t.Fatal(err)
	}
	defer syscall.LocalFree(syscall.Handle(uintptr(unsafe.Pointer(result))))
	var units []uint16
	for p := unsafe.Pointer(result); ; p = unsafe.Add(p, 2) {
		v := *(*uint16)(p)
		if v == 0 {
			break
		}
		units = append(units, v)
	}
	return syscall.UTF16ToString(units)
}

func setTestACL(t *testing.T, name, sddl string) {
	t.Helper()
	advapi := syscall.NewLazyDLL("advapi32.dll")
	convert := advapi.NewProc("ConvertStringSecurityDescriptorToSecurityDescriptorW")
	set := advapi.NewProc("SetFileSecurityW")
	text, _ := syscall.UTF16PtrFromString(sddl)
	path, _ := syscall.UTF16PtrFromString(name)
	var descriptor uintptr
	r, _, err := convert.Call(uintptr(unsafe.Pointer(text)), 1, uintptr(unsafe.Pointer(&descriptor)), 0)
	if r == 0 {
		t.Fatal(err)
	}
	defer syscall.LocalFree(syscall.Handle(descriptor))
	r, _, err = set.Call(uintptr(unsafe.Pointer(path)), 0x80000004, descriptor)
	if r == 0 {
		t.Fatal(err)
	}
}
