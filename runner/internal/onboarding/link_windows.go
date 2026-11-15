//go:build windows

package onboarding

import (
	"os"
	"syscall"
)

func linked(st os.FileInfo) bool {
	if st.Mode()&os.ModeSymlink != 0 {
		return true
	}
	data, ok := st.Sys().(*syscall.Win32FileAttributeData)
	return ok && data.FileAttributes&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0
}
