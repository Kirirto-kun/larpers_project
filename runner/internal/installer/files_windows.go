//go:build windows

package installer

import (
	"os"
	"os/user"
	"syscall"
	"unsafe"
)

func linked(st os.FileInfo) bool {
	if st.Mode()&os.ModeSymlink != 0 {
		return true
	}
	data, ok := st.Sys().(*syscall.Win32FileAttributeData)
	return ok && data.FileAttributes&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0
}
func protect(name string) error {
	return privateACL(name, true)
}

func protectFile(name string) error { return privateACL(name, false) }

func privateACL(name string, directory bool) error {
	u, err := user.Current()
	if err != nil {
		return err
	}
	flags := ""
	if directory {
		flags = "OICI"
	}
	sddl, err := syscall.UTF16PtrFromString("D:P(A;" + flags + ";FA;;;" + u.Uid + ")")
	if err != nil {
		return err
	}
	path, err := syscall.UTF16PtrFromString(name)
	if err != nil {
		return err
	}
	advapi := syscall.NewLazyDLL("advapi32.dll")
	convert := advapi.NewProc("ConvertStringSecurityDescriptorToSecurityDescriptorW")
	set := advapi.NewProc("SetFileSecurityW")
	var descriptor uintptr
	r, _, callErr := convert.Call(uintptr(unsafe.Pointer(sddl)), 1, uintptr(unsafe.Pointer(&descriptor)), 0)
	if r == 0 {
		return callErr
	}
	defer syscall.LocalFree(syscall.Handle(descriptor))
	// Replace the DACL, including explicit grants; prevent inheritance from a
	// broadly accessible parent from reintroducing access for other users.
	r, _, callErr = set.Call(uintptr(unsafe.Pointer(path)), 0x80000004, descriptor)
	if r == 0 {
		return callErr
	}
	return nil
}
