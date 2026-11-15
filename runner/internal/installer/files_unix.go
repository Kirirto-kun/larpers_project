//go:build linux || darwin

package installer

import "os"

func linked(st os.FileInfo) bool    { return st.Mode()&os.ModeSymlink != 0 }
func protect(name string) error     { return os.Chmod(name, 0700) }
func protectFile(name string) error { return os.Chmod(name, 0600) }
