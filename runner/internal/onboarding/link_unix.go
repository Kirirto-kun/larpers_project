//go:build linux || darwin

package onboarding

import "os"

func linked(st os.FileInfo) bool { return st.Mode()&os.ModeSymlink != 0 }
