package containers

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
)

// Only task-input subtrees become world-readable; the enclosing runner state is
// private and the container sees these trees solely through read-only bind mounts.
func inputModes(p Paths, readonly bool) error {
	for _, root := range []string{p.Inputs, p.Data, p.Resume} {
		if root == "" {
			continue
		}
		if _, e := os.Lstat(root); os.IsNotExist(e) {
			continue
		}
		e := filepath.WalkDir(root, func(path string, d fs.DirEntry, e error) error {
			if e != nil {
				return e
			}
			if d.Type()&os.ModeSymlink != 0 {
				return errors.New("symlink in task input tree")
			}
			mode := os.FileMode(0444)
			if d.IsDir() {
				mode = 0555
			} else if !d.Type().IsRegular() {
				return errors.New("special file in task input tree")
			}
			if !readonly {
				mode = 0600
				if d.IsDir() {
					mode = 0700
				}
			}
			return os.Chmod(path, mode)
		})
		if e != nil {
			return e
		}
	}
	return nil
}
func PrepareInputs(p Paths) error { return inputModes(p, true) }
func RestoreInputs(p Paths) error { return inputModes(p, false) }
