//go:build linux || darwin

package installer

import (
	"os"
	"testing"
)

func directorySecurity(t *testing.T, root string) string {
	t.Helper()
	info, err := os.Stat(root)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode().String()
}
